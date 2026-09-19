"""Docker-free baseline: the package imports and exposes its write seam."""

import strata_member


def test_package_exposes_the_demographics_seam() -> None:
    """`converge_demographics` is importable from the package root (its public seam)."""
    assert hasattr(strata_member, "converge_demographics")
    assert callable(strata_member.converge_demographics)


def test_all_lists_only_the_public_seam() -> None:
    """`__all__` names exactly the exported write helpers (iteration 1: demographics)."""
    assert strata_member.__all__ == ["converge_demographics"]
