"""The committed Data Classification Register matches the code.

The drift gate for the rule 6 projection: the register is generated,
never hand-edited, so any schema or classification change that skips
``inv classification-register`` — or any hand edit to the register — fails
here. Only the informational Regenerated date line is exempt.
"""

from strata_core.classification_register import (
    REGENERATED_PREFIX,
    REGISTER_PATH,
    render_register,
)

PLACEHOLDER_DATE = "00-00-0000"


def _without_date_line(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if not line.startswith(REGENERATED_PREFIX))


def test_register_exists() -> None:
    """The generated per-field register file exists on disk."""
    assert REGISTER_PATH.is_file(), "regenerate with: inv classification-register"


def test_register_matches_code() -> None:
    """The committed register matches what the registry renders (drift gate)."""
    committed = _without_date_line(REGISTER_PATH.read_text())
    rendered = _without_date_line(render_register(PLACEHOLDER_DATE))
    assert committed == rendered, (
        "DATA_CLASSIFICATION_REGISTER.md is stale or hand-edited — "
        "regenerate with: inv classification-register"
    )
