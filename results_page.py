"""
ABSTRACT
--------
Results page for the AutoMapper V1 GUI.

The left side is the data area: import controls, the current mapping table, and
statistics.  The right side shows one selected mapping parameter at a time as
an interpolated color map with the actual measured XY locations overlaid.

Interaction:
- Move the mouse over the map to see X, Y, and the interpolated parameter value
  in a small readout below the plot.
- Click a measured point to select it and highlight the corresponding table row.
- Ignore/Enable changes that coordinate globally for every parameter.
- Ignored points remain visible as red X markers but are excluded from
  interpolation and statistics.

The Results page has no stage or CompleteEASE communication.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from matplotlib.backends.backend_qtagg import (
    FigureCanvasQTAgg as FigureCanvas,
    NavigationToolbar2QT as NavigationToolbar,
)
from matplotlib.figure import Figure

from data_parser import load_csv_table, parse_txt_folder
from plotting import (
    MapData,
    available_parameters,
    build_map,
    interpolated_value,
    statistics_for,
)


class ResultsPage(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)

        self.data = pd.DataFrame()
        self.map_data: MapData | None = None
        self.selected_row: int | None = None
        self._measurement_pixel_positions: list[tuple[int, float, float]] = []

        self._build_ui()

    # ------------------------------------------------------------------
    # Public handoff from the Measurement page
    # ------------------------------------------------------------------
    def set_dataframe(self, dataframe: pd.DataFrame) -> None:
        """Replace the current analysis table with a DataFrame from acquisition."""
        if dataframe is None or dataframe.empty:
            return

        self.data = dataframe.copy().reset_index(drop=True)
        if "Included" not in self.data.columns:
            self.data["Included"] = True

        self.selected_row = None
        self._refresh_table()
        self._refresh_parameter_list()
        self._redraw()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(splitter, 1)

        # ---------------------------- LEFT: data ------------------------
        left = QWidget()
        left_layout = QVBoxLayout(left)

        import_group = QGroupBox("Data")
        import_layout = QHBoxLayout(import_group)
        self.import_txt = QPushButton("Import TXT Folder")
        self.import_csv = QPushButton("Import Table")
        self.import_txt.clicked.connect(self._import_txt_folder)
        self.import_csv.clicked.connect(self._import_csv)
        import_layout.addWidget(self.import_txt)
        import_layout.addWidget(self.import_csv)
        left_layout.addWidget(import_group)

        table_group = QGroupBox("Current Results Table")
        table_layout = QVBoxLayout(table_group)
        self.table = QTableWidget()
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.itemSelectionChanged.connect(self._table_selection_changed)
        table_layout.addWidget(self.table)
        left_layout.addWidget(table_group, 3)

        stats_group = QGroupBox("Statistics — Selected Parameter")
        stats_layout = QVBoxLayout(stats_group)
        self.stats_table = QTableWidget(0, 2)
        self.stats_table.setHorizontalHeaderLabels(["Statistic", "Value"])
        self.stats_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        stats_layout.addWidget(self.stats_table)
        left_layout.addWidget(stats_group, 2)

        splitter.addWidget(left)

        # ---------------------------- RIGHT: plot -----------------------
        right = QWidget()
        right_layout = QVBoxLayout(right)

        parameter_row = QHBoxLayout()
        parameter_row.addWidget(QLabel("Parameter:"))
        self.parameter_combo = QComboBox()
        self.parameter_combo.currentTextChanged.connect(self._redraw)
        parameter_row.addWidget(self.parameter_combo)
        parameter_row.addStretch()
        right_layout.addLayout(parameter_row)

        self.figure = Figure(figsize=(7, 6))
        self.canvas = FigureCanvas(self.figure)
        self.toolbar = NavigationToolbar(self.canvas, self)
        right_layout.addWidget(self.toolbar)
        right_layout.addWidget(self.canvas, 1)

        # Cursor information is deliberately outside the graph so it never
        # obscures the scientific data.
        self.cursor_readout = QLabel("X: —    Y: —    Value: —")
        self.cursor_readout.setStyleSheet("color: #555555;")
        right_layout.addWidget(self.cursor_readout)

        self.selected_info = QLabel("No measured point selected.")
        self.selected_info.setWordWrap(True)
        right_layout.addWidget(self.selected_info)

        self.toggle_include = QPushButton("Ignore Selected Point")
        self.toggle_include.setEnabled(False)
        self.toggle_include.clicked.connect(self._toggle_selected_inclusion)
        right_layout.addWidget(self.toggle_include)

        self.canvas.mpl_connect("motion_notify_event", self._mouse_moved)
        self.canvas.mpl_connect("button_press_event", self._plot_clicked)

        splitter.addWidget(right)
        splitter.setSizes([430, 700])

    # ------------------------------------------------------------------
    # Import / table handling
    # ------------------------------------------------------------------
    def _import_txt_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Import CompleteEASE TXT Folder")
        if not folder:
            return

        try:
            self.set_dataframe(parse_txt_folder(folder))
        except Exception as exc:
            QMessageBox.warning(self, "Import TXT Folder", str(exc))

    def _import_csv(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(
            self, "Import Mapping Table", "", "CSV files (*.csv);;All files (*.*)"
        )
        if not filename:
            return

        try:
            self.set_dataframe(load_csv_table(filename))
        except Exception as exc:
            QMessageBox.warning(self, "Import Table", str(exc))

    def _refresh_table(self) -> None:
        self.table.clear()

        if self.data.empty:
            self.table.setRowCount(0)
            self.table.setColumnCount(0)
            return

        columns = list(self.data.columns)
        self.table.setColumnCount(len(columns))
        self.table.setHorizontalHeaderLabels(columns)
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

    def _refresh_parameter_list(self) -> None:
        parameters = available_parameters(self.data)
        previous = self.parameter_combo.currentText()

        self.parameter_combo.blockSignals(True)
        self.parameter_combo.clear()
        self.parameter_combo.addItems(parameters)

        if previous in parameters:
            self.parameter_combo.setCurrentText(previous)
        self.parameter_combo.blockSignals(False)

    # ------------------------------------------------------------------
    # Plot / statistics
    # ------------------------------------------------------------------
    def _redraw(self) -> None:
        self.figure.clear()
        self.map_data = None
        self._measurement_pixel_positions.clear()

        parameter = self.parameter_combo.currentText()
        if self.data.empty or not parameter:
            self.canvas.draw_idle()
            self._refresh_stats({})
            return

        try:
            self.map_data = build_map(self.data, parameter)
        except Exception as exc:
            ax = self.figure.add_subplot(111)
            ax.text(0.5, 0.5, str(exc), ha="center", va="center", transform=ax.transAxes)
            self.canvas.draw_idle()
            return

        ax = self.figure.add_subplot(111)

        # Interpolated color map.
        if np.isfinite(self.map_data.z_grid).any():
            xx, yy = np.meshgrid(self.map_data.x_axis, self.map_data.y_axis)
            contour = ax.contourf(xx, yy, self.map_data.z_grid, levels=20, cmap="viridis")
            self.figure.colorbar(contour, ax=ax, label=parameter)

        # Actual measured locations are always visible on top.
        inc = self.map_data.included
        exc = self.map_data.excluded
        if not inc.empty:
            ax.scatter(inc["X"], inc["Y"], marker="o", s=36, c="black", zorder=5)
        if not exc.empty:
            ax.scatter(exc["X"], exc["Y"], marker="x", s=60, c="red", linewidths=2, zorder=6)

        ax.set_xlabel("X (mm)")
        ax.set_ylabel("Y (mm)")
        ax.set_title(parameter)
        ax.set_aspect("equal", adjustable="box")
        self.figure.tight_layout()
        self.canvas.draw()

        # Cache measurement positions in display pixels for easy click selection.
        for row_index, row in self.data.iterrows():
            if pd.isna(row.get(parameter, np.nan)):
                continue
            px, py = ax.transData.transform((float(row["X"]), float(row["Y"])))
            self._measurement_pixel_positions.append((row_index, px, py))

        self._refresh_stats(statistics_for(self.data, parameter))
        self._refresh_selected_info()

    def _refresh_stats(self, stats: dict[str, float]) -> None:
        self.stats_table.setRowCount(len(stats))
        for row, (name, value) in enumerate(stats.items()):
            self.stats_table.setItem(row, 0, QTableWidgetItem(name))
            if np.isfinite(value):
                text = f"{value:.6g}"
            else:
                text = "—"
            self.stats_table.setItem(row, 1, QTableWidgetItem(text))
        self.stats_table.resizeColumnsToContents()

    # ------------------------------------------------------------------
    # Mouse interaction
    # ------------------------------------------------------------------
    def _mouse_moved(self, event) -> None:
        if self.map_data is None or event.inaxes is None:
            self.cursor_readout.setText("X: —    Y: —    Value: —")
            return

        x, y = event.xdata, event.ydata
        if x is None or y is None:
            return

        value = interpolated_value(self.map_data, x, y)
        value_text = "—" if value is None else f"{value:.6g}"
        self.cursor_readout.setText(
            f"X: {x:.3f} mm    Y: {y:.3f} mm    Value: {value_text}"
        )

    def _plot_clicked(self, event) -> None:
        if event.inaxes is None or not self._measurement_pixel_positions:
            return

        # Use display-pixel distance so clicking feels consistent regardless of
        # axis scale or map size.
        best = None
        for row_index, px, py in self._measurement_pixel_positions:
            d = ((event.x - px) ** 2 + (event.y - py) ** 2) ** 0.5
            if d <= 12 and (best is None or d < best[1]):
                best = (row_index, d)

        if best is None:
            return

        self._select_row(best[0])

    def _select_row(self, row_index: int) -> None:
        self.selected_row = row_index
        self.table.selectRow(row_index)
        self.table.scrollToItem(self.table.item(row_index, 0))
        self._refresh_selected_info()

    def _table_selection_changed(self) -> None:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return
        self.selected_row = rows[0].row()
        self._refresh_selected_info()

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

        value_text = "—" if pd.isna(parameter_value) else f"{float(parameter_value):.6g}"
        mse_text = "—" if pd.isna(mse) else f"{float(mse):.6g}"

        self.selected_info.setText(
            f"<b>Selected measurement:</b> X = {int(row['X'])} mm, "
            f"Y = {int(row['Y'])} mm &nbsp;&nbsp; "
            f"{parameter} = {value_text} &nbsp;&nbsp; MSE = {mse_text}"
        )

        self.toggle_include.setText(
            "Ignore Selected Point" if included else "Enable Selected Point"
        )
        self.toggle_include.setEnabled(True)

    def _toggle_selected_inclusion(self) -> None:
        if self.selected_row is None:
            return

        current = bool(self.data.at[self.selected_row, "Included"])
        self.data.at[self.selected_row, "Included"] = not current

        self._refresh_table()
        self.table.selectRow(self.selected_row)
        self._redraw()
