"""Task runner for strata — Python/invoke (replaces the old Makefile).

Run `inv --list` to see tasks. Tasks run against the project venv (assumes `.venv` exists),
so invoke them via the venv: `source .venv/bin/activate && inv <task>` or `.venv/bin/inv <task>`.

"""

from invoke import task

VENV = ".venv/bin"


@task
def install(c):
    """Install the package + dev deps into the venv (assumes .venv exists)."""
    c.run(f'{VENV}/pip install -e ".[dev]"')


@task
def dev(c):
    """Run the API locally with autoreload (http://127.0.0.1:8000, docs at /docs)."""
    c.run(f"{VENV}/uvicorn strata_engine.main:app --reload", pty=True)


@task
def test(c):
    """Run the test suite with coverage."""
    c.run(
        f"{VENV}/python -m pytest --cov --cov-report=term-missing"
        " --cov-report=xml --junitxml=test-results/junit.xml"
    )


@task
def cov(c):
    """Coverage with an HTML report (htmlcov/index.html)."""
    c.run(f"{VENV}/python -m pytest --cov --cov-report=html")


@task
def lint(c):
    """Ruff lint + format check."""
    c.run(f"{VENV}/ruff check .")
    c.run(f"{VENV}/ruff format --check .")


@task
def typecheck(c):
    """Static type check (mypy, strict)."""
    c.run(f"{VENV}/mypy strata_engine")


@task
def security(c):
    """SAST (bandit) + dependency CVE scan (pip-audit)."""
    c.run(f"{VENV}/bandit -q -c pyproject.toml -r strata_engine")
    c.run(f"{VENV}/pip-audit --skip-editable")


@task(pre=[lint, typecheck, security, test])
def check(c):
    """Everything CI runs, locally (lint + typecheck + security + tests)."""


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
