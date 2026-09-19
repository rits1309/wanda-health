"""Invoke tasks — the project's task runner (run as `.venv/bin/inv <task>`; `inv --list`).

Commands shell out to the project venv's own binaries (VENV prefix, mirroring
the sibling services) so tasks work without activating the venv.
"""

from invoke import task
from invoke.context import Context

VENV = ".venv/bin"


@task
def dev(c: Context) -> None:
    """Run the dev server with autoreload — http://127.0.0.1:8000 (docs at /docs)."""
    c.run(f"{VENV}/uvicorn strata_engine_auth.main:app --reload", pty=True)


@task
def seed(c: Context) -> None:
    """Seed the dev cast into the pool + identity tables (DEV ONLY; needs AWS creds)."""
    c.run(f"{VENV}/python -m scripts.seed", pty=True)


@task
def test(c: Context) -> None:
    """Run the test suite with coverage."""
    c.run(f"{VENV}/pytest --cov")


@task
def lint(c: Context) -> None:
    """Ruff lint + format check."""
    c.run(f"{VENV}/ruff check .")
    c.run(f"{VENV}/ruff format --check .")


@task
def typecheck(c: Context) -> None:
    """mypy (strict)."""
    c.run(f"{VENV}/mypy strata_engine_auth scripts tests")


@task
def security(c: Context) -> None:
    """bandit (SAST) + pip-audit (dependency CVEs; the editable local dist is skipped)."""
    c.run(f"{VENV}/bandit -q -c pyproject.toml -r strata_engine_auth")
    c.run(f"{VENV}/pip-audit --skip-editable")


@task(pre=[lint, typecheck, security, test])
def check(c: Context) -> None:
    """Everything CI runs: lint + typecheck + security + tests."""
