"""Settings for CI-only validation steps.

The test settings disable secure cookies so the Django test client can use
plain HTTP. ``check_config`` (correctly) refuses that posture, so CI validates
the deployment command against this variant instead.
"""
from __future__ import annotations

from core.test_settings import *  # noqa: F401,F403

SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
