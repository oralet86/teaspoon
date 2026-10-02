"""Format-agnostic routing instance model, registry, and bundled formats."""

from .model import Instance, MatrixLayer, NodeSet, Sequence
from .registry import (
    DatasetEntry,
    DatasetFormat,
    build_catalog,
    format_for,
    load_entry,
    load_path,
    load_tours,
    register_format,
    registered_formats,
)
from .tsplib import TSPLIB_FORMAT, TsplibError, load_instance

__all__ = [
    "TSPLIB_FORMAT",
    "DatasetEntry",
    "DatasetFormat",
    "Instance",
    "MatrixLayer",
    "NodeSet",
    "Sequence",
    "TsplibError",
    "build_catalog",
    "format_for",
    "load_entry",
    "load_instance",
    "load_path",
    "load_tours",
    "register_format",
    "registered_formats",
]
