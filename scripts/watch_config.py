"""Shared watch.yaml serving-mode parser."""
from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit

SERVING_MODES = ("static", "service")
_LOOPBACK = {"localhost", "127.0.0.1", "::1"}


def normalize_serving(raw, *, require_service_url: bool = True) -> dict:
    """Validate serving config. Raises SystemExit on unknown mode or unsafe URL."""
    if raw is None:
        cfg = {}
    elif isinstance(raw, dict):
        cfg = raw
    else:
        raise SystemExit("serving must be a mapping")
    mode = str(cfg.get("mode") or "static").strip().lower()
    if mode not in SERVING_MODES:
        raise SystemExit(f"serving.mode must be 'static' or 'service', got {mode!r}")
    url = str(cfg.get("service_url") or "").strip()
    if url and not url.endswith("/"):
        url += "/"
    if mode == "service" and require_service_url and not url:
        raise SystemExit("serving.mode: service requires serving.service_url")
    if url:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        try:
            parts.port
        except ValueError as exc:
            raise SystemExit(f"invalid serving.service_url: {exc}") from exc
        if not host or parts.username is not None or parts.password is not None:
            raise SystemExit("serving.service_url must be an origin without credentials")
        if parts.path not in ("", "/") or parts.query or parts.fragment:
            raise SystemExit("serving.service_url must be an origin without a path, query, or fragment")
        if parts.scheme == "https":
            pass
        elif parts.scheme == "http" and host in _LOOPBACK:
            pass
        else:
            raise SystemExit(
                "serving.service_url must use https (http is allowed only for localhost development)"
            )
    return {"mode": mode, "service_url": url}


def load_serving(path: Path, *, require_service_url: bool = True) -> dict:
    """Parse watch.yaml and return normalize_serving(data['serving']).

    SystemExit on missing PyYAML or unparseable YAML (never a silent static fallback).
    """
    try:
        import yaml  # type: ignore
    except Exception:
        raise SystemExit("PyYAML is required to parse watch.yaml")
    try:
        data = yaml.safe_load(path.read_text())
    except Exception as exc:
        raise SystemExit(f"unparseable YAML {path}: {exc}")
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise SystemExit(f"watch config {path} must be a mapping")
    return normalize_serving(data.get("serving"), require_service_url=require_service_url)
