# syntax=docker/dockerfile:1
# Backend image for the loan platform.
#
# Runtime configuration comes entirely from environment variables (see
# Backend/.env.example and .env.compose.example); nothing environment-
# specific is baked in. Dependencies install from the hash-locked lockfile,
# so builds are byte-reproducible given the same lock.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Dependency layer first so source-only changes do not bust the cache.
# --require-hashes is enforced by the generated lockfile itself on pip 24+;
# state it explicitly so a tool downgrade cannot silently weaken it.
COPY Backend/requirements.txt Backend/requirements.txt
RUN pip install --no-cache-dir --require-hashes -r Backend/requirements.txt

COPY Backend/ Backend/

# Static assets are baked into the image. core.ci_settings imports cleanly
# with no secrets and asserts the collectable state in every build.
RUN python Backend/manage.py collectstatic --noinput --settings=core.ci_settings

EXPOSE 8000

# Migrations run at container start (expand-and-contract, per the release
# process) before gunicorn accepts traffic. Mirrors the Render startCommand
# so the compose stack exercises the same boot sequence as deployment.
CMD ["sh", "-c", "python Backend/manage.py migrate --noinput && python Backend/manage.py check_config --allow-test-mode --settings=core.settings && gunicorn core.wsgi:application --chdir Backend --bind 0.0.0.0:8000 --workers 2 --threads 4 --timeout 60 --access-logfile - --error-logfile -"]
