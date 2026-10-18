#!/usr/bin/env python3
"""Render blueprint generator + drift gate.

render.yaml (production) and render.staging.yaml are GENERATED artifacts:
one shared service/env template (deploy/render.common.yaml.j2) plus one
parameter file per environment (deploy/render.{staging,production}.params).

    python scripts/render_blueprints.py --write   # regenerate after edits
    python scripts/render_blueprints.py --check   # CI gate: fail on drift

This removes the class of bug where 'staging' and 'production' blueprints
silently diverge on shared keys after a hand edit. Secrets never appear in
any of these files (every credential stays `sync: false` / generated).
"""
from __future__ import annotations

import difflib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "deploy" / "render.common.yaml.j2"

HEADER = """\
# Render blueprint for the Django backend.
#
# Safety posture of this blueprint:
#   * no secret values committed — Render generates the Django key and every
#     credential (including the Paystack pair) is marked `sync: false`, so it
#     lives in the Render dashboard and is never readable from the repository;
#   * the payment posture is a dashboard decision, one of:
#       A) payments off — no Paystack keys, PAYMENTS_ENABLED=false; or
#       B) payments on in TEST mode — sk_test_/pk_test_ keys,
#          PAYMENTS_ENABLED=true, PAYMENTS_LIVE_MODE=false (staging refuses
#          live mode at startup);
#     unset is the safe default: payments off, live mode off, fee collection
#     disabled and unapproved;
#   * the start command runs `check_config` before `migrate`, so a bad posture
#     fails the boot with the gate report instead of serving traffic.
# Do not set PAYMENTS_LIVE_MODE or LEGAL_TERMS_APPROVED until the compliance,
# product and legal gates are signed off.

# THIS FILE IS GENERATED from deploy/render.common.yaml.j2 +
# deploy/render.{staging,production}.params. Do not edit directly;
# scripts/render_blueprints.py --check (run in CI) fails on drift.

"""

_PARAM_LINE = re.compile(r"^(\w+):\s*(.*\S)\s*$")


def load_params(path: Path) -> dict[str, str]:
    params: dict[str, str] = {}
    for line in path.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = _PARAM_LINE.match(line)
        if not match:
            raise SystemExit(f"bad parameter line in {path}: {line!r}")
        params[match.group(1)] = match.group(2)
    return params


def render(template: str, params: dict[str, str]) -> str:
    out = template
    for key, value in params.items():
        token = "{{%s}}" % key
        if token not in out:
            raise SystemExit(f"parameter {key} has no matching placeholder in template")
        out = out.replace(token, value)
    leftovers = re.findall(r"\{\{\w+\}\}", out)
    if leftovers:
        raise SystemExit(f"template placeholders not bound by params: {leftovers}")
    return out


def main(argv: list[str]) -> int:
    check = "--check" in argv
    write = "--write" in argv
    if check == write:
        print(__doc__)
        return 2
    template = TEMPLATE.read_text()
    drifted = False
    for env_name in ("staging", "production"):
        rendered = HEADER + render(
            template, load_params(ROOT / "deploy" / f"render.{env_name}.params")
        )
        filename = "render.staging.yaml" if env_name == "staging" else "render.yaml"
        target = ROOT / filename
        if write:
            target.write_text(rendered)
            print(f"wrote {filename}")
        else:
            current = target.read_text() if target.exists() else ""
            if current != rendered:
                drifted = True
                sys.stdout.writelines(
                    difflib.unified_diff(
                        current.splitlines(True),
                        rendered.splitlines(True),
                        fromfile=f"committed:{filename}",
                        tofile=f"generated:{filename}",
                    )
                )
    if drifted:
        print(
            "\nBlueprint drift detected. Edit deploy/*.{j2,params} and regenerate "
            "with: python scripts/render_blueprints.py --write"
        )
        return 1
    if check:
        print("render blueprints are in sync with deploy/ sources")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
