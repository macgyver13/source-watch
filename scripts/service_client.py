#!/usr/bin/env python3
"""HTTP client for Source Watch service-mode ingest."""
from __future__ import annotations

import json
import os
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen


class ServiceError(RuntimeError):
    """Non-2xx or unparseable response from the Source Watch service."""



class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ServiceError(f"refusing redirect {code} -> {newurl} (bearer token would be forwarded)")


def no_redirect_opener():
    return build_opener(_RejectRedirects()).open

def require_ingest_token() -> str:
    token = os.environ.get("SOURCE_WATCH_INGEST_TOKEN", "").strip()
    if not token:
        raise SystemExit("SOURCE_WATCH_INGEST_TOKEN is required in serving.mode: service")
    return token


class ServiceClient:
    CHUNK_ROWS = 200
    TIMEOUT = 30

    def __init__(self, base_url: str, token: str, opener=None) -> None:
        url = str(base_url or "").strip()
        if url and not url.endswith("/"):
            url += "/"
        self.base_url = url
        self.token = token
        self.opener = opener or no_redirect_opener()

    def _open(self, request):
        try:
            return self.opener(request, timeout=self.TIMEOUT)
        except TypeError:
            return self.opener(request)


    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
            "User-Agent": "source-watch-collector",
        }

    def _request(self, method: str, path: str, body: dict | None = None) -> dict:
        url = f"{self.base_url}{path.lstrip('/')}"
        data = None
        headers = dict(self._headers())
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(url, data=data, headers=headers, method=method)
        try:
            with self._open(request) as response:

                status = getattr(response, "status", None) or response.getcode()
                raw = response.read()
        except HTTPError as exc:
            raw = exc.read() if exc.fp else b""
            raise ServiceError(f"{method} {url} -> {exc.code}: {raw[:200]!r}") from exc
        except URLError as exc:
            raise ServiceError(f"{method} {url} failed: {exc}") from exc
        if status < 200 or status >= 300:
            raise ServiceError(f"{method} {url} -> {status}: {raw[:200]!r}")
        if not raw:
            return {}
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ServiceError(f"{method} {url} unparseable body: {raw[:200]!r}") from exc
        if not isinstance(payload, dict):
            raise ServiceError(f"{method} {url} expected object, got {type(payload).__name__}")
        return payload

    def collector_config(self) -> dict:
        return self._request("GET", "api/collector/config")

    def collector_state(self) -> tuple[dict, dict, dict]:
        payload = self._request("GET", "api/collector/state")
        items = {
            row["id"]: row
            for row in payload.get("items") or []
            if isinstance(row, dict) and row.get("id")
        }
        projects = {
            row["id"]: row
            for row in payload.get("projects") or []
            if isinstance(row, dict) and row.get("id")
        }
        sources = {
            row["id"]: row
            for row in payload.get("sources") or []
            if isinstance(row, dict) and row.get("id")
        }
        return items, projects, sources

    def ingest(self, payload: dict) -> dict:
        begin = self._request("POST", "api/ingest/begin", {
            "generated_at": payload.get("generated_at"),
            "watch": payload.get("watch"),
            "feed_title": payload.get("feed_title"),
            "feed_description": payload.get("feed_description"),
        })
        ingest_id = begin.get("ingest_id")
        if not ingest_id:
            raise ServiceError("ingest/begin did not return ingest_id")
        for kind in ("items", "projects", "sources"):
            rows = list(payload.get(kind) or [])
            for start in range(0, len(rows), self.CHUNK_ROWS):
                chunk = rows[start:start + self.CHUNK_ROWS]
                self._request("POST", "api/ingest/chunk", {
                    "ingest_id": ingest_id,
                    "kind": kind,
                    "rows": chunk,
                })
        result = self._request("POST", "api/ingest/commit", {"ingest_id": ingest_id})
        result.setdefault("ingest_id", ingest_id)
        return result
