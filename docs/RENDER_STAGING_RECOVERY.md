# Render staging recovery: recreate from the blueprint, then choose one payment posture

Runbook for the staging deploy that failed at build with:

```
django.core.exceptions.ImproperlyConfigured:
Paystack keys are set while PAYMENTS_ENABLED is false.
```

This is an intentional boot guard (`Backend/core/settings.py`), not a bug in the
application: a deployment that has Paystack credentials but has payments
switched off is ambiguous, so it refuses to start. The fix is configuration,
not code.

## 1. Why it failed at build

The blueprint's build command runs `collectstatic`, which imports the Django
settings module. Any startup-fatal settings error therefore stops the **build**,
before Render ever tries to boot the service. The same error would appear at
start if the build did not import settings.

The service that failed had been configured by hand, so its environment had
drifted from `render.staging.yaml`: Paystack keys had been pasted into the
dashboard while `PAYMENTS_ENABLED` was `false`, and it ran Python 3.13.4 instead
of the pinned 3.11.9.

## 2. Why recreate instead of patching the old service

Render preserves environment variables that a blueprint does not declare, even
on services the blueprint manages. A blueprint sync onto the old hand-rolled
service would keep the stray Paystack keys (and any other leftover variable).
A **new** service created from `render.staging.yaml` receives exactly the
declared variables - nothing else - with:

- `PYTHON_VERSION=3.11.9`, matching CI;
- every credential `sync: false`, so it is entered in the dashboard and never
  committed;
- payment posture deliberately left to the dashboard (see section 4).

## 3. Preflight locally (do this before every deploy)

The dashboard is the only place `sync: false` values exist, so keep a local,
git-ignored copy and validate the exact environment Render will boot with:

```bash
# once: writes deploy/render.staging.env with one empty line per secret key
python scripts/render_preflight.py --init

# paste the dashboard values into deploy/render.staging.env, then:
python scripts/render_preflight.py               # rebuilds Render's env and runs the gate
```

The preflight calls the same command the service runs at boot
(`cd Backend && python manage.py check_config`), so a clean preflight means a
clean boot, not just a clean parse. If Render assigned a hostname other than
`<service>.onrender.com`, pass it with `--render-hostname`.

The preflight never prints secret values. It checks the things that are easy to
get wrong in a dashboard - missing storage or database credentials, a hostname
missing from `DJANGO_ALLOWED_HOSTS`, Paystack keys with payments off, live mode
on staging - then runs `check_config` and prints the resolved posture. A clean
run ends with a line like:

```
Configuration OK (1 warning(s)). payments=off mode=test fees=disabled
```

`deploy/render.staging.env` is git-ignored. Never commit it, paste it into a
ticket, or attach it to a PR.

## 4. Payment posture: exactly one

Choose **one** of these in Render -> your service -> Environment. The six
switches are `sync: false` in the blueprint on purpose: if the file pinned them,
a later sync would reset a dashboard change while the dashboard-only Paystack
keys stayed behind - recreating the failure above.

### A. Payments OFF (fastest green deploy)

| Variable | Value |
| --- | --- |
| `PAYSTACK_SECRET_KEY` | *delete the variable (or leave it empty)* |
| `PAYSTACK_PUBLIC_KEY` | *delete the variable (or leave it empty)* |
| `PAYMENTS_ENABLED` | `false` |
| `PAYMENTS_LIVE_MODE` | `false` |
| `FEE_COLLECTION_MODE` | `disabled` |
| `FEE_COLLECTION_APPROVED` | `false` |

Leaving all six unset is equivalent: the settings defaults are payments off,
live mode off, fees disabled and unapproved.

### B. Payments ON in TEST mode (full demo, no real money)

| Variable | Value |
| --- | --- |
| `PAYMENTS_ENABLED` | `true` |
| `PAYMENTS_LIVE_MODE` | `false` - staging refuses `true` at startup |
| `PAYSTACK_SECRET_KEY` | your `sk_test_...` key from the Paystack dashboard |
| `PAYSTACK_PUBLIC_KEY` | your `pk_test_...` key |
| `FEE_COLLECTION_MODE` | `upfront` |
| `FEE_COLLECTION_APPROVED` | `true` |

Rules that are enforced by the application, not by this document:

- `PAYMENTS_LIVE_MODE=true` on `DEPLOY_ENVIRONMENT=staging` is startup-fatal,
  whatever key is present.
- Live mode also requires `LEGAL_TERMS_APPROVED=true` and an `sk_live_` key;
  a live key with live mode off is refused.
- Upfront fees require `FEE_COLLECTION_APPROVED=true`.
- Keys with payments off are refused (the original failure).
- `SMS_BACKEND` stays `console` unless `AFRICASTALKING_*` credentials are set;
  a misconfigured provider is startup-fatal.

## 5. Recreate the Blueprint service

