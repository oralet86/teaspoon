"""Format registry and dataset catalog.

A :class:`DatasetFormat` knows how to discover files in a directory and how
to turn one of them into an :class:`~instances.model.Instance`.
Registering a new format (TSPLIB today, Amazon Last Mile JSON later) makes
its files appear in the viewer without any viewer changes.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from .model import Instance, Sequence


@dataclass(eq=False, frozen=True, slots=True)
class DatasetEntry:
    """One loadable item in the dataset catalog.

    Attributes:
        label: Display name shown in the viewer's file tree.
        path: File the entry was discovered in.
        group: Catalog group, usually the directory relative to the root.
        format_key: Key of the :class:`DatasetFormat` that discovered it.
        companions: Supporting files keyed by role, e.g. ``{"tour": ...}``.
        annotations: Format-specific string metadata, e.g. a route id inside
            a shared JSON file.  The TSPLIB format leaves this empty.
    """

    label: str
    path: Path
    group: str
    format_key: str
    companions: dict[str, Path] = field(default_factory=dict)
    annotations: dict[str, str] = field(default_factory=dict)


class DatasetFormat(Protocol):
    """Interface every instance format must implement.

    Attributes:
        key: Unique registry key, stored on loaded instances.
        display_name: Human-readable format name.
    """

    key: str
    display_name: str

    def scan_directory(self, directory: Path, group: str) -> Iterable[DatasetEntry]:
        """Yield the entries this format discovers directly in ``directory``."""
        ...

    def matches(self, path: Path) -> bool:
        """Return whether this format can load ``path``."""
        ...

    def load(self, entry: DatasetEntry) -> Instance:
        """Load ``entry`` into a format-agnostic instance."""
        ...

    def load_tours(self, instance: Instance, path: Path) -> tuple[Sequence, ...]:
        """Load visit sequences for ``instance`` from ``path``."""
        ...


_FORMATS: dict[str, DatasetFormat] = {}


def register_format(dataset_format: DatasetFormat) -> None:
    """Make a format available to the catalog and the viewer."""
    _FORMATS[dataset_format.key] = dataset_format


def registered_formats() -> tuple[DatasetFormat, ...]:
    """Return all registered formats in registration order."""
    return tuple(_FORMATS.values())


def format_for(path: Path) -> DatasetFormat:
    """Return the first registered format that matches ``path``.

    Raises:
        ValueError: If no registered format matches the path.
    """
    for dataset_format in _FORMATS.values():
        if dataset_format.matches(path):
            return dataset_format
    raise ValueError(f"no registered instance format matches {path}")


def build_catalog(data_root: Path) -> list[DatasetEntry]:
    """Discover every loadable entry under ``data_root``.

    Each directory is offered to every registered format, so groups mirror
    the directory layout on disk.
    """
    directories = [data_root]
    directories.extend(sorted(path for path in data_root.rglob("*") if path.is_dir()))
    entries: list[DatasetEntry] = []
    for directory in directories:
        relative = directory.relative_to(data_root)
        if any(part.startswith(".") for part in relative.parts):
            continue
        group = str(relative) if relative.parts else data_root.name
        for dataset_format in _FORMATS.values():
            entries.extend(dataset_format.scan_directory(directory, group))
    entries.sort(key=lambda entry: (entry.group, entry.label.lower()))
    return entries


def load_entry(entry: DatasetEntry) -> Instance:
    """Load a catalog entry through the format that discovered it."""
    dataset_format = _FORMATS[entry.format_key]
    return dataset_format.load(entry)


def load_path(path: Path, tour_path: Path | None = None) -> Instance:
    """Load an arbitrary file, optionally attaching a tour file.

    Raises:
        ValueError: If no registered format matches the path.
    """
    dataset_format = format_for(path)
    companions = {"tour": tour_path} if tour_path is not None else {}
    entry = DatasetEntry(
        label=path.name,
        path=path,
        group=path.parent.name,
        format_key=dataset_format.key,
        companions=companions,
    )
    return dataset_format.load(entry)


def load_tours(instance: Instance, path: Path) -> tuple[Sequence, ...]:
    """Load visit sequences for ``instance`` through its own format."""
    dataset_format = _FORMATS[instance.format_key]
    return dataset_format.load_tours(instance, path)
