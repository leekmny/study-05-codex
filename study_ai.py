"""OpenRouter completion and diary response validation."""

from __future__ import annotations

import json
import os
import re
from http.client import HTTPException
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener


PROJECT_DIR = Path(__file__).resolve().parent
FALLBACK_ENV = Path(__file__).resolve().parent.parent.parent / "Study-04" / "Api.env"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "nvidia/nemotron-3.5-lightning:free"
SETTINGS = ("OPENROUTER_API_KEY", "OPENROUTER_MODEL")
MAX_RESPONSE_BYTES = 128 * 1024
FREE_MODEL = re.compile(r"[A-Za-z0-9][\w.-]*/[A-Za-z0-9][\w.-]*:free", re.ASCII)
INVALID_REPLY = "모델 응답을 읽을 수 없습니다. 잠시 후 다시 시도해 주세요."
TIMEOUT = "모델 응답 시간이 초과되었습니다. 다시 시도해 주세요."
SERVICE_ERROR = "모델 서비스에 연결할 수 없습니다. 잠시 후 다시 시도해 주세요."
UPSTREAM_MESSAGES = {
    401: "OpenRouter 인증에 실패했습니다. API 키를 확인해 주세요.",
    429: "무료 모델 요청 한도에 도달했습니다. 잠시 후 다시 시도해 주세요.",
}


class UpstreamError(Exception):
    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status
        self.message = message


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _read_settings(path: Path, names: set[str]) -> dict:
    settings = {}
    try:
        with path.open(encoding="utf-8-sig") as source:
            for line in source:
                name, separator, value = line.strip().removeprefix("export ").partition("=")
                name = name.strip()
                if not separator or name not in names:
                    continue
                value = value.strip()
                if value.startswith(("\"", "'")):
                    end = value.find(value[0], 1)
                    value = value[1:end] if end != -1 else ""
                else:
                    value = re.split(r"\s+#|^#", value, maxsplit=1)[0]
                if value:
                    settings[name] = value
    except FileNotFoundError:
        pass
    except (OSError, UnicodeError):
        raise UpstreamError("OpenRouter 설정 파일을 읽을 수 없습니다.", 503) from None
    return settings


def _configuration() -> tuple[str, str]:
    settings = {name: os.environ.get(name, "").strip() for name in SETTINGS}
    missing = {name for name, value in settings.items() if not value}
    for path in (PROJECT_DIR / ".env", FALLBACK_ENV):
        if not missing:
            break
        found = _read_settings(path, missing)
        settings.update(found)
        missing.difference_update(found)

    api_key = settings["OPENROUTER_API_KEY"]
    if not api_key:
        raise UpstreamError("OpenRouter API 키를 설정한 뒤 다시 시도해 주세요.", 503)
    if not api_key.isascii() or not api_key.isprintable() or any(ch.isspace() for ch in api_key):
        raise UpstreamError("OpenRouter API 키 설정을 확인해 주세요.", 503)

    model = settings["OPENROUTER_MODEL"] or DEFAULT_MODEL
    if not FREE_MODEL.fullmatch(model) or "deepseek" in model.casefold():
        raise UpstreamError("DeepSeek을 제외한 명시적인 :free 모델을 설정해 주세요.", 503)
    return api_key, model


def complete_openrouter(system_prompt: str, user_text: str, max_tokens: int) -> tuple[str, str]:
    api_key, model = _configuration()
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_text},
        ],
        "temperature": 0.2,
        "max_tokens": max_tokens,
        "reasoning": {"enabled": False},
    }
    try:
        request_data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    except UnicodeEncodeError:
        raise UpstreamError("입력에 처리할 수 없는 문자가 포함되어 있습니다.", 400) from None
    request = Request(
        OPENROUTER_URL,
        data=request_data,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )

    try:
        with build_opener(_NoRedirects()).open(request, timeout=45) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except HTTPError as error:
        code = error.code
        error.close()
        if code in UPSTREAM_MESSAGES:
            raise UpstreamError(UPSTREAM_MESSAGES[code], code) from None
        if code in (408, 504):
            raise UpstreamError(TIMEOUT, 504) from None
        raise UpstreamError(SERVICE_ERROR) from None
    except TimeoutError:
        raise UpstreamError(TIMEOUT, 504) from None
    except URLError as error:
        if isinstance(error.reason, TimeoutError):
            raise UpstreamError(TIMEOUT, 504) from None
        raise UpstreamError(SERVICE_ERROR) from None
    except (HTTPException, OSError, ValueError):
        raise UpstreamError(SERVICE_ERROR) from None

    if len(raw) > MAX_RESPONSE_BYTES:
        raise UpstreamError(INVALID_REPLY)
    try:
        payload = json.loads(raw.decode("utf-8"))
        reply = payload["choices"][0]["message"]["content"]
        served_model = payload["model"]
    except (KeyError, IndexError, TypeError, ValueError, RecursionError):
        raise UpstreamError(INVALID_REPLY) from None
    if not all(_is_nonempty_utf8(item) for item in (reply, served_model)):
        raise UpstreamError(INVALID_REPLY)
    return reply.strip(), served_model.strip()


def _is_nonempty_utf8(value: object) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _diary_fields(value) -> dict | None:
    if not isinstance(value, dict):
        return None
    fields = {name: value.get(name) for name in ("emotion", "message")}
    if all(_is_nonempty_utf8(item) for item in fields.values()):
        return {name: item.strip() for name, item in fields.items()}
    return None


def parse_diary_reply(text: str) -> dict:
    if not isinstance(text, str) or len(text) > MAX_RESPONSE_BYTES:
        raise UpstreamError(INVALID_REPLY)

    decoder = json.JSONDecoder()
    try:
        value = decoder.decode(text.strip())
    except (ValueError, RecursionError):
        end = 0
        for match in re.finditer(r"[\[{]", text):
            if match.start() < end:
                continue
            try:
                value, end = decoder.raw_decode(text, match.start())
            except (ValueError, RecursionError):
                continue
            fields = _diary_fields(value)
            if fields is not None:
                return fields
    else:
        fields = _diary_fields(value)
        if fields is not None:
            return fields
    raise UpstreamError(INVALID_REPLY)
