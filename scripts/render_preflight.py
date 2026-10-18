#!/usr/bin/env python3
"""Reconstruct a Render Blueprint's runtime environment and validate it locally.

The Render dashboard is the only place the ``sync: false`` values live. Keep a
local, git-ignored copy of them (``--init`` writes the skeleton) and this script
rebuilds the environment the service actually boots with:

* Blueprint-managed values come from the committed YAML. The dashboard cannot
  durably override them: the next Blueprint sync resets them to the file value.
* ``sync: false`` values come from the env file. A freshly created service
  receives those keys (and nothing else) from the Blueprint.
* Render-provided values (``RENDER_EXTERNAL_HOSTNAME``, ``GIT_SHA``) are
  injected locally.

It then runs the same gate the service runs at boot::

    python scripts/render_preflight.py --init   # write deploy/render.staging.env
    python scripts/render_preflight.py          # validate it

Secret values are never printed: the report names keys and where each value
came from, then the gate prints the resolved posture.

Exit codes: 0 = deployable, 1 = gate failed, 2 = input/precondition error.
"""
from __future__ import annotations

import argparse
import os
import platform
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "Backend"

_ENV_KEY = re.compile(r"^\s*- key:\s*(?P<key>[A-Za-z_][A-Za-z0-9_]*)\s*$")
_ENV_ATTR = re.compile(r"^\s+(?P<name>value|sync|generateValue):\s*(?P<value>.*?)\s*$")
_SERVICE_NAME = re.compile(r"^    name:\s*(?P<name>[A-Za-z0-9._-]+)\s*$")
_SERVICE_START = re.compile(r"^  - type:")
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Render generates DJANGO_SECRET_KEY. Locally we only need *a* value that
# clears the length/placeholder validation; it is never written anywhere.
_PREVIEW_SECRET = "preflight-only-secret-key-" + "0" * 40

# The only variables a fresh staging service cannot boot without. Storage keys
# are required because the blueprint sets REQUIRE_DOCUMENT_STORAGE=true; the
# Paystack pair only when payments are on. Validate the rest through Django.
_DB_REQUIRED = ("SUPABASE_DB_HOST", "SUPABASE_DB_PASSWORD")
_STORAGE_REQUIRED = ("SUPABASE_URL", "SUPABASE_SERVICE_KEY")
_PAYSTACK_KEYS = ("PAYSTACK_SECRET_KEY", "PAYSTACK_PUBLIC_KEY")

_TRUTHY = {"1", "true", "yes", "on"}


@dataclass
class EnvVar:
    """One ``envVars`` entry from a Render blueprint."""

    key: str
    value: str | None = None
    sync: bool = True
    generate: bool = False


@dataclass
class Blueprint:
    path: Path
    name: str
    vars: list[EnvVar]

    @property
    def keys(self) -> set[str]:
        return {var.key for var in self.vars}


@dataclass
class Resolution:
    """The environment Render would boot with, plus where each value came from."""

    env: dict[str, str] = field(default_factory=dict)
    sources: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def _unquote(raw: str) -> str:
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
        return raw[1:-1]
    return raw


def parse_blueprint(path: Path) -> Blueprint:
    """Parse the ``envVars`` block of a generated Render blueprint."""
    text = path.read_text(encoding="utf-8")
    name = ""
    env_vars: list[EnvVar] = []
    in_env = False
    current: EnvVar | None = None

    for line in text.splitlines():
        if not in_env:
            if line.strip() == "envVars:":
                in_env = True
                continue
            match = _SERVICE_NAME.match(line)
            if match and not name:
                name = match.group("name")
            continue

        if _SERVICE_START.match(line):
            break
        match = _ENV_KEY.match(line)
        if match:
            current = EnvVar(key=match.group("key"))
            env_vars.append(current)
            continue
        match = _ENV_ATTR.match(line)
        if match and current is not None:
            attribute, raw = match.group("name"), _unquote(match.group("value"))
            if attribute == "value":
                current.value = raw
            elif attribute == "sync":
                current.sync = raw.lower() != "false"
            elif attribute == "generateValue":
                current.generate = raw.lower() == "true"

    if not env_vars:
        raise ValueError(f"{path} has no envVars block")
    return Blueprint(path=path, name=name, vars=env_vars)


