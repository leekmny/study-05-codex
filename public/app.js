"use strict";

const get = (id) => document.getElementById(id);
const page = document.body.dataset.page;
const form = get(`${page}-form`);
const submit = form.querySelector('button[type="submit"]');
const resultView = get("result-panel");
const result = get("result-content");
const empty = get("result-empty");
const loading = get("result-loading");
const status = get("request-status");
const error = get("request-error");
let pending = false;
let hasResult = false;

async function post(path, contentType, body) {
  let response;
  try {
    response = await fetch(path, {
      method: "POST",
      mode: "same-origin",
      headers: { "Content-Type": contentType },
      body,
    });
  } catch {
    throw new Error("서버에 연결하지 못했습니다. 연결을 확인한 후 다시 시도해 주세요.");
  }

  const data = await response.json().catch(() => null);
  if (!response.ok) {
    throw new Error(typeof data?.error === "string" && data.error.trim()
      ? data.error
      : "요청을 처리하지 못했습니다. 잠시 후 다시 시도해 주세요.");
  }
  return data;
}

function setPending(active) {
  pending = active;
  submit.setAttribute("aria-disabled", String(active));
  resultView.setAttribute("aria-busy", String(active));
  loading.hidden = !active;
  result.hidden = active || !hasResult;
  empty.hidden = active || hasResult;
}

function isText(value) {
  return typeof value === "string" && Boolean(value.trim());
}

function invalidResult() {
  return new Error("결과를 읽을 수 없습니다. 잠시 후 다시 시도해 주세요.");
}

function makeDiary() {
  const input = get("diary-text");
  const list = get("history-list");
  const historyEmpty = get("history-empty");
  const storageKey = "study-05.diary.history";
  const dateFormat = new Intl.DateTimeFormat("ko-KR", {
    dateStyle: "medium",
    timeStyle: "short",
  });

  function readHistory() {
    const raw = localStorage.getItem(storageKey);
    try {
      const saved = JSON.parse(raw);
      return Array.isArray(saved)
        ? saved.filter((entry) => isText(entry?.text) && isText(entry.emotion)
          && isText(entry.message) && isText(entry.timestamp)
          && Number.isFinite(Date.parse(entry.timestamp)))
        : [];
    } catch {
      return [];
    }
  }

  function renderHistory(entries) {
    entries.sort((first, second) => Date.parse(second.timestamp) - Date.parse(first.timestamp));
    const items = entries.map((entry) => {
      const item = document.createElement("li");
      const heading = document.createElement("div");
      heading.className = "history-item-heading";
      const emotion = document.createElement("h3");
      emotion.textContent = entry.emotion;
      const time = document.createElement("time");
      time.dateTime = entry.timestamp;
      time.textContent = dateFormat.format(new Date(entry.timestamp));
      heading.append(emotion, time);

      const text = document.createElement("p");
      text.className = "history-text";
      text.textContent = entry.text;
      const message = document.createElement("p");
      message.className = "history-message";
      message.textContent = entry.message;
      item.append(heading, text, message);
      return item;
    });
    historyEmpty.textContent = "아직 저장된 기록이 없습니다.";
    historyEmpty.hidden = items.length > 0;
    list.hidden = items.length === 0;
    list.replaceChildren(...items);
  }

  function refreshHistory() {
    try {
      renderHistory(readHistory());
    } catch {
      historyEmpty.textContent = "이 브라우저에서 저장된 기록을 읽을 수 없습니다.";
      historyEmpty.hidden = false;
    }
  }

  refreshHistory();
  window.addEventListener("storage", (event) => {
    if (event.key === storageKey || event.key === null) refreshHistory();
  });

  return {
    loadingMessage: "감정을 분석하고 있습니다.",
    async run() {
      const text = input.value.trim();
      if (!text) {
        input.focus();
        throw new Error("오늘의 기록을 입력해 주세요.");
      }
      const data = await post("/api/diary", "application/json", JSON.stringify({ text }));
      if (!isText(data?.emotion) || !isText(data.message) || !isText(data.model)) {
        throw invalidResult();
      }
      get("result-emotion").textContent = data.emotion;
      get("result-message").textContent = data.message;
      get("result-model").textContent = data.model;

      const entry = {
        timestamp: new Date().toISOString(),
        text,
        emotion: data.emotion,
        message: data.message,
      };
      try {
        const entries = [entry, ...readHistory()];
        localStorage.setItem(storageKey, JSON.stringify(entries));
        renderHistory(entries);
        return "분석이 완료되었습니다. 지난 기록에 저장했습니다.";
      } catch {
        return "분석이 완료되었습니다. 이 브라우저에 기록을 저장하지 못했습니다.";
      }
    },
  };
}

function renderSummary(summary) {
  const content = document.createDocumentFragment();
  let list = null;
  for (const line of summary.split(/\r?\n/)) {
    const text = line.trim().replace(/\*\*([^*]+)\*\*/g, "$1");
    if (!text) continue;
    const bullet = text.match(/^(?:[-*•]|\d+\.)\s+(.+)$/);
    if (bullet) {
      if (!list) {
        list = document.createElement("ul");
        content.append(list);
      }
      const item = document.createElement("li");
      item.textContent = bullet[1];
      list.append(item);
    } else {
      list = null;
      const paragraph = document.createElement("p");
      paragraph.textContent = text.replace(/^#{1,6}\s+/, "");
      content.append(paragraph);
    }
  }
  get("result-summary").replaceChildren(content);
}

function makePdf() {
  const input = get("pdf-file");
  const maxBytes = 10 * 1024 * 1024;
  const pageFormat = new Intl.NumberFormat("ko-KR");

  return {
    loadingMessage: "PDF를 요약하고 있습니다.",
    async run() {
      const file = input.files[0];
      const mode = form.querySelector('input[name="mode"]:checked').value;
      if (!file || (!/\.pdf$/i.test(file.name) && file.type !== "application/pdf")) {
        input.focus();
        throw new Error("PDF 파일을 선택해 주세요.");
      }
      if (file.size === 0 || file.size > maxBytes) {
        input.focus();
        throw new Error("내용이 있는 10 MiB 이하의 PDF 파일을 선택해 주세요.");
      }

      const data = await post(`/api/pdf?mode=${mode}`, "application/pdf", file);
      if (!isText(data?.summary) || !isText(data.model)
        || !Number.isSafeInteger(data.pages) || data.pages < 1) {
        throw invalidResult();
      }
      get("result-file").textContent = file.name;
      get("result-pages").textContent = `${pageFormat.format(data.pages)}쪽`;
      renderSummary(data.summary);
      get("result-model").textContent = data.model;
      return "요약이 완료되었습니다.";
    },
  };
}

const app = page === "diary" ? makeDiary() : makePdf();
form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (pending) return;

  error.textContent = "";
  status.textContent = app.loadingMessage;
  setPending(true);
  try {
    status.textContent = await app.run();
    hasResult = true;
  } catch (failure) {
    status.textContent = "";
    error.textContent = failure.message;
  } finally {
    setPending(false);
  }
});
submit.disabled = false;
