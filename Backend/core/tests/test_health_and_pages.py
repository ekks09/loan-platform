from __future__ import annotations

from io import StringIO
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

from core import views as core_views

from loans.models import Loan


class HealthEndpointTests(TestCase):
    def test_liveness_does_not_require_database_credentials(self):
        response = self.client.get("/health/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")
        self.assertIn("environment", response.json())
        self.assertTrue(response["X-Request-ID"])

    def test_readiness_reports_database_and_safety_gates(self):
        response = self.client.get("/health/ready/")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "ready")
        self.assertEqual(body["checks"]["database"], "ok")
        self.assertEqual(body["checks"]["payments"], "enabled")
        self.assertEqual(body["checks"]["fee_collection"], "disabled")
        self.assertEqual(body["checks"]["document_storage"], "missing")
        self.assertEqual(body["checks"]["legal_terms"], "draft")

    @override_settings(PAYMENTS_LIVE_MODE=True, LEGAL_TERMS_APPROVED=False)
    def test_readiness_fails_closed_when_live_payments_lack_legal_approval(self):
        response = self.client.get("/health/ready/")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["checks"]["legal_terms"], "live_blocked")

    @override_settings(REQUIRE_DOCUMENT_STORAGE=True, SUPABASE_URL="", SUPABASE_SERVICE_KEY="")
    def test_readiness_fails_when_storage_is_required_but_missing(self):
        response = self.client.get("/health/ready/")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["checks"]["document_storage"], "missing")

    def test_incoming_request_id_is_echoed_only_when_well_formed(self):
        response = self.client.get("/health/", HTTP_X_REQUEST_ID="abc12345-ok")
        self.assertEqual(response["X-Request-ID"], "abc12345-ok")

        response = self.client.get("/health/", HTTP_X_REQUEST_ID="<script>alert(1)</script>")
        self.assertNotEqual(response["X-Request-ID"], "<script>alert(1)</script>")


class PublicPageTests(TestCase):
    def test_index_publishes_the_full_fee_schedule_without_login(self):
        response = self.client.get(reverse("index"))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        # Every band's range and computed example fee is published, not typed.
        for token in (
            "100 – 999",
            "1,000 – 4,999",
            "5,000 – 9,999",
            "10,000 – 19,999",
            "20,000 – 34,999",
            "35,000 – 60,000",
            "KES 45 at KES 500",
            "KES 499 at KES 4,999",
            "KES 7,200 at KES 60,000",
            "2026-10-draft",
            "KES 45 minimum",
        ):
            self.assertIn(token, content)
        self.assertIn("Processing fee", content)
        self.assertIn("No application fee is collected", content)
        # Licence details must never be invented.
        self.assertIn("Licence status not yet published", content)
        self.assertNotIn("CBK", content)

    def test_registration_page_requires_explicit_consent(self):
        response = self.client.get(reverse("register"))
        content = response.content.decode()
        self.assertIn('id="regConsent"', content)
        self.assertIn("privacy notice", content)
        self.assertIn("terms", content)

    def test_legal_pages_are_marked_as_drafts(self):
        for name in ("privacy", "terms"):
            response = self.client.get(reverse(name))
            self.assertEqual(response.status_code, 200)
            self.assertIn("Draft", response.content.decode())

    def test_rendered_pages_never_leak_template_syntax(self):
        # Django {# #} comments are single-line only; a stretched one leaks
        # its raw text into the page (this regressed on the register page).
        for name in ("index", "register", "login_view", "dashboard", "privacy", "terms", "offline"):
            with self.subTest(page=name):
                response = self.client.get(reverse(name))
                self.assertEqual(response.status_code, 200)
                content = response.content.decode()
                self.assertNotIn("{#", content)
                self.assertNotIn("#}", content)
                self.assertNotIn("{%", content)
                self.assertNotIn("%}", content)

    def test_dashboard_page_consumes_the_active_loan_contract(self):
        response = self.client.get(reverse("dashboard"))
        content = response.content.decode()
        for token in (
            'id="loanStatus"',
            'data-min-amount="100"',
            'data-max-amount="60000"',
            "KES 100 and",
            "/static/js/page-dashboard.js",
            'id="payResend"',
            'id="payRingProgress"',
            'id="expressSheet"',
            'data-ledger-filter="repayment"',
            'id="receiptDialog"',
        ):
            self.assertIn(token, content)

        script = (Path(settings.BASE_DIR) / "Frontend/js/page-dashboard.js").read_text()
        for token in ("Loan.active()", "data.loan", "data.latest", "Loan.quote("):
            self.assertIn(token, script)

    @override_settings(FEE_COLLECTION_MODE="upfront", FEE_COLLECTION_APPROVED=True)
    def test_index_discloses_upfront_fee_only_after_approval(self):
        response = self.client.get(reverse("index"))
        self.assertIn(
            "collected only after your application is approved",
            response.content.decode(),
        )


