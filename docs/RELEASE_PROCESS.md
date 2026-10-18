# Release process: staging → production

This platform has two environments, generated from one template plus
parameter files (edit `deploy/*`, regenerate with
`python scripts/render_blueprints.py --write`; CI fails on drift):

| | Staging | Production |
|---|---|---|
| Blueprint | `render.staging.yaml` | `render.yaml` |
| Service name | `loan-platform-backend-staging` | `loan-platform-backend` |
| Isolation | Separate Render service; ideally a separate Supabase project (isolated DB + own storage bucket) or at minimum a distinct bucket and empty schema; separate secrets in the Render dashboard | Live credentials |
| `DEPLOY_ENVIRONMENT` | `staging` | `production` |
| Payments | test keys or off; **live mode is refused at startup** | live only after business/legal sign-off |
| `REQUIRE_DOCUMENT_STORAGE` | `true` (bucket must exist; exercises the full upload path) | flips to `true` once the prod bucket is provisioned |
| SMS | `console` (log lines, zero spend) | provider credentials |
| Document retention purge | 30 days | 90 days |

Isolation rules:

1. Staging points at its **own** database. Never let staging share the
   prod `SUPABASE_DB_*` — a single copied env var would put rehearsal
   traffic in the live dataset.
2. Staging uses its **own** storage bucket (e.g. `identity-documents-staging`)
   or a separate MinIO/S3 account. Purge and signed URLs are scoped to it.
3. No live payment credentials ever exist in staging. This is not policy-only:
   `DEPLOY_ENVIRONMENT=staging` + `PAYMENTS_LIVE_MODE=true` **fails startup**
   (`ImproperlyConfigured`, tested in `core.tests.test_config`).
4. Staging mirrors production posture or the rehearsal means nothing:
   `check_config` hard-fails staging on DEBUG=true, insecure cookies, or
   `REQUIRE_DOCUMENT_STORAGE=false` (same test file).
5. Both environments stamp the release with `GIT_SHA`. Promotion deploys the
   **identical SHA** that staging rehearsed.
6. autoDeploy is `false` on both — deploys are deliberate human acts.

## Promotion

1. Land changes on `main` through review; CI must be green (both jobs:
   SQLite suite + Postgres suite; locks, audits, blueprints drift checks).
2. Deploy staging with the candidate SHA (Render dashboard → Manual Deploy →
   pick the commit). Set staging's `GIT_SHA` env to that SHA.
3. Staging smoke checklist (all must pass):
   - `check_config --allow-test-mode` clean (deploy runs it at boot already);
   - `GET /health/` 200 and `GET /health/ready/` OK including
     `document_store`;
   - register → upload verification docs → download via signed URL
     (exercises the storage adapter end to end);
   - password-reset OTP request honours the 60 s cooldown (second request
     within a minute returns the uniform 200, `SmsAuditLog` rows show
     `rate_limited`), and the reset completes with the code from the
     `console` SMS in the deploy logs (staging runs `SMS_BACKEND=console`);
   - one payment init → webhook round trip using **paystack test keys** (a
     test-mode `charge.success` delivered from the provider's dashboard) if
     the release touches payment code; watch the deploy log for webhook
     processing and confirm the loan state moves through the documented
     transitions only;
   - loan application → approve → disburse (test mode) → repay →
     close; verify loyalty points accrue and admin audit rows are written;
   - purge dry run over the staging bucket:
     `python Backend/manage.py purge_documents --dry-run` shows only
     staging-scoped keys;
   - migrations: rehearsal of the *actual* migration order below.
4. Record the SHA + checklist outcome in the release tracking issue.
5. **Human approval gate**: a second human (not the deployer) confirms the
   staging evidence, then production deploys the SAME SHA, sets the same
   `GIT_SHA`, and updates production env only if the release's "Env/migration
   changes" section (required in every release PR description) says so.
6. Post-deploy verification on production: `/health/`, `/health/ready/`,
   one read-only page, watch 15 minutes of logs and the error rate.

## Migrations: expand-and-contract only

Never deploy a migration and code that requires the OLD shape in separate
releases. Rules in review:

- add only (new column/table, nullable or defaulted) → any order works;
- rename/move → expand (add new, backfill, dual-write one release) then
  contract (drop old) in a LATER release after production runs the
  dual-write version;
- destructive removals are `BreakGlassRelease`: staged, explicitly approved,
  and rehearsed on staging data;
- the staging checklist always runs the real migration order first.

## Rollback

Ordered by blast radius, smallest first:

1. **Config-only regression** → revert the env change on Render (history is
   auditable), redeploy the same SHA.
2. **Code regression, DB-compatible** → Render Manual Deploy of the previous
   known-good SHA. Expand-and-contract discipline (above) guarantees the
   previous code still understands the current schema; never roll back across
   a contract migration without an export.
3. **Data-touching regression** → restore from backup, then redeploy the
   previous SHA. Supabase PITR covers 7 days on the platform tier; before
   any destructive release, take an explicit `supabase db dump` and store it
   outside the project. Rolling back data means deciding which rows written
   since the restore point to replay — that decision is always manual and
   documented in the incident record.

Each release PR must name its rollback plan (1/2/3) in the description; the
traceability matrix in the final audit maps acceptance criteria to the
relevant plan.
