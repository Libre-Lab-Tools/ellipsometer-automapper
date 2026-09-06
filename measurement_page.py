"""
ABSTRACT
--------
Measurement page for the AutoMapper V1 GUI prototype.

The left side contains experiment setup and simplified automatic-stage controls.
The right side contains acquisition controls, the mapping progress grid, and the
live measurement table.

CompleteEASE acquisition is NOT connected in this first prototype.  Start
Measurement generates simulated Thickness/MSE data so the workflow, progress
colors, MSE warning behavior, and Results-page handoff can be tested first.
"""

from __future__ import annotations

import math
import random
import re
from pathlib import Path

import pandas as pd
from PyQt6.QtCore import QTimer, Qt, pyqtSignal, QPointF
from PyQt6.QtGui import QColor, QPainter, QPen, QBrush
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from stage import StageController
from stage_simulator import StageSimulator


INVALID_WINDOWS_CHARS = re.compile(r'[\\/:*?"<>|]')


class ProgressGrid(QWidget):
    """Small interactive XY grid used only for acquisition progress/retakes."""

    point_clicked = pyqtSignal(int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.points: list[dict] = []
        self.selected_index: int | None = None
        self._screen_positions: dict[int, QPointF] = {}
        self.setMinimumHeight(330)

    def set_points(self, points: list[dict]) -> None:
        self.points = points
        self.update()

    def set_selected(self, index: int | None) -> None:
        self.selected_index = index
        self.update()

    def paintEvent(self, event) -> None:
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        if not self.points:
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "No map defined")
            return

        area = self.rect().adjusted(42, 20, -22, -36)
        xs = [p["X"] for p in self.points]
        ys = [p["Y"] for p in self.points]
        xmin, xmax = min(xs), max(xs)
        ymin, ymax = min(ys), max(ys)
        xspan = max(xmax - xmin, 1)
        yspan = max(ymax - ymin, 1)

        painter.setPen(QPen(QColor("#888888"), 1))
        painter.drawLine(area.left(), area.bottom(), area.right(), area.bottom())
        painter.drawLine(area.left(), area.top(), area.left(), area.bottom())

        self._screen_positions.clear()

        for i, point in enumerate(self.points):
            sx = area.left() + (point["X"] - xmin) / xspan * area.width()
            sy = area.bottom() - (point["Y"] - ymin) / yspan * area.height()
            center = QPointF(sx, sy)
            self._screen_positions[i] = center

            status = point.get("status", "PENDING")
            fill = {
                "PENDING": QColor("#b7b7b7"),
                "CURRENT": QColor("#e1b84a"),
                "MEASURED": QColor("#61a875"),
                "WARNING": QColor("#df8b38"),
            }.get(status, QColor("#b7b7b7"))

            pen = QPen(QColor("#202020"), 1.2)
            if i == self.selected_index:
                pen = QPen(QColor("#1f5fa8"), 3)

            painter.setPen(pen)
            painter.setBrush(QBrush(fill))
            painter.drawEllipse(center, 9, 9)

        painter.setPen(QPen(QColor("#444444"), 1))
        painter.drawText(area.center().x() - 18, self.height() - 8, "X (mm)")
        painter.save()
        painter.translate(14, area.center().y() + 20)
        painter.rotate(-90)
        painter.drawText(0, 0, "Y (mm)")
        painter.restore()

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return

        pos = event.position()
        best = None
        for i, center in self._screen_positions.items():
            d = math.hypot(pos.x() - center.x(), pos.y() - center.y())
            if d <= 16 and (best is None or d < best[1]):
                best = (i, d)

        if best is not None:
            self.selected_index = best[0]
            self.point_clicked.emit(best[0])
            self.update()


