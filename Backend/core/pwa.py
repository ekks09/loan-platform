"""Deterministic PWA cache version derived from the app shell itself.

The service worker's cache name embeds a version so browsers fetch new
precached assets exactly once per change. Previously that version was a
hand-bumped string in settings — which humans forget, so stale shells could
be served indefinitely (the privacy and offline rules were fine; the
invalidation discipline was not).

This module replaces the manual bump:

* the default is a sha256 fingerprint of every file that makes up the app
  shell (``Frontend`` static assets, the service worker source and the
  offline page templates). Identical code yields an identical version on any
  machine (deterministic, so rollouts don't differ per instance) and ANY
  change to a precached asset yields a new version automatically;
* deployment pipelines can still pin the version explicitly to a build id or
  commit SHA by setting PWA_CACHE_VERSION — the override always wins;
* the fingerprint is computed once at process start (module import).

The offline caching rules themselves live in Frontend/sw.js and are untouched.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

_FINGERPRINT_LENGTH = 16
# Directories whose entire contents constitute the cacheable shell.
_SHELL_DIRS = ("css", "js", "icons", "img")
# Individual files that feed the fingerprint (service worker source, plus
# any HTML templates rendered by the pre-cached offline page).
_SHELL_FILES = ("sw.js", "offline.html")


def shell_fingerprint(frontend_root: Path) -> str:
    """sha256 over every app-shell file (rel-path keyed), truncated hex."""
    digest = hashlib.sha256()
    frontend_root = Path(frontend_root)
    entries: list[tuple[str, Path]] = []
    for directory in _SHELL_DIRS:
        base = frontend_root / directory
        if not base.is_dir():
            continue
        entries.extend(
            (f"{directory}/{path.relative_to(base).as_posix()}", path)
            for path in base.rglob("*")
            if path.is_file()
        )
    for name in _SHELL_FILES:
        candidate = frontend_root / name
        if candidate.is_file():
            entries.append((name, candidate))
    for rel_path, path in sorted(set(entries)):
        digest.update(rel_path.encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()[:_FINGERPRINT_LENGTH]


def resolve_cache_version(env_override: str, frontend_root: Path) -> str:
    """PWA_CACHE_VERSION override wins; otherwise the shell fingerprint."""
    if env_override.strip():
        return env_override.strip()
    return shell_fingerprint(frontend_root)