class SystemCheckTests(TestCase):
    def test_fee_schedule_invariant(self):
        self.assertEqual(Loan.validate_schedule(), [])

    def test_migrations_are_in_sync_with_models(self):
        out = StringIO()
        call_command(
            "makemigrations", "--check", "--dry-run", "--noinput", stdout=out
        )
        self.assertIn("No changes detected", out.getvalue())


    def test_appearance_boot_is_blocking_and_progressive(self):
        css = (settings.BASE_DIR / "Frontend" / "css" / "style.css").read_text()
        for token in (
            '[data-theme="dark"]',
            ".no-js .theme-toggle",
            ".has-reveal [data-reveal]",
            "prefers-reduced-motion: reduce",
        ):
            self.assertIn(token, css)

        for name in ("index", "register", "login_view", "dashboard"):
            response = self.client.get(reverse(name))
            self.assertEqual(response.status_code, 200)
            content = response.content.decode()
            # The paint-before-stylesheet script runs first and the toggle is unique.
            self.assertLess(
                content.index("/static/js/theme-init.js"),
                content.index("/static/css/style.css"),
            )
            # The header and the sidebar both expose a switch, and both are
            # wired by the same theme module.
            self.assertGreaterEqual(content.count("data-theme-toggle"), 2)
            self.assertIn('role="switch"', content)
            self.assertIn('aria-checked="false"', content)
            self.assertIn("data-reveal", content)
            self.assertIn('<html lang="en-KE" class="no-js">', content)


    def test_home_section_anchors_have_matching_targets(self):
        content = self.client.get(reverse("index")).content.decode()
        for anchor in ("overview", "how-it-works", "fees", "security", "faq", "contact"):
            self.assertIn(f'id="{anchor}"', content)
        # Links stay route-qualified so they also work from legal pages, and
        # the sidebar marks the same six ids for the scroll spy.
        for anchor in ("overview", "how-it-works", "fees", "security", "faq", "contact"):
            self.assertIn(f'href="/#{anchor}"', content)


    def test_auth_pages_load_their_api_client_before_the_page_script(self):
        for name in ("login_view", "register"):
            body = self.client.get(reverse(name)).content.decode()
            self.assertIn("/static/js/auth.js", body, name)
            self.assertLess(
                body.index("/static/js/auth.js"),
                body.index("/static/js/page-auth.js"),
                f"{name} must load auth.js before page-auth.js",
            )

    def test_static_illustrations_are_shipped_and_referenced(self):
        img_dir = settings.BASE_DIR / "Frontend" / "img"
        plates = {
            "hero-skyline": (1440, 900),
            "how-flow": (1400, 659),
            "security-plate": (600, 900),
            "identity-plate": (750, 1000),
            "verify-plate": (1400, 788),
            "staff-plate": (1400, 663),
            "texture-band": (1200, 514),
            "commerce-plate": (1200, 531),
        }
        for name, (width, height) in plates.items():
            for suffix in (".webp", ".jpg"):
                path = img_dir / f"{name}{suffix}"
                self.assertTrue(path.is_file(), f"missing asset {name}{suffix}")
                self.assertLess(
                    path.stat().st_size, 200_000, f"{name}{suffix} should stay small"
                )
            # Masters are working files; they must not ship or be referenced.
            self.assertFalse((img_dir / f"src-{name}.png").exists())

        index = self.client.get(reverse("index")).content.decode()
        self.assertIn("/static/img/hero-skyline.jpg", index)
        self.assertIn("/static/img/how-flow.webp", index)
        self.assertIn("/static/img/security-plate.webp", index)
        self.assertIn("/static/img/commerce-plate.webp", index)
        self.assertIn('type="image/webp"', index)
        self.assertNotIn("src-", index)
        self.assertNotIn("/static/img/security.webp", index)

        offline = self.client.get("/offline/").content.decode()
        self.assertIn("/static/img/texture-band.webp", offline)

        for name in ("register", "login_view"):
            page = self.client.get(reverse(name)).content.decode()
            self.assertIn("/static/img/identity-plate.webp", page)

        dashboard = self.client.get(reverse("dashboard")).content.decode()
        self.assertIn("/static/img/verify-plate.webp", dashboard)
        self.assertIn('id="payDialog"', dashboard)
        self.assertIn('id="payDialogStart"', dashboard)

    def test_branded_500_page_renders_with_context(self):
        request = RequestFactory().get("/")
        response = core_views.server_error(request)
        self.assertEqual(response.status_code, 500)
        body = response.content.decode()
        self.assertIn("Something went wrong", body)
        self.assertIn(settings.PLATFORM_NAME, body)
        self.assertIn("skip-link", body)

    def test_staff_queue_and_error_pages_carry_illustration_plates(self):
        user = get_user_model().objects.create_user(
            phone="254712345679",
            national_id="12345678",
            password="correct-horse-battery-1",
            is_staff=True,
        )
        self.client.force_login(user)
        page = self.client.get(reverse("verification-dashboard"))
        self.assertEqual(page.status_code, 200)
        self.assertIn("/static/img/staff-plate.webp", page.content.decode())

        with self.settings(DEBUG=False):
            not_found = self.client.get("/this-route-does-not-exist/")
            self.assertEqual(not_found.status_code, 404)
            body = not_found.content.decode()
            self.assertIn("That page is not here", body)
            # Error pages reuse the shell; they must not point at deleted art.
            self.assertNotIn("/static/img/error", body)
            self.assertNotIn("src-", body)