def parse_env_file(path: Path) -> dict[str, str]:
    """Read a KEY=VALUE file (comments, blank lines and ``export`` allowed)."""
    values: dict[str, str] = {}
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            # Do not echo the line: a malformed entry may contain a secret.
            raise ValueError(
                f"{path}:{number}: expected KEY=VALUE; fix or remove the line"
            )
        key, value = line.split("=", 1)
        key = key.strip()
        if not _ENV_NAME.match(key):
            raise ValueError(f"{path}:{number}: invalid variable name {key!r}")
        values[key] = _unquote(value.strip())
    return values


def env_bool(raw: str | None) -> bool:
    return (raw or "").strip().lower() in _TRUTHY


def resolve(
    blueprint: Blueprint,
    file_values: dict[str, str],
    *,
    render_hostname: str = "",
    git_sha: str = "",
) -> Resolution:
    """Merge blueprint and env-file values the way a fresh Render service would.

    Blueprint-managed values always win (a sync would reset the dashboard), and
    variables that are not declared in the blueprint are ignored with a warning
    because a newly created service never receives them.
    """
    resolution = Resolution()
    for var in blueprint.vars:
        if var.generate:
            resolution.env[var.key] = _PREVIEW_SECRET
            resolution.sources[var.key] = "generated (preview)"
        elif not var.sync:
            value = file_values.get(var.key, "")
            if value:
                resolution.env[var.key] = value
                resolution.sources[var.key] = "env file"
            else:
                resolution.sources[var.key] = "unset"
        else:
            resolution.env[var.key] = var.value or ""
            resolution.sources[var.key] = "blueprint"
            file_value = file_values.get(var.key, "")
            if file_value and file_value != (var.value or ""):
                resolution.warnings.append(
                    f"{var.key} is blueprint-managed: the env-file value is ignored "
                    "(Render resets it on the next Blueprint sync)"
                )

    for key in sorted(file_values):
        if key not in blueprint.keys:
            resolution.warnings.append(
                f"{key} is not declared in {blueprint.path.name}: a freshly created "
                "service would not receive it (ignored)"
            )

    if render_hostname:
        resolution.env.setdefault("RENDER_EXTERNAL_HOSTNAME", render_hostname)
        resolution.sources.setdefault(
            "RENDER_EXTERNAL_HOSTNAME", "injected (Render provides it)"
        )
    if "GIT_SHA" in blueprint.keys and not resolution.env.get("GIT_SHA") and git_sha:
        resolution.env["GIT_SHA"] = git_sha
        resolution.sources["GIT_SHA"] = "local git HEAD (preview)"
        resolution.warnings.append(
            "GIT_SHA is unset: the release process pins the deployed SHA"
        )
    return resolution


def required_keys(env: dict[str, str]) -> set[str]:
    """Keys this particular posture cannot boot without."""
    required = set(_DB_REQUIRED)
    if env_bool(env.get("REQUIRE_DOCUMENT_STORAGE")):
        required.update(_STORAGE_REQUIRED)
    if env_bool(env.get("PAYMENTS_ENABLED")):
        required.update(_PAYSTACK_KEYS)
    return required


