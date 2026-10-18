"""Deterministic PWA cache versioning (Phase 7).

The service worker cache name must change whenever any shell asset changes
and must NOT change otherwise — both by construction, across machines.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from django.test import SimpleTestCase, override_settings

from core.pwa import resolve_cache_version, shell_fingerprint


def _make_shell(root: Path, *, css: str = "body{}", js: str = "x=1;") -> None:
    (root / "css").mkdir(parents=True, exist_ok=True)
    (root / "js").mkdir(parents=True, exist_ok=True)
    (root / "css" / "style.css").write_text(css)
    (root / "js" / "core.js").write_text(js)
    (root / "sw.js").write_text("const VERSION = '__CACHE_VERSION__';")
    (root / "offline.html").write_text("<p>offline</p>")


class ShellFingerprintTests(SimpleTestCase):
    def test_same_content_same_fingerprint(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            _make_shell(Path(a))
            _make_shell(Path(b))
            self.assertEqual(
                shell_fingerprint(Path(a)), shell_fingerprint(Path(b))
            )

    def test_any_shell_change_changes_the_fingerprint(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_shell(root)
            before = shell_fingerprint(root)
            (root / "css" / "style.css").write_text("body{color:red}")
            self.assertNotEqual(before, shell_fingerprint(root))

    def test_sw_source_is_fingerprinted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_shell(root)
            before = shell_fingerprint(root)
            (root / "sw.js").write_text("// bumped")
            self.assertNotEqual(before, shell_fingerprint(root))

    def test_non_shell_files_do_not_affect_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_shell(root)
            before = shell_fingerprint(root)
            (root / "templates").mkdir()
            (root / "templates" / "unrelated.html").write_text("<p>x</p>")
            self.assertEqual(before, shell_fingerprint(root))

    def test_path_names_feed_the_hash(self):
        # renaming a file must bump the version even if bytes are identical
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_shell(root)
            before = shell_fingerprint(root)
            (root / "js" / "core.js").rename(root / "js" / "app.js")
            self.assertNotEqual(before, shell_fingerprint(root))


class ResolveCacheVersionTests(SimpleTestCase):
    def test_override_wins_and_is_stripped(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(
                resolve_cache_version("  build-1234  ", Path(tmp)), "build-1234"
            )

    def test_blank_override_falls_back_to_fingerprint(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_shell(root)
            self.assertEqual(
                resolve_cache_version("", root), shell_fingerprint(root)
            )

    def test_settings_value_is_the_fingerprint_by_default(self):
        # settings computes the value at import; assert the invariant shape
        from django.conf import settings

        self.assertEqual(len(settings.PWA_CACHE_VERSION), 16)
        int(settings.PWA_CACHE_VERSION, 16)


class ServedWorkerUsesTheVersion(SimpleTestCase):
    def test_sw_placeholder_replaced_by_settings_value(self):
        from django.conf import settings

        response = self.client.get("/sw.js")
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        # the source computes CACHE_NAME as "mb-shell-" + VERSION; assert the
        # injected constant carries the deterministic value.
        self.assertIn(f'const VERSION = "{settings.PWA_CACHE_VERSION}"', body)
        self.assertNotIn("__CACHE_VERSION__", body)

    @override_settings(PWA_CACHE_VERSION="build-999")
    def test_override_flows_through_to_the_served_worker(self):
        response = self.client.get("/sw.js")
        self.assertIn('const VERSION = "build-999"', response.content.decode())
