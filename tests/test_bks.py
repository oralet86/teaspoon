"""Tests for BKS provenance and the fetch-bks maintenance command."""

from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path

import pytest

from bench.fetch_bks import fetch_bks, parse_index
from bench.metadata import file_sha256
from bench.suites import bks_provenance, load_manifest

_INDEX_HTML = """
<html><body>
<a href="/cvrplib/en/download/instance-set/17" title="Set File">zip</a>
<a href="/cvrplib/en/download/instance/901" title="Instance File">
    X-n5-k2
</a>
<td><a href="/cvrplib/en/download/bks/901" title="Solution File">3</a></td>
<a href="/cvrplib/en/download/instance/902" title="Instance File">
    X-n6-k2
</a>
<td><a href="/cvrplib/en/download/bks/902" title="Solution File">4</a></td>
</body></html>
"""

_INDEX_URL = "https://example.test/cvrplib/en"
_SOLUTION_URL = "https://example.test/cvrplib/en/download/bks/901"
_SOLUTION_TEXT = "Route #1: 1 2\nCost 3\n"


def _write_instance(directory: Path, *, with_solution: bool = True) -> Path:
    """Write a tiny three-node CVRP instance, optionally with its BKS."""
    directory.mkdir(parents=True, exist_ok=True)
    instance_path = directory / "X-n5-k2.vrp"
    instance_path.write_text(
        "NAME : X-n5-k2\nTYPE : CVRP\nDIMENSION : 3\n"
        "EDGE_WEIGHT_TYPE : EUC_2D\nCAPACITY : 10\n"
        "NODE_COORD_SECTION\n1 0 0\n2 1 0\n3 0 1\n"
        "DEMAND_SECTION\n1 0\n2 1\n3 1\nDEPOT_SECTION\n1\n-1\nEOF\n"
    )
    if with_solution:
        (directory / "X-n5-k2.sol").write_text(_SOLUTION_TEXT)
    return instance_path


def test_parse_index_pairs_instance_and_solution_links() -> None:
    entries = parse_index(_INDEX_HTML, base_url=_INDEX_URL)
    assert set(entries) == {"X-n5-k2", "X-n6-k2"}
    entry = entries["X-n5-k2"]
    assert entry.instance_url == f"{_INDEX_URL}/download/instance/901"
    assert entry.solution_url == _SOLUTION_URL


def test_fetch_bks_adopts_local_solutions(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    instance_path = _write_instance(data_root / "X")
    companion = data_root / "X" / "X-n5-k2.sol"
    manifest = tmp_path / "bks_manifest.json"
    fetched_urls: list[str] = []

    def opener(url: str) -> bytes:
        fetched_urls.append(url)
        assert url == _INDEX_URL
        return _INDEX_HTML.encode("utf-8")

    entries = fetch_bks(
        ["x"],
        data_root=data_root,
        index_url=_INDEX_URL,
        manifest_path=manifest,
        opener=opener,
    )
    assert fetched_urls == [_INDEX_URL]
    entry = entries["X-n5-k2.sol"]
    assert entry["instance"] == "X-n5-k2"
    assert entry["source"] == _SOLUTION_URL
    assert entry["fetched"] is None
    assert entry["sha256"] == file_sha256(companion)
    assert entry["bks"] == pytest.approx(3.0)
    assert isinstance(instance_path, Path)
    assert load_manifest(manifest) == entries
    assert json.loads(manifest.read_text())["version"] == 1


def test_fetch_bks_downloads_missing_solutions(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    _write_instance(data_root / "X", with_solution=False)
    manifest = tmp_path / "bks_manifest.json"
    solution_bytes = _SOLUTION_TEXT.encode("utf-8")

    def opener(url: str) -> bytes:
        if url == _INDEX_URL:
            return _INDEX_HTML.encode("utf-8")
        assert url == _SOLUTION_URL
        return solution_bytes

    entries = fetch_bks(
        ["x"],
        data_root=data_root,
        index_url=_INDEX_URL,
        manifest_path=manifest,
        opener=opener,
    )
    companion = data_root / "X" / "X-n5-k2.sol"
    assert companion.read_bytes() == solution_bytes
    entry = entries["X-n5-k2.sol"]
    assert entry["fetched"] == date.today().isoformat()
    assert entry["sha256"] == file_sha256(companion)
    assert entry["bks"] == pytest.approx(3.0)


def test_fetch_bks_refresh_replaces_changed_solution(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    data_root = tmp_path / "data"
    _write_instance(data_root / "X")
    companion = data_root / "X" / "X-n5-k2.sol"
    manifest = tmp_path / "bks_manifest.json"
    old_digest = file_sha256(companion)
    manifest.write_text(
        json.dumps(
            {
                "version": 1,
                "entries": {
                    "X-n5-k2.sol": {
                        "instance": "X-n5-k2",
                        "source": _SOLUTION_URL,
                        "fetched": None,
                        "sha256": old_digest,
                        "bks": 3.0,
                    }
                },
            }
        )
    )
    updated = b"Route #1: 2 1\nCost 3\n"

    def opener(url: str) -> bytes:
        if url == _INDEX_URL:
            return _INDEX_HTML.encode("utf-8")
        assert url == _SOLUTION_URL
        return updated

    with caplog.at_level(logging.WARNING):
        entries = fetch_bks(
            ["x"],
            data_root=data_root,
            index_url=_INDEX_URL,
            refresh=True,
            manifest_path=manifest,
            opener=opener,
        )
    assert companion.read_bytes() == updated
    entry = entries["X-n5-k2.sol"]
    assert entry["fetched"] == date.today().isoformat()
    assert entry["sha256"] == file_sha256(companion)
    assert entry["sha256"] != old_digest
    assert "changed at the source" in caplog.text


def test_bks_provenance_reports_actual_digest(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    data_root = tmp_path / "data"
    instance_path = _write_instance(data_root / "X")
    companion = data_root / "X" / "X-n5-k2.sol"
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "version": 1,
                "entries": {
                    "X-n5-k2.sol": {
                        "source": _SOLUTION_URL,
                        "fetched": "2026-01-01",
                        "sha256": "0" * 64,
                    }
                },
            }
        )
    )
    with caplog.at_level(logging.WARNING):
        provenance = bks_provenance(instance_path, manifest_path=manifest)
    assert provenance is not None
    assert provenance.source == _SOLUTION_URL
    assert provenance.sha256 == file_sha256(companion)
    assert provenance.fetched == "2026-01-01"
    assert "mismatch" in caplog.text


def test_bks_provenance_without_manifest_entry(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    data_root = tmp_path / "data"
    instance_path = _write_instance(data_root / "X")
    companion = data_root / "X" / "X-n5-k2.sol"
    with caplog.at_level(logging.WARNING):
        provenance = bks_provenance(
            instance_path, manifest_path=tmp_path / "absent.json"
        )
    assert provenance is not None
    assert provenance.source is None
    assert provenance.sha256 == file_sha256(companion)
    assert "no BKS manifest entry" in caplog.text


def test_bks_provenance_without_companion(tmp_path: Path) -> None:
    instance_path = _write_instance(tmp_path / "data" / "X", with_solution=False)
    assert bks_provenance(instance_path, manifest_path=tmp_path / "absent.json") is None