1. In Render: **New -> Blueprint**, pick this repository. If the old service
   still exists, either delete/suspend it first or give the new one a different
   service name - Render service names are unique per workspace.
2. Confirm Render picked up `render.staging.yaml` (not `render.yaml`, which is
   production). The blueprint deploys `branch: main`, so merge the fix first;
   an unmerged branch is not deployable from the blueprint.
3. Fill every prompted `sync: false` value from section 6, and choose the six
   payment switches from section 4 (leaving all six blank is posture A: the
   settings defaults are payments off, live mode off, fees disabled).
4. Deploy. The start command runs `check_config` before `migrate`, so a bad
   posture fails the boot with the gate report in the log instead of serving
   traffic.
5. Point the old service's custom domain / bookmarks at the new hostname once
   the smoke checks pass.

## 6. Environment checklist

| Variable | Source | Notes |
| --- | --- | --- |
| `DJANGO_SECRET_KEY` | Render `generateValue` | Never copy from another service |
| `GIT_SHA` | dashboard | The deployed commit; the release process pins it |
| `DJANGO_ALLOWED_HOSTS` | optional | Leave blank to derive from `RENDER_EXTERNAL_HOSTNAME`; if set, it must contain the `*.onrender.com` hostname |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | optional | Leave blank to derive `https://RENDER_EXTERNAL_HOSTNAME`; if set, include that origin |
| `CORS_ALLOWED_ORIGINS` | optional | Empty for the same-origin deployment |
| `SUPABASE_DB_HOST` | Supabase -> database settings | Host only, no scheme |
| `SUPABASE_DB_PASSWORD` | Supabase -> database settings | Required |
| `SUPABASE_URL` | Supabase -> project URL | `https://<ref>.supabase.co` |
| `SUPABASE_SERVICE_KEY` | Supabase -> service role key | Server-side only |
| `SUPABASE_STORAGE_BUCKET` | blueprint | `identity-documents`; must exist and be **private** |
| `PAYSTACK_*` | Paystack dashboard | Only for posture B |

Staging sets `REQUIRE_DOCUMENT_STORAGE=true`, so the service will not boot
without `SUPABASE_URL` and `SUPABASE_SERVICE_KEY`, and `/health/ready/` reports
`document_storage: missing` (503) if they stop working. Create the bucket
**private**; document uploads use short-lived signed URLs only.

## 7. Smoke checks after boot

```bash
SERVICE=https://<service>.onrender.com

curl -sS "$SERVICE/health/" | jq .
# {"status":"ok","service":"loan-platform","product":"PesaFix","environment":"test"}

curl -sS "$SERVICE/health/ready/" | jq .
# {"status":"ready","checks":{"database":"ok", ... "document_storage":"configured" ...}}

curl -sS "$SERVICE/api/v1/loans/quote/?amount=1000" | jq .
# {"amount":1000,"service_fee":100,"fee_collection_mode":"disabled", ...}
```

`/health/` is the Render health check. `/health/ready/` is the meaningful one:
it returns 503 if the database, required storage bucket or a safety gate is
wrong.

## 8. Troubleshooting

| Symptom | Cause | Action |
| --- | --- | --- |
| Build fails with `Paystack keys are set ...` | Keys present with `PAYMENTS_ENABLED=false` | Delete both keys (posture A) or switch on test payments (posture B) |
| Start fails with `Staging can never run PAYMENTS_LIVE_MODE=true` | `PAYMENTS_LIVE_MODE=true` on staging | Set it to `false`; live mode belongs to production |
| Start fails with `Document storage is required ...` | `REQUIRE_DOCUMENT_STORAGE=true`, storage keys missing | Add `SUPABASE_URL` and `SUPABASE_SERVICE_KEY` |
| `/health/` returns 400 | `DJANGO_ALLOWED_HOSTS` does not include the onrender hostname | Clear it (derived automatically) or add the hostname |
| Deploy fails on `migrate` | Database is not empty and its `django_migrations` history predates this codebase | Point staging at its own fresh database (see `docs/RELEASE_PROCESS.md`, isolation rule 1); never migrate a legacy database in place |
| Deploy still runs Python 3.13 | Blueprint not applied, or a dashboard `PYTHON_VERSION` | Recreate from `render.staging.yaml`; it pins `3.11.9` |

## 9. What CI already guarantees

- `render.staging.yaml` and `render.yaml` are generated from
  `deploy/render.common.yaml.j2` plus the params files; CI fails on drift.
- The suite asserts that both blueprints pin Python 3.11, commit no secret
  values, keep every credential `sync: false`, and keep the payment posture
  dashboard-owned rather than pinned to a stale value.
- `check_config` is tested for staging: secure cookies, required storage and
  `PAYMENTS_LIVE_MODE=false` are non-negotiable.

The remaining steps - creating the Render service and entering dashboard
values - are deliberate human actions and cannot be performed from CI.