class PWATests(TestCase):
    def test_manifest_declares_an_installable_identity(self):
        response = self.client.get("/manifest.webmanifest")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/manifest+json")
        body = response.json()
        self.assertEqual(body["name"], settings.PLATFORM_NAME)
        self.assertEqual(body["short_name"], settings.PLATFORM_NAME)
        self.assertEqual(body["id"], "/")
        self.assertEqual(body["start_url"], "/dashboard/")
        self.assertEqual(body["scope"], "/")
        self.assertEqual(body["display"], "standalone")
        self.assertEqual(body["theme_color"], settings.PWA_THEME_COLOR)
        icons = {(icon["sizes"], icon["purpose"]) for icon in body["icons"]}
        self.assertIn(("192x192", "any"), icons)
        self.assertIn(("512x512", "any"), icons)
        self.assertIn(("512x512", "maskable"), icons)

    def test_service_worker_is_versioned_and_root_scoped(self):
        response = self.client.get("/sw.js")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Service-Worker-Allowed"], "/")
        self.assertIn("no-store", response["Cache-Control"])
        body = response.content.decode()
        self.assertNotIn("__CACHE_VERSION__", body)
        self.assertIn(settings.PWA_CACHE_VERSION, body)
        # Private and authenticated routes are explicitly excluded.
        for private in ('pathname.startsWith("/api/")', 'pathname.startsWith("/admin/")'):
            self.assertIn(private, body)
        self.assertIn('request.method !== "GET"', body)

    def test_offline_page_is_available_without_a_loan_or_balance(self):
        response = self.client.get("/offline/")
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("offline", content.lower())
        self.assertNotIn("balance", content.lower())

    def test_robots_disallows_private_areas(self):
        body = self.client.get("/robots.txt").content.decode()
        for path in ("/api/", "/admin/", "/dashboard/", "/health/"):
            self.assertIn(f"Disallow: {path}", body)
        self.assertIn("Allow: /legal/", body)

    def test_html_pages_send_hardening_headers(self):
        response = self.client.get(reverse("index"))
        csp = response["Content-Security-Policy"]
        self.assertIn("frame-ancestors 'none'", csp)
        self.assertIn("object-src 'none'", csp)
        self.assertNotIn("'unsafe-eval'", csp)
        self.assertEqual(response["Referrer-Policy"], "strict-origin-when-cross-origin")
        self.assertIn("camera=(self)", response["Permissions-Policy"])
        self.assertEqual(response["X-Frame-Options"], "DENY")

    @override_settings(PREVIEW_EMBED_ALLOWED=True)
    def test_preview_flag_relaxes_framing_only(self):
        response = self.client.get(reverse("index"))
        csp = response["Content-Security-Policy"]
        self.assertIn("frame-ancestors *", csp)
        self.assertNotIn("frame-ancestors 'none'", csp)
        self.assertNotIn("X-Frame-Options", response)
        # The rest of the policy is untouched by the preview flag.
        self.assertIn("object-src 'none'", csp)
        self.assertNotIn("'unsafe-eval'", csp)
        self.assertEqual(response["Referrer-Policy"], "strict-origin-when-cross-origin")
