"""
ABSTRACT
--------
Interactive Results workspace for Ellipsometer AutoMapper.

The Results page is intentionally independent from acquisition. It accepts the
current mapping DataFrame from a live measurement or from imported TXT/CSV
files and provides plotting, point inclusion/exclusion, statistics, and export.

Main plot types:
- Interpolated Map
- Contour Map
- Measured Points + Values
- Pixel / Cell Map
- 3D Surface
- Histogram

Plot formatting is available from Plot Options and through direct shortcuts
(click title, colorbar label, or spatial axis ticks). Ignored points are shown
as red X markers during analysis but are omitted from exported scientific
figures. Missing measurements simply do not exist in the Results dataset.
"""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
import re

import numpy as np
import pandas as pd
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QAbstractSpinBox,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
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
from scipy.interpolate import RegularGridInterpolator

from data_parser import load_csv_table, parse_txt_folder
from plotting import (
    MapData,
    available_parameters,
    build_map,
    interpolated_value,
    statistics_for,
)


SPATIAL_PLOT_TYPES = {
    "Interpolated Map",
    "Contour Map",
    "Measured Points + Values",
    "Pixel / Cell Map",
    "3D Surface",
}

POINT_STATISTICS_PLOT_TYPES = {
    "Interpolated Map",
    "Contour Map",
    "Measured Points + Values",
    "Pixel / Cell Map",
}


