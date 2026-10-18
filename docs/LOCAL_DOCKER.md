# Local docker-compose stack

Purpose: a **reproducible local verification environment** that matches
production topology much more closely than the SQLite runserver flow:
PostgreSQL **15** (the major the production host runs — Supabase Postgres 15)
plus MinIO so the real S3-compatible document adapter runs end to end.

This stack is **not** a production deployment description:

- every credential is a docker-local placeholder from `.env.compose.example`;
- payments are off, fee collection is off, legal approval is off, SMS prints
  to the app log;
- no provider credentials of any kind are required or used.

## Use

```bash
cp .env.compose.example .env.compose      # placeholders are already usable
docker compose up --build                 # db + minio + bucket init + app
curl http://localhost:8000/health/
```

MinIO console: http://localhost:9001 (credentials from `.env.compose`).
Documents uploaded through the API land in the `identity-documents` bucket;
viewing them exercises presigned URLs, which are generated against
`S3_PUBLIC_ENDPOINT_URL` (`http://localhost:9000`) so the browser can reach
the store while the app container talks to it as `http://minio:9000`. That
split is first-class configuration, not a compose hack — SigV4 signs the
host header, and the signature must cover the origin the browser hits.

## What was verified before committing

The sandbox this change was authored in has **no Docker daemon**, so
`docker compose up` itself could not be executed here. In its place:

- `compose.yml` parses as valid YAML and every service carries an image or
  build context; dependency conditions and healthchecks were reviewed
  against the compose spec;
- `check_config` was rehearsed with the **exact** compose environment
  (placeholder values, `s3` document store) and passed:
  `Configuration OK (1 warning(s)). payments=off mode=test fees=disabled`;
- the `DOCUMENT_STORE=s3` path is covered by the committed adapter contract
  tests, and the new `S3_PUBLIC_ENDPOINT_URL` split-horizon signing path
  has its own test (`core.tests.test_document_storage`);
- the same env wiring is exercised in CI on every push: the `tests-postgres`
  job runs migrations + the full 360-test suite against a `postgres:15`
  service container.

`check_config` itself was made store-agnostic in this phase — previously it
required Supabase credentials even when `DOCUMENT_STORE=s3` was selected.

## Known limitations (commit-time)

- First `docker compose up` on a machine with Docker is expected to work,
  but has not been smoke-tested here; treat the first run as the smoke test
  and check `docker compose ps` health for `db`, `minio`, `app`.
- `minio/minio:latest` and `minio/mc:latest` are floating tags; pin them to a
  dated `RELEASE.` tag in your registry for fully reproducible builds across
  time. (Docker Hub metadata was not reachable from the authoring sandbox to
  pre-select one.)
- POSTGRES data and MinIO objects persist in named volumes `pgdata` /
  `miniodata`; `docker compose down -v` resets everything.
