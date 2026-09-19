"""Invoke tasks — the project's task runner (run as `.venv/bin/inv <task>`; `inv --list`).

Commands shell out to the project venv's own binaries (VENV prefix, mirroring
strata.identity/tasks.py) so tasks work without activating the venv and never pick
up a different global tool version from PATH.
"""

from invoke import task
from invoke.context import Context

VENV = ".venv/bin"


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
    c.run(f"{VENV}/mypy strata_member tests")


@task
def security(c: Context) -> None:
    """bandit (SAST) + pip-audit (dependency CVEs; the editable local dist is skipped)."""
    c.run(f"{VENV}/bandit -q -c pyproject.toml -r strata_member")
    c.run(f"{VENV}/pip-audit --skip-editable")


@task(pre=[lint, typecheck, security, test])
def check(c: Context) -> None:
    """Everything CI runs: lint + typecheck + security + tests."""
