"""Test settings backed by a real PostgreSQL database.

Used by the CI Postgres job (and by anyone reproducing it locally) so the
schema and query behaviour under test match production. Applies the shared
test overrides, then swaps the database for the one supplied via
TESTDATABASE_URL (defaults to the CI service container).
"""
from __future__ import annotations

import os
from urllib.parse import urlsplit

from core.test_settings import *  # noqa: F401,F403 -- deliberately reuses all test overrides

_database_url = urlsplit(
    os.environ.get(
        "TESTDATABASE_URL",
        "postgresql://postgres:postgres@localhost:5432/testdb",
    )
)

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": _database_url.path.lstrip("/") or "testdb",
        "USER": _database_url.username or "postgres",
        "PASSWORD": _database_url.password or "",
        "HOST": _database_url.hostname or "localhost",
        "PORT": _database_url.port or 5432,
        "OPTIONS": {},
        "CONN_MAX_AGE": 0,
    }
}