def validate_environment(
    blueprint: Blueprint,
    resolution: Resolution,
    *,
    render_hostname: str = "",
    python_version: str | None = None,
) -> tuple[list[str], list[str]]:
    """Return ``(errors, warnings)`` for the resolved environment.

    Django remains the authority for the full posture; these checks exist to
    fail with a precise, actionable message for the mistakes that are easy to
    make in a dashboard.
    """
    env = resolution.env
    errors: list[str] = []
    warnings: list[str] = list(resolution.warnings)

    for key in sorted(required_keys(env)):
        if not env.get(key):
            errors.append(
                f"{key} is required for this posture but is empty or unset "
                f"(declared sync: false in {blueprint.path.name})"
            )

    hosts = [host.strip() for host in env.get("DJANGO_ALLOWED_HOSTS", "").split(",")]
    hosts = [host for host in hosts if host]
    if render_hostname and hosts and render_hostname not in hosts:
        errors.append(
            f"DJANGO_ALLOWED_HOSTS does not include {render_hostname}: Render's "
            "health check would get HTTP 400 and the deploy would fail"
        )

    origins = [
        origin.strip().rstrip("/")
        for origin in env.get("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",")
        if origin.strip()
    ]
    if render_hostname and origins and f"https://{render_hostname}" not in origins:
        warnings.append(
            f"DJANGO_CSRF_TRUSTED_ORIGINS does not include https://{render_hostname}: "
            "cross-origin form posts to the service would be rejected"
        )

    paystack = [key for key in _PAYSTACK_KEYS if env.get(key)]
    if paystack and not env_bool(env.get("PAYMENTS_ENABLED")):
        errors.append(
            "Paystack keys are set ("
            + ", ".join(paystack)
            + ") while PAYMENTS_ENABLED is false. This is the configuration that "
            "fails the Render build. Remove both keys (posture A), or set "
            "PAYMENTS_ENABLED=true with test keys (posture B)."
        )

    if (
        (env.get("DEPLOY_ENVIRONMENT") or "").strip().lower() == "staging"
        and env_bool(env.get("PAYMENTS_LIVE_MODE"))
    ):
        errors.append(
            "staging can never run PAYMENTS_LIVE_MODE=true; settings refuse to boot"
        )

    pin = env.get("PYTHON_VERSION", "")
    local = python_version or platform.python_version()
    if pin and pin.split(".")[:2] != local.split(".")[:2]:
        warnings.append(
            f"the blueprint pins Python {pin} but this interpreter is {local}; "
            "re-run the preflight on the pinned version"
        )

    return errors, warnings


def describe_posture(env: dict[str, str]) -> str:
    payments = "on" if env_bool(env.get("PAYMENTS_ENABLED")) else "off"
    live = "true" if env_bool(env.get("PAYMENTS_LIVE_MODE")) else "false"
    fees = (env.get("FEE_COLLECTION_MODE") or "disabled").strip().lower() or "disabled"
    storage = (
        "required" if env_bool(env.get("REQUIRE_DOCUMENT_STORAGE")) else "optional"
    )
    return f"payments={payments} live={live} fees={fees} storage={storage}"


def write_env_template(blueprint: Blueprint, path: Path) -> None:
    """Write a skeleton env file containing every ``sync: false`` key."""
    lines = [
        f"# Render dashboard values for {blueprint.path.name} - LOCAL ONLY.",
        "#",
        "# Paste the value you set in Render -> Environment after each key.",
        "# This file is git-ignored: never commit it, never paste it in a ticket.",
        "# Validate it with: python scripts/render_preflight.py",
        "#",
        "# Payment posture - choose exactly ONE in the Render dashboard:",
        "#   A) payments OFF    : leave PAYSTACK_* empty, keep PAYMENTS_ENABLED=false,",
        "#                        PAYMENTS_LIVE_MODE=false, FEE_COLLECTION_MODE=disabled,",
        "#                        FEE_COLLECTION_APPROVED=false.",
        "#   B) payments TEST   : PAYMENTS_ENABLED=true, PAYMENTS_LIVE_MODE=false,",
        "#                        PAYSTACK_SECRET_KEY=sk_test_..., PAYSTACK_PUBLIC_KEY=pk_test_...",
        "#                        FEE_COLLECTION_MODE=upfront, FEE_COLLECTION_APPROVED=true.",
        "# Never set PAYMENTS_LIVE_MODE=true on staging: settings refuse to boot.",
        "#",
        "# Blueprint-managed values are deliberately absent: the blueprint owns",
        "# them and a sync resets any dashboard change.",
    ]
    for var in blueprint.vars:
        if not var.sync:
            lines.append(f"{var.key}=")
    lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    try:
        path.chmod(0o600)  # the file is about to hold real credentials
    except OSError:  # pragma: no cover - filesystems without POSIX modes
        pass


def _process_env(resolution: Resolution) -> dict[str, str]:
    """Only the resolved variables plus the bare minimum to run Python."""
    keep = (
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
    env = {name: os.environ[name] for name in keep if name in os.environ}
    env.update(resolution.env)
    return env


def _print_report(
    blueprint: Blueprint,
    env_file: Path,
    resolution: Resolution,
    *,
    render_hostname: str,
) -> None:
    env = resolution.env
    secrets = [var.key for var in blueprint.vars if not var.sync and not var.generate]
    provided = [key for key in secrets if env.get(key)]
    missing = [key for key in secrets if not env.get(key)]
    required = required_keys(env)

    def row(label: str, value: str) -> None:
        print(f"{label:<12}: {value}")

    row("Blueprint", f"{blueprint.path.name} (service {blueprint.name})")
    row("Env file", f"{_display_path(env_file)} ({len(provided)} values)")
    row("Render host", render_hostname or "(not set)")
    row(
        "Python",
        f"pinned {env.get('PYTHON_VERSION', '?')}, local {platform.python_version()}",
    )
    row("Posture", describe_posture(env))
    row("Secrets", f"{len(provided)} of {len(secrets)} sync:false keys set")
    if missing:
        optional = [key for key in missing if key not in required]
        required_missing = [key for key in missing if key in required]
        if required_missing:
            row("Missing", ", ".join(required_missing))
        if optional:
            row("Unset", ", ".join(optional) + " (app defaults apply)")
    print()


def _display_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _git_sha() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
    except OSError:
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate a Render blueprint's runtime environment locally."
    )
    parser.add_argument(
        "--blueprint",
        default="render.staging.yaml",
        help="blueprint file relative to the repository root (default: %(default)s)",
    )
    parser.add_argument(
        "--env-file",
        default="",
        help="local KEY=VALUE file with the dashboard secrets "
        "(default: deploy/<blueprint stem>.env)",
    )
    parser.add_argument(
        "--render-hostname",
        default="",
        help="service hostname Render assigns (default: <service>.onrender.com)",
    )
    parser.add_argument(
        "--python",
        default=sys.executable,
        help="interpreter that runs manage.py (default: the one running this script)",
    )
    parser.add_argument(
        "--init",
        action="store_true",
        help="write an empty env-file skeleton and exit",
    )
    parser.add_argument(
        "--no-run",
        action="store_true",
        help="stop after the local checks; do not run manage.py check_config",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    blueprint_path = Path(args.blueprint)
    if not blueprint_path.is_absolute():
        blueprint_path = ROOT / blueprint_path
    try:
        blueprint = parse_blueprint(blueprint_path)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    env_file = (
        Path(args.env_file)
        if args.env_file
        else ROOT / "deploy" / f"{blueprint_path.stem}.env"
    )
    if not env_file.is_absolute():
        env_file = ROOT / env_file
    render_hostname = args.render_hostname or f"{blueprint.name}.onrender.com"

    if args.init:
        if env_file.exists():
            print(f"error: {_display_path(env_file)} already exists", file=sys.stderr)
            return 2
        write_env_template(blueprint, env_file)
        print(f"wrote {_display_path(env_file)} (git-ignored)")
        print("fill in the values from Render -> Environment, then run this again")
        return 0

    if env_file.exists():
        try:
            file_values = parse_env_file(env_file)
        except (OSError, ValueError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
    else:
        print(
            f"note: {_display_path(env_file)} does not exist; validating blueprint "
            "values only (run with --init to create it)",
            file=sys.stderr,
        )
        file_values = {}

    resolution = resolve(
        blueprint,
        file_values,
        render_hostname=render_hostname,
        git_sha=_git_sha(),
    )
    errors, warnings = validate_environment(
        blueprint, resolution, render_hostname=render_hostname
    )

    _print_report(blueprint, env_file, resolution, render_hostname=render_hostname)
    for warning in warnings:
        print(f"WARN  {warning}")
    for error in errors:
        print(f"ERROR {error}", file=sys.stderr)
    if errors:
        print(
            "\nThe service would not boot. Fix the errors above before deploying.",
            file=sys.stderr,
        )
        return 2

    if args.no_run:
        print("Local checks passed (gate not run: --no-run).")
        return 0

    gate = "manage.py check_config --allow-test-mode"
    print(f"Running : {Path(args.python).name} {gate}")
    print(flush=True)
    try:
        probe = subprocess.run(
            [args.python, "-c", "import django"],
            cwd=BACKEND,
            env=_process_env(resolution),
            text=True,
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        print(f"error: could not run {args.python}: {exc}", file=sys.stderr)
        return 2
    if probe.returncode != 0:
        print(
            f"error: {args.python} cannot import Django. Re-run with "
            "--python <venv>/bin/python (or activate the virtualenv first).",
            file=sys.stderr,
        )
        return 2
    try:
        result = subprocess.run(
            [args.python, "manage.py", "check_config", "--allow-test-mode"],
            cwd=BACKEND,
            env=_process_env(resolution),
            text=True,
            check=False,
        )
    except OSError as exc:
        print(f"error: could not run the gate: {exc}", file=sys.stderr)
        return 2
    if result.returncode != 0:
        print(
            "\nGate failed. See docs/RENDER_STAGING_RECOVERY.md for the two "
            "supported payment postures.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
