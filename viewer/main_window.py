"""Qt application for browsing and drawing routing instances.

The window only knows the format-agnostic instance model from
:mod:`instances`; it never imports a concrete parser.  Adding a new
format to the registry therefore makes its files show up here automatically.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Callable
from pathlib import Path

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDockWidget,
    QFileDialog,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from instances import (
    DatasetEntry,
    Instance,
    build_catalog,
    load_entry,
    load_path,
    load_tours,
)

# Show matrices with rows on the vertical axis and columns on the horizontal.
pg.setConfigOption("imageAxisOrder", "row-major")

logger = logging.getLogger(__name__)

_NODE_COLORS = ("#4c72b0", "#dd8452", "#55a868", "#c44e52", "#8172b3", "#937860")
_ROUTE_COLORS = ("#d62728", "#1f77b4", "#2ca02c", "#9467bd", "#ff7f0e", "#8c564b")

_MAX_HOVER_NODES = 50_000

_INSTANCE_FILTER = (
    "Routing instances (*.tsp *.tsp.gz *.atsp *.atsp.gz *.vrp *.vrp.gz);;All files (*)"
)
_TOUR_FILTER = "Tour files (*.tour *.tour.gz *.opt.tour.gz);;All files (*)"


class MainWindow(QMainWindow):
    """Main window listing datasets and drawing the selected instance."""

    def __init__(self, data_root: Path) -> None:
        super().__init__()
        self._data_root = data_root
        self._instance: Instance | None = None
        self._nodes_visible = True
        self._routes_visible = True
        self._scatter_item: pg.ScatterPlotItem | None = None
        self._sequence_items: list[pg.PlotDataItem] = []
        self._build_actions()
        self._build_layout()
        self._populate_catalog()
        self.statusBar().showMessage(f"Catalog: {self._data_root}")

    @property
    def instance(self) -> Instance | None:
        """Currently displayed instance, if any."""
        return self._instance

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def load_entry(self, entry: DatasetEntry) -> None:
        """Load a catalog entry and show it, reporting errors in a dialog."""
        self._load_with_cursor(lambda: load_entry(entry), entry.path)

    def open_instance(self, path: Path) -> None:
        """Load an arbitrary file through the format registry."""
        self._load_with_cursor(lambda: load_path(path), path)

    def _load_with_cursor(self, loader: Callable[[], Instance], path: Path) -> None:
        """Run ``loader`` with a wait cursor, printing and showing failures."""
        error: Exception | None = None
        instance: Instance | None = None
        logger.info("loading %s", path)
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            instance = loader()
        except (OSError, ValueError) as caught:
            error = caught
        finally:
            QApplication.restoreOverrideCursor()
        if error is not None:
            logger.error("failed to load %s: %s", path, error)
            QMessageBox.critical(self, "Cannot load instance", str(error))
            return
        if instance is None:
            return
        self.set_instance(instance)
        self.statusBar().showMessage(f"Loaded {path}")
        logger.info(
            "loaded %s: kind=%s nodes=%s sequences=%d matrices=%d",
            instance.name,
            instance.kind,
            instance.nodes.num_nodes if instance.nodes else "none",
            len(instance.sequences),
            len(instance.matrices),
        )

    def set_instance(self, instance: Instance) -> None:
        """Display ``instance`` in the plan, matrix and stats views."""
        self._instance = instance
        self.setWindowTitle(f"teaspoon instance viewer — {instance.name}")
        self._render_plan()
        self._render_matrices()
        self._render_stats()

    # ------------------------------------------------------------------
    # User interface construction
    # ------------------------------------------------------------------

    def _build_actions(self) -> None:
        self._open_instance_action = QAction("Open instance…", self)
        self._open_instance_action.triggered.connect(self._choose_instance)

        self._open_tour_action = QAction("Open tour…", self)
        self._open_tour_action.triggered.connect(self._choose_tour)

        self._toggle_nodes_action = QAction("Show nodes", self)
        self._toggle_nodes_action.setCheckable(True)
        self._toggle_nodes_action.setChecked(True)
        self._toggle_nodes_action.toggled.connect(self._toggle_nodes)

        self._toggle_routes_action = QAction("Show sequences", self)
        self._toggle_routes_action.setCheckable(True)
        self._toggle_routes_action.setChecked(True)
        self._toggle_routes_action.toggled.connect(self._toggle_sequences)

        self._fit_action = QAction("Fit view", self)
        self._fit_action.triggered.connect(self._fit_view)

        self._export_action = QAction("Export PNG…", self)
        self._export_action.triggered.connect(self._export_png)

        toolbar = self.addToolBar("Main")
        toolbar.addAction(self._open_instance_action)
        toolbar.addAction(self._open_tour_action)
        toolbar.addSeparator()
        toolbar.addAction(self._toggle_nodes_action)
        toolbar.addAction(self._toggle_routes_action)
        toolbar.addAction(self._fit_action)
        toolbar.addSeparator()
        toolbar.addAction(self._export_action)

    def _build_layout(self) -> None:
        self._tree = QTreeWidget()
        self._tree.setHeaderLabels(["Dataset"])
        self._tree.itemDoubleClicked.connect(self._on_tree_item_activated)
        catalog_dock = QDockWidget("Datasets", self)
        catalog_dock.setWidget(self._tree)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, catalog_dock)

        self._stats = QPlainTextEdit()
        self._stats.setReadOnly(True)
        stats_dock = QDockWidget("Instance", self)
        stats_dock.setWidget(self._stats)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, stats_dock)

        self._plot = pg.PlotWidget()
        self._plot.setAspectLocked(True)
        self._plot.showGrid(x=True, y=True, alpha=0.15)
        scene = self._plot.scene()
        if isinstance(scene, pg.GraphicsScene):
            scene.sigMouseMoved.connect(self._on_mouse_moved)

        self._matrix_selector = QComboBox()
        self._matrix_selector.currentIndexChanged.connect(self._show_selected_matrix)
        self._matrix_view = pg.ImageView()
        self._matrix_view.setMinimumSize(320, 320)

        matrix_page = QWidget()
        matrix_layout = QVBoxLayout(matrix_page)
        matrix_layout.addWidget(self._matrix_selector)
        matrix_layout.addWidget(self._matrix_view)

        self._tabs = QTabWidget()
        self._tabs.addTab(self._plot, "Plan")
        self._tabs.addTab(matrix_page, "Matrices")
        self.setCentralWidget(self._tabs)

    def _populate_catalog(self) -> None:
        self._tree.clear()
        try:
            entries = build_catalog(self._data_root)
        except (OSError, ValueError) as error:
            logger.error("cannot scan catalog under %s: %s", self._data_root, error)
            QMessageBox.critical(self, "Cannot scan dataset", str(error))
            return
        groups: dict[str, QTreeWidgetItem] = {}
        for entry in entries:
            group_item = groups.get(entry.group)
            if group_item is None:
                group_item = QTreeWidgetItem([entry.group])
                self._tree.addTopLevelItem(group_item)
                groups[entry.group] = group_item
            child = QTreeWidgetItem([entry.label])
            child.setData(0, Qt.ItemDataRole.UserRole, entry)
            child.setToolTip(0, str(entry.path))
            group_item.addChild(child)
        self._tree.expandToDepth(0)
        logger.info(
            "catalog: %d entries in %d groups under %s",
            len(entries),
            len(groups),
            self._data_root,
        )

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def _render_plan(self) -> None:
        self._plot.clear()
        self._scatter_item = None
        self._sequence_items = []
        instance = self._instance
        if instance is None:
            return
        if instance.nodes is None:
            message = (
                "No coordinates available for this instance.\n"
                "See the Matrices tab for its edge weights."
            )
            logger.info("no coordinates to draw for %s", instance.name)
            self._plot.addItem(pg.TextItem(message, color="#d0d0d0", anchor=(0.5, 0.5)))
            return
        nodes = instance.nodes
        coordinates = nodes.coordinates
        if nodes.categories is not None:
            brushes = [
                pg.mkBrush(_NODE_COLORS[int(category) % len(_NODE_COLORS)])
                for category in nodes.categories
            ]
        else:
            brushes = pg.mkBrush(_NODE_COLORS[0])
        node_count = coordinates.shape[0]
        logger.info(
            "drawing %d nodes and %d sequences", node_count, len(instance.sequences)
        )
        self._scatter_item = pg.ScatterPlotItem(
            x=coordinates[:, 0],
            y=coordinates[:, 1],
            symbol="o",
            size=self._node_size(node_count),
            pen=None,
            brush=brushes,
            pxMode=True,
        )
        self._scatter_item.setVisible(self._nodes_visible)
        self._plot.addItem(self._scatter_item)
        self._draw_sequences(instance)
        self._plot.autoRange()

    def _draw_sequences(self, instance: Instance) -> None:
        if instance.nodes is None:
            return
        coordinates = instance.nodes.coordinates
        for position, sequence in enumerate(instance.sequences):
            indices = sequence.node_indices
            if indices.size == 0:
                continue
            x_values = coordinates[indices, 0]
            y_values = coordinates[indices, 1]
            if sequence.closed:
                x_values = np.append(x_values, x_values[0])
                y_values = np.append(y_values, y_values[0])
            color = _ROUTE_COLORS[position % len(_ROUTE_COLORS)]
            item = self._plot.plot(x_values, y_values, pen=pg.mkPen(color, width=1.5))
            item.setDownsampling(auto=True, method="peak")
            item.setVisible(self._routes_visible)
            self._sequence_items.append(item)

    def _render_matrices(self) -> None:
        self._matrix_selector.blockSignals(True)
        self._matrix_selector.clear()
        matrices = self._instance.matrices if self._instance else ()
        for matrix in matrices:
            self._matrix_selector.addItem(
                f"{matrix.label} — {matrix.shape[0]} x {matrix.shape[1]}"
            )
        self._matrix_selector.blockSignals(False)
        if matrices:
            self._matrix_selector.setCurrentIndex(0)
            self._show_selected_matrix()
        else:
            self._matrix_view.clear()

    def _show_selected_matrix(self) -> None:
        if self._instance is None:
            return
        index = self._matrix_selector.currentIndex()
        if index < 0 or index >= len(self._instance.matrices):
            return
        layer = self._instance.matrices[index]
        values = layer.values
        mask = np.isfinite(values)
        if values.ndim == 2 and values.shape[0] == values.shape[1]:
            # A square matrix often stores a sentinel on the diagonal (TSPLIB
            # uses 9999 for ATSP); leave it out when choosing the color range.
            mask = mask & ~np.eye(values.shape[0], dtype=bool)
        candidates = values[mask]
        if candidates.size == 0:
            candidates = values[np.isfinite(values)]
        if candidates.size == 0:
            self._matrix_view.setImage(values, autoLevels=True, autoRange=True)
            return
        low, high = (float(value) for value in np.percentile(candidates, (2.0, 98.0)))
        if high <= low:
            low, high = float(candidates.min()), float(candidates.max())
        self._matrix_view.setImage(
            values, autoLevels=False, levels=(low, high), autoRange=True
        )

    def _render_stats(self) -> None:
        instance = self._instance
        if instance is None:
            self._stats.setPlainText("No instance loaded.")
            return
        lines = [f"{key}: {value}" for key, value in instance.metadata.items()]
        if instance.nodes is not None:
            nodes = instance.nodes
            coordinates = nodes.coordinates
            lines.append("")
            lines.append(f"Nodes: {nodes.num_nodes}")
            lines.append(
                f"X range: {coordinates[:, 0].min():g} … {coordinates[:, 0].max():g}"
            )
            lines.append(
                f"Y range: {coordinates[:, 1].min():g} … {coordinates[:, 1].max():g}"
            )
            if nodes.categories is not None:
                for position, name in enumerate(nodes.category_names):
                    count = int((nodes.categories == position).sum())
                    lines.append(f"{name.capitalize()} nodes: {count}")
            for name, values in nodes.attributes.items():
                lines.append(
                    f"{name.capitalize()}: min {values.min():g}, "
                    f"max {values.max():g}, sum {values.sum():g}"
                )
        else:
            lines.append("")
            lines.append(
                "Nodes: none (the file stores only edge weights; see the Matrices tab)"
            )
        if instance.sequences:
            lines.append("")
            lines.append("Sequences:")
            for sequence in instance.sequences:
                length = instance.compute_length(sequence)
                length_text = f"{length:,.4g}" if length is not None else "unknown"
                state = "closed" if sequence.closed else "open"
                lines.append(
                    f"  {sequence.label}: {sequence.num_stops} stops, "
                    f"{state}, length {length_text}"
                )
        if instance.matrices:
            lines.append("")
            for matrix in instance.matrices:
                lines.append(
                    f"Matrix: {matrix.label}, "
                    f"shape {matrix.shape[0]} x {matrix.shape[1]}"
                )
        self._stats.setPlainText("\n".join(lines))

    # ------------------------------------------------------------------
    # Interaction
    # ------------------------------------------------------------------

    def _choose_instance(self) -> None:
        filename, _selected_filter = QFileDialog.getOpenFileName(
            self,
            "Open instance",
            str(self._data_root),
            _INSTANCE_FILTER,
        )
        if filename:
            self.open_instance(Path(filename))

    def _choose_tour(self) -> None:
        if self._instance is None:
            self.statusBar().showMessage("Load an instance before opening a tour")
            return
        filename, _selected_filter = QFileDialog.getOpenFileName(
            self,
            "Open tour",
            str(self._data_root),
            _TOUR_FILTER,
        )
        if not filename:
            return
        try:
            sequences = load_tours(self._instance, Path(filename))
        except (OSError, ValueError) as error:
            logger.error("failed to load tour %s: %s", filename, error)
            QMessageBox.critical(self, "Cannot load tour", str(error))
            return
        logger.info(
            "loaded %d sequence(s) from %s",
            len(sequences),
            filename,
        )
        self.set_instance(self._instance.with_sequences(*sequences))

    def _fit_view(self) -> None:
        self._plot.autoRange()

    def _export_png(self) -> None:
        filename, _selected_filter = QFileDialog.getSaveFileName(
            self,
            "Export figure",
            str(Path.cwd() / "instance.png"),
            "PNG image (*.png)",
        )
        if not filename:
            return
        if not self._plot.grab().save(filename):
            logger.error("could not write %s", filename)
            QMessageBox.warning(self, "Export failed", f"Could not write {filename}")
            return
        logger.info("saved figure to %s", filename)
        self.statusBar().showMessage(f"Saved {filename}")

    def _on_tree_item_activated(self, item: QTreeWidgetItem, _column: int) -> None:
        entry = item.data(0, Qt.ItemDataRole.UserRole)
        if isinstance(entry, DatasetEntry):
            self.load_entry(entry)

    def _on_mouse_moved(self, position: QPointF) -> None:
        instance = self._instance
        if instance is None or instance.nodes is None:
            return
        plot_item = self._plot.plotItem
        if plot_item is None or plot_item.vb is None:
            return
        point = plot_item.vb.mapSceneToView(position)
        message = f"x={point.x():.6g}  y={point.y():.6g}"
        coordinates = instance.nodes.coordinates
        if coordinates.shape[0] <= _MAX_HOVER_NODES:
            offsets = coordinates - np.array([point.x(), point.y()])
            nearest = int(np.argmin((offsets**2).sum(axis=1)))
            label = (
                instance.nodes.labels[nearest]
                if instance.nodes.labels
                else str(nearest)
            )
            message += (
                f"   nearest node: {label} "
                f"({coordinates[nearest, 0]:g}, {coordinates[nearest, 1]:g})"
            )
        self.statusBar().showMessage(message)

    def _toggle_nodes(self, visible: bool) -> None:
        self._nodes_visible = visible
        if self._scatter_item is not None:
            self._scatter_item.setVisible(visible)

    def _toggle_sequences(self, visible: bool) -> None:
        self._routes_visible = visible
        for item in self._sequence_items:
            item.setVisible(visible)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _node_size(count: int) -> int:
        if count <= 2_000:
            return 7
        if count <= 20_000:
            return 5
        if count <= 250_000:
            return 3
        return 2


def main() -> int:
    """Run the viewer, optionally loading one instance immediately."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser(
        description="Browse routing instances from a dataset directory."
    )
    parser.add_argument(
        "data_root",
        nargs="?",
        default=Path("data"),
        type=Path,
        help="directory scanned for loadable instances (default: data)",
    )
    parser.add_argument(
        "instance",
        nargs="?",
        default=None,
        type=Path,
        help="instance file to open on startup",
    )
    arguments = parser.parse_args()

    application = QApplication(sys.argv[:1])
    window = MainWindow(arguments.data_root)
    if arguments.instance is not None:
        window.open_instance(arguments.instance)
    window.resize(1400, 900)
    window.show()
    return application.exec()
