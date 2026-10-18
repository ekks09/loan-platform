"""Environment parsing and validation helpers.

All configuration is read from the environment exactly once at import time so
that a misconfigured deployment fails at boot rather than at the first request.
"""
from __future__ import annotations

import os
from typing import Iterable, Sequence

from django.core.exceptions import ImproperlyConfigured


def env(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    if value is None:
        return default
    value = value.strip()
    return value if value != "" else default


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int) -> int:
    raw = env(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ImproperlyConfigured(f"{name} must be an integer, got {raw!r}") from exc


def env_float(name: str, default: float) -> float:
    raw = env(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ImproperlyConfigured(f"{name} must be a number, got {raw!r}") from exc


def env_list(name: str, default: Sequence[str] = ()) -> list[str]:
    raw = env(name)
    if raw is None:
        return [item for item in default if item]
    return [item.strip() for item in raw.split(",") if item.strip()]


def normalise_hosts(values: Iterable[str], *, debug: bool) -> list[str]:
    """Validate ALLOWED_HOSTS entries.

    Rejects schemes, paths and wildcards (except explicit ``*`` in DEBUG) so a
    typo cannot silently open the host header check.
    """
    hosts: list[str] = []
    for raw in values:
        host = raw.strip()
        if not host:
            continue
        if "://" in host or "/" in host:
            raise ImproperlyConfigured(
                f"ALLOWED_HOSTS entry {raw!r} must be a hostname only (no scheme or path)."
            )
        if host == "*":
            if not debug:
                raise ImproperlyConfigured("ALLOWED_HOSTS='*' is not permitted outside DEBUG.")
            hosts.append(host)
            continue
        if "*" in host:
            raise ImproperlyConfigured(
                f"ALLOWED_HOSTS entry {raw!r} uses a wildcard; list explicit hosts instead."
            )
        hosts.append(host)
    return hosts


def normalise_origins(values: Iterable[str]) -> list[str]:
    """Validate CSRF/CORS origins: scheme://host[:port], no path, no trailing slash."""
    origins: list[str] = []
    for raw in values:
        origin = raw.strip().rstrip("/")
        if not origin:
            continue
        if not (origin.startswith("https://") or origin.startswith("http://")):
            raise ImproperlyConfigured(
                f"Origin {raw!r} must include http:// or https://."
            )
        if origin.count("://") != 1 or "/" in origin.split("://", 1)[1]:
            raise ImproperlyConfigured(
                f"Origin {raw!r} must not contain a path."
            )
        origins.append(origin)
    return origins


PLACEHOLDER_SECRETS = {
    "",
    "change-me",
    "changeme",
    "secret",
    "django-insecure",
    "<your_django_secret_key>",
    "<your-secret-key>",
    "your-secret-key",
    "replace-me",
}


def validate_secret(name: str, value: str, *, debug: bool, allow_insecure: bool) -> str:
    """Fail closed on missing or obviously unsafe secrets in production."""
    if not value:
        raise ImproperlyConfigured(f"{name} is required.")
    if allow_insecure:
        return value
    if value.strip().lower() in PLACEHOLDER_SECRETS or value.startswith("<"):
        raise ImproperlyConfigured(
            f"{name} still contains a placeholder value. Generate a real secret "
            "and store it in the platform secret manager."
        )
    if not debug and len(value) < 32:
        raise ImproperlyConfigured(
            f"{name} is too short for production (minimum 32 characters)."
        )
    return value
