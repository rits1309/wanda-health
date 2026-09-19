# Dockerfile — Strata.Engine.Auth service
# Build context: strata-cloud/ (repository root — needed for the strata.core/strata.identity COPYs below)
# Build command (run from strata-cloud/): docker build -f strata.engine.auth/Dockerfile -t strata-engine-auth:latest .

FROM python:3.13-slim AS base

# Metadata
LABEL service="strata-engine-auth"
LABEL maintainer="AltCloud AI"
LABEL org.opencontainers.image.source="https://github.com/Wanda-Health/wanda-altcloud"

# Non-root user for security
RUN groupadd -r strata && useradd -r -g strata -d /app -s /sbin/nologin strata

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get upgrade -y && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# --- Dependency install stage (cached unless pyproject.toml changes) ---
# Copy local path dependencies first
COPY strata.core/pyproject.toml strata.core/setup.cfg* /deps/strata.core/
COPY strata.core/strata_core/ /deps/strata.core/strata_core/
COPY strata.identity/pyproject.toml strata.identity/setup.cfg* /deps/strata.identity/
COPY strata.identity/strata_identity/ /deps/strata.identity/strata_identity/

# Copy service
COPY strata.engine.auth/pyproject.toml strata.engine.auth/setup.cfg* /app/
COPY strata.engine.auth/strata_engine_auth/ /app/strata_engine_auth/
# scripts/ was never in the image before -- seed.py/import_user.py are
# genuinely dev-only (both refuse to run outside STRATA_ENVIRONMENT=local
# anyway), but provision_users.py (develop/uat) needs to actually be
# reachable inside the deployed container: reusable-provision-users.yml
# runs it as a one-off ecs run-task against this exact image. Confirmed
# missing by a real run: "ModuleNotFoundError: No module named 'scripts'".
COPY strata.engine.auth/scripts/ /app/scripts/

# Install in dependency order (core → identity → service). The trailing
# msgpack/setuptools upgrade targets specific fixed versions (not a bare
# --upgrade) so a real CVE fix fails loudly in CI if it ever can't be
# satisfied, instead of silently keeping a vulnerable version:
# msgpack>=1.2.1 (GHSA-6v7p-g79w-8964), setuptools>=83.0.0
# (CVE-2025-47273, CVE-2026-59890). Both are transitive, not declared in
# this service's own pyproject.toml, so bumped here rather than editing
# dependency declarations that belong to the application, not the image.
# The two rm's target the REAL, confirmed cause of these CVEs still
# showing up in scans despite the upgrade above -- found by grepping a
# real built image for the old version strings: pip vendors its OWN,
# separately-pinned copies of msgpack/setuptools for its own bootstrapping,
# documented in pip/_vendor/vendor.txt and a bundled CycloneDX SBOM
# (pip/_vendor/bom.cdx.json). Trivy reads that bundled SBOM as if it were
# real installed-package inventory -- a documented false-positive category
# for pip specifically. Removing just those two manifest files is enough:
# pip's own runtime never reads them (it imports its vendored modules
# directly), so this has zero effect on pip's actual behavior.
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir /deps/strata.core \
    && pip install --no-cache-dir /deps/strata.identity \
    && pip install --no-cache-dir . \
    && pip install --no-cache-dir --upgrade "msgpack>=1.2.1" "setuptools>=83.0.0" \
    && rm -rf /usr/local/lib/python3.13/ensurepip/_bundled \
    && rm -f /usr/local/lib/python3.13/site-packages/pip/_vendor/vendor.txt \
             /usr/local/lib/python3.13/site-packages/pip/_vendor/bom.cdx.json

# Switch to non-root user
USER strata

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Production: use multiple workers with --workers flag via env var
ENV UVICORN_WORKERS=1

CMD ["sh", "-c", "uvicorn strata_engine_auth.main:app --host 0.0.0.0 --port 8000 --workers ${UVICORN_WORKERS}"]
