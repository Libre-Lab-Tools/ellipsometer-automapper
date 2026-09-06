"""Measurement page with progress grid and dummy live data."""

import random

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QLabel, QMessageBox, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from mapping_model import MappingModel
from progress_grid import ProgressGrid


class MeasurementPage(QWidget):
    def __init__(self, model: MappingModel, parent=None) -> None:
        super().__init__(parent)
        self.model = model
        self.selected_index: int | None = None
        self.sequence_position = 0
        self.retake_mode = False
        self._build_ui()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._finish_dummy_measurement)
        self.refresh_from_model()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        controls = QHBoxLayout()

        self.start_button = QPushButton("Start Measurement")
        self.start_button.clicked.connect(self.start_measurement)
        controls.addWidget(self.start_button)

        self.abort_button = QPushButton("Abort")
        self.abort_button.setEnabled(False)
        self.abort_button.clicked.connect(self.abort_measurement)
        controls.addWidget(self.abort_button)

        self.retake_button = QPushButton("Retake Selected Point")
        self.retake_button.setEnabled(False)
        self.retake_button.clicked.connect(self.retake_selected)
        controls.addWidget(self.retake_button)

        controls.addStretch()
        controls.addWidget(QLabel("Status:"))
        self.status_label = QLabel("READY")
        controls.addWidget(self.status_label)
        root.addLayout(controls)

        self.point_label = QLabel("Point: —")
        root.addWidget(self.point_label)

        self.progress = ProgressGrid(self.model)
        self.progress.point_selected.connect(self._select_point)
        root.addWidget(self.progress, 3)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["Point", "X (mm)", "Y (mm)", "Thickness", "MSE"]
        )
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setMinimumHeight(190)
        root.addWidget(self.table, 2)

    def refresh_from_model(self) -> None:
        if self.model.running:
            return
        self.progress.selected_index = None
        self.progress.refresh()
        self._refresh_table()
        self.status_label.setText("READY")
        self.point_label.setText("Point: —")

    def start_measurement(self) -> None:
        if not self.model.valid_range:
            QMessageBox.warning(self, "Mapping range", "Selected mapping span is too large.")
            return

        self.model.reset_progress()
        self.model.running = True
        self.sequence_position = 0
        self.retake_mode = False
        self.start_button.setEnabled(False)
        self.abort_button.setEnabled(True)
        self.retake_button.setEnabled(False)
        self._start_next_point()

    def abort_measurement(self) -> None:
        self.timer.stop()
        point = self.model.point_by_index(self.model.current_index or -1)
        if point and point.status == "CURRENT":
            point.status = "PENDING"
        self.model.current_index = None
        self.model.running = False
        self.start_button.setEnabled(True)
        self.abort_button.setEnabled(False)
        self.status_label.setText("ABORTED")
        self.point_label.setText("Point: —")
        self.progress.refresh()

    def _start_next_point(self) -> None:
        if self.sequence_position >= len(self.model.points):
            self.model.running = False
            self.model.current_index = None
            self.start_button.setEnabled(True)
            self.abort_button.setEnabled(False)
            self.status_label.setText("COMPLETE")
            self.point_label.setText(f"Complete: {len(self.model.points)} / {len(self.model.points)} points")
            self.progress.refresh()
            return

        point = self.model.points[self.sequence_position]
        self._begin_point(point)

    def _begin_point(self, point) -> None:
        if self.model.stage_type == "Manual Stage":
            answer = QMessageBox.question(
                self,
                "Manual Stage",
                f"Move to X = {point.x_mm:.1f} mm, Y = {point.y_mm:.1f} mm "
                "relative to the mapping center.\n\nSelect Yes when ready to measure.",
            )
            if answer != QMessageBox.StandardButton.Yes:
                if self.retake_mode:
                    self.retake_mode = False
                    self.status_label.setText("READY")
                else:
                    self.abort_measurement()
                return

        point.status = "CURRENT"
        self.model.current_index = point.index
        self.status_label.setText("RETAKING" if self.retake_mode else "MEASURING")
        self.point_label.setText(
            f"Point {point.index} / {len(self.model.points)}   "
            f"Target: X = {point.x_mm:.1f} mm, Y = {point.y_mm:.1f} mm"
        )
        self.progress.refresh()
        self.timer.start(450)

    def _finish_dummy_measurement(self) -> None:
        self.timer.stop()
        point = self.model.point_by_index(self.model.current_index or -1)
        if point is None:
            return

        point.results = {
            "Thickness": round(500 + random.uniform(-35, 35), 2),
            "MSE": round(random.uniform(0.8, 5.0), 3),
        }
        point.status = "MEASURED"
        self.model.current_index = None
        self._refresh_table()
        self.progress.refresh()

        if self.retake_mode:
            self.retake_mode = False
            self.status_label.setText("READY")
            self.retake_button.setEnabled(True)
            return

        self.sequence_position += 1
        self._start_next_point()

    def _refresh_table(self) -> None:
        measured = [p for p in self.model.points if p.results]
        self.table.setRowCount(len(measured))
        for row, point in enumerate(measured):
            values = [
                point.index,
                f"{point.x_mm:.2f}",
                f"{point.y_mm:.2f}",
                point.results.get("Thickness", ""),
                point.results.get("MSE", ""),
            ]
            for col, value in enumerate(values):
                self.table.setItem(row, col, QTableWidgetItem(str(value)))
        self.table.resizeColumnsToContents()

    def _select_point(self, point_index: int) -> None:
        self.selected_index = point_index
        point = self.model.point_by_index(point_index)
        if point is None:
            return
        self.point_label.setText(
            f"Selected point {point.index}: X = {point.x_mm:.1f} mm, "
            f"Y = {point.y_mm:.1f} mm, Status = {point.status}"
        )
        self.retake_button.setEnabled(not self.model.running and point.status == "MEASURED")

    def retake_selected(self) -> None:
        if self.selected_index is None or self.model.running:
            return
        point = self.model.point_by_index(self.selected_index)
        if point is None or point.status != "MEASURED":
            return
        self.retake_mode = True
        self.retake_button.setEnabled(False)
        self._begin_point(point)
