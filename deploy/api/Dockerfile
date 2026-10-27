# The API image. Build from the repository root:
#   docker build -f deploy/api/Dockerfile -t denial-appeal-agent-api .
#
# It installs exactly the versions in requirements.lock (with their hashes). Refresh the lock
# after a change to pyproject.toml, in the same base image so the Linux / Python 3.11 packages
# are the ones chosen:
#   docker run --rm -v "$PWD/pyproject.toml:/w/pyproject.toml:ro" \
#     -v "$PWD/README.md:/w/README.md:ro" -v "$PWD/deploy/api:/w/out" -w /w \
#     python:3.11-slim@sha256:ae952e0a57138a4317b9b61caaa19a8217c2f356803f8d7e63aad08769950937 \
#     sh -c "pip install -q --root-user-action=ignore pip-tools==7.6.2 && pip-compile -q \
#       --upgrade --generate-hashes --strip-extras --allow-unsafe \
#       --output-file out/requirements.lock pyproject.toml"
#
# Not in the image: settings and secrets (given at run time), the model files (mounted at
# MODEL_DIR), tests, data. See .dockerignore.

# python:3.11-slim, the Python version CI tests on; the digest names one exact image.
FROM python:3.11-slim@sha256:ae952e0a57138a4317b9b61caaa19a8217c2f356803f8d7e63aad08769950937 AS build

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN python -m venv /opt/venv
COPY deploy/api/requirements.lock /tmp/requirements.lock
# Every package must match a hash in the lock, so a changed file on the index fails the build.
RUN /opt/venv/bin/pip install --require-hashes --no-deps -r /tmp/requirements.lock


FROM python:3.11-slim@sha256:ae952e0a57138a4317b9b61caaa19a8217c2f356803f8d7e63aad08769950937

# The project is not pip-installed: uvicorn and Alembic import `src` from /app.
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONPATH=/app \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN useradd --uid 10001 --user-group --no-create-home --shell /usr/sbin/nologin app

WORKDIR /app
COPY --from=build /opt/venv /opt/venv
COPY alembic.ini ./
COPY config/prompts ./config/prompts
COPY src ./src

USER 10001

EXPOSE 8000

# The slim image has no curl. urlopen raises on a 503, so the check fails when the database
# does not answer.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)"]

# One worker only: upload jobs run inside this process, and a second process would mark the
# first one's running jobs as interrupted at its start.
CMD ["uvicorn", "src.api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
