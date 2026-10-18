"""Tests for the Render blueprints and the local preflight tool.

The blueprints are generated artifacts, but the decisions they encode are
load-bearing: the Python pin, the absence of committed secrets, and the fact
that the payment posture belongs to the dashboard (so a Blueprint sync cannot
reset it while leaving the Paystack keys behind). Those are asserted here so a
future edit cannot quietly reintroduce the failed deploy.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import re
import sys
import tempfile
from pathlib import Path

from django.test import SimpleTestCase

ROOT = Path(__file__).resolve().parents[3]
STAGING = "render.staging.yaml"
PRODUCTION = "render.yaml"
SERVICE_HOSTNAME = "loan-platform-backend-staging.onrender.com"


def _load_preflight():
    path = ROOT / "scripts" / "render_preflight.py"
    spec = importlib.util.spec_from_file_location("render_preflight", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # dataclasses resolve annotations through sys.modules; register first.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


preflight = _load_preflight()

BASE_VALUES = {
    "GIT_SHA": "abc1234",
    "SUPABASE_DB_HOST": "db.example.supabase.co",
    "SUPABASE_DB_PASSWORD": "db-password",
    "SUPABASE_URL": "https://example.supabase.co",
    "SUPABASE_SERVICE_KEY": "service-role-key",
}
PAYSTACK_TEST_KEYS = {
    "PAYSTACK_SECRET_KEY": "sk_test_example",
    "PAYSTACK_PUBLIC_KEY": "pk_test_example",
}


class RenderBlueprintTests(SimpleTestCase):
    def blueprint(self, filename: str):
        return preflight.parse_blueprint(ROOT / filename)

    def env(self, filename: str) -> dict[str, preflight.EnvVar]:
        return {var.key: var for var in self.blueprint(filename).vars}

    def test_blueprints_are_generated_and_parse(self):
        for filename in (STAGING, PRODUCTION):
            blueprint = self.blueprint(filename)
            self.assertTrue(blueprint.name, filename)
            self.assertGreater(len(blueprint.vars), 20, filename)

    def test_both_blueprints_pin_the_ci_python(self):
        # The failed deploy ran 3.13.4 because it had been configured by hand.
        for filename in (STAGING, PRODUCTION):
            python = self.env(filename)["PYTHON_VERSION"]
            self.assertRegex(python.value, r"^3\.11\.\d+$", filename)

    def test_no_secret_material_is_committed(self):
        secretish = re.compile(
            r"(sk_live_|sk_test_|pk_live_|pk_test_|-----BEGIN|eyJ[A-Za-z0-9_-]{20,})"
        )
        for filename in (STAGING, PRODUCTION):
            for var in self.blueprint(filename).vars:
                self.assertIsNone(
                    secretish.search(var.value or ""),
                    f"{filename}: {var.key} carries secret-looking material",
                )

    def test_credentials_are_dashboard_owned_or_generated(self):
        for filename in (STAGING, PRODUCTION):
            for var in self.blueprint(filename).vars:
                looks_secret = var.key.endswith(
                    ("_SECRET", "_PASSWORD", "_KEY", "_TOKEN")
                )
                if looks_secret:
                    self.assertTrue(
                        (not var.sync) or var.generate,
                        f"{filename}: {var.key} must be sync: false or generated",
                    )

    def test_payment_posture_is_a_dashboard_decision(self):
        # Pinning PAYMENTS_ENABLED=false would make Render reset a dashboard
        # change on the next sync while the dashboard-only keys stayed behind.
        env = self.env(STAGING)
        for key in (
            "PAYMENTS_ENABLED",
            "PAYMENTS_LIVE_MODE",
            "PAYSTACK_SECRET_KEY",
            "PAYSTACK_PUBLIC_KEY",
            "FEE_COLLECTION_MODE",
            "FEE_COLLECTION_APPROVED",
        ):
            self.assertIn(key, env)
            self.assertFalse(env[key].sync, f"{key} must be sync: false")
            self.assertIsNone(env[key].value, f"{key} must not be pinned in the file")

    def test_staging_keeps_the_production_like_posture(self):
        env = {var.key: var.value for var in self.blueprint(STAGING).vars}
        self.assertEqual(env["DEPLOY_ENVIRONMENT"], "staging")
        self.assertEqual(env["DJANGO_DEBUG"], "false")
        self.assertEqual(env["REQUIRE_DOCUMENT_STORAGE"], "true")
        self.assertEqual(env["SMS_BACKEND"], "console")
        self.assertEqual(env["LEGAL_TERMS_APPROVED"], "false")
        self.assertEqual(env["DJANGO_HSTS_SECONDS"], "0")

    def test_start_command_runs_the_gate_before_migrating(self):
        text = (ROOT / STAGING).read_text(encoding="utf-8")
        gate = text.index("manage.py check_config --allow-test-mode")
        migrate = text.index("manage.py migrate")
        self.assertLess(gate, migrate)


class RenderPreflightTests(SimpleTestCase):
    def setUp(self):
        self.blueprint = preflight.parse_blueprint(ROOT / STAGING)

    def resolve(self, values=None, *, hostname=SERVICE_HOSTNAME):
        return preflight.resolve(
            self.blueprint,
            dict(values or {}),
            render_hostname=hostname,
            git_sha="abc1234",
        )

    def validate(self, resolution, *, hostname=SERVICE_HOSTNAME):
        return preflight.validate_environment(
            self.blueprint, resolution, render_hostname=hostname
        )

    def test_env_file_accepts_comments_exports_quotes_and_empties(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "render.env"
            path.write_text(
                "# a comment\n"
                "\n"
                "export A=1\n"
                "B='two words'\n"
                'C="quoted"\n'
                "D=\n",
                encoding="utf-8",
            )
            self.assertEqual(
                preflight.parse_env_file(path),
                {"A": "1", "B": "two words", "C": "quoted", "D": ""},
            )

    def test_env_file_rejects_malformed_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "render.env"
            path.write_text("NOT A PAIR\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                preflight.parse_env_file(path)

    def test_blueprint_managed_values_win_over_the_dashboard_copy(self):
        resolution = self.resolve({"DEPLOY_ENVIRONMENT": "production"})
        self.assertEqual(resolution.env["DEPLOY_ENVIRONMENT"], "staging")
        self.assertTrue(
            any("blueprint-managed" in warning for warning in resolution.warnings)
        )

    def test_undeclared_variables_are_ignored(self):
        # Render preserves them on an existing service but a fresh one never
        # receives them; the preflight models the fresh service.
        resolution = self.resolve({"LEGACY_LEFTOVER": "stray"})
        self.assertNotIn("LEGACY_LEFTOVER", resolution.env)
        self.assertTrue(
            any("not declared" in warning for warning in resolution.warnings)
        )

    def test_git_sha_preview_and_warning_when_unset(self):
        resolution = self.resolve()
        self.assertEqual(resolution.env["GIT_SHA"], "abc1234")
        self.assertTrue(any("GIT_SHA" in warning for warning in resolution.warnings))

    def test_clean_posture_a_passes(self):
        errors, _ = self.validate(self.resolve(BASE_VALUES))
        self.assertEqual(errors, [])

    def test_clean_posture_b_passes(self):
        resolution = self.resolve(
            {
                **BASE_VALUES,
                **PAYSTACK_TEST_KEYS,
                "PAYMENTS_ENABLED": "true",
                "PAYMENTS_LIVE_MODE": "false",
                "FEE_COLLECTION_MODE": "upfront",
                "FEE_COLLECTION_APPROVED": "true",
            }
        )
        errors, _ = self.validate(resolution)
        self.assertEqual(errors, [])

    def test_paystack_keys_with_payments_off_are_caught_early(self):
        # This is the exact configuration that failed the Render build.
        resolution = self.resolve({**BASE_VALUES, **PAYSTACK_TEST_KEYS})
        errors, _ = self.validate(resolution)
        self.assertTrue(any("Paystack keys are set" in error for error in errors))

    def test_missing_storage_is_an_error_when_storage_is_required(self):
        values = {key: value for key, value in BASE_VALUES.items()}
        values.pop("SUPABASE_URL")
        errors, _ = self.validate(self.resolve(values))
        self.assertTrue(any("SUPABASE_URL" in error for error in errors))

    def test_missing_database_is_an_error(self):
        values = {key: value for key, value in BASE_VALUES.items()}
        values.pop("SUPABASE_DB_HOST")
        errors, _ = self.validate(self.resolve(values))
        self.assertTrue(any("SUPABASE_DB_HOST" in error for error in errors))

    def test_live_mode_on_staging_is_an_error(self):
        resolution = self.resolve(
            {
                **BASE_VALUES,
                **PAYSTACK_TEST_KEYS,
                "PAYMENTS_ENABLED": "true",
                "PAYMENTS_LIVE_MODE": "true",
            }
        )
        errors, _ = self.validate(resolution)
        self.assertTrue(any("PAYMENTS_LIVE_MODE" in error for error in errors))

    def test_allowed_hosts_must_include_the_service_hostname(self):
        resolution = self.resolve(
            {**BASE_VALUES, "DJANGO_ALLOWED_HOSTS": "example.com"}
        )
        errors, _ = self.validate(resolution)
        self.assertTrue(any("DJANGO_ALLOWED_HOSTS" in error for error in errors))

    def test_blank_allowed_hosts_is_derived_from_render(self):
        errors, _ = self.validate(self.resolve(BASE_VALUES))
        self.assertFalse(any("DJANGO_ALLOWED_HOSTS" in error for error in errors))

    def test_init_writes_the_dashboard_owned_keys_without_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "render.staging.env"
            preflight.write_env_template(self.blueprint, path)
            text = path.read_text(encoding="utf-8")
        for key in (
            "SUPABASE_DB_HOST=",
            "SUPABASE_URL=",
            "SUPABASE_SERVICE_KEY=",
            "PAYSTACK_SECRET_KEY=",
            "PAYMENTS_ENABLED=",
        ):
            self.assertIn(key, text)
        self.assertNotIn("DJANGO_SECRET_KEY", text)  # Render generates it
        self.assertNotIn("PYTHON_VERSION", text)  # blueprint-managed

    def test_main_fails_fast_on_the_incident_posture(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "render.staging.env"
            lines = [f"{key}={value}" for key, value in BASE_VALUES.items()]
            lines += [f"{key}={value}" for key, value in PAYSTACK_TEST_KEYS.items()]
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = preflight.main(
                    ["--env-file", str(path), "--no-run"]
                )
        self.assertEqual(code, 2)
        self.assertIn("PAYMENTS_ENABLED", err.getvalue())

    def test_main_accepts_a_clean_posture_without_running_the_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "render.staging.env"
            path.write_text(
                "\n".join(f"{key}={value}" for key, value in BASE_VALUES.items())
                + "\n",
                encoding="utf-8",
            )
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = preflight.main(["--env-file", str(path), "--no-run"])
        self.assertEqual(code, 0, err.getvalue())
        self.assertIn("payments=off", out.getvalue())
