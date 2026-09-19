"""Unit tests for the reference-data source registry (``scripts/sources.py``).

These cover the only logic in the fetch path that isn't just I/O: resolving download URL(s) for
each domain and refusing anything that isn't HTTPS. The manifest lookup is mocked, so no network.
"""

from __future__ import annotations

import pytest

from scripts import sources


def test_cms_sources_use_stable_per_year_https_urls() -> None:
    """CMS sources resolve to one stable per-year HTTPS URL each."""
    for name in ("procedures", "diagnoses"):
        urls = sources.get_source(name).resolve_urls()
        assert len(urls) == 1
        assert urls[0].startswith("https://www.cms.gov/files/zip/")
        assert str(sources.ICD10_YEAR) in urls[0]


def test_openfda_ndc_urls_parses_manifest(monkeypatch: pytest.MonkeyPatch) -> None:
    """The openFDA NDC source resolves its download URL from the manifest."""
    file_url = "https://download.open.fda.gov/drug/ndc/drug-ndc-0001-of-0001.json.zip"
    manifest = {"results": {"drug": {"ndc": {"partitions": [{"file": file_url}]}}}}
    monkeypatch.setattr(sources, "_read_json", lambda _url: manifest)

    assert sources.get_source("drugs").resolve_urls() == [file_url]


def test_openfda_ndc_urls_handles_multiple_partitions(monkeypatch: pytest.MonkeyPatch) -> None:
    """A multi-partition openFDA manifest resolves every partition URL in order."""
    base = "https://download.open.fda.gov/drug/ndc/drug-ndc-000{}-of-0002.json.zip"
    partitions = [{"file": base.format(1)}, {"file": base.format(2)}]
    manifest = {"results": {"drug": {"ndc": {"partitions": partitions}}}}
    monkeypatch.setattr(sources, "_read_json", lambda _url: manifest)

    assert sources.get_source("drugs").resolve_urls() == [base.format(1), base.format(2)]


def test_non_https_source_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """A manifest offering a non-HTTPS download is rejected."""
    manifest = {"results": {"drug": {"ndc": {"partitions": [{"file": "http://insecure/x.zip"}]}}}}
    monkeypatch.setattr(sources, "_read_json", lambda _url: manifest)

    with pytest.raises(ValueError, match="non-HTTPS"):
        sources.get_source("drugs").resolve_urls()


def test_unknown_source_errors() -> None:
    """Requesting an unknown refresh source exits with an error."""
    with pytest.raises(SystemExit):
        sources.get_source("nope")


def test_dest_name_uses_url_basename_for_drugs() -> None:
    """The drugs source names its download after the URL basename."""
    url = "https://download.open.fda.gov/drug/ndc/drug-ndc-0001-of-0001.json.zip"
    assert sources.get_source("drugs").dest_name(url) == "drug-ndc-0001-of-0001.json.zip"
