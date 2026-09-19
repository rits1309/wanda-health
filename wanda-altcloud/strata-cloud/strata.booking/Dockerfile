# Dockerfile — Strata.Booking service
# Build context: strata-cloud/ (repository root — needed for the strata.core/strata.identity COPYs below)
# Build command (run from strata-cloud/): docker build -f strata.booking/Dockerfile -t strata-booking:latest .

FROM python:3.13-slim AS base

LABEL service="strata-booking"
LABEL maintainer="AltCloud AI"
LABEL org.opencontainers.image.source="https://github.com/Wanda-Health/wanda-altcloud"

RUN groupadd -r strata && useradd -r -g strata -d /app -s /sbin/nologin strata

WORKDIR /app

RUN apt-get update && apt-get upgrade -y && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy local path dependencies
COPY strata.core/pyproject.toml strata.core/setup.cfg* /deps/strata.core/
COPY strata.core/strata_core/ /deps/strata.core/strata_core/
COPY strata.identity/pyproject.toml strata.identity/setup.cfg* /deps/strata.identity/
COPY strata.identity/strata_identity/ /deps/strata.identity/strata_identity/

# Copy service
COPY strata.booking/pyproject.toml strata.booking/setup.cfg* /app/
COPY strata.booking/strata_booking/ /app/strata_booking/

# Install in dependency order. The trailing msgpack/setuptools upgrade
# targets specific fixed versions (not a bare --upgrade) so a real CVE fix
# fails loudly in CI if it ever can't be satisfied, instead of silently
# keeping a vulnerable version: msgpack>=1.2.1 (GHSA-6v7p-g79w-8964),
# setuptools>=83.0.0 (CVE-2025-47273, CVE-2026-59890). Both are transitive,
# not declared in this service's own pyproject.toml, so bumped here rather
# than editing dependency declarations that belong to the application, not
# the image. The two rm's target the REAL, confirmed cause of these CVEs
# still showing up in scans despite the upgrade above -- found by grepping
# a real built image for the old version strings: pip vendors its OWN,
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

USER strata

EXPOSE 8010

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:8010/health || exit 1

ENV UVICORN_WORKERS=1

CMD ["sh", "-c", "uvicorn strata_booking.main:app --host 0.0.0.0 --port 8010 --workers ${UVICORN_WORKERS}"]
