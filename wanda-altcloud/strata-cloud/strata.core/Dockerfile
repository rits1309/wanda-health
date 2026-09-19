# Dockerfile — Strata.Core migration runner
# Build context: strata-cloud/ (repository root)
# Build command (run from strata-cloud/): docker build -f strata.core/Dockerfile -t strata-core:latest .
#
# Not a service -- strata.core is a library every other service depends on
# (see strata.engine.auth/Dockerfile etc., which pip-install it from
# /deps/strata.core). This image exists solely to run `alembic upgrade head`
# as a one-off ECS task (modules/compute/ecs's "migration" task definition,
# already Terraform-provisioned but never actually built/pushed by any CI
# pipeline until now -- confirmed by a real failure: the migration task's
# image tag was never pushed to ECR, and separately, UAT's database had
# literally never been migrated, "relation user_profiles does not exist").
#
# invoke is deliberately NOT installed here even though tasks.py's `migrate`
# task is `alembic upgrade head` under the hood -- invoke is a [dev] extra
# (pyproject.toml), and running alembic directly avoids pulling a dev-only
# tool into a deploy-pipeline image for no benefit.

FROM python:3.13-slim AS base

LABEL service="strata-core-migrate"
LABEL maintainer="AltCloud AI"
LABEL org.opencontainers.image.source="https://github.com/Wanda-Health/wanda-altcloud"

RUN groupadd -r strata && useradd -r -g strata -d /app -s /sbin/nologin strata

WORKDIR /app

RUN apt-get update && apt-get upgrade -y && rm -rf /var/lib/apt/lists/*

COPY strata.core/pyproject.toml strata.core/setup.cfg* /app/
COPY strata.core/strata_core/ /app/strata_core/
# alembic.ini's script_location = "alembic" is relative to the CWD alembic
# runs from (WORKDIR /app here) -- both must land at the same level.
COPY strata.core/alembic.ini /app/alembic.ini
COPY strata.core/alembic/ /app/alembic/

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir .

USER strata

# No CMD -- the ECS task definition always supplies an explicit command
# (["alembic", "upgrade", "head"]), matching how modules/compute/ecs's other
# task definitions work.
