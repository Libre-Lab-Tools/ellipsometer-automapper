"""
ABSTRACT
--------
Interactive Results workspace for Ellipsometer AutoMapper.

The left side contains Mapping Data and statistics. The right side contains one
interactive scientific map at a time.

V1.1 supports:
- importing CompleteEASE TXT folders or CSV tables,
- one parameter selector,
- three plot types:
    1) Interpolated Map,
    2) Measured Points + Values,
    3) Pixel / Cell Map,
- actual measured XY locations overlaid on the map,
- mouse-position X/Y/parameter readout below the plot,
- click-to-select measurement points,
- bidirectional point <-> table-row selection,
- global Ignore / Enable for measurements,
- statistics for all parameters at the same time,
- stable plot/colorbar width proportions during resizing,
- Save Plot without the default Matplotlib toolbar,
- click the plot title to edit it,
- click an axis tick label to change X/Y tick spacing.

The Results workspace contains no stage or CompleteEASE acquisition control.
"""

from __future__ import annotations

import re

import numpy as np
import pandas as pd
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from matplotlib.ticker import MultipleLocator
from mpl_toolkits.axes_grid1.inset_locator import inset_axes

from data_parser import load_csv_table, parse_txt_folder
from plotting import (
    MapData,
    available_parameters,
    build_map,
    interpolated_value,
    statistics_for,
)