class LineProfileWindow(QDialog):
    """Secondary qualitative thickness profile taken from the interpolated map."""

    closed = pyqtSignal()

    def __init__(self, results_page, parent=None) -> None:
        super().__init__(parent)
        self.results_page = results_page
        self.setWindowTitle("Interpolated Thickness Line Profile")
        self.setModal(False)
        self.resize(720, 480)

        layout = QVBoxLayout(self)

        self.info = QLabel()
        self.info.setWordWrap(True)
        layout.addWidget(self.info)

        note = QLabel(
            "Qualitative profile sampled from the interpolated thickness map. "
            "Values between measured locations are interpolated."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: #666666;")
        layout.addWidget(note)

        self.figure = Figure(figsize=(6.6, 3.8))
        self.canvas = FigureCanvas(self.figure)
        layout.addWidget(self.canvas, 1)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        save = QPushButton("Save Profile Plot")
        close = QPushButton("Close")
        save.clicked.connect(self._save_plot)
        close.clicked.connect(self.close)
        buttons.addWidget(save)
        buttons.addWidget(close)
        layout.addLayout(buttons)

    def update_profile(
        self,
        *,
        start: tuple[float, float],
        end: tuple[float, float],
        distance: np.ndarray,
        values: np.ndarray,
        parameter: str,
    ) -> None:
        """Redraw the secondary profile window from the current map."""
        length = float(np.hypot(end[0] - start[0], end[1] - start[1]))
        self.info.setText(
            f"<b>A:</b> ({start[0]:.3g}, {start[1]:.3g}) mm &nbsp;&nbsp; "
            f"<b>B:</b> ({end[0]:.3g}, {end[1]:.3g}) mm &nbsp;&nbsp; "
            f"<b>Length:</b> {length:.3g} mm"
        )

        self.figure.clear()
        ax = self.figure.add_subplot(111)
        finite = np.isfinite(values)
        if finite.any():
            ax.plot(distance[finite], values[finite], linewidth=1.8)
        else:
            ax.text(
                0.5,
                0.5,
                "No interpolated values are available along this line.",
                ha="center",
                va="center",
                transform=ax.transAxes,
            )
        ax.set_xlabel("Distance along line (mm)")
        ax.set_ylabel(parameter)
        ax.set_title("Interpolated Thickness Line Profile")
        ax.grid(True, alpha=0.25)
        self.figure.tight_layout()
        self.canvas.draw_idle()

    def _save_plot(self) -> None:
        suggested = "Thickness_Line_Profile.png"
        if self.results_page.default_results_folder is not None:
            suggested = str(self.results_page.default_results_folder / suggested)

        filename, _ = QFileDialog.getSaveFileName(
            self,
            "Save Line Profile",
            suggested,
            "PNG image (*.png);;PDF (*.pdf);;SVG (*.svg);;All files (*.*)",
        )
        if filename:
            self.figure.savefig(filename, dpi=300, bbox_inches="tight")

    def closeEvent(self, event) -> None:
        self.closed.emit()
        super().closeEvent(event)


class PointStatisticsWindow(QDialog):
    """Statistics for user-selected measured points."""

    closed = pyqtSignal()

    def __init__(self, results_page, parent=None) -> None:
        super().__init__(parent)
        self.results_page = results_page
        self.setWindowTitle("Point Statistics")
        self.setModal(False)
        self.resize(420, 330)

        layout = QVBoxLayout(self)
        self.info = QLabel()
        self.info.setWordWrap(True)
        layout.addWidget(self.info)

        note = QLabel(
            "Click a measured point to select it. Hold Ctrl and click to add or "
            "remove multiple points. Statistics update for the selected points. "
            "Closing this window clears the Point Statistics selection."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: #666666;")
        layout.addWidget(note)

        self.table = QTableWidget()
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setColumnCount(2)
        self.table.setHorizontalHeaderLabels(["Statistic", "Value"])
        self.table.setRowCount(5)
        layout.addWidget(self.table, 1)

        close = QPushButton("Close")
        close.clicked.connect(self.close)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(close)
        layout.addLayout(row)

    def update_statistics(
        self,
        *,
        parameter: str,
        selected_data: pd.DataFrame,
    ) -> None:
        count = len(selected_data)
        self.info.setText(
            f"<b>Parameter:</b> {parameter}<br>"
            f"<b>Selected measurements:</b> {count}"
        )

        names = ["Mean", "Std. Dev.", "Minimum", "Maximum", "Range (%)"]
        stats = statistics_for(selected_data, parameter) if count else {}
        for row, name in enumerate(names):
            self.table.setItem(row, 0, QTableWidgetItem(name))
            value = stats.get(name, np.nan)
            text = "—" if not np.isfinite(value) else f"{value:.6g}"
            self.table.setItem(row, 1, QTableWidgetItem(text))
        self.table.resizeColumnsToContents()
        self.table.resizeRowsToContents()

    def closeEvent(self, event) -> None:
        self.closed.emit()
        super().closeEvent(event)


class AxisOptionsDialog(QDialog):
    """Edit spatial X/Y range and tick spacing from a direct axis click."""

    def __init__(self, results_page, parent=None) -> None:
        super().__init__(parent)
        self.results_page = results_page
        self.setWindowTitle("Axis Options")
        self.setModal(True)
        self.setMinimumWidth(480)

        layout = QVBoxLayout(self)
        info = QLabel(
            "Axis range changes visualization only. They do not remove points "
            "from interpolation or automatically ignore measurements."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        self.x_controls = _AxisControlWidget(
            "X axis", results_page._x_range, results_page._x_tick_spacing,
            results_page._default_axis_bounds("x"), self
        )
        self.y_controls = _AxisControlWidget(
            "Y axis", results_page._y_range, results_page._y_tick_spacing,
            results_page._default_axis_bounds("y"), self
        )
        layout.addWidget(self.x_controls)
        layout.addWidget(self.y_controls)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept_options)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _accept_options(self) -> None:
        try:
            x_range, x_spacing = self.x_controls.values()
            y_range, y_spacing = self.y_controls.values()
        except ValueError as exc:
            QMessageBox.warning(self, "Axis Options", str(exc))
            return

        self.results_page._set_axis_options(
            x_range=x_range,
            y_range=y_range,
            x_spacing=x_spacing,
            y_spacing=y_spacing,
        )
        self.accept()


class _AxisControlWidget(QGroupBox):
    """Reusable controls for one spatial axis."""

    def __init__(
        self,
        title: str,
        current_range,
        current_spacing,
        default_bounds,
        parent=None,
    ) -> None:
        super().__init__(title, parent)
        self.default_bounds = default_bounds

        layout = QFormLayout(self)

        self.range_mode = QComboBox()
        self.range_mode.addItems(["Auto", "Custom"])
        self.range_mode.setCurrentText("Custom" if current_range else "Auto")

        range_row = QWidget()
        range_layout = QHBoxLayout(range_row)
        range_layout.setContentsMargins(0, 0, 0, 0)
        range_layout.setSpacing(6)

        self.minimum = self._number_box()
        self.maximum = self._number_box()

        initial_min, initial_max = (
            current_range if current_range is not None else default_bounds
        )
        self.minimum.setValue(initial_min)
        self.maximum.setValue(initial_max)

        range_layout.addWidget(QLabel("Min"))
        range_layout.addWidget(self.minimum)
        range_layout.addWidget(QLabel("Max"))
        range_layout.addWidget(self.maximum)

        self.spacing_mode = QComboBox()
        self.spacing_mode.addItems(["Auto", "Custom"])
        self.spacing_mode.setCurrentText(
            "Custom" if current_spacing is not None else "Auto"
        )

        self.spacing = self._number_box()
        self.spacing.setMinimum(0.0001)
        self.spacing.setValue(current_spacing if current_spacing is not None else 1.0)

        layout.addRow("Range:", self.range_mode)
        layout.addRow("Limits:", range_row)
        layout.addRow("Tick spacing:", self.spacing_mode)
        layout.addRow("Spacing:", self.spacing)

        self.range_mode.currentTextChanged.connect(self._update_enabled)
        self.spacing_mode.currentTextChanged.connect(self._update_enabled)
        self._update_enabled()

    @staticmethod
    def _number_box() -> QDoubleSpinBox:
        box = QDoubleSpinBox()
        box.setRange(-1_000_000.0, 1_000_000.0)
        box.setDecimals(4)
        box.setSingleStep(0.1)
        # Native upper spin arrows have been unreliable on the target Windows
        # system, so axis values are intentionally typed directly.
        box.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        return box

    def _update_enabled(self) -> None:
        custom_range = self.range_mode.currentText() == "Custom"
        self.minimum.setEnabled(custom_range)
        self.maximum.setEnabled(custom_range)
        self.spacing.setEnabled(self.spacing_mode.currentText() == "Custom")

    def values(self):
        axis_range = None
        if self.range_mode.currentText() == "Custom":
            minimum = float(self.minimum.value())
            maximum = float(self.maximum.value())
            if minimum >= maximum:
                raise ValueError(
                    f"{self.title()} minimum must be smaller than maximum."
                )
            axis_range = (minimum, maximum)

        spacing = None
        if self.spacing_mode.currentText() == "Custom":
            spacing = float(self.spacing.value())
            if spacing <= 0:
                raise ValueError(f"{self.title()} tick spacing must be positive.")

        return axis_range, spacing


class BatchSaveDialog(QDialog):
    """Choose plot types to export for every available parameter."""

    def __init__(self, results_page, parent=None) -> None:
        super().__init__(parent)
        self.results_page = results_page
        self.setWindowTitle("Save All Plots")
        self.setModal(True)
        self.setMinimumWidth(430)

        layout = QVBoxLayout(self)
        intro = QLabel(
            "Choose the plot types to save. Each selected type is exported for "
            "every parameter for which that plot can be generated."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self.checkboxes: dict[str, QCheckBox] = {}

        checkbox_style = """
            QCheckBox {
                spacing: 8px;
                padding: 3px 0px;
            }

            QCheckBox::indicator {
                width: 18px;
                height: 18px;
                border: 2px solid #303030;
                border-radius: 3px;
                background-color: white;
            }

            QCheckBox::indicator:checked {
                background-color: #1976D2;
                border: 2px solid #0D47A1;
            }

            QCheckBox:checked {
                color: #0D47A1;
                font-weight: 700;
            }

            QCheckBox:disabled {
                color: #999999;
            }

            QCheckBox::indicator:disabled {
                border: 2px solid #B0B0B0;
                background-color: #EEEEEE;
            }
        """

        for plot_type in results_page.PLOT_TYPES:
            checkbox = QCheckBox(plot_type)
            checkbox.setStyleSheet(checkbox_style)

            available = results_page._plot_type_available_for_any_parameter(plot_type)
            checkbox.setEnabled(available)
            checkbox.setChecked(plot_type == "Interpolated Map" and available)

            if not available:
                checkbox.setToolTip("Not available for the current dataset.")

            checkbox.stateChanged.connect(self._update_count)
            layout.addWidget(checkbox)
            self.checkboxes[plot_type] = checkbox

        self.file_count = QLabel()
        self.file_count.setStyleSheet("font-weight: 600;")
        layout.addWidget(self.file_count)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self.save_button = buttons.button(QDialogButtonBox.StandardButton.Save)
        layout.addWidget(buttons)
        self._update_count()

    def selected_plot_types(self) -> list[str]:
        return [
            plot_type
            for plot_type, checkbox in self.checkboxes.items()
            if checkbox.isEnabled() and checkbox.isChecked()
        ]

    def _update_count(self) -> None:
        count = self.results_page._batch_file_count(self.selected_plot_types())
        self.file_count.setText(f"Files to create: {count}")
        self.save_button.setEnabled(count > 0)


class StatisticsOptionsDialog(QDialog):
    """Statistics scope and export controls."""

    def __init__(self, results_page, parent=None) -> None:
        super().__init__(parent)
        self.results_page = results_page
        self.setWindowTitle("Statistics Options")
        self.setModal(True)
        self.setMinimumWidth(430)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Statistics scope:"))

        self.all_data = QRadioButton("All included measurements")
        self.plot_range = QRadioButton("Measurements inside current plot range")

        radio_style = """
            QRadioButton {
                spacing: 8px;
                padding: 4px 0px;
            }

            QRadioButton::indicator {
                width: 18px;
                height: 18px;
                border: 2px solid #303030;
                border-radius: 10px;
                background-color: white;
            }

            QRadioButton::indicator:checked {
                background-color: #1976D2;
                border: 2px solid #0D47A1;
            }

            QRadioButton:checked {
                color: #0D47A1;
                font-weight: 700;
            }
        """

        self.all_data.setStyleSheet(radio_style)
        self.plot_range.setStyleSheet(radio_style)

        layout.addWidget(self.all_data)
        layout.addWidget(self.plot_range)

        if results_page._statistics_scope == "range":
            self.plot_range.setChecked(True)
        else:
            self.all_data.setChecked(True)

        self.all_data.toggled.connect(self._scope_changed)
        self.plot_range.toggled.connect(self._scope_changed)

        note = QLabel(
            "Ignored points are excluded in either mode. Points outside the "
            "selected plot range are not considered 'ignored'; they are simply "
            "outside the statistics scope."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: #666666;")
        layout.addWidget(note)

        export = QPushButton("Export Statistics…")
        export.clicked.connect(results_page._export_statistics)
        layout.addWidget(export)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _scope_changed(self) -> None:
        if self.plot_range.isChecked():
            self.results_page._set_statistics_scope("range")
        elif self.all_data.isChecked():
            self.results_page._set_statistics_scope("all")


class PlotOptionsDialog(QDialog):
    """Primary discoverable editor for plot formatting and export."""

    def __init__(self, results_page, parent=None) -> None:
        super().__init__(parent)
        self.results_page = results_page
        self._syncing = False
        self.setWindowTitle("Plot Options")
        self.setModal(True)
        self.setMinimumWidth(560)

        root = QVBoxLayout(self)

        labels_group = QGroupBox("Labels")
        labels_form = QFormLayout(labels_group)
        self.title_edit = QLineEdit()
        self.colorbar_edit = QLineEdit()
        labels_form.addRow("Plot title:", self.title_edit)
        labels_form.addRow("Colorbar title:", self.colorbar_edit)
        root.addWidget(labels_group)

        axes_group = QGroupBox("Spatial Map Axes")
        axes_layout = QVBoxLayout(axes_group)
        info = QLabel(
            "Axis limits change visualization only. Auto uses the dataset range. "
            "These settings are not used by Histogram."
        )
        info.setWordWrap(True)
        info.setStyleSheet("color: #666666;")
        axes_layout.addWidget(info)

        self.x_controls = _AxisControlWidget(
            "X axis", results_page._x_range, results_page._x_tick_spacing,
            results_page._default_axis_bounds("x"), self
        )
        self.y_controls = _AxisControlWidget(
            "Y axis", results_page._y_range, results_page._y_tick_spacing,
            results_page._default_axis_bounds("y"), self
        )
        axes_layout.addWidget(self.x_controls)
        axes_layout.addWidget(self.y_controls)
        root.addWidget(axes_group)

        # Reset belongs with the plot settings rather than the export actions.
        reset_row = QHBoxLayout()
        reset = QPushButton("Reset Plot Settings")
        reset.clicked.connect(self._reset)
        reset_row.addWidget(reset)
        reset_row.addStretch(1)
        root.addLayout(reset_row)

        export_group = QGroupBox("Export")
        export_layout = QVBoxLayout(export_group)

        self.include_markers = QCheckBox("Include measured-point markers")
        self.include_markers.setStyleSheet("""
            QCheckBox {
                spacing: 8px;
                padding: 3px 0px;
            }
            QCheckBox::indicator {
                width: 18px;
                height: 18px;
                border: 2px solid #303030;
                border-radius: 3px;
                background-color: white;
            }
            QCheckBox::indicator:checked {
                background-color: #1976D2;
                border: 2px solid #0D47A1;
            }
            QCheckBox:checked {
                color: #0D47A1;
                font-weight: 700;
            }
        """)
        self.include_markers.setChecked(results_page._export_include_markers)
        self.include_markers.toggled.connect(self._apply_export_options)
        export_layout.addWidget(self.include_markers)

        save_row = QHBoxLayout()
        save_current = QPushButton("Save Current Plot")
        save_all = QPushButton("Save All Plots…")
        save_current.clicked.connect(results_page._save_current_plot)
        save_all.clicked.connect(self._save_all)
        save_row.addWidget(save_current)
        save_row.addWidget(save_all)
        export_layout.addLayout(save_row)
        root.addWidget(export_group)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        self.title_edit.editingFinished.connect(self._apply_labels)
        self.colorbar_edit.editingFinished.connect(self._apply_labels)

        for widget in (self.x_controls, self.y_controls):
            widget.range_mode.currentTextChanged.connect(self._apply_axes)
            widget.spacing_mode.currentTextChanged.connect(self._apply_axes)
            widget.minimum.editingFinished.connect(self._apply_axes)
            widget.maximum.editingFinished.connect(self._apply_axes)
            widget.spacing.editingFinished.connect(self._apply_axes)

        self.sync_from_page()

    def sync_from_page(self) -> None:
        self._syncing = True
        try:
            parameter = self.results_page.parameter_combo.currentText()
            self.title_edit.setText(
                self.results_page._custom_title
                or self.results_page._default_title(parameter)
            )
            self.colorbar_edit.setText(
                self.results_page._custom_colorbar_label or parameter
            )
            self.colorbar_edit.setEnabled(
                self.results_page.plot_type_combo.currentText() != "Histogram"
            )
            self.include_markers.setChecked(
                self.results_page._export_include_markers
            )

            self._sync_axis_widget(
                self.x_controls,
                self.results_page._x_range,
                self.results_page._x_tick_spacing,
                self.results_page._default_axis_bounds("x"),
            )
            self._sync_axis_widget(
                self.y_controls,
                self.results_page._y_range,
                self.results_page._y_tick_spacing,
                self.results_page._default_axis_bounds("y"),
            )
        finally:
            self._syncing = False

    @staticmethod
    def _sync_axis_widget(widget, axis_range, spacing, defaults) -> None:
        widget.range_mode.setCurrentText("Custom" if axis_range else "Auto")
        minimum, maximum = axis_range if axis_range is not None else defaults
        widget.minimum.setValue(minimum)
        widget.maximum.setValue(maximum)
        widget.spacing_mode.setCurrentText("Custom" if spacing is not None else "Auto")
        widget.spacing.setValue(spacing if spacing is not None else 1.0)
        widget._update_enabled()

    def _apply_labels(self) -> None:
        if self._syncing:
            return
        title = self.title_edit.text().strip()
        colorbar = self.colorbar_edit.text().strip()
        self.results_page._custom_title = title or None
        self.results_page._custom_colorbar_label = colorbar or None
        self.results_page._redraw()

    def _apply_axes(self) -> None:
        if self._syncing:
            return
        try:
            x_range, x_spacing = self.x_controls.values()
            y_range, y_spacing = self.y_controls.values()
        except ValueError:
            # While the user is still typing, leave the last valid settings in
            # place. Direct Axis Options performs explicit validation on OK.
            return
        self.results_page._set_axis_options(
            x_range=x_range,
            y_range=y_range,
            x_spacing=x_spacing,
            y_spacing=y_spacing,
        )

    def _apply_export_options(self) -> None:
        if self._syncing:
            return
        self.results_page._export_include_markers = (
            self.include_markers.isChecked()
        )

    def _save_all(self) -> None:
        dialog = BatchSaveDialog(self.results_page, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.results_page._save_all_plots(dialog.selected_plot_types())

    def _reset(self) -> None:
        self.results_page._reset_plot_settings()
        self.sync_from_page()


class ResultsPage(QWidget):
    PLOT_TYPES = [
        "Interpolated Map",
        "Contour Map",
        "Measured Points + Values",
        "Pixel / Cell Map",
        "3D Surface",
        "Histogram",
    ]

    def __init__(self, parent=None) -> None:
        super().__init__(parent)

        self.data = pd.DataFrame()
        self.map_data: MapData | None = None
        self.selected_row: int | None = None
        self.default_results_folder: Path | None = None

        self.ax = None
        self.cax = None
        self._title_artist = None
        self._custom_title: str | None = None

        self.colorbar = None
        self._colorbar_label_artist = None
        self._custom_colorbar_label: str | None = None

        self._selected_artist = None
        self._ignored_artists: list = []
        self._measurement_marker_artists: list = []
        self._profile_overlay_artists: list = []
        self._point_statistics_artists: list = []
        self._plot_render_ok = False
        self._export_include_markers = True

        self._point_statistics_active = False
        self._point_statistics_rows: set[int] = set()
        self._point_statistics_window: PointStatisticsWindow | None = None

        self._line_profile_selecting = False
        self._line_profile_start: tuple[float, float] | None = None
        self._line_profile_end: tuple[float, float] | None = None
        self._line_profile_window: LineProfileWindow | None = None

        self._x_tick_spacing: float | None = None
        self._y_tick_spacing: float | None = None
        self._x_range: tuple[float, float] | None = None
        self._y_range: tuple[float, float] | None = None
        self._statistics_scope = "all"

        self._build_ui()

    # ------------------------------------------------------------------
    # Public handoff from Measurement
    # ------------------------------------------------------------------
    def set_dataframe(
        self,
        dataframe: pd.DataFrame,
        preserve_inclusion: bool = True,
    ) -> None:
        """
        Load the current mapping table.

        Live measurement updates preserve Ignore/Enable choices for coordinates
        already present. Explicit TXT/CSV imports call this with
        preserve_inclusion=False so an old dataset's ignored points never leak
        into a newly imported dataset.
        """
        if dataframe is None or dataframe.empty:
            self._clear_point_statistics(close_window=True, redraw=False)
            self.data = pd.DataFrame()
            self.selected_row = None
            self.table.clear()
            self.table.setRowCount(0)
            self.table.setColumnCount(0)
            self.stats_table.clear()
            self.stats_table.setRowCount(0)
            self.stats_table.setColumnCount(0)
            self.parameter_combo.blockSignals(True)
            self.parameter_combo.clear()
            self.parameter_combo.blockSignals(False)
            self._redraw()
            return

        self._clear_point_statistics(close_window=True, redraw=False)
        incoming = dataframe.copy().reset_index(drop=True)
        if "Included" not in incoming.columns:
            incoming["Included"] = True

        if not preserve_inclusion:
            incoming.loc[:, "Included"] = True
        elif not self.data.empty and "Included" in self.data.columns:
            previous = {
                (float(row["X"]), float(row["Y"])): bool(row["Included"])
                for _, row in self.data.iterrows()
            }
            for index, row in incoming.iterrows():
                key = (float(row["X"]), float(row["Y"]))
                if key in previous:
                    incoming.at[index, "Included"] = previous[key]

        self.data = incoming
        self.selected_row = None
        self._refresh_table()
        self._refresh_parameter_list()
        self._refresh_statistics()
        self._redraw()

    def set_default_results_folder(self, folder: str) -> None:
        path = Path(folder)
        path.mkdir(parents=True, exist_ok=True)
        self.default_results_folder = path

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(splitter, 1)

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
        self.table.cellClicked.connect(self._table_row_clicked)
        data_layout.addWidget(self.table)
        left_layout.addWidget(data_group, 3)

        stats_group = QGroupBox("Parameter Statistics")
        stats_layout = QVBoxLayout(stats_group)
        stats_button_row = QHBoxLayout()
        stats_button_row.addStretch(1)
        self.stats_options = QPushButton("Statistics Options…")
        self.stats_options.setMaximumWidth(150)
        self.stats_options.clicked.connect(self._show_statistics_options)
        stats_button_row.addWidget(self.stats_options)
        stats_layout.addLayout(stats_button_row)

        self.stats_table = QTableWidget()
        self.stats_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        stats_layout.addWidget(self.stats_table)
        left_layout.addWidget(stats_group, 2)

        splitter.addWidget(left)

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

        self.line_profile_button = QPushButton("Line Profile")
        self.line_profile_button.setVisible(False)
        self.line_profile_button.setToolTip(
            "Draw a qualitative thickness profile on the interpolated map."
        )
        self.line_profile_button.clicked.connect(self._toggle_line_profile_mode)
        controls.addWidget(self.line_profile_button)

        self.point_statistics_button = QPushButton("Point Statistics")
        self.point_statistics_button.setVisible(False)
        self.point_statistics_button.setToolTip(
            "Select one or more measured points for Point Statistics. Hold Ctrl while clicking to select multiple points."
        )
        self.point_statistics_button.clicked.connect(
            self._toggle_point_statistics_mode
        )
        controls.addWidget(self.point_statistics_button)

        self.plot_options = QPushButton("Plot Options…")
        self.plot_options.clicked.connect(self._show_plot_options)
        controls.addWidget(self.plot_options)
        right_layout.addLayout(controls)

        self.figure = Figure(figsize=(7.2, 6.0))
        self.canvas = FigureCanvas(self.figure)
        self.canvas.setMinimumSize(430, 360)
        right_layout.addWidget(self.canvas, 1)

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
            "Tip: Plot Options contains all formatting/export controls. "
            "Shortcuts: click the plot title, colorbar title, or spatial axis "
            "ticks to edit them directly."
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
            self.default_results_folder = None
            self.set_dataframe(parse_txt_folder(folder), preserve_inclusion=False)
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
            self.default_results_folder = None
            self.set_dataframe(load_csv_table(filename), preserve_inclusion=False)
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
        # Custom labels are tied to the currently selected parameter. Dataset
        # imports preserve them when the same parameter remains selected.
        self._custom_title = None
        self._custom_colorbar_label = None
        self._redraw()

    def _statistics_dataframe(self) -> pd.DataFrame:
        if self.data.empty:
            return self.data.copy()

        work = self.data.copy()
        if self._statistics_scope != "range":
            return work

        xmin, xmax, ymin, ymax = self._statistics_bounds()
        x = pd.to_numeric(work["X"], errors="coerce")
        y = pd.to_numeric(work["Y"], errors="coerce")
        mask = x.between(xmin, xmax, inclusive="both") & y.between(
            ymin, ymax, inclusive="both"
        )
        return work.loc[mask].copy()

    def _statistics_bounds(self) -> tuple[float, float, float, float]:
        xmin, xmax = self._default_axis_bounds("x")
        ymin, ymax = self._default_axis_bounds("y")
        if self._x_range is not None:
            xmin, xmax = self._x_range
        if self._y_range is not None:
            ymin, ymax = self._y_range
        return xmin, xmax, ymin, ymax

    def _refresh_statistics(self) -> None:
        parameters = available_parameters(self.data)
        statistic_names = ["Mean", "Std. Dev.", "Minimum", "Maximum", "Range (%)"]
        scoped = self._statistics_dataframe()

        self.stats_table.clear()
        self.stats_table.setRowCount(len(statistic_names))
        self.stats_table.setColumnCount(len(parameters))
        self.stats_table.setVerticalHeaderLabels(statistic_names)
        self.stats_table.setHorizontalHeaderLabels(parameters)

        for column, parameter in enumerate(parameters):
            stats = statistics_for(scoped, parameter)
            for row, statistic in enumerate(statistic_names):
                value = stats.get(statistic, np.nan)
                text = "—" if not np.isfinite(value) else f"{value:.6g}"
                self.stats_table.setItem(row, column, QTableWidgetItem(text))

        self.stats_table.resizeColumnsToContents()
        self.stats_table.resizeRowsToContents()

    def _set_statistics_scope(self, scope: str) -> None:
        self._statistics_scope = "range" if scope == "range" else "all"
        self._refresh_statistics()

    def _show_statistics_options(self) -> None:
        StatisticsOptionsDialog(self, self).exec()

    def _export_statistics(self) -> None:
        if self.data.empty or not available_parameters(self.data):
            QMessageBox.information(
                self, "Export Statistics", "No statistics are available to export."
            )
            return

        suggested = "statistics.csv"
        if self.default_results_folder is not None:
            suggested = str(self.default_results_folder / suggested)

        filename, _ = QFileDialog.getSaveFileName(
            self,
            "Export Statistics",
            suggested,
            "CSV files (*.csv);;All files (*.*)",
        )
        if not filename:
            return

        scoped = self._statistics_dataframe()
        included_all = self.data["Included"].astype(bool)
        ignored_count = int((~included_all).sum())

        if self._statistics_scope == "range":
            xmin, xmax, ymin, ymax = self._statistics_bounds()
            scope_text = f"X = {xmin:g} to {xmax:g} mm; Y = {ymin:g} to {ymax:g} mm"
            inside_scope_count = len(scoped)
        else:
            scope_text = "Full Dataset"
            inside_scope_count = len(self.data)

        used_count = int(scoped["Included"].astype(bool).sum())
        parameters = available_parameters(self.data)
        statistic_names = ["Mean", "Std. Dev.", "Minimum", "Maximum", "Range (%)"]

        try:
            with Path(filename).open("w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerow(["Statistics Scope", scope_text])
                writer.writerow(["Valid Measurements", len(self.data)])
                writer.writerow(["User-Ignored Measurements", ignored_count])
                if self._statistics_scope == "range":
                    writer.writerow(["Measurements Inside Scope", inside_scope_count])
                writer.writerow(["Measurements Used for Statistics", used_count])
                writer.writerow([])
                writer.writerow(["Statistic", *parameters])

                stats_by_parameter = {
                    parameter: statistics_for(scoped, parameter)
                    for parameter in parameters
                }
                for statistic in statistic_names:
                    row = [statistic]
                    for parameter in parameters:
                        value = stats_by_parameter[parameter].get(statistic, np.nan)
                        row.append("" if not np.isfinite(value) else f"{value:.12g}")
                    writer.writerow(row)
        except Exception as exc:
            QMessageBox.warning(self, "Export Statistics", str(exc))
            return

        QMessageBox.information(
            self, "Export Statistics", f"Statistics saved to:\n{filename}"
        )

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
        self._ignored_artists = []
        self._measurement_marker_artists = []
        self._profile_overlay_artists = []
        self._point_statistics_artists = []
        self._plot_render_ok = False

        parameter = self.parameter_combo.currentText()
        plot_type = self.plot_type_combo.currentText()

        if not self._line_profile_mode_applicable(parameter, plot_type):
            self._clear_line_profile(close_window=True, redraw=False)
        if not self._point_statistics_mode_applicable(parameter, plot_type):
            self._clear_point_statistics(close_window=True, redraw=False)

        if self.data.empty or not parameter:
            self._update_line_profile_button()
            self._update_point_statistics_button()
            self.canvas.draw_idle()
            return

        if plot_type != "Histogram":
            try:
                self.map_data = build_map(self.data, parameter)
            except Exception as exc:
                ax = self.figure.add_subplot(111)
                ax.text(0.5, 0.5, str(exc), ha="center", va="center", transform=ax.transAxes)
                self.canvas.draw_idle()
                return

        if plot_type == "3D Surface":
            self.figure.subplots_adjust(left=0.06, right=0.84, bottom=0.08, top=0.90)
            self.ax = self.figure.add_subplot(111, projection="3d")
        elif plot_type == "Histogram":
            self.figure.subplots_adjust(left=0.12, right=0.96, bottom=0.13, top=0.90)
            self.ax = self.figure.add_subplot(111)
        else:
            self.figure.subplots_adjust(left=0.11, right=0.84, bottom=0.12, top=0.90)
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

        try:
            if plot_type == "Interpolated Map":
                self._draw_interpolated(parameter)
            elif plot_type == "Contour Map":
                self._draw_contour(parameter)
            elif plot_type == "Measured Points + Values":
                self._draw_points_with_values(parameter)
            elif plot_type == "Pixel / Cell Map":
                self._draw_pixel_map(parameter)
            elif plot_type == "3D Surface":
                self._draw_3d_surface(parameter)
            elif plot_type == "Histogram":
                self._draw_histogram(parameter)
            self._plot_render_ok = True
        except Exception as exc:
            self.ax.clear()
            if self.cax is not None:
                self.cax.clear()
                self.cax.set_visible(False)
            if plot_type == "3D Surface":
                self.ax.text2D(
                    0.5, 0.5, str(exc), ha="center", va="center",
                    transform=self.ax.transAxes,
                )
            else:
                self.ax.text(
                    0.5, 0.5, str(exc), ha="center", va="center",
                    transform=self.ax.transAxes,
                )

        self._draw_selected_marker(parameter)
        self._draw_point_statistics_markers(parameter)

        if plot_type == "Histogram":
            self.ax.set_xlabel(parameter)
            self.ax.set_ylabel("Count")
        elif plot_type == "3D Surface":
            self.ax.set_xlabel("X (mm)")
            self.ax.set_ylabel("Y (mm)")
            self.ax.set_zlabel(parameter)
            self._apply_axis_options()
        else:
            self.ax.set_xlabel("X (mm)")
            self.ax.set_ylabel("Y (mm)")
            self.ax.set_aspect("equal", adjustable="box")
            self._apply_axis_options()

        title = self._custom_title or self._default_title(parameter)
        self._title_artist = self.ax.set_title(title)

        self._draw_line_profile_overlay()
        self._update_line_profile_window()
        self._update_line_profile_button()
        self._update_point_statistics_window()
        self._update_point_statistics_button()

        self.canvas.draw()
        self._refresh_selected_info()

    def _default_title(self, parameter: str) -> str:
        if self.plot_type_combo.currentText() == "Histogram":
            return f"Distribution of {parameter}"

        parameters = available_parameters(self.data)
        thickness_parameters = [
            p for p in parameters
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
            xx, yy, self.map_data.z_grid, levels=20, cmap="viridis"
        )
        self._create_colorbar(contour, parameter)
        self._draw_measurement_markers()
        self._set_data_limits(exact=True)

    def _draw_contour(self, parameter: str) -> None:
        if not np.isfinite(self.map_data.z_grid).any():
            raise ValueError("Not enough two-dimensional data for interpolation.")

        xx, yy = np.meshgrid(self.map_data.x_axis, self.map_data.y_axis)
        filled = self.ax.contourf(
            xx, yy, self.map_data.z_grid, levels=20, cmap="viridis"
        )
        lines = self.ax.contour(
            xx,
            yy,
            self.map_data.z_grid,
            levels=10,
            colors="black",
            linewidths=0.65,
            alpha=0.70,
        )
        self.ax.clabel(lines, inline=True, fontsize=7, fmt="%.3g")
        self._create_colorbar(filled, parameter)
        self._draw_measurement_markers()
        self._set_data_limits(exact=True)

    def _draw_points_with_values(self, parameter: str) -> None:
        inc = self.map_data.included
        exc = self.map_data.excluded

        values = pd.to_numeric(inc[parameter], errors="coerce")
        scatter = self.ax.scatter(
            inc["X"], inc["Y"], c=values, cmap="viridis", s=85,
            edgecolors="black", linewidths=1.0, zorder=5,
        )
        self._create_colorbar(scatter, parameter)

        y_values = sorted(set(float(v) for v in self.data["Y"]))
        y_spacing = min(np.diff(y_values)) if len(y_values) > 1 else 1.0
        offset = 0.12 * y_spacing

        for _, row in inc.iterrows():
            self.ax.text(
                float(row["X"]),
                float(row["Y"]) + offset,
                f"{float(row[parameter]):.1f}",
                ha="center",
                va="bottom",
                fontsize=8,
                zorder=6,
            )

        if not exc.empty:
            artist = self.ax.scatter(
                exc["X"], exc["Y"], marker="x", s=65, c="red",
                linewidths=2, zorder=7,
            )
            self._ignored_artists.append(artist)

        self._set_data_limits()

    def _draw_pixel_map(self, parameter: str) -> None:
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
            value = pd.to_numeric(pd.Series([row.get(parameter)]), errors="coerce").iloc[0]
            if pd.isna(value):
                continue
            ix = int(np.where(all_x == float(row["X"]))[0][0])
            iy = int(np.where(all_y == float(row["Y"]))[0][0])
            z[iy, ix] = float(value)

        mesh = self.ax.pcolormesh(
            x_edges, y_edges, np.ma.masked_invalid(z), cmap="viridis", shading="flat"
        )
        self._create_colorbar(mesh, parameter)
        self._draw_measurement_markers()
        self.ax.set_xlim(x_edges[0], x_edges[-1])
        self.ax.set_ylim(y_edges[0], y_edges[-1])

    def _draw_3d_surface(self, parameter: str) -> None:
        if not np.isfinite(self.map_data.z_grid).any():
            raise ValueError("Not enough two-dimensional data for a 3D surface.")

        # Downsample only the visual surface for responsive interaction. The
        # underlying interpolation remains at full resolution.
        step = max(1, len(self.map_data.x_axis) // 70)
        x_axis = self.map_data.x_axis[::step]
        y_axis = self.map_data.y_axis[::step]
        z_grid = self.map_data.z_grid[::step, ::step]
        xx, yy = np.meshgrid(x_axis, y_axis)

        surface = self.ax.plot_surface(
            xx, yy, z_grid, cmap="viridis", linewidth=0, antialiased=True,
            alpha=0.96,
        )
        self._create_colorbar(surface, parameter)

        inc = self.map_data.included
        if not inc.empty:
            z = pd.to_numeric(inc[parameter], errors="coerce")
            artist = self.ax.scatter(
                inc["X"], inc["Y"], z, c="black", s=20, depthshade=False
            )
            self._measurement_marker_artists.append(artist)

        exc = self.map_data.excluded
        if not exc.empty:
            z = pd.to_numeric(exc[parameter], errors="coerce")
            artist = self.ax.scatter(
                exc["X"], exc["Y"], z, c="red", marker="x", s=45,
                linewidths=2, depthshade=False,
            )
            self._ignored_artists.append(artist)

        self._set_data_limits(exact=True)

    def _draw_histogram(self, parameter: str) -> None:
        work = self.data[self.data["Included"].astype(bool)].copy()
        values = pd.to_numeric(work[parameter], errors="coerce").dropna().to_numpy()
        if len(values) == 0:
            raise ValueError(f"No included values are available for {parameter}.")

        self.ax.hist(values, bins="auto", edgecolor="black", linewidth=0.8)
        self.cursor_readout.setText("X: —    Y: —    Value: —")

    def _draw_measurement_markers(self) -> None:
        inc = self.map_data.included
        exc = self.map_data.excluded

        if not inc.empty:
            artist = self.ax.scatter(
                inc["X"], inc["Y"], marker="o", s=34, c="black", zorder=8
            )
            self._measurement_marker_artists.append(artist)

        if not exc.empty:
            artist = self.ax.scatter(
                exc["X"], exc["Y"], marker="x", s=65, c="red",
                linewidths=2, zorder=9,
            )
            self._ignored_artists.append(artist)

        self._set_data_limits()

    def _draw_selected_marker(self, parameter: str) -> None:
        if self.ax is None or self.selected_row is None or self.data.empty:
            return
        if self.selected_row >= len(self.data):
            return

        plot_type = self.plot_type_combo.currentText()
        if plot_type == "Histogram":
            return

        row = self.data.iloc[self.selected_row]
        if plot_type == "3D Surface":
            value = pd.to_numeric(pd.Series([row.get(parameter)]), errors="coerce").iloc[0]
            if pd.isna(value):
                return
            self._selected_artist = self.ax.scatter(
                [float(row["X"])], [float(row["Y"])], [float(value)],
                s=95, facecolors="none", edgecolors="#1f5fa8", linewidths=2.2,
                depthshade=False,
            )
            return

        self._selected_artist = self.ax.scatter(
            [float(row["X"])], [float(row["Y"])], s=150,
            facecolors="none", edgecolors="#1f5fa8", linewidths=2.2, zorder=12,
        )

    def _draw_point_statistics_markers(self, parameter: str) -> None:
        if (
            self.ax is None
            or not self._point_statistics_rows
            or self.plot_type_combo.currentText() not in POINT_STATISTICS_PLOT_TYPES
        ):
            return

        for row_index in sorted(self._point_statistics_rows):
            if row_index < 0 or row_index >= len(self.data):
                continue
            row = self.data.iloc[row_index]
            if not bool(row.get("Included", True)):
                continue
            value = pd.to_numeric(
                pd.Series([row.get(parameter)]), errors="coerce"
            ).iloc[0]
            if pd.isna(value):
                continue
            artist = self.ax.scatter(
                [float(row["X"])],
                [float(row["Y"])],
                s=190,
                facecolors="none",
                edgecolors="#1565C0",
                linewidths=2.8,
                zorder=14,
            )
            self._point_statistics_artists.append(artist)

    def _create_colorbar(self, mappable, parameter: str) -> None:
        label = self._custom_colorbar_label or parameter
        if self.cax is not None:
            self.colorbar = self.figure.colorbar(mappable, cax=self.cax, label=label)
        else:
            self.colorbar = self.figure.colorbar(
                mappable, ax=self.ax, label=label, shrink=0.72, pad=0.10
            )
        self._colorbar_label_artist = self.colorbar.ax.yaxis.label

    def _set_data_limits(self, exact: bool = False) -> None:
        if self.data.empty or self.ax is None:
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

    def _default_axis_bounds(self, axis: str) -> tuple[float, float]:
        if self.data.empty:
            return (-1.0, 1.0)
        column = "X" if axis.lower() == "x" else "Y"
        values = pd.to_numeric(self.data[column], errors="coerce").dropna()
        if values.empty:
            return (-1.0, 1.0)
        minimum = float(values.min())
        maximum = float(values.max())
        if minimum == maximum:
            return (minimum - 0.5, maximum + 0.5)
        return (minimum, maximum)

    def _set_axis_options(
        self,
        *,
        x_range,
        y_range,
        x_spacing,
        y_spacing,
    ) -> None:
        self._x_range = x_range
        self._y_range = y_range
        self._x_tick_spacing = x_spacing
        self._y_tick_spacing = y_spacing
        self._refresh_statistics()
        self._redraw()

    def _apply_axis_options(self) -> None:
        if self.ax is None:
            return

        if self._x_range is not None:
            self.ax.set_xlim(*self._x_range)
        if self._y_range is not None:
            self.ax.set_ylim(*self._y_range)

        if self._x_tick_spacing is not None:
            self.ax.xaxis.set_major_locator(MultipleLocator(self._x_tick_spacing))
        if self._y_tick_spacing is not None:
            self.ax.yaxis.set_major_locator(MultipleLocator(self._y_tick_spacing))

    # ------------------------------------------------------------------
    # Mouse interaction
    # ------------------------------------------------------------------
    def _mouse_moved(self, event) -> None:
        if self.plot_type_combo.currentText() in {"Histogram", "3D Surface"}:
            self.cursor_readout.setText("X: —    Y: —    Value: —")
            return

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
            f"X: {event.xdata:.3f} mm    Y: {event.ydata:.3f} mm    "
            f"{parameter}: {value_text}"
        )

    def _plot_clicked(self, event) -> None:
        if (
            self._line_profile_selecting
            and self.ax is not None
            and event.inaxes is self.ax
            and event.xdata is not None
            and event.ydata is not None
        ):
            self._line_profile_map_click(float(event.xdata), float(event.ydata))
            return

        if (
            self._point_statistics_active
            and self.ax is not None
            and event.inaxes is self.ax
        ):
            self._point_statistics_map_click(event)
            return

        if self._title_artist is not None:
            try:
                contains, _ = self._title_artist.contains(event)
            except Exception:
                contains = False
            if contains:
                self._edit_title()
                return

        if self._colorbar_label_artist is not None:
            try:
                contains, _ = self._colorbar_label_artist.contains(event)
            except Exception:
                contains = False
            if contains:
                self._edit_colorbar_label()
                return

        plot_type = self.plot_type_combo.currentText()
        if self.ax is not None and plot_type in SPATIAL_PLOT_TYPES:
            tick_labels = list(self.ax.get_xticklabels()) + list(self.ax.get_yticklabels())
            for label in tick_labels:
                try:
                    contains, _ = label.contains(event)
                except Exception:
                    contains = False
                if contains:
                    self._edit_axis_options()
                    return

        # 3D point picking is deliberately left to the table because projected
        # screen coordinates are ambiguous. All 2D spatial maps remain clickable.
        if plot_type in {"Histogram", "3D Surface"}:
            return
        if self.ax is None or event.inaxes is not self.ax:
            return

        best = None
        parameter = self.parameter_combo.currentText()
        for row_index, row in self.data.iterrows():
            value = pd.to_numeric(pd.Series([row.get(parameter)]), errors="coerce").iloc[0]
            if pd.isna(value):
                continue
            px, py = self.ax.transData.transform((float(row["X"]), float(row["Y"])))
            distance = ((event.x - px) ** 2 + (event.y - py) ** 2) ** 0.5
            if distance <= 12 and (best is None or distance < best[1]):
                best = (row_index, distance)

        if best is not None:
            self._select_row(best[0])

    def _select_row(self, row_index: int) -> None:
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

        value_text = "—" if pd.isna(parameter_value) else f"{float(parameter_value):.6g}"
        mse_text = "—" if pd.isna(mse) else f"{float(mse):.6g}"

        self.selected_info.setText(
            f"<b>Selected measurement:</b> "
            f"X = {float(row['X']):g} mm, Y = {float(row['Y']):g} mm &nbsp;&nbsp; "
            f"{parameter} = {value_text} &nbsp;&nbsp; MSE = {mse_text}"
        )
        self.toggle_include.setText("Ignore Point" if included else "Enable Point")
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
    # Point statistics
    # ------------------------------------------------------------------
    def _point_statistics_mode_applicable(
        self,
        parameter: str | None = None,
        plot_type: str | None = None,
    ) -> bool:
        parameter = (
            parameter if parameter is not None else self.parameter_combo.currentText()
        )
        plot_type = (
            plot_type if plot_type is not None else self.plot_type_combo.currentText()
        )
        return bool(
            parameter
            and not self.data.empty
            and plot_type in POINT_STATISTICS_PLOT_TYPES
        )

    def _point_statistics_allowed(self) -> bool:
        if not self._point_statistics_mode_applicable():
            return False
        parameter = self.parameter_combo.currentText()
        values = pd.to_numeric(self.data.get(parameter), errors="coerce")
        included = self.data["Included"].astype(bool)
        return bool((included & values.notna()).any())

    def _update_point_statistics_button(self) -> None:
        self.point_statistics_button.setVisible(self._point_statistics_allowed())
        self.point_statistics_button.setText(
            "Cancel Point Statistics"
            if self._point_statistics_active
            else "Point Statistics"
        )

    def _toggle_point_statistics_mode(self) -> None:
        if self._point_statistics_active:
            self._clear_point_statistics(close_window=True, redraw=True)
            return
        if not self._point_statistics_allowed():
            return

        self._clear_line_profile(close_window=True, redraw=False)
        self.selected_row = None
        self.table.blockSignals(True)
        self.table.clearSelection()
        self.table.blockSignals(False)
        self._refresh_selected_info()

        self._point_statistics_rows.clear()
        self._point_statistics_active = True
        self.point_statistics_button.setText("Cancel Point Statistics")
        self.cursor_readout.setText(
            "Point statistics: click a measured point; Ctrl+click adds/removes points."
        )
        self._redraw()

    def _point_statistics_map_click(self, event) -> None:
        if self.ax is None or event.inaxes is not self.ax:
            return

        parameter = self.parameter_combo.currentText()
        best = None
        for row_index, row in self.data.iterrows():
            if not bool(row.get("Included", True)):
                continue
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
                best = (int(row_index), distance)

        if best is None:
            return

        row_index = best[0]
        # Matplotlib's MouseEvent.key is not reliable for Ctrl on every Qt/
        # Windows combination. Read the live Qt keyboard modifiers directly,
        # with the Matplotlib value only as a fallback.
        modifiers = QApplication.keyboardModifiers()
        ctrl = bool(modifiers & Qt.KeyboardModifier.ControlModifier)
        if not ctrl:
            key_text = str(event.key).lower() if event.key is not None else ""
            ctrl = "control" in key_text or "ctrl" in key_text

        if ctrl:
            if row_index in self._point_statistics_rows:
                self._point_statistics_rows.remove(row_index)
            else:
                self._point_statistics_rows.add(row_index)
        else:
            self._point_statistics_rows = {row_index}

        if self._point_statistics_rows and self._point_statistics_window is None:
            self._point_statistics_window = PointStatisticsWindow(self, self)
            self._point_statistics_window.closed.connect(
                self._point_statistics_window_closed
            )

        self._redraw()
        if self._point_statistics_window is not None:
            self._point_statistics_window.show()
            self._point_statistics_window.raise_()
            self._point_statistics_window.activateWindow()

    def _point_statistics_dataframe(self) -> pd.DataFrame:
        valid_rows = [
            row
            for row in sorted(self._point_statistics_rows)
            if 0 <= row < len(self.data) and bool(self.data.iloc[row]["Included"])
        ]
        if not valid_rows:
            return self.data.iloc[0:0].copy()
        return self.data.iloc[valid_rows].copy()

    def _update_point_statistics_window(self) -> None:
        if self._point_statistics_window is None:
            return
        self._point_statistics_window.update_statistics(
            parameter=self.parameter_combo.currentText(),
            selected_data=self._point_statistics_dataframe(),
        )

    def _point_statistics_window_closed(self) -> None:
        self._clear_point_statistics(close_window=False, redraw=True)

    def _clear_point_statistics(
        self,
        *,
        close_window: bool,
        redraw: bool,
    ) -> None:
        window = self._point_statistics_window
        self._point_statistics_window = None
        self._point_statistics_active = False
        self._point_statistics_rows.clear()

        if close_window and window is not None:
            window.blockSignals(True)
            window.close()
            window.blockSignals(False)
            window.deleteLater()

        if hasattr(self, "point_statistics_button"):
            self.point_statistics_button.setText("Point Statistics")
        if redraw and hasattr(self, "canvas"):
            self._redraw()

    # ------------------------------------------------------------------
    # Qualitative interpolated thickness line profile
    # ------------------------------------------------------------------
    def _line_profile_mode_applicable(
        self,
        parameter: str | None = None,
        plot_type: str | None = None,
    ) -> bool:
        parameter = (
            parameter if parameter is not None else self.parameter_combo.currentText()
        )
        plot_type = (
            plot_type if plot_type is not None else self.plot_type_combo.currentText()
        )
        lower = parameter.lower() if parameter else ""
        return (
            plot_type == "Interpolated Map"
            and "thickness" in lower
            and "non-uniformity" not in lower
        )

    def _line_profile_allowed(
        self,
        parameter: str | None = None,
        plot_type: str | None = None,
    ) -> bool:
        # NumPy's .any() returns numpy.bool_, but Qt methods such as
        # QWidget.setVisible() require a native Python bool.
        return bool(
            self._line_profile_mode_applicable(parameter, plot_type)
            and self.map_data is not None
            and np.isfinite(self.map_data.z_grid).any()
        )

    def _update_line_profile_button(self) -> None:
        self.line_profile_button.setVisible(self._line_profile_allowed())
        self.line_profile_button.setText(
            "Cancel Profile" if self._line_profile_selecting else "Line Profile"
        )

    def _toggle_line_profile_mode(self) -> None:
        if not self._line_profile_allowed():
            return

        if self._line_profile_selecting:
            self._clear_line_profile(close_window=True, redraw=True)
            return

        self._clear_point_statistics(close_window=True, redraw=False)
        self._clear_line_profile(close_window=True, redraw=False)
        self._line_profile_selecting = True
        self.line_profile_button.setText("Cancel Profile")
        self.cursor_readout.setText("Line profile: click point A on the map.")

    def _line_profile_map_click(self, x: float, y: float) -> None:
        if self.map_data is None:
            return

        xmin = float(np.min(self.map_data.x_axis))
        xmax = float(np.max(self.map_data.x_axis))
        ymin = float(np.min(self.map_data.y_axis))
        ymax = float(np.max(self.map_data.y_axis))
        if not (xmin <= x <= xmax and ymin <= y <= ymax):
            self.cursor_readout.setText(
                "Line profile: select a point inside the interpolated data area."
            )
            return

        if self._line_profile_start is None:
            self._line_profile_start = (x, y)
            self.cursor_readout.setText("Line profile: click point B on the map.")
            self._redraw()
            return

        self._line_profile_end = (x, y)
        if np.allclose(self._line_profile_start, self._line_profile_end):
            self._line_profile_end = None
            self.cursor_readout.setText(
                "Line profile: point B must differ from point A."
            )
            return

        self._line_profile_selecting = False
        self.line_profile_button.setText("Line Profile")

        if self._line_profile_window is None:
            self._line_profile_window = LineProfileWindow(self, self)
            self._line_profile_window.closed.connect(
                self._line_profile_window_closed
            )

        self._redraw()
        self._line_profile_window.show()
        self._line_profile_window.raise_()
        self._line_profile_window.activateWindow()

    def _sample_line_profile(self):
        if (
            self.map_data is None
            or self._line_profile_start is None
            or self._line_profile_end is None
        ):
            return None

        start = self._line_profile_start
        end = self._line_profile_end
        length = float(np.hypot(end[0] - start[0], end[1] - start[1]))
        if length <= 0:
            return None

        count = 250
        x_values = np.linspace(start[0], end[0], count)
        y_values = np.linspace(start[1], end[1], count)
        distance = np.linspace(0.0, length, count)

        interpolator = RegularGridInterpolator(
            (self.map_data.y_axis, self.map_data.x_axis),
            self.map_data.z_grid,
            bounds_error=False,
            fill_value=np.nan,
        )
        values = interpolator(np.column_stack([y_values, x_values]))
        return distance, np.asarray(values, dtype=float)

    def _draw_line_profile_overlay(self) -> None:
        if (
            not self._line_profile_allowed()
            or self.ax is None
            or self._line_profile_start is None
        ):
            return

        start = self._line_profile_start
        if self._line_profile_end is None:
            marker = self.ax.scatter(
                [start[0]], [start[1]], s=70, c="#1565C0", zorder=20
            )
            label = self.ax.text(
                start[0],
                start[1],
                "  A",
                color="#1565C0",
                fontweight="bold",
                va="bottom",
                zorder=21,
            )
            self._profile_overlay_artists.extend([marker, label])
            return

        end = self._line_profile_end
        line, = self.ax.plot(
            [start[0], end[0]],
            [start[1], end[1]],
            color="#1565C0",
            linewidth=2.2,
            zorder=20,
        )
        markers = self.ax.scatter(
            [start[0], end[0]],
            [start[1], end[1]],
            s=70,
            c="#1565C0",
            zorder=21,
        )
        label_a = self.ax.text(
            start[0],
            start[1],
            "  A",
            color="#1565C0",
            fontweight="bold",
            va="bottom",
            zorder=22,
        )
        label_b = self.ax.text(
            end[0],
            end[1],
            "  B",
            color="#1565C0",
            fontweight="bold",
            va="bottom",
            zorder=22,
        )
        self._profile_overlay_artists.extend(
            [line, markers, label_a, label_b]
        )

    def _update_line_profile_window(self) -> None:
        if (
            self._line_profile_window is None
            or self._line_profile_start is None
            or self._line_profile_end is None
            or not self._line_profile_allowed()
        ):
            return

        sampled = self._sample_line_profile()
        if sampled is None:
            return
        distance, values = sampled
        self._line_profile_window.update_profile(
            start=self._line_profile_start,
            end=self._line_profile_end,
            distance=distance,
            values=values,
            parameter=self.parameter_combo.currentText(),
        )

    def _line_profile_window_closed(self) -> None:
        self._line_profile_window = None
        self._line_profile_selecting = False
        self._line_profile_start = None
        self._line_profile_end = None
        self._profile_overlay_artists = []
        self._update_line_profile_button()
        self._redraw()

    def _clear_line_profile(
        self,
        *,
        close_window: bool,
        redraw: bool,
    ) -> None:
        window = self._line_profile_window
        self._line_profile_window = None
        self._line_profile_selecting = False
        self._line_profile_start = None
        self._line_profile_end = None
        self._profile_overlay_artists = []

        if close_window and window is not None:
            window.blockSignals(True)
            window.close()
            window.blockSignals(False)

        self._update_line_profile_button()
        if redraw:
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
        if self.plot_type_combo.currentText() == "Histogram":
            return
        parameter = self.parameter_combo.currentText()
        current = self._custom_colorbar_label or parameter
        new_label, ok = QInputDialog.getText(
            self, "Colorbar Label", "Colorbar label:", text=current
        )
        if ok and new_label.strip():
            self._custom_colorbar_label = new_label.strip()
            self._redraw()

    def _edit_axis_options(self) -> None:
        AxisOptionsDialog(self, self).exec()

    def _show_plot_options(self) -> None:
        PlotOptionsDialog(self, self).exec()

    def _reset_plot_settings(self) -> None:
        if not self.data.empty and "Included" in self.data.columns:
            self.data.loc[:, "Included"] = True

        self.selected_row = None
        self._custom_title = None
        self._custom_colorbar_label = None
        self._x_tick_spacing = None
        self._y_tick_spacing = None
        self._x_range = None
        self._y_range = None
        self._export_include_markers = True
        self._clear_line_profile(close_window=True, redraw=False)

        self.table.blockSignals(True)
        self.table.clearSelection()
        self.table.blockSignals(False)

        self.plot_type_combo.blockSignals(True)
        self.plot_type_combo.setCurrentText("Interpolated Map")
        self.plot_type_combo.blockSignals(False)

        self._refresh_table()
        self._refresh_statistics()
        self._redraw()

    def _save_current_plot(self) -> None:
        if self.data.empty or not self.parameter_combo.currentText() or not self._plot_render_ok:
            QMessageBox.information(
                self, "Save Current Plot", "No valid plot is available to save."
            )
            return

        parameter = self.parameter_combo.currentText()
        plot_type = self.plot_type_combo.currentText()
        suggested = self._plot_filename(parameter, plot_type)
        if self.default_results_folder is not None:
            suggested_path = str(self.default_results_folder / suggested)
        else:
            suggested_path = suggested

        filename, _ = QFileDialog.getSaveFileName(
            self,
            "Save Current Plot",
            suggested_path,
            "PNG image (*.png);;PDF (*.pdf);;SVG (*.svg);;All files (*.*)",
        )
        if filename:
            self._save_figure_clean(Path(filename))

    def _plot_type_available_for_any_parameter(self, plot_type: str) -> bool:
        return any(
            self._can_render_plot(parameter, plot_type)
            for parameter in available_parameters(self.data)
        )

    def _batch_file_count(self, plot_types: list[str]) -> int:
        return sum(
            1
            for parameter in available_parameters(self.data)
            for plot_type in plot_types
            if self._can_render_plot(parameter, plot_type)
        )

    def _can_render_plot(self, parameter: str, plot_type: str) -> bool:
        if self.data.empty:
            return False

        work = self.data[self.data["Included"].astype(bool)].copy()
        numeric = pd.to_numeric(work.get(parameter), errors="coerce")
        if not numeric.notna().any():
            return False

        if plot_type in {"Measured Points + Values", "Histogram"}:
            return True

        if plot_type == "Pixel / Cell Map":
            try:
                all_x = np.array(sorted(set(pd.to_numeric(self.data["X"]))), dtype=float)
                all_y = np.array(sorted(set(pd.to_numeric(self.data["Y"]))), dtype=float)
                if len(all_x) < 2 or len(all_y) < 2:
                    return False
                return bool(
                    np.allclose(np.diff(all_x), np.diff(all_x)[0])
                    and np.allclose(np.diff(all_y), np.diff(all_y)[0])
                )
            except Exception:
                return False

        try:
            map_data = build_map(self.data, parameter)
            return bool(np.isfinite(map_data.z_grid).any())
        except Exception:
            return False

    def _save_all_plots(self, plot_types: list[str]) -> None:
        if self.data.empty or not available_parameters(self.data):
            QMessageBox.information(
                self, "Save All Plots", "No mapping data are available to export."
            )
            return
        if not plot_types:
            return

        start_folder = str(self.default_results_folder) if self.default_results_folder else ""
        parent = QFileDialog.getExistingDirectory(
            self, "Choose Parent Folder for Plot Export", start_folder
        )
        if not parent:
            return

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        export_folder = Path(parent) / f"plots_{stamp}"
        suffix = 1
        while export_folder.exists():
            export_folder = Path(parent) / f"plots_{stamp}_{suffix:02d}"
            suffix += 1
        export_folder.mkdir(parents=True)

        original_parameter = self.parameter_combo.currentText()
        original_plot_type = self.plot_type_combo.currentText()
        original_title = self._custom_title
        original_colorbar = self._custom_colorbar_label
        original_selected = self.selected_row

        saved = 0
        skipped = 0
        try:
            self.selected_row = None
            for parameter in available_parameters(self.data):
                self.parameter_combo.blockSignals(True)
                self.parameter_combo.setCurrentText(parameter)
                self.parameter_combo.blockSignals(False)

                if parameter == original_parameter:
                    self._custom_title = original_title
                    self._custom_colorbar_label = original_colorbar
                else:
                    self._custom_title = None
                    self._custom_colorbar_label = None

                for plot_type in plot_types:
                    if not self._can_render_plot(parameter, plot_type):
                        skipped += 1
                        continue

                    self.plot_type_combo.blockSignals(True)
                    self.plot_type_combo.setCurrentText(plot_type)
                    self.plot_type_combo.blockSignals(False)
                    self._redraw()

                    if not self._plot_render_ok:
                        skipped += 1
                        continue

                    path = export_folder / self._plot_filename(parameter, plot_type)
                    self._save_figure_clean(path)
                    saved += 1
        finally:
            self.parameter_combo.blockSignals(True)
            self.parameter_combo.setCurrentText(original_parameter)
            self.parameter_combo.blockSignals(False)
            self.plot_type_combo.blockSignals(True)
            self.plot_type_combo.setCurrentText(original_plot_type)
            self.plot_type_combo.blockSignals(False)
            self._custom_title = original_title
            self._custom_colorbar_label = original_colorbar
            self.selected_row = original_selected
            self._redraw()
            if original_selected is not None and original_selected < len(self.data):
                self.table.selectRow(original_selected)

        message = f"Saved {saved} plots to:\n{export_folder}"
        if skipped:
            message += f"\n\nSkipped {skipped} unavailable plot(s)."
        QMessageBox.information(self, "Save All Plots", message)

    def _save_figure_clean(self, path: Path) -> None:
        artists = []
        if self._selected_artist is not None:
            artists.append(self._selected_artist)
        artists.extend(self._ignored_artists)
        artists.extend(self._profile_overlay_artists)
        artists.extend(self._point_statistics_artists)

        if not self._export_include_markers:
            artists.extend(self._measurement_marker_artists)

        visibility = []
        seen = set()
        for artist in artists:
            if id(artist) in seen:
                continue
            seen.add(id(artist))
            visibility.append((artist, artist.get_visible()))
            artist.set_visible(False)

        try:
            self.figure.savefig(path, dpi=300, bbox_inches="tight")
        finally:
            for artist, was_visible in visibility:
                artist.set_visible(was_visible)
            self.canvas.draw_idle()

    @staticmethod
    def _plot_filename(parameter: str, plot_type: str) -> str:
        safe_parameter = re.sub(r"[^A-Za-z0-9_-]+", "_", parameter).strip("_")
        safe_type = re.sub(r"[^A-Za-z0-9_-]+", "_", plot_type).strip("_")
        return f"{safe_parameter}_{safe_type}.png"
