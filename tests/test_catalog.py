"""Catalog discovery and loading tests."""

from pathlib import Path

from instances import build_catalog, load_entry

DATA_ROOT = Path(__file__).resolve().parents[1] / "data"


def test_catalog_finds_bundled_formats() -> None:
    entries = build_catalog(DATA_ROOT)
    by_label = {entry.label: entry for entry in entries}
    assert "a280.tsp" in by_label
    assert "A-n32-k5.vrp" in by_label
    assert "eil22.vrp" in by_label
    assert "world.tsp" in by_label
    a280 = by_label["a280.tsp"]
    assert a280.group == "ALL_tsp"
    assert a280.companions["tour"].name == "a280.opt.tour"
    assert by_label["A-n32-k5.vrp"].group == "A"


def test_catalog_entry_loads_with_tour() -> None:
    entries = build_catalog(DATA_ROOT)
    entry = next(item for item in entries if item.label == "a280.tsp")
    instance = load_entry(entry)
    assert instance.dimension == 280
    assert len(instance.sequences) == 1
    assert instance.sequences[0].length is not None


def test_whole_catalog_loads() -> None:
    failures = []
    for entry in build_catalog(DATA_ROOT):
        try:
            load_entry(entry)
        except (OSError, ValueError) as error:
            failures.append(f"{entry.label}: {error}")
    assert not failures, "failed to load:\n" + "\n".join(failures)
