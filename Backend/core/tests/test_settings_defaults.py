"""Regression tests for the *default* payment posture of core.settings.

The main suite pins ``PAYMENTS_ENABLED`` / ``PAYMENTS_LIVE_MODE`` in
``core/test_settings.py`` (it must, to exercise the payments app), so a
accidental change of the **defaults** in ``core/settings.py`` cannot be
caught there — the suite would stay green while every deployment that
simply does not set the variables changes meaning.

Commit beca1fe flipped both defaults to ``True`` ("fail open"): staging
stopped booting with the variables unset (the live-mode guard fired), and
a production service holding an ``sk_live_`` key would have started moving
real money without ever opting in. These tests boot ``core.settings`` in a
subprocess with the payment variables scrubbed from the environment — the
exact posture of a freshly created Render service — and pin the resolved
values.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

# Only the bare minimum the interpreter needs, like the Render preflight
# does: the parent process (core.test_settings) pins payments ON, and a
# whitelist is the only way to be sure none of it leaks into the child.
_KEEP = (
    "PATH",
    "HOME",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TMPDIR",
    "TEMP",
    "TMP",
    "SYSTEMROOT",
    "PYTHONIOENCODING",
)

# A Render-fresh staging service: no PAYMENTS_*, no PAYSTACK_*, no fee or
# legal switches — only what any deployment needs to boot at all.
_STAGING_ENV = {
    "DEPLOY_ENVIRONMENT": "staging",
    "REQUIRE_DOCUMENT_STORAGE": "false",
    "DJANGO_SECRET_KEY": "staging-default-posture-probe-0123456789abcdef",  # nosec B105 -- throwaway subprocess secret, not a credential
    "DJANGO_DEV_SQLITE": "1",
    "DJANGO_ALLOWED_HOSTS": "loan-platform-backend-staging.onrender.com",
}

# The production money scenario from the regression: someone pasted an
# sk_live_ key into a dashboard and set none of the switches.
_PRODUCTION_LIVE_KEY_ENV = {
    "DEPLOY_ENVIRONMENT": "production",
    "REQUIRE_DOCUMENT_STORAGE": "false",
    "DJANGO_SECRET_KEY": "production-default-posture-probe-0123456789abcdef",  # nosec B105 -- throwaway subprocess secret, not a credential
    "DJANGO_DEV_SQLITE": "1",
    "DJANGO_ALLOWED_HOSTS": "loan-platform-backend.onrender.com",
    "PAYSTACK_SECRET_KEY": "sk_live_regression_probe_not_a_real_key",  # nosec B105 -- deliberately malformed probe, never a credential
    "PAYSTACK_PUBLIC_KEY": "pk_live_regression_probe_not_a_real_key",
}

# Runs in the child before importing core.settings. load_dotenv is
# neutralised because Backend/.env is git-ignored and Render never sees it;
# override=False would otherwise backfill the scrubbed variables from a
# developer's local file and mask the default under test.
_CHILD = """
import sys

try:
    import dotenv

    dotenv.load_dotenv = lambda *args, **kwargs: False
except ImportError:
    pass

import core.settings as s

print(f"DEPLOY_ENVIRONMENT={s.DEPLOY_ENVIRONMENT}")
print(f"PAYMENTS_ENABLED={s.PAYMENTS_ENABLED}")
print(f"PAYMENTS_LIVE_MODE={s.PAYMENTS_LIVE_MODE}")
print(f"FEE_COLLECTION_MODE={s.FEE_COLLECTION_MODE}")
print("BOOT-OK")
"""


class PaymentDefaultPostureTests(SimpleTestCase):
    def _boot(self, extra_env: dict[str, str]) -> subprocess.CompletedProcess:
        with tempfile.TemporaryDirectory() as tmp:
            env = {name: os.environ[name] for name in _KEEP if name in os.environ}
            env.update(extra_env)
            env["DJANGO_SQLITE_PATH"] = str(Path(tmp) / "default-posture.sqlite3")
            # Guard against the test itself drifting: these must be absent,
            # that is the whole point of the posture under test.
            for key in (
                "PAYMENTS_ENABLED",
                "PAYMENTS_LIVE_MODE",
                "PAYSTACK_SECRET_KEY",
                "PAYSTACK_PUBLIC_KEY",
                "FEE_COLLECTION_MODE",
                "FEE_COLLECTION_APPROVED",
                "LEGAL_TERMS_APPROVED",
            ):
                if key in extra_env:
                    continue
                self.assertNotIn(key, env, f"{key} must be scrubbed for this probe")
            return subprocess.run(
                [sys.executable, "-c", _CHILD],
                cwd=settings.BASE_DIR,
                env=env,
                capture_output=True,
                text=True,
                timeout=120,
            )

    def test_unset_payments_boot_staging_with_defaults_off(self):
        """Staging boots with every payment variable unset — and stays off."""
        result = self._boot(_STAGING_ENV)
        self.assertEqual(
            result.returncode,
            0,
            f"settings refused to boot with PAYMENTS_* unset:\n{result.stderr}",
        )
        self.assertNotIn("ImproperlyConfigured", result.stderr)
        self.assertNotIn("Staging can never run", result.stderr)
        self.assertIn("BOOT-OK", result.stdout)
        # DEPLOY_ENVIRONMENT=staging proves the live-mode guard branch was
        # live: with the fail-open defaults this exact boot died at import.
        self.assertIn("DEPLOY_ENVIRONMENT=staging", result.stdout)
        self.assertIn("PAYMENTS_ENABLED=False", result.stdout)
        self.assertIn("PAYMENTS_LIVE_MODE=False", result.stdout)
        self.assertIn("FEE_COLLECTION_MODE=disabled", result.stdout)

    def test_a_live_key_alone_cannot_turn_payments_on(self):
        """Production + sk_live_ key + no switches refuses to boot."""
        result = self._boot(_PRODUCTION_LIVE_KEY_ENV)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ImproperlyConfigured", result.stderr)
        self.assertIn("Paystack keys are set", result.stderr)
        self.assertIn("PAYMENTS_ENABLED", result.stderr)
        self.assertNotIn("BOOT-OK", result.stdout)
