"""Endpoints that reject a temperature field must still work."""

from __future__ import annotations

import io
import sys
import unittest
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import discussion_judge as dj  # noqa: E402
import draft_discussion as dd  # noqa: E402


def temperature_400() -> HTTPError:
    body = b'{"error":{"message":"temperature is deprecated for this model"}}'
    return HTTPError(
        "http://x/v1/chat/completions", 400, "Bad Request", {}, io.BytesIO(body)
    )


def other_400() -> HTTPError:
    body = b'{"error":{"message":"context length exceeded"}}'
    return HTTPError(
        "http://x/v1/chat/completions", 400, "Bad Request", {}, io.BytesIO(body)
    )


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def chat_body() -> bytes:
    return b'{"choices":[{"message":{"content":"{\\"claims\\":[]}"}}]}'


class DraftTemperatureTest(unittest.TestCase):
    def test_flag_omits_temperature(self) -> None:
        sent: list[dict] = []

        def fake_urlopen(request, timeout=None):
            sent.append(dd.json.loads(request.data.decode("utf-8")))
            return FakeResponse(chat_body())

        with mock.patch.object(dd, "urlopen", fake_urlopen):
            dd.complete_chat(
                [{"role": "user", "content": "hi"}],
                base_url="http://x/v1",
                model="m",
                api_key="",
                timeout=5,
                send_temperature=False,
            )
        self.assertEqual(len(sent), 1)
        self.assertNotIn("temperature", sent[0])

    def test_retries_without_temperature_on_400(self) -> None:
        sent: list[dict] = []
        calls = {"n": 0}

        def fake_urlopen(request, timeout=None):
            sent.append(dd.json.loads(request.data.decode("utf-8")))
            calls["n"] += 1
            if calls["n"] == 1:
                raise temperature_400()
            return FakeResponse(chat_body())

        with mock.patch.object(dd, "urlopen", fake_urlopen):
            dd.complete_chat(
                [{"role": "user", "content": "hi"}],
                base_url="http://x/v1",
                model="m",
                api_key="",
                timeout=5,
            )
        self.assertEqual(len(sent), 2)
        self.assertIn("temperature", sent[0])
        self.assertNotIn("temperature", sent[1])

    def test_other_400_is_not_retried(self) -> None:
        def fake_urlopen(request, timeout=None):
            raise other_400()

        with mock.patch.object(dd, "urlopen", fake_urlopen):
            with self.assertRaises(HTTPError):
                dd.complete_chat(
                    [{"role": "user", "content": "hi"}],
                    base_url="http://x/v1",
                    model="m",
                    api_key="",
                    timeout=5,
                )


class JudgeTemperatureTest(unittest.TestCase):
    def test_retry_then_stays_off(self) -> None:
        sent: list[dict] = []
        calls = {"n": 0}

        def fake_urlopen(request, timeout=None):
            sent.append(dj.json.loads(request.data.decode("utf-8")))
            calls["n"] += 1
            if calls["n"] == 1:
                raise temperature_400()
            return FakeResponse(b'{"choices":[{"message":{"content":"{}"}}]}')

        complete = dj.openai_complete(
            base_url="http://x/v1", model="m", api_key="", timeout=5
        )
        with mock.patch.object(dj, "urlopen", fake_urlopen):
            complete([{"role": "user", "content": "a"}])
            complete([{"role": "user", "content": "b"}])
        self.assertEqual(len(sent), 3)
        self.assertIn("temperature", sent[0])
        self.assertNotIn("temperature", sent[1])
        self.assertNotIn("temperature", sent[2])

    def test_env_flag_omits_temperature(self) -> None:
        sent: list[dict] = []

        def fake_urlopen(request, timeout=None):
            sent.append(dj.json.loads(request.data.decode("utf-8")))
            return FakeResponse(b'{"choices":[{"message":{"content":"{}"}}]}')

        judge = dj.judge_from_env(
            {
                "DISCUSSION_JUDGE": "openai-compatible",
                "DISCUSSION_JUDGE_MODEL": "m",
                "DISCUSSION_JUDGE_NO_TEMPERATURE": "1",
            }
        )
        with mock.patch.object(dj, "urlopen", fake_urlopen):
            judge.complete([{"role": "user", "content": "a"}])
        self.assertNotIn("temperature", sent[0])


if __name__ == "__main__":
    unittest.main()