class MeasurementPage(QWidget):
    """Combined Setup + Measurement screen."""

    dataframe_updated = pyqtSignal(object)

    GRID_OPTIONS = [3, 5, 7, 9]
    SPACING_OPTIONS = [1, 2, 3, 4, 5]
    MAX_MAP_SPAN_MM = 20

    def __init__(self, parent=None) -> None:
        super().__init__(parent)

        self.stage = None
        self.points: list[dict] = []
        self.data = pd.DataFrame()
        self.current_sequence_index = 0
        self.selected_point_index: int | None = None

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._finish_simulated_point)

        self._build_ui()
        self._refresh_ports()
        self._rebuild_grid()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        prototype_note = QLabel(
            "V1 prototype: GUI workflow, stage controls, progress behavior and "
            "Results interaction are functional. CompleteEASE acquisition is "
            "still simulated."
        )
        prototype_note.setWordWrap(True)
        prototype_note.setStyleSheet("color: #666666;")
        root.addWidget(prototype_note)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(splitter, 1)

        # --------------------------- LEFT: setup ------------------------
        left = QWidget()
        left_layout = QVBoxLayout(left)

        setup_group = QGroupBox("Measurement Setup")
        self.setup_group = setup_group
        form = QFormLayout(setup_group)

        self.recipe_combo = QComboBox()
        self.recipe_combo.addItems(["Example Recipe", "Recipe connection pending"])
        form.addRow("Recipe:", self.recipe_combo)

        self.map_name = QLineEdit("Sample01")
        self.map_name.textChanged.connect(self._validate_setup)
        form.addRow("Map name:", self.map_name)

        save_row = QHBoxLayout()
        self.save_location = QLineEdit()
        self.save_location.setPlaceholderText("Choose experiment parent folder")
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._choose_save_location)
        save_row.addWidget(self.save_location, 1)
        save_row.addWidget(browse)
        form.addRow("Save location:", save_row)

        self.grid_combo = QComboBox()
        for n in self.GRID_OPTIONS:
            self.grid_combo.addItem(f"{n} × {n}", n)
        self.grid_combo.setCurrentIndex(1)
        self.grid_combo.currentIndexChanged.connect(self._rebuild_grid)
        form.addRow("Grid:", self.grid_combo)

        self.spacing_combo = QComboBox()
        for spacing in self.SPACING_OPTIONS:
            self.spacing_combo.addItem(f"{spacing} mm", spacing)
        self.spacing_combo.setCurrentIndex(1)
        self.spacing_combo.currentIndexChanged.connect(self._rebuild_grid)
        form.addRow("Spacing:", self.spacing_combo)

        self.stage_type = QComboBox()
        self.stage_type.addItems([
            "Manual Stage",
            "Low-Profile Automatic XY Stage",
        ])
        self.stage_type.currentTextChanged.connect(self._stage_type_changed)
        form.addRow("Stage:", self.stage_type)

        self.span_label = QLabel()
        form.addRow("Mapping span:", self.span_label)

        self.points_label = QLabel()
        form.addRow("Points:", self.points_label)

        self.validation_label = QLabel()
        self.validation_label.setWordWrap(True)
        form.addRow("", self.validation_label)

        left_layout.addWidget(setup_group)

        # Optional MSE acquisition flag.
        qc_group = QGroupBox("Measurement Quality")
        qc_layout = QFormLayout(qc_group)
        self.flag_mse = QCheckBox("Flag high MSE")
        self.flag_mse.setChecked(False)
        qc_layout.addRow(self.flag_mse)

        self.max_mse = QDoubleSpinBox()
        self.max_mse.setRange(0.01, 100000.0)
        self.max_mse.setValue(10.0)
        self.max_mse.setDecimals(2)
        qc_layout.addRow("Maximum MSE:", self.max_mse)
        left_layout.addWidget(qc_group)

        # Automatic stage block.
        self.stage_group = QGroupBox("Automatic XY Stage")
        stage_layout = QVBoxLayout(self.stage_group)

        instruction = QLabel(
            "<b>Before mapping:</b> move the sample stage so that the "
            "ellipsometer beam is aligned with the center of the desired "
            "sampling area, then select <b>Set Mapping Center</b>."
        )
        instruction.setWordWrap(True)
        stage_layout.addWidget(instruction)

        port_row = QHBoxLayout()
        self.port_combo = QComboBox()
        self.connect_button = QPushButton("Connect")
        self.connect_button.clicked.connect(self._toggle_stage_connection)
        port_row.addWidget(self.port_combo, 1)
        port_row.addWidget(self.connect_button)
        stage_layout.addLayout(port_row)

        jog = QGridLayout()
        self.up = QPushButton("↑")
        self.down = QPushButton("↓")
        self.left_btn = QPushButton("←")
        self.right_btn = QPushButton("→")
        jog.addWidget(self.up, 0, 1)
        jog.addWidget(self.left_btn, 1, 0)
        jog.addWidget(self.right_btn, 1, 2)
        jog.addWidget(self.down, 2, 1)

        self.jog_step = QDoubleSpinBox()
        self.jog_step.setRange(0.01, 10.0)
        self.jog_step.setDecimals(2)
        self.jog_step.setValue(0.10)
        self.jog_step.setSuffix(" mm")
        jog.addWidget(QLabel("Step:"), 3, 0)
        jog.addWidget(self.jog_step, 3, 1, 1, 2)
        stage_layout.addLayout(jog)

        self.set_center = QPushButton("Set Mapping Center")
        self.set_center.clicked.connect(self._set_mapping_center)
        stage_layout.addWidget(self.set_center)

        self.stage_status = QLabel("DISCONNECTED")
        stage_layout.addWidget(self.stage_status)

        self.up.clicked.connect(lambda: self._jog("Y", +1))
        self.down.clicked.connect(lambda: self._jog("Y", -1))
        self.left_btn.clicked.connect(lambda: self._jog("X", -1))
        self.right_btn.clicked.connect(lambda: self._jog("X", +1))

        left_layout.addWidget(self.stage_group)
        left_layout.addStretch()

        splitter.addWidget(left)

        # ----------------------- RIGHT: measurement ---------------------
        right = QWidget()
        right_layout = QVBoxLayout(right)

        run_row = QHBoxLayout()
        self.start_button = QPushButton("Start Measurement")
        self.abort_button = QPushButton("Abort")
        self.abort_button.setEnabled(False)
        self.start_button.clicked.connect(self._start_measurement)
        self.abort_button.clicked.connect(self._abort_measurement)
        run_row.addWidget(self.start_button)
        run_row.addWidget(self.abort_button)
        run_row.addStretch()
        self.measurement_status = QLabel("READY")
        run_row.addWidget(QLabel("Status:"))
        run_row.addWidget(self.measurement_status)
        right_layout.addLayout(run_row)

        self.current_label = QLabel("No measurement running.")
        right_layout.addWidget(self.current_label)

        self.progress = ProgressGrid()
        self.progress.point_clicked.connect(self._progress_point_clicked)
        right_layout.addWidget(self.progress, 3)

        retake_row = QHBoxLayout()
        self.retake_button = QPushButton("Retake Selected Point")
        self.retake_button.setEnabled(False)
        self.retake_button.clicked.connect(self._retake_selected)
        retake_row.addWidget(self.retake_button)
        retake_row.addStretch()
        right_layout.addLayout(retake_row)

        self.table = QTableWidget()
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setMinimumHeight(210)
        right_layout.addWidget(self.table, 2)

        splitter.addWidget(right)
        splitter.setSizes([370, 720])

        self._stage_type_changed()

    # ------------------------------------------------------------------
    # Mapping definition
    # ------------------------------------------------------------------
    def _rebuild_grid(self) -> None:
        n = int(self.grid_combo.currentData())
        spacing = int(self.spacing_combo.currentData())
        half = (n - 1) // 2

        points: list[dict] = []
        for row in range(n):
            y = (row - half) * spacing
            columns = range(n) if row % 2 == 0 else range(n - 1, -1, -1)
            for column in columns:
                x = (column - half) * spacing
                points.append({"X": x, "Y": y, "status": "PENDING"})

        self.points = points
        self.progress.set_points(points)

        span = (n - 1) * spacing
        self.span_label.setText(f"{span} × {span} mm")
        self.points_label.setText(str(n * n))

        self._validate_setup()

    def _validate_setup(self) -> bool:
        if not hasattr(self, "grid_combo"):
            return False

        name = self.map_name.text().strip()
        span = (int(self.grid_combo.currentData()) - 1) * int(
            self.spacing_combo.currentData()
        )

        problems = []
        if not name:
            problems.append("Map name cannot be empty.")
        elif INVALID_WINDOWS_CHARS.search(name):
            problems.append('Map name contains an invalid Windows filename character.')

        if span > self.MAX_MAP_SPAN_MM:
            problems.append(
                f"Mapping span exceeds the {self.MAX_MAP_SPAN_MM} × "
                f"{self.MAX_MAP_SPAN_MM} mm V1 limit."
            )

        if problems:
            self.validation_label.setText("✕ " + " ".join(problems))
            self.validation_label.setStyleSheet("color: #b33939;")
            self.start_button.setEnabled(False)
            return False

        self.validation_label.setText("✓ Mapping definition is valid.")
        self.validation_label.setStyleSheet("color: #2769b0;")
        self.start_button.setEnabled(not self.timer.isActive())
        return True

    def _choose_save_location(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Choose experiment parent folder")
        if folder:
            self.save_location.setText(folder)

    # ------------------------------------------------------------------
    # Stage controls
    # ------------------------------------------------------------------
    def _stage_type_changed(self) -> None:
        automatic = self.stage_type.currentText().startswith("Low-Profile")
        self.stage_group.setVisible(automatic)

    def _refresh_ports(self) -> None:
        self.port_combo.clear()
        self.port_combo.addItem("SIMULATOR")
        for port in StageController.available_ports():
            self.port_combo.addItem(port)

    def _toggle_stage_connection(self) -> None:
        try:
            if self.stage is not None and self.stage.connected:
                self.stage.disconnect()
                self.stage = None
                self.connect_button.setText("Connect")
                self.stage_status.setText("DISCONNECTED")
                return

            port = self.port_combo.currentText()
            self.stage = StageSimulator() if port == "SIMULATOR" else StageController()
            self.stage.connect(port)
            self.connect_button.setText("Disconnect")
            self.stage_status.setText("IDLE")
        except Exception as exc:
            QMessageBox.critical(self, "Stage connection", str(exc))

    def _jog(self, axis: str, direction: int) -> None:
        if self.stage is None or not self.stage.connected:
            QMessageBox.information(self, "Stage", "Connect the stage first.")
            return
        try:
            self.stage.move_relative(axis, direction * self.jog_step.value())
            self.stage_status.setText(self.stage.status)
        except Exception as exc:
            QMessageBox.warning(self, "Stage", str(exc))

    def _set_mapping_center(self) -> None:
        if self.stage is None or not self.stage.connected:
            QMessageBox.information(self, "Stage", "Connect the stage first.")
            return
        try:
            self.stage.set_origin()
            self.stage_status.setText("MAPPING CENTER SET")
        except Exception as exc:
            QMessageBox.warning(self, "Stage", str(exc))

    # ------------------------------------------------------------------
    # Simulated acquisition for GUI testing
    # ------------------------------------------------------------------
    def _start_measurement(self) -> None:
        if not self._validate_setup():
            return

        for p in self.points:
            p["status"] = "PENDING"

        self.data = pd.DataFrame(columns=["X", "Y", "Thickness (nm)", "MSE", "Included"])
        self.current_sequence_index = 0
        self.selected_point_index = None

        self.setup_group.setEnabled(False)
        self.start_button.setEnabled(False)
        self.abort_button.setEnabled(True)
        self.retake_button.setEnabled(False)

        self._start_next_point()

    def _start_next_point(self) -> None:
        if self.current_sequence_index >= len(self.points):
            self._finish_mapping()
            return

        point = self.points[self.current_sequence_index]
        point["status"] = "CURRENT"
        self.measurement_status.setText("MEASURING")
        self.current_label.setText(
            f"Point {self.current_sequence_index + 1} / {len(self.points)}   "
            f"X = {point['X']} mm, Y = {point['Y']} mm"
        )
        self.progress.update()

        # Placeholder for CompleteEASE.  The real workflow will wait for the
        # recipe response instead of this timer.
        self.timer.start(350)

    def _finish_simulated_point(self) -> None:
        self.timer.stop()
        point = self.points[self.current_sequence_index]

        # Smooth synthetic map + small noise, useful for testing interpolation.
        x, y = point["X"], point["Y"]
        thickness = 850 + 3.0 * x - 2.0 * y + random.uniform(-6, 6)
        mse = max(0.2, 2.5 + 0.12 * abs(x) + random.uniform(-0.8, 1.4))

        row = pd.DataFrame([{
            "X": x,
            "Y": y,
            "Thickness (nm)": round(thickness, 3),
            "MSE": round(mse, 3),
            "Included": True,
        }])
        self.data = pd.concat([self.data, row], ignore_index=True)

        if self.flag_mse.isChecked() and mse > self.max_mse.value():
            point["status"] = "WARNING"
        else:
            point["status"] = "MEASURED"

        self.current_sequence_index += 1
        self._refresh_table()
        self.progress.update()
        self.dataframe_updated.emit(self.data.copy())
        self._start_next_point()

    def _finish_mapping(self) -> None:
        self.timer.stop()
        self.measurement_status.setText("COMPLETE")
        self.current_label.setText(f"Complete: {len(self.points)} points measured.")
        self.setup_group.setEnabled(True)
        self.abort_button.setEnabled(False)
        self._validate_setup()
        self.dataframe_updated.emit(self.data.copy())

    def _abort_measurement(self) -> None:
        self.timer.stop()
        if self.current_sequence_index < len(self.points):
            current = self.points[self.current_sequence_index]
            if current["status"] == "CURRENT":
                current["status"] = "PENDING"

        self.measurement_status.setText("ABORTED")
        self.setup_group.setEnabled(True)
        self.abort_button.setEnabled(False)
        self._validate_setup()
        self.progress.update()

    def _progress_point_clicked(self, index: int) -> None:
        self.selected_point_index = index
        point = self.points[index]
        self.current_label.setText(
            f"Selected: X = {point['X']} mm, Y = {point['Y']} mm, "
            f"status = {point['status']}"
        )
        self.retake_button.setEnabled(
            not self.timer.isActive() and point["status"] in ("MEASURED", "WARNING")
        )

    def _retake_selected(self) -> None:
        if self.selected_point_index is None:
            return

        point = self.points[self.selected_point_index]
        x, y = point["X"], point["Y"]

        # Replace the selected simulated result.
        mask = (self.data["X"] == x) & (self.data["Y"] == y)
        thickness = 850 + 3.0 * x - 2.0 * y + random.uniform(-6, 6)
        mse = max(0.2, 2.5 + 0.12 * abs(x) + random.uniform(-0.8, 1.4))

        if mask.any():
            self.data.loc[mask, "Thickness (nm)"] = round(thickness, 3)
            self.data.loc[mask, "MSE"] = round(mse, 3)
            self.data.loc[mask, "Included"] = True

        point["status"] = (
            "WARNING"
            if self.flag_mse.isChecked() and mse > self.max_mse.value()
            else "MEASURED"
        )

        self._refresh_table()
        self.progress.update()
        self.dataframe_updated.emit(self.data.copy())

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
            for col_i, column in enumerate(columns):
                value = row[column]
                if isinstance(value, float):
                    text = f"{value:.4g}"
                else:
                    text = str(value)
                self.table.setItem(row_i, col_i, QTableWidgetItem(text))

        self.table.resizeColumnsToContents()
