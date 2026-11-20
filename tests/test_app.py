import json
from pathlib import Path
from threading import Thread
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import study_app


SAMPLE_PDF = Path(__file__).resolve().parents[1] / "samples" / "한글맞춤법.pdf"
MODEL_ID = "google/gemma-4-31b-it:free"
DIARY_TEXT = "어려웠던 실습 과제를 끝내서 뿌듯했다."
DIARY_REPLY = {"emotion": "뿌듯함", "message": "오늘의 노력을 스스로 인정해도 좋아요."}
PDF_TITLE = "한글 맞춤법 이해와 실용"
PDF_SUMMARY = "한글 맞춤법의 기본 원칙은 소리대로 적되 어법에 맞도록 하는 것이다."


class StudyAppTests(unittest.TestCase):
    def setUp(self):
        self.completion_calls = []
        self.server = study_app.make_server(
            host="127.0.0.1", port=0, complete=self.complete
        )
        host, port = self.server.server_address
        self.base_url = f"http://{host}:{port}"
        self.thread = Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def complete(self, system_prompt, user_text, max_tokens):
        self.completion_calls.append((system_prompt, user_text, max_tokens))
        if DIARY_TEXT in user_text:
            return json.dumps(DIARY_REPLY, ensure_ascii=False), MODEL_ID
        return PDF_SUMMARY, MODEL_ID

    def request(self, path, body=None, content_type=None):
        headers = {"Content-Type": content_type} if content_type else {}
        request = Request(self.base_url + path, data=body, headers=headers)
        return urlopen(request, timeout=10)

    def test_serves_both_app_pages(self):
        for path in ("/index.html", "/index_pdf.html"):
            with self.subTest(path=path), self.request(path) as response:
                self.assertEqual(response.status, 200)
                self.assertEqual(response.headers.get_content_type(), "text/html")

    def test_diary_returns_emotion_message_and_served_model(self):
        body = json.dumps({"text": DIARY_TEXT}).encode()
        with self.request("/api/diary", body, "application/json") as response:
            result = json.load(response)

        self.assertEqual(result["emotion"], DIARY_REPLY["emotion"])
        self.assertEqual(result["message"], DIARY_REPLY["message"])
        self.assertEqual(result["model"], MODEL_ID)
        self.assertEqual(len(self.completion_calls), 1)
        self.assertIn(DIARY_TEXT, self.completion_calls[0][1])

    def test_diary_rejects_invalid_input_before_completion(self):
        for body in (b"{", b"[]", b"{}", b'{"text": "   "}', b'{"text": 10}'):
            with self.subTest(body=body):
                with self.assertRaises(HTTPError) as error:
                    self.request("/api/diary", body, "application/json")
                self.assertEqual(error.exception.code, 400)
                error.exception.close()

        self.assertEqual(self.completion_calls, [])

    def test_pdf_extracts_real_document_for_each_summary_mode(self):
        pdf = SAMPLE_PDF.read_bytes()

        for mode in ("standard", "brief"):
            with self.subTest(mode=mode):
                with self.request(f"/api/pdf?mode={mode}", pdf, "application/pdf") as response:
                    result = json.load(response)

                self.assertEqual(result["summary"], PDF_SUMMARY)
                self.assertEqual(result["model"], MODEL_ID)
                self.assertEqual(result["pages"], 2)

        self.assertEqual(len(self.completion_calls), 2)
        for _, user_text, _ in self.completion_calls:
            self.assertIn(PDF_TITLE, user_text)
        self.assertNotEqual(self.completion_calls[0], self.completion_calls[1])

    def test_pdf_rejects_non_pdf_bytes(self):
        with self.assertRaises(HTTPError) as error:
            self.request("/api/pdf?mode=standard", b"not a PDF", "application/pdf")
        self.assertEqual(error.exception.code, 400)
        error.exception.close()
        self.assertEqual(self.completion_calls, [])

    def test_pdf_rejects_unknown_summary_mode(self):
        with self.assertRaises(HTTPError) as error:
            self.request("/api/pdf?mode=unknown", SAMPLE_PDF.read_bytes(), "application/pdf")
        self.assertEqual(error.exception.code, 400)
        error.exception.close()
        self.assertEqual(self.completion_calls, [])


if __name__ == "__main__":
    unittest.main()
