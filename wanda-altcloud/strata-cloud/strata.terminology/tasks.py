"""Task runner (invoke). `inv --list` lists tasks; run via the venv (.venv/bin/inv <task>)."""

from invoke import task

VENV = ".venv/bin"


@task
def install(c):
    """Install the package + dev deps and the local path deps into the venv."""
    c.run(f'{VENV}/pip install -e ".[dev]" -e ../strata.core -e ../strata.identity')


@task
def dev(c):
    """Run the API with autoreload at http://127.0.0.1:8020 (docs at /docs)."""
    c.run(f"{VENV}/uvicorn strata_terminology.main:app --reload --port 8020", pty=True)


@task
def token(c, role="coach", sub="t-1"):
    """Mint a dev bearer token (STRATA_DEV_AUTH_SECRET from .env; any role passes here)."""
    c.run(f"{VENV}/python -m scripts.mint_token --role {role} --sub {sub}")


@task
def test(c):
    """Run the default suite (unit + api + contract + hermetic integration) with coverage.

    Excludes the opt-in `external` real-data tests (live FDA/CMS, slow) — see `test-realdata`.
    """
    c.run(
        f'{VENV}/python -m pytest -m "not external" --cov --cov-report=term-missing'
        " --cov-report=xml --junitxml=test-results/junit.xml"
    )


@task(name="test-unit")
def test_unit(c):
    """Run only the unit suite."""
    c.run(f"{VENV}/python -m pytest tests/unit")


@task(name="test-api")
def test_api(c):
    """Run only the API/endpoint suite."""
    c.run(f"{VENV}/python -m pytest tests/api")


@task(name="test-contract")
def test_contract(c):
    """Run only the Schemathesis contract suite."""
    c.run(f"{VENV}/python -m pytest tests/contract")


@task(name="test-realdata")
def test_realdata(c):
    """Run the opt-in real-data end-to-end tests (live FDA/CMS fetch + ingest; needs Docker)."""
    c.run(f'{VENV}/python -m pytest -m "external"')


@task
def lint(c):
    """Ruff lint + format check."""
    c.run(f"{VENV}/ruff check .")
    c.run(f"{VENV}/ruff format --check .")


@task
def typecheck(c):
    """Static type check (mypy, strict)."""
    c.run(f"{VENV}/mypy strata_terminology scripts")


@task
def security(c):
    """SAST (bandit) + dependency CVE scan (pip-audit)."""
    c.run(f"{VENV}/bandit -q -c pyproject.toml -r strata_terminology scripts")
    c.run(f"{VENV}/pip-audit --skip-editable")


@task(pre=[lint, typecheck, security, test])
def check(c):
    """Everything CI runs, locally (lint + typecheck + security + tests)."""


@task(name="db-up")
def db_up(c):
    """Start the shared workspace Postgres container and wait until it is healthy."""
    c.run("docker compose -f ../docker-compose.yml up -d --wait db")


@task(name="db-down")
def db_down(c):
    """Stop the shared workspace Postgres container (keeps data)."""
    c.run("docker compose -f ../docker-compose.yml down")


@task(name="db-reset")
def db_reset(c):
    """Wipe and recreate the shared workspace Postgres container (drops all data)."""
    c.run("docker compose -f ../docker-compose.yml down -v")
    c.run("docker compose -f ../docker-compose.yml up -d --wait db")


@task(name="db-logs")
def db_logs(c):
    """Tail the Postgres container logs."""
    c.run("docker compose -f ../docker-compose.yml logs -f db", pty=True)


@task(name="db-shell")
def db_shell(c):
    """Open a psql shell on the local database."""
    c.run("docker compose -f ../docker-compose.yml exec db psql -U strata -d strata", pty=True)


@task(name="trace-up")
def trace_up(c):
    """Start the local Jaeger trace backend (UI at http://localhost:16686). See.

    Shares host ports with the other services' Jaeger — run one at a time. Then run the app
    with STRATA_OTEL_TRACES_EXPORTER=otlp and
    STRATA_OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318 (see .env.example).
    """
    c.run("docker compose -f ../docker-compose.yml --profile observability up -d jaeger")


@task(name="trace-down")
def trace_down(c):
    """Stop the local Jaeger trace backend."""
    c.run("docker compose -f ../docker-compose.yml --profile observability down")


@task
def migrate(c):
    """Apply the canonical migrations (strata.core owns the one history)."""
    c.run(
        f'{VENV}/python -c "from strata_core.migrations import upgrade_to_head; '
        "from strata_core.settings import Settings; "
        "upgrade_to_head(Settings(_env_file='.env').database_url)\""
    )


# ---------------------------------------------------------------------------
# Reference data — one uniform lifecycle per domain: fetch → ingest → clean,
# with refresh chaining all three. Sources download into the gitignored,
# project-local var/ dir; the source URLs / filenames live in scripts/sources.py.
# ---------------------------------------------------------------------------


@task(name="fetch-drugs")
def fetch_drugs(c):
    """Download the FDA NDC export (openFDA) into var/."""
    c.run(f"{VENV}/python -m scripts.fetch drugs")


@task(name="ingest-drugs")
def ingest_drugs(c):
    """Load the FDA NDC data from var/ into Postgres (idempotent)."""
    c.run(f"{VENV}/python -m scripts.ingest_drugs")


@task(name="clean-drugs")
def clean_drugs(c):
    """Delete the downloaded FDA NDC file(s) from var/."""
    c.run(f"{VENV}/python -m scripts.clean drugs")


@task(pre=[fetch_drugs, ingest_drugs], name="refresh-drugs")
def refresh_drugs(c):
    """Fetch → ingest → clean the FDA NDC medications data in one step."""
    c.run(f"{VENV}/python -m scripts.clean drugs")


@task(name="fetch-procedures")
def fetch_procedures(c):
    """Download the CMS ICD-10-PCS order file into var/."""
    c.run(f"{VENV}/python -m scripts.fetch procedures")


@task(name="ingest-procedures")
def ingest_procedures(c):
    """Load the ICD-10-PCS procedure codes from var/ into Postgres (idempotent)."""
    c.run(f"{VENV}/python -m scripts.ingest_procedures")


@task(name="clean-procedures")
def clean_procedures(c):
    """Delete the downloaded ICD-10-PCS file(s) from var/."""
    c.run(f"{VENV}/python -m scripts.clean procedures")


@task(pre=[fetch_procedures, ingest_procedures], name="refresh-procedures")
def refresh_procedures(c):
    """Fetch → ingest → clean the ICD-10-PCS procedures data in one step."""
    c.run(f"{VENV}/python -m scripts.clean procedures")


@task(name="fetch-diagnoses")
def fetch_diagnoses(c):
    """Download the CMS ICD-10-CM order file into var/."""
    c.run(f"{VENV}/python -m scripts.fetch diagnoses")


@task(name="ingest-diagnoses")
def ingest_diagnoses(c):
    """Load the ICD-10-CM diagnosis codes from var/ into Postgres (idempotent)."""
    c.run(f"{VENV}/python -m scripts.ingest_diagnoses")


@task(name="clean-diagnoses")
def clean_diagnoses(c):
    """Delete the downloaded ICD-10-CM file(s) from var/."""
    c.run(f"{VENV}/python -m scripts.clean diagnoses")


@task(pre=[fetch_diagnoses, ingest_diagnoses], name="refresh-diagnoses")
def refresh_diagnoses(c):
    """Fetch → ingest → clean the ICD-10-CM diagnoses data in one step."""
    c.run(f"{VENV}/python -m scripts.clean diagnoses")
