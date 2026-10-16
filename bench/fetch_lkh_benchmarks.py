"""Download the benchmark archives published with LKH-3.

Keld Helsgaun publishes one archive per problem type next to LKH-3, holding
the instances, the parameter files he used and the best tours.  This module
downloads the archives the pilot needs into ``data/LKH3/`` and records
their source and SHA-256 digest in ``bench/lkh_manifest.json``.
"""

from __future__ import annotations

import json
import logging
import shutil
import tarfile
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from urllib.request import Request, urlopen

from .metadata import REPOSITORY_ROOT, file_sha256
from .suites import DATA_ROOT

logger = logging.getLogger(__name__)

# The site only answers plain HTTP; the recorded digests make a changed
# archive visible.
DEFAULT_BASE_URL = "http://webhotel4.ruc.dk/~keld/research/LKH-3/BENCHMARKS/"
LKH_MANIFEST_PATH = REPOSITORY_ROOT / "bench" / "lkh_manifest.json"
# mTSP is left out of the default: its archive holds .tsp and .vrp files with
# LKH-only header lines that the TSPLIB parser, and so the viewer catalog,
# cannot load.  Fetch it explicitly when it is needed.
BENCHMARK_TYPES: tuple[str, ...] = ("SOP", "TSPTW", "CVRPTW")

Downloader = Callable[[str, Path], None]
"""Saves a URL to a local path; injectable for tests."""


def _default_downloader(url: str, destination: Path) -> None:
    """Stream a URL to a file."""
    request = Request(url, headers={"User-Agent": "teaspoon-lkh-fetcher"})
    with urlopen(request, timeout=120) as response, destination.open("wb") as handle:
        shutil.copyfileobj(response, handle)


def fetch_lkh_benchmarks(
    types: Sequence[str] = BENCHMARK_TYPES,
    *,
    data_root: Path = DATA_ROOT,
    base_url: str = DEFAULT_BASE_URL,
    refresh: bool = False,
    manifest_path: Path | None = None,
    downloader: Downloader = _default_downloader,
) -> dict[str, dict[str, object]]:
    """Download and unpack LKH-3 benchmark archives.

    Each archive is kept as ``data/LKH3/<type>.tgz`` and unpacked into
    ``data/LKH3/<type>/``.  A type that is already unpacked is only recorded
    unless ``refresh`` is set.  Archives are checked against the digests in
    the manifest, because the site only offers plain HTTP.

    Args:
        types: Archive names without suffix, e.g. ``"SOP"``.
        data_root: Root of the local data folders.
        base_url: Folder URL that holds the ``<type>.tgz`` archives.
        refresh: Download and unpack again even when the type exists.
        manifest_path: Manifest to update; defaults to
            :data:`LKH_MANIFEST_PATH`.
        downloader: URL fetcher; injectable for tests.

    Returns:
        The updated manifest entries, keyed by type.

    Raises:
        OSError: If a download fails.
        ValueError: If an archive does not match the digest in the manifest
            and ``refresh`` is not set.
        tarfile.TarError: If an archive is corrupt or unsafe to unpack.
        EOFError: If an archive is truncated.
    """
    target = manifest_path if manifest_path is not None else LKH_MANIFEST_PATH
    entries: dict[str, dict[str, object]] = {}
    if target.is_file():
        entries = json.loads(target.read_text(encoding="utf-8")).get("entries", {})
    root = data_root / "LKH3"
    root.mkdir(parents=True, exist_ok=True)
    for name in types:
        archive_path = root / f"{name}.tgz"
        extract_path = root / name
        url = f"{base_url.rstrip('/')}/{name}.tgz"
        downloaded = False
        if refresh or not archive_path.is_file():
            logger.info("downloading %s", url)
            # A download that stops halfway must not look like a cached archive.
            partial_path = archive_path.with_name(archive_path.name + ".partial")
            downloader(url, partial_path)
            partial_path.replace(archive_path)
            downloaded = True
        digest = file_sha256(archive_path)
        previous = entries.get(name)
        if previous and previous.get("sha256") != digest:
            if not refresh:
                raise ValueError(
                    f"{archive_path} has SHA-256 {digest}, but the manifest "
                    f"records {previous.get('sha256')}; pass refresh to accept "
                    "a changed archive"
                )
            logger.warning("%s changed at the source", archive_path.name)
        if downloaded or not extract_path.is_dir():
            _unpack(archive_path, extract_path)
        entries[name] = {
            "source": url,
            "fetched": time.strftime("%Y-%m-%d")
            if downloaded
            else (previous or {}).get("fetched"),
            "sha256": digest,
            "bytes": archive_path.stat().st_size,
        }
    document = {"version": 1, "entries": {key: entries[key] for key in sorted(entries)}}
    target.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    return entries


def _unpack(archive_path: Path, extract_path: Path) -> None:
    """Unpack an archive into a fresh directory with tarfile's data filter.

    The data filter rejects absolute paths, parent-directory members and
    special files, so an archive cannot write outside ``extract_path``.
    """
    staging_path = extract_path.with_name(extract_path.name + ".partial")
    if staging_path.exists():
        shutil.rmtree(staging_path)
    staging_path.mkdir(parents=True)
    with tarfile.open(archive_path) as archive:
        archive.extractall(staging_path, filter="data")
    if extract_path.exists():
        shutil.rmtree(extract_path)
    staging_path.rename(extract_path)
