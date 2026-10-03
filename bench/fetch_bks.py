"""Download and verify the best-known solutions used by the benchmark.

CVRPLIB publishes one solution file per instance behind
``/cvrplib/en/download/bks/<id>``.  This module parses the site's instance
index once to map benchmark instance names to those URLs, then fills
``bench/bks_manifest.json`` with the source URL, download date and SHA-256
digest of every companion solution.  Existing files are only re-downloaded
with ``refresh=True``, so a normal call adopts local files and only fetches
the (small) index page.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from .metadata import file_sha256
from .suites import (
    DATA_ROOT,
    load_bks,
    load_manifest,
    save_manifest,
    suite_instances,
)

logger = logging.getLogger(__name__)

DEFAULT_INDEX_URL = "https://galgos.inf.puc-rio.br/cvrplib/en"

Opener = Callable[[str], bytes]
"""Fetches a URL and returns its raw body; injectable for tests."""

_INDEX_INSTANCE_PATTERN = re.compile(
    r'href="(?P<instance_url>[^"]*download/instance/\d+)"[^>]*>'
    r"\s*(?P<name>[^<]+?)\s*</a>"
)
_INDEX_SOLUTION_PATTERN = re.compile(r'href="(?P<solution_url>[^"]*download/bks/\d+)"')


@dataclass(eq=False, frozen=True, slots=True)
class IndexEntry:
    """One instance listed on the CVRPLIB index page.

    Attributes:
        name: Instance name, e.g. ``X-n101-k25``.
        instance_url: Absolute URL of the instance file.
        solution_url: Absolute URL of the best-known solution file.
    """

    name: str
    instance_url: str
    solution_url: str


def _default_opener(url: str) -> bytes:
    """Fetch a URL with a browser-like user agent."""
    request = Request(url, headers={"User-Agent": "teaspoon-bks-fetcher"})
    with urlopen(request, timeout=60) as response:
        return response.read()


def parse_index(
    html: str, *, base_url: str = DEFAULT_INDEX_URL
) -> dict[str, IndexEntry]:
    """Map instance names to download URLs from the CVRPLIB index page.

    The page lists each instance's file and solution links in order, so
    every instance link is paired with the first solution link that follows
    it and precedes the next instance link.  Links for other problem classes
    or resources are ignored.
    """
    instance_matches = list(_INDEX_INSTANCE_PATTERN.finditer(html))
    solution_matches = list(_INDEX_SOLUTION_PATTERN.finditer(html))
    entries: dict[str, IndexEntry] = {}
    solution_position = 0
    for index, instance_match in enumerate(instance_matches):
        next_instance_start = (
            instance_matches[index + 1].start()
            if index + 1 < len(instance_matches)
            else len(html)
        )
        while (
            solution_position < len(solution_matches)
            and solution_matches[solution_position].start() < instance_match.end()
        ):
            solution_position += 1
        if (
            solution_position >= len(solution_matches)
            or solution_matches[solution_position].start() >= next_instance_start
        ):
            continue
        name = instance_match.group("name").strip()
        entries[name] = IndexEntry(
            name=name,
            instance_url=urljoin(base_url, instance_match.group("instance_url")),
            solution_url=urljoin(
                base_url, solution_matches[solution_position].group("solution_url")
            ),
        )
        solution_position += 1
    return entries


def _instance_stem(instance_path: Path) -> str:
    """Return the instance name without directory or file suffix."""
    name = instance_path.name.removesuffix(".gz")
    return name.rpartition(".")[0] or name


def fetch_bks(
    suites: Sequence[str],
    *,
    data_root: Path = DATA_ROOT,
    index_url: str = DEFAULT_INDEX_URL,
    refresh: bool = False,
    manifest_path: Path | None = None,
    opener: Opener = _default_opener,
) -> dict[str, dict[str, object]]:
    """Adopt or download the companion solutions of the given suites.

    Existing solutions are recorded from disk unless ``refresh`` is set.
    A refresh that changes a file already in the manifest logs a warning,
    because the published best-known solution may have moved.

    Args:
        suites: Suite names understood by
            :func:`~bench.suites.suite_instances`, e.g. ``"x"``, ``"xl"``,
            ``"ags"``.
        data_root: Root that holds the suite directories.
        index_url: CVRPLIB instance index, or any local ``file://`` copy.
        refresh: Re-download solutions that already exist on disk.
        manifest_path: Manifest to update; defaults to
            :data:`~bench.suites.BKS_MANIFEST_PATH`.
        opener: URL fetcher; injectable for tests.

    Returns:
        The updated manifest entries, keyed by solution file name.

    Raises:
        ValueError: If a suite name or the downloaded data is invalid.
        OSError: If the index or a solution cannot be fetched.
    """
    index = parse_index(opener(index_url).decode("utf-8"), base_url=index_url)
    entries = load_manifest(manifest_path)
    today = date.today().isoformat()
    for suite in suites:
        for instance_path in suite_instances(suite, data_root=data_root):
            stem = _instance_stem(instance_path)
            index_entry = index.get(stem)
            if index_entry is None:
                logger.warning("no CVRPLIB index entry for %s", stem)
                continue
            companion = instance_path.parent / f"{stem}.sol"
            downloaded = False
            if refresh or not companion.is_file():
                companion.write_bytes(opener(index_entry.solution_url))
                downloaded = True
            previous = entries.get(companion.name)
            if (
                downloaded
                and previous is not None
                and previous.get("sha256") != file_sha256(companion)
            ):
                logger.warning("%s changed at the source", companion.name)
            entries[companion.name] = {
                "instance": stem,
                "source": index_entry.solution_url,
                "fetched": today if downloaded else (previous or {}).get("fetched"),
                "sha256": file_sha256(companion),
                "bks": load_bks(instance_path),
            }
    save_manifest(entries, manifest_path)
    logger.info("recorded %d BKS entries", len(entries))
    return entries