class AxisTicksDialog(QDialog):
    """Simple tick-spacing editor opened by clicking an axis tick label."""

    OPTIONS = [
        ("Auto", None),
        ("1 mm", 1.0),
        ("2 mm", 2.0),
        ("3 mm", 3.0),
        ("5 mm", 5.0),
    ]

    def __init__(self, x_spacing, y_spacing, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Axis Tick Spacing")
        self.setModal(True)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.x_combo = QComboBox()
        self.y_combo = QComboBox()
        for label, value in self.OPTIONS:
            self.x_combo.addItem(label, value)
            self.y_combo.addItem(label, value)

        self._select_value(self.x_combo, x_spacing)
        self._select_value(self.y_combo, y_spacing)

        form.addRow("X-axis ticks:", self.x_combo)
        form.addRow("Y-axis ticks:", self.y_combo)
        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @staticmethod
    def _select_value(combo, value) -> None:
        for index in range(combo.count()):
            if combo.itemData(index) == value:
                combo.setCurrentIndex(index)
                break

    @property
    def x_spacing(self):
        return self.x_combo.currentData()

    @property
    def y_spacing(self):
        return self.y_combo.currentData()


class ResultsPage(QWidget):
    PLOT_TYPES = [
        "Interpolated Map",
        "Measured Points + Values",
        "Pixel / Cell Map",
    ]

    def __init__(self, parent=None) -> None:
        super().__init__(parent)

        self.data = pd.DataFrame()
        self.map_data: MapData | None = None
        self.selected_row: int | None = None

        self.ax = None
        self.cax = None
        self._title_artist = None
        self._custom_title: str | None = None

        # Colorbar label can be edited independently from the parameter name.
        self.colorbar = None
        self._colorbar_label_artist = None
        self._custom_colorbar_label: str | None = None

        # The selected-point ring is a temporary GUI artist. It is hidden
        # automatically when a plot is exported.
        self._selected_artist = None

        self._x_tick_spacing = None
        self._y_tick_spacing = None

        self._build_ui()

    # ------------------------------------------------------------------
    # Public handoff from Measurement
    # ------------------------------------------------------------------
    def set_dataframe(self, dataframe: pd.DataFrame) -> None:
        if dataframe is None or dataframe.empty:
            return

        self.data = dataframe.copy().reset_index(drop=True)
        if "Included" not in self.data.columns:
            self.data["Included"] = True

        self.selected_row = None
        self._refresh_table()
        self._refresh_parameter_list()
        self._refresh_statistics()
        self._redraw()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(splitter, 1)

        # ----------------------------- LEFT -----------------------------
        left = QWidget()
        left_layout = QVBoxLayout(left)

        data_group = QGroupBox("Mapping Data")
        data_layout = QVBoxLayout(data_group)

        import_row = QHBoxLayout()
        self.import_txt = QPushButton("Import TXT Folder")
        self.import_csv = QPushButton("Import Table")
        self.import_txt.clicked.connect(self._import_txt_folder)
        self.import_csv.clicked.connect(self._import_csv)
        import_row.addWidget(self.import_txt)
        import_row.addWidget(self.import_csv)
        data_layout.addLayout(import_row)

        self.table = QTableWidget()
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)

        # Use the same high-contrast selection used in Measurement. Ignored
        # rows remain red when not selected, but a selected row is solid blue.
        self.table.setStyleSheet("""
            QTableWidget::item:selected {
                background-color: #1976D2;
                color: white;
            }
            QTableWidget::item:selected:!active {
                background-color: #1976D2;
                color: white;
            }
        """)

        # cellClicked is used rather than only selectionChanged so clicking
        # the already-selected row can deselect it.
        self.table.cellClicked.connect(self._table_row_clicked)
        data_layout.addWidget(self.table)

        left_layout.addWidget(data_group, 3)

        stats_group = QGroupBox("Parameter Statistics")
        stats_layout = QVBoxLayout(stats_group)
        self.stats_table = QTableWidget()
        self.stats_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        stats_layout.addWidget(self.stats_table)
        left_layout.addWidget(stats_group, 2)

        splitter.addWidget(left)

        # ----------------------------- RIGHT ----------------------------
        right = QWidget()
        right_layout = QVBoxLayout(right)

        controls = QHBoxLayout()

        controls.addWidget(QLabel("Parameter:"))
        self.parameter_combo = QComboBox()
        self.parameter_combo.setMinimumWidth(280)
        self.parameter_combo.currentTextChanged.connect(self._parameter_changed)
        controls.addWidget(self.parameter_combo)

        controls.addWidget(QLabel("Plot type:"))
        self.plot_type_combo = QComboBox()
        self.plot_type_combo.addItems(self.PLOT_TYPES)
        self.plot_type_combo.currentTextChanged.connect(self._redraw)
        controls.addWidget(self.plot_type_combo)

        controls.addStretch()

        self.save_plot = QPushButton("Save Plot")
        self.save_plot.clicked.connect(self._save_plot)
        controls.addWidget(self.save_plot)

        right_layout.addLayout(controls)

        self.figure = Figure(figsize=(7.2, 6.0))
        self.canvas = FigureCanvas(self.figure)
        self.canvas.setMinimumSize(430, 360)
        right_layout.addWidget(self.canvas, 1)

        # Small cursor readout below the graph: no tooltip obscures the map.
        self.cursor_readout = QLabel("X: —    Y: —    Value: —")
        self.cursor_readout.setStyleSheet("color: #555555;")
        right_layout.addWidget(self.cursor_readout)

        selected_row = QHBoxLayout()

        self.selected_info = QLabel("No measured point selected.")
        self.selected_info.setWordWrap(True)
        selected_row.addWidget(self.selected_info, 1)

        self.toggle_include = QPushButton("Ignore Point")
        self.toggle_include.setMaximumWidth(125)
        self.toggle_include.setEnabled(False)
        self.toggle_include.clicked.connect(self._toggle_selected_inclusion)
        selected_row.addWidget(self.toggle_include)

        right_layout.addLayout(selected_row)

        hint = QLabel(
            "Tip: click a measured point to select it. Click the plot title or "
            "colorbar label to edit it, or click an axis tick label to change "
            "tick spacing."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #777777;")
        right_layout.addWidget(hint)

        self.canvas.mpl_connect("motion_notify_event", self._mouse_moved)
        self.canvas.mpl_connect("button_press_event", self._plot_clicked)

        splitter.addWidget(right)
        splitter.setSizes([470, 730])

    # ------------------------------------------------------------------
    # Imports
    # ------------------------------------------------------------------
    def _import_txt_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "Import CompleteEASE TXT Folder"
        )
        if not folder:
            return

        try:
            self.set_dataframe(parse_txt_folder(folder))
        except Exception as exc:
            QMessageBox.warning(self, "Import TXT Folder", str(exc))

    def _import_csv(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(
            self,
            "Import Mapping Table",
            "",
            "CSV files (*.csv);;All files (*.*)",
        )
        if not filename:
            return

        try:
            self.set_dataframe(load_csv_table(filename))
        except Exception as exc:
            QMessageBox.warning(self, "Import Table", str(exc))

    # ------------------------------------------------------------------
    # Mapping Data table
    # ------------------------------------------------------------------
    def _refresh_table(self) -> None:
        self.table.clear()

        if self.data.empty:
            self.table.setRowCount(0)
            self.table.setColumnCount(0)
            return

        columns = list(self.data.columns)
        display_names = {"X": "X (mm)", "Y": "Y (mm)"}

        self.table.setColumnCount(len(columns))
        self.table.setHorizontalHeaderLabels(
            [display_names.get(column, column) for column in columns]
        )
        self.table.setRowCount(len(self.data))

        for row_i, (_, row) in enumerate(self.data.iterrows()):
            included = bool(row["Included"])

            for col_i, column in enumerate(columns):
                value = row[column]

                if pd.isna(value):
                    text = ""
                elif isinstance(value, (float, np.floating)):
                    text = f"{float(value):.6g}"
                else:
                    text = str(value)

                item = QTableWidgetItem(text)

                if not included:
                    item.setBackground(QColor("#f5dada"))

                self.table.setItem(row_i, col_i, item)

        self.table.resizeColumnsToContents()

    def _table_row_clicked(self, row_index: int, column: int) -> None:
        del column
        self._select_row(row_index)

    # ------------------------------------------------------------------
    # Parameters/statistics
    # ------------------------------------------------------------------
    def _refresh_parameter_list(self) -> None:
        parameters = available_parameters(self.data)
        previous = self.parameter_combo.currentText()

        self.parameter_combo.blockSignals(True)
        self.parameter_combo.clear()
        self.parameter_combo.addItems(parameters)

        if previous in parameters:
            self.parameter_combo.setCurrentText(previous)

        self.parameter_combo.blockSignals(False)

    def _parameter_changed(self) -> None:
        self._custom_title = None
        self._custom_colorbar_label = None
        self._redraw()

    def _refresh_statistics(self) -> None:
        """
        Show every mapped numeric parameter at the same time.

        Columns = parameter names (with units preserved from CompleteEASE)
        Rows    = statistic names
        """
        parameters = available_parameters(self.data)
        statistic_names = ["Mean", "Std. Dev.", "Minimum", "Maximum", "Range (%)"]

        self.stats_table.clear()
        self.stats_table.setRowCount(len(statistic_names))
        self.stats_table.setColumnCount(len(parameters))
        self.stats_table.setVerticalHeaderLabels(statistic_names)
        self.stats_table.setHorizontalHeaderLabels(parameters)

        for column, parameter in enumerate(parameters):
            stats = statistics_for(self.data, parameter)
            for row, statistic in enumerate(statistic_names):
                value = stats.get(statistic, np.nan)
                text = "—" if not np.isfinite(value) else f"{value:.6g}"
                self.stats_table.setItem(row, column, QTableWidgetItem(text))

        self.stats_table.resizeColumnsToContents()
        self.stats_table.resizeRowsToContents()

    # ------------------------------------------------------------------
    # Plot construction
    # ------------------------------------------------------------------
    def _redraw(self) -> None:
        self.figure.clear()
        self.ax = None
        self.cax = None
        self.map_data = None
        self._title_artist = None
        self.colorbar = None
        self._colorbar_label_artist = None
        self._selected_artist = None

        parameter = self.parameter_combo.currentText()

        if self.data.empty or not parameter:
            self.canvas.draw_idle()
            return

        try:
            self.map_data = build_map(self.data, parameter)
        except Exception as exc:
            ax = self.figure.add_subplot(111)
            ax.text(
                0.5,
                0.5,
                str(exc),
                ha="center",
                va="center",
                transform=ax.transAxes,
            )
            self.canvas.draw_idle()
            return

        # The colorbar is attached to the map itself rather than to a separate
        # figure column. Its width/height therefore stay proportional to the
        # scientific plot as the Results splitter is resized.
        self.figure.subplots_adjust(
            left=0.11, right=0.84, bottom=0.12, top=0.90
        )
        self.ax = self.figure.add_subplot(111)
        self.cax = inset_axes(
            self.ax,
            width="4.5%",
            height="100%",
            loc="lower left",
            bbox_to_anchor=(1.04, 0.0, 1.0, 1.0),
            bbox_transform=self.ax.transAxes,
            borderpad=0,
        )

        plot_type = self.plot_type_combo.currentText()

        try:
            if plot_type == "Interpolated Map":
                self._draw_interpolated(parameter)
            elif plot_type == "Measured Points + Values":
                self._draw_points_with_values(parameter)
            elif plot_type == "Pixel / Cell Map":
                self._draw_pixel_map(parameter)
        except Exception as exc:
            self.ax.clear()
            self.cax.clear()
            self.cax.set_visible(False)
            self.ax.text(
                0.5,
                0.5,
                str(exc),
                ha="center",
                va="center",
                transform=self.ax.transAxes,
            )

        # Highlight the selected real measurement independently of the plot type.
        self._draw_selected_marker()

        self.ax.set_xlabel("X (mm)")
        self.ax.set_ylabel("Y (mm)")
        self.ax.set_aspect("equal", adjustable="box")

        title = self._custom_title or self._default_title(parameter)
        self._title_artist = self.ax.set_title(title)

        self._apply_tick_spacing()

        self.canvas.draw()
        self._refresh_selected_info()

    def _default_title(self, parameter: str) -> str:
        # Keep the plot simple when there is only one thickness quantity.
        parameters = available_parameters(self.data)
        thickness_parameters = [
            p
            for p in parameters
            if "thickness" in p.lower() and "non-uniformity" not in p.lower()
        ]
        if parameter in thickness_parameters and len(thickness_parameters) == 1:
            return "Thickness (nm) vs position"
        return f"{parameter} vs position"

    def _draw_interpolated(self, parameter: str) -> None:
        if not np.isfinite(self.map_data.z_grid).any():
            raise ValueError("Not enough two-dimensional data for interpolation.")

        xx, yy = np.meshgrid(self.map_data.x_axis, self.map_data.y_axis)
        contour = self.ax.contourf(
            xx,
            yy,
            self.map_data.z_grid,
            levels=20,
            cmap="viridis",
        )
        self._create_colorbar(contour, parameter)
        self._draw_measurement_markers()

        # For interpolated maps, the mapped area ends exactly at the outer
        # measurement coordinates, matching the user's preferred map style.
        self._set_data_limits(exact=True)

    def _draw_points_with_values(self, parameter: str) -> None:
        inc = self.map_data.included
        exc = self.map_data.excluded

        values = pd.to_numeric(inc[parameter], errors="coerce")
        scatter = self.ax.scatter(
            inc["X"],
            inc["Y"],
            c=values,
            cmap="viridis",
            s=85,
            edgecolors="black",
            linewidths=1.0,
            zorder=5,
        )
        self._create_colorbar(scatter, parameter)

        # Value labels are offset slightly above each measured point.
        y_values = sorted(set(float(v) for v in self.data["Y"]))
        y_spacing = min(np.diff(y_values)) if len(y_values) > 1 else 1.0
        offset = 0.12 * y_spacing

        for _, row in inc.iterrows():
            value = row[parameter]
            self.ax.text(
                float(row["X"]),
                float(row["Y"]) + offset,
                f"{float(value):.1f}",
                ha="center",
                va="bottom",
                fontsize=8,
                zorder=6,
            )

        if not exc.empty:
            self.ax.scatter(
                exc["X"],
                exc["Y"],
                marker="x",
                s=65,
                c="red",
                linewidths=2,
                zorder=7,
            )

        self._set_data_limits()

    def _draw_pixel_map(self, parameter: str) -> None:
        """
        Draw a no-interpolation cell map.

        Each coordinate owns one rectangular cell. This is available only for
        regular square/rectangular grids.
        """
        all_x = np.array(sorted(set(pd.to_numeric(self.data["X"]))), dtype=float)
        all_y = np.array(sorted(set(pd.to_numeric(self.data["Y"]))), dtype=float)

        if len(all_x) < 2 or len(all_y) < 2:
            raise ValueError("Pixel / Cell Map requires at least a 2 × 2 grid.")

        dx = np.diff(all_x)
        dy = np.diff(all_y)

        if not (np.allclose(dx, dx[0]) and np.allclose(dy, dy[0])):
            raise ValueError(
                "Pixel / Cell Map is available only for regularly spaced grids."
            )

        x_edges = np.r_[
            all_x[0] - dx[0] / 2,
            (all_x[:-1] + all_x[1:]) / 2,
            all_x[-1] + dx[-1] / 2,
        ]
        y_edges = np.r_[
            all_y[0] - dy[0] / 2,
            (all_y[:-1] + all_y[1:]) / 2,
            all_y[-1] + dy[-1] / 2,
        ]

        z = np.full((len(all_y), len(all_x)), np.nan, dtype=float)

        included = self.data[self.data["Included"].astype(bool)].copy()

        for _, row in included.iterrows():
            value = pd.to_numeric(
                pd.Series([row.get(parameter)]), errors="coerce"
            ).iloc[0]
            if pd.isna(value):
                continue

            ix = int(np.where(all_x == float(row["X"]))[0][0])
            iy = int(np.where(all_y == float(row["Y"]))[0][0])
            z[iy, ix] = float(value)

        mesh = self.ax.pcolormesh(
            x_edges,
            y_edges,
            np.ma.masked_invalid(z),
            cmap="viridis",
            shading="flat",
        )
        self._create_colorbar(mesh, parameter)
        self._draw_measurement_markers()

        self.ax.set_xlim(x_edges[0], x_edges[-1])
        self.ax.set_ylim(y_edges[0], y_edges[-1])

    def _draw_measurement_markers(self) -> None:
        inc = self.map_data.included
        exc = self.map_data.excluded

        if not inc.empty:
            self.ax.scatter(
                inc["X"],
                inc["Y"],
                marker="o",
                s=34,
                c="black",
                zorder=8,
            )

        if not exc.empty:
            self.ax.scatter(
                exc["X"],
                exc["Y"],
                marker="x",
                s=65,
                c="red",
                linewidths=2,
                zorder=9,
            )

        self._set_data_limits()

    def _draw_selected_marker(self) -> None:
        """Draw a visible ring around the currently selected measurement."""
        if self.ax is None or self.selected_row is None or self.data.empty:
            return
        if self.selected_row >= len(self.data):
            return

        row = self.data.iloc[self.selected_row]
        self._selected_artist = self.ax.scatter(
            [float(row["X"])],
            [float(row["Y"])],
            s=150,
            facecolors="none",
            edgecolors="#1f5fa8",
            linewidths=2.2,
            zorder=12,
        )

    def _create_colorbar(self, mappable, parameter: str) -> None:
        """Create the plot colorbar and keep its label editable."""
        label = self._custom_colorbar_label or parameter
        self.colorbar = self.figure.colorbar(
            mappable,
            cax=self.cax,
            label=label,
        )
        self._colorbar_label_artist = self.colorbar.ax.yaxis.label

    def _set_data_limits(self, exact: bool = False) -> None:
        if self.data.empty:
            return

        xs = pd.to_numeric(self.data["X"], errors="coerce").dropna().to_numpy()
        ys = pd.to_numeric(self.data["Y"], errors="coerce").dropna().to_numpy()

        if len(xs) == 0 or len(ys) == 0:
            return

        if exact:
            self.ax.set_xlim(xs.min(), xs.max())
            self.ax.set_ylim(ys.min(), ys.max())
            return

        x_pad = max((xs.max() - xs.min()) * 0.03, 0.15)
        y_pad = max((ys.max() - ys.min()) * 0.03, 0.15)

        self.ax.set_xlim(xs.min() - x_pad, xs.max() + x_pad)
        self.ax.set_ylim(ys.min() - y_pad, ys.max() + y_pad)

    def _apply_tick_spacing(self) -> None:
        if self.ax is None:
            return

        if self._x_tick_spacing is not None:
            self.ax.xaxis.set_major_locator(MultipleLocator(self._x_tick_spacing))

        if self._y_tick_spacing is not None:
            self.ax.yaxis.set_major_locator(MultipleLocator(self._y_tick_spacing))

    # ------------------------------------------------------------------
    # Mouse interaction
    # ------------------------------------------------------------------
    def _mouse_moved(self, event) -> None:
        if (
            self.map_data is None
            or self.ax is None
            or event.inaxes is not self.ax
            or event.xdata is None
            or event.ydata is None
        ):
            self.cursor_readout.setText("X: —    Y: —    Value: —")
            return

        value = interpolated_value(self.map_data, event.xdata, event.ydata)
        parameter = self.parameter_combo.currentText()

        value_text = "—" if value is None else f"{value:.6g}"
        self.cursor_readout.setText(
            f"X: {event.xdata:.3f} mm    "
            f"Y: {event.ydata:.3f} mm    "
            f"{parameter}: {value_text}"
        )

    def _plot_clicked(self, event) -> None:
        # Title click -> edit title.
        if self._title_artist is not None:
            try:
                contains, _ = self._title_artist.contains(event)
            except Exception:
                contains = False
            if contains:
                self._edit_title()
                return

        # Colorbar-label click -> edit colorbar title.
        if self._colorbar_label_artist is not None:
            try:
                contains, _ = self._colorbar_label_artist.contains(event)
            except Exception:
                contains = False
            if contains:
                self._edit_colorbar_label()
                return

        # Tick-label click -> edit tick spacing.
        if self.ax is not None:
            tick_labels = list(self.ax.get_xticklabels()) + list(self.ax.get_yticklabels())
            for label in tick_labels:
                try:
                    contains, _ = label.contains(event)
                except Exception:
                    contains = False
                if contains:
                    self._edit_tick_spacing()
                    return

        # Measured-point click -> select row.
        if self.ax is None or event.inaxes is not self.ax:
            return

        best = None
        parameter = self.parameter_combo.currentText()

        for row_index, row in self.data.iterrows():
            value = pd.to_numeric(
                pd.Series([row.get(parameter)]), errors="coerce"
            ).iloc[0]
            if pd.isna(value):
                continue

            px, py = self.ax.transData.transform(
                (float(row["X"]), float(row["Y"]))
            )
            distance = ((event.x - px) ** 2 + (event.y - py) ** 2) ** 0.5

            if distance <= 12 and (best is None or distance < best[1]):
                best = (row_index, distance)

        if best is not None:
            self._select_row(best[0])

    def _select_row(self, row_index: int) -> None:
        # Clicking the same plot point or table row again deselects it.
        if self.selected_row == row_index:
            self._clear_selection()
            return

        self.selected_row = row_index

        self.table.blockSignals(True)
        self.table.selectRow(row_index)
        first_item = self.table.item(row_index, 0)
        if first_item:
            self.table.scrollToItem(first_item)
        self.table.blockSignals(False)

        self._refresh_selected_info()
        self._redraw()

    def _clear_selection(self) -> None:
        """Clear only the temporary GUI selection; do not alter inclusion."""
        self.selected_row = None

        self.table.blockSignals(True)
        self.table.clearSelection()
        self.table.blockSignals(False)

        self._refresh_selected_info()
        self._redraw()

    # ------------------------------------------------------------------
    # Selected point / inclusion
    # ------------------------------------------------------------------
    def _refresh_selected_info(self) -> None:
        if self.selected_row is None or self.data.empty:
            self.selected_info.setText("No measured point selected.")
            self.toggle_include.setEnabled(False)
            return

        row = self.data.iloc[self.selected_row]
        parameter = self.parameter_combo.currentText()
        parameter_value = row.get(parameter, np.nan)
        mse = row.get("MSE", np.nan)
        included = bool(row["Included"])

        value_text = (
            "—"
            if pd.isna(parameter_value)
            else f"{float(parameter_value):.6g}"
        )
        mse_text = "—" if pd.isna(mse) else f"{float(mse):.6g}"

        self.selected_info.setText(
            f"<b>Selected measurement:</b> "
            f"X = {int(row['X'])} mm, Y = {int(row['Y'])} mm &nbsp;&nbsp; "
            f"{parameter} = {value_text} &nbsp;&nbsp; MSE = {mse_text}"
        )

        self.toggle_include.setText(
            "Ignore Point" if included else "Enable Point"
        )
        self.toggle_include.setEnabled(True)

    def _toggle_selected_inclusion(self) -> None:
        if self.selected_row is None:
            return

        current = bool(self.data.at[self.selected_row, "Included"])
        self.data.at[self.selected_row, "Included"] = not current

        self._refresh_table()
        self.table.selectRow(self.selected_row)
        self._refresh_statistics()
        self._redraw()

    # ------------------------------------------------------------------
    # Plot formatting / saving
    # ------------------------------------------------------------------
    def _edit_title(self) -> None:
        current = self._custom_title or self._default_title(
            self.parameter_combo.currentText()
        )
        new_title, ok = QInputDialog.getText(
            self, "Plot Title", "Title:", text=current
        )
        if ok and new_title.strip():
            self._custom_title = new_title.strip()
            self._redraw()

    def _edit_colorbar_label(self) -> None:
        parameter = self.parameter_combo.currentText()
        current = self._custom_colorbar_label or parameter

        new_label, ok = QInputDialog.getText(
            self,
            "Colorbar Label",
            "Colorbar label:",
            text=current,
        )
        if ok and new_label.strip():
            self._custom_colorbar_label = new_label.strip()
            self._redraw()

    def _edit_tick_spacing(self) -> None:
        dialog = AxisTicksDialog(
            self._x_tick_spacing,
            self._y_tick_spacing,
            self,
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._x_tick_spacing = dialog.x_spacing
            self._y_tick_spacing = dialog.y_spacing
            self._redraw()

    def _save_plot(self) -> None:
        if self.data.empty or not self.parameter_combo.currentText():
            QMessageBox.information(self, "Save Plot", "No plot is available to save.")
            return

        parameter = self.parameter_combo.currentText()
        plot_type = self.plot_type_combo.currentText()

        safe_parameter = re.sub(r"[^A-Za-z0-9_-]+", "_", parameter).strip("_")
        safe_type = re.sub(r"[^A-Za-z0-9_-]+", "_", plot_type).strip("_")
        suggested = f"{safe_parameter}_{safe_type}.png"

        filename, _ = QFileDialog.getSaveFileName(
            self,
            "Save Plot",
            suggested,
            "PNG image (*.png);;PDF (*.pdf);;SVG (*.svg);;All files (*.*)",
        )

        if filename:
            # The selection ring is a GUI-only aid and should never appear in
            # exported scientific figures.
            selected_artist = self._selected_artist
            if selected_artist is not None:
                selected_artist.set_visible(False)

            try:
                self.figure.savefig(filename, dpi=300, bbox_inches="tight")
            finally:
                if selected_artist is not None:
                    selected_artist.set_visible(True)
                    self.canvas.draw_idle()
