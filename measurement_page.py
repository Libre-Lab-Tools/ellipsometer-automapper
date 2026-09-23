"""
ABSTRACT
--------
Measurement workspace for Ellipsometer AutoMapper.

This page intentionally combines experiment setup and acquisition in one
workspace. The left side defines the mapping and provides the simplified XY
stage controls. The right side shows measurement progress and the live table.

This revision improves usability by:
- making the automatic XY stage the default,
- moving optional MSE quality settings into a pop-out dialog,
- showing coordinate tick labels on the progress grid,
- linking point selection between the progress grid and live table,
- removing the analysis-only "Included" field from the Measurement table,
- showing X/Y units explicitly in the displayed table,
- and keeping validation silent unless an actual setup error exists.

CompleteEASE access is provided through a replaceable real/simulator black box.
"""

from __future__ import annotations

import math
import re

import pandas as pd
from PyQt6.QtCore import QTimer, Qt, pyqtSignal, QPointF
from PyQt6.QtGui import QColor, QPainter, QPen, QBrush
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QAbstractSpinBox,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app_config import AppConfig
from completeease import CompleteEASEClient
from completeease_simulator import CompleteEASESimulator
from mapping_runner import (
    MappingRunner,
    PointAcquisitionThread,
    create_experiment_folder,
)
from stage import StageController
from stage_simulator import StageSimulator


INVALID_WINDOWS_CHARS = re.compile(r'[\\/:*?"<>|]')


def _change_spinbox_value(control: QDoubleSpinBox, direction: int) -> None:
    """Reliably change a spin-box value without native Qt arrow behavior."""
    step = float(control.singleStep()) * (1 if direction > 0 else -1)
    value = min(max(float(control.value()) + step, control.minimum()), control.maximum())
    control.setValue(value)


def _connect_spin_buttons(
    up_button: QPushButton,
    down_button: QPushButton,
    control: QDoubleSpinBox,
) -> None:
    """Connect explicit ▲/▼ buttons using direct value changes."""
    up_button.clicked.connect(
        lambda _checked=False, c=control: _change_spinbox_value(c, +1)
    )
    down_button.clicked.connect(
        lambda _checked=False, c=control: _change_spinbox_value(c, -1)
    )


class MeasurementQualityDialog(QDialog):
    """Small optional dialog for acquisition-time quality flags."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Measurement Quality")
        self.setModal(True)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.flag_mse = QCheckBox("Flag high MSE")
        self.flag_mse.setChecked(False)

        # Make the checkbox state obvious on the light application theme.
        # The unchecked box has a dark outline; the checked box is filled blue.
        self.flag_mse.setStyleSheet("""
            QCheckBox::indicator {
                width: 18px;
                height: 18px;
                border: 1.5px solid #303030;
                border-radius: 3px;
                background-color: white;
            }
            QCheckBox::indicator:checked {
                border: 1.5px solid #303030;
                background-color: #1976D2;
            }
        """)
        form.addRow(self.flag_mse)

        self.max_mse = QDoubleSpinBox()
        self.max_mse.setRange(0.01, 100000.0)
        self.max_mse.setValue(10.0)
        self.max_mse.setDecimals(2)
        self.max_mse.setSingleStep(0.10)

        # Use explicit arrow buttons instead of the platform-native spinbox
        # buttons. This avoids the Windows/Qt issue where the upper arrow was
        # visible but did not reliably respond to clicks.
        self.max_mse.setButtonSymbols(
            QAbstractSpinBox.ButtonSymbols.NoButtons
        )

        mse_control = QWidget()
        mse_layout = QHBoxLayout(mse_control)
        mse_layout.setContentsMargins(0, 0, 0, 0)
        mse_layout.setSpacing(4)
        mse_layout.addWidget(self.max_mse, 1)

        up_button = QPushButton("▲")
        down_button = QPushButton("▼")

        for button in (up_button, down_button):
            button.setFixedSize(30, 30)
            button.setStyleSheet("""
                QPushButton {
                    padding: 0px;
                    font-size: 12px;
                    font-weight: 700;
                    background-color: #eeeeee;
                    border: 1px solid #9a9a9a;
                    border-radius: 3px;
                }
                QPushButton:hover {
                    background-color: #dddddd;
                }
                QPushButton:pressed {
                    background-color: #cccccc;
                }
            """)

        _connect_spin_buttons(up_button, down_button, self.max_mse)

        mse_layout.addWidget(up_button)
        mse_layout.addWidget(down_button)

        form.addRow("Maximum MSE:", mse_control)

        layout.addLayout(form)

        note = QLabel(
            "Measurements above the selected MSE limit are flagged in orange. "
            "They are not automatically removed from the data."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: #666666;")
        layout.addWidget(note)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).clicked.connect(self.accept)
        layout.addWidget(buttons)


class ManualStageDialog(QDialog):
    """
    Keyboard-friendly prompt used for manual-stage mapping.

    The default action is Measure. Left/Right changes the highlighted action
    and Enter activates it, which is useful when operating inside a glovebox.
    """

    MEASURE = 1
    SKIP = 2
    ABORT = 0

    def __init__(
        self,
        x: int,
        y: int,
        *,
        absolute_x: float | None = None,
        absolute_y: float | None = None,
        allow_skip: bool = True,
        retake: bool = False,
        parent=None,
    ) -> None:
        super().__init__(parent)

        self.setWindowTitle("Manual Stage")
        self.setModal(True)
        self._action = self.ABORT

        layout = QVBoxLayout(self)

        action_word = "retake" if retake else "measure"

        if absolute_x is not None and absolute_y is not None:
            coordinate_text = (
                "<span style='font-size:15px; font-weight:600;'>Map position</span><br>"
                f"<span style='font-size:30px; font-weight:700;'>"
                f"X = {x:.1f} mm&nbsp;&nbsp;&nbsp;Y = {y:.1f} mm</span><br><br>"
                "<span style='font-size:15px; font-weight:600;'>"
                "Absolute stage position</span><br>"
                f"<span style='font-size:30px; font-weight:700;'>"
                f"X = {absolute_x:.1f} mm&nbsp;&nbsp;&nbsp;"
                f"Y = {absolute_y:.1f} mm</span>"
            )
            instruction_text = (
                f"Move the stage to the absolute position above, then select "
                f"Measure to {action_word} this point."
            )
        else:
            coordinate_text = (
                "<span style='font-size:15px; font-weight:600;'>Map position</span><br>"
                f"<span style='font-size:30px; font-weight:700;'>"
                f"X = {x:.1f} mm&nbsp;&nbsp;&nbsp;Y = {y:.1f} mm</span>"
            )
            instruction_text = (
                f"Move the sample to the position above, then select Measure "
                f"to {action_word} this point."
            )

        coordinates = QLabel(coordinate_text)
        coordinates.setTextFormat(Qt.TextFormat.RichText)
        coordinates.setWordWrap(True)
        coordinates.setStyleSheet(
            "padding: 8px 2px 4px 2px;"
        )
        layout.addWidget(coordinates)

        message = QLabel(instruction_text)
        message.setWordWrap(True)
        layout.addWidget(message)

        keyboard_hint = QLabel(
            "Keyboard: Left / Right selects an action. Enter activates it."
        )
        keyboard_hint.setStyleSheet("color: #666666;")
        layout.addWidget(keyboard_hint)

        button_row = QHBoxLayout()

        self.measure_button = QPushButton("Measure")
        self.measure_button.setMinimumSize(120, 42)
        self.measure_button.setDefault(True)
        self.measure_button.setAutoDefault(True)
        self.measure_button.setStyleSheet("""
            QPushButton {
                background-color: #4FAE67;
                color: white;
                font-weight: 700;
                border: 1px solid #3F9255;
                border-radius: 5px;
            }
            QPushButton:focus {
                border: 3px solid #1557A0;
            }
        """)
        self.measure_button.clicked.connect(
            lambda: self._finish(self.MEASURE)
        )
        button_row.addWidget(self.measure_button)

        # Keep Measure visually separate from the less common actions.
        button_row.addStretch(1)

        self.buttons = [self.measure_button]

        if allow_skip:
            self.skip_button = QPushButton("Skip Point")
            self.skip_button.setMinimumSize(110, 42)
            self.skip_button.setStyleSheet("""
                QPushButton {
                    background-color: #E6A23C;
                    color: white;
                    font-weight: 700;
                    border: 1px solid #C88729;
                    border-radius: 5px;
                }
                QPushButton:focus {
                    border: 3px solid #1557A0;
                }
            """)
            self.skip_button.clicked.connect(
                lambda: self._finish(self.SKIP)
            )
            button_row.addWidget(self.skip_button)
            self.buttons.append(self.skip_button)

        self.abort_button = QPushButton("Cancel" if retake else "Abort")
        self.abort_button.setMinimumSize(95, 42)
        self.abort_button.setStyleSheet("""
            QPushButton {
                background-color: #D9534F;
                color: white;
                font-weight: 700;
                border: 1px solid #BD4541;
                border-radius: 5px;
            }
            QPushButton:focus {
                border: 3px solid #1557A0;
            }
        """)
        self.abort_button.clicked.connect(
            lambda: self._finish(self.ABORT)
        )
        button_row.addWidget(self.abort_button)
        self.buttons.append(self.abort_button)

        layout.addLayout(button_row)

        self._focus_index = 0
        self.measure_button.setFocus()

    def _finish(self, action: int) -> None:
        self._action = action
        self.done(action)

    def keyPressEvent(self, event) -> None:
        key = event.key()

        if key == Qt.Key.Key_Right:
            self._focus_index = (self._focus_index + 1) % len(self.buttons)
            self.buttons[self._focus_index].setFocus()
            return

        if key == Qt.Key.Key_Left:
            self._focus_index = (self._focus_index - 1) % len(self.buttons)
            self.buttons[self._focus_index].setFocus()
            return

        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.buttons[self._focus_index].click()
            return

        super().keyPressEvent(event)

    def reject(self) -> None:
        # Closing the prompt is treated like Abort/Cancel, never like Measure.
        self._finish(self.ABORT)


class SinglePointDialog(QDialog):
    """Repeated single-point acquisition controls.

    Automatic-stage mode uses jog arrows and reports the current relative stage
    coordinate. Manual-stage mode accepts relative coordinates and, when the
    Measurement page is already tracking absolute coordinates, shows linked
    absolute-coordinate fields without duplicating the tracking option itself.
    """

    jog_requested = pyqtSignal(str, int, float)
    measure_requested = pyqtSignal(float, float)
    closed = pyqtSignal()

    def __init__(
        self,
        *,
        automatic: bool,
        track_absolute: bool = False,
        center_x: float = 0.0,
        center_y: float = 0.0,
        current_x: float = 0.0,
        current_y: float = 0.0,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Single Point Measurement")
        self.setModal(False)
        self.setMinimumWidth(360)

        self.automatic = automatic
        self.track_absolute = track_absolute
        self.center_x = float(center_x)
        self.center_y = float(center_y)
        self._syncing_coordinates = False

        layout = QVBoxLayout(self)

        note = QLabel(
            "Use the currently selected recipe and measurement settings. "
            "Each successful point is added to the current experiment."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: #666666;")
        layout.addWidget(note)

        if automatic:
            self._build_automatic_controls(layout, current_x, current_y)
        else:
            self._build_manual_controls(layout)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        self.measure_button = QPushButton("Measure Point")
        self.measure_button.setMinimumSize(125, 40)
        self.measure_button.setStyleSheet("""
            QPushButton {
                background-color: #4FAE67;
                color: white;
                font-weight: 700;
                border: 1px solid #3F9255;
                border-radius: 5px;
            }
            QPushButton:hover { background-color: #449D5B; }
            QPushButton:disabled {
                background-color: #A9CFB3;
                color: #F3F3F3;
            }
        """)
        self.measure_button.clicked.connect(self._emit_measure)
        button_row.addWidget(self.measure_button)

        close = QPushButton("Close")
        close.clicked.connect(self.close)
        button_row.addWidget(close)
        layout.addLayout(button_row)

    def _build_automatic_controls(
        self,
        layout: QVBoxLayout,
        current_x: float,
        current_y: float,
    ) -> None:
        jog = QGridLayout()
        self.up = QPushButton("↑")
        self.down = QPushButton("↓")
        self.left_btn = QPushButton("←")
        self.right_btn = QPushButton("→")
        arrow_style = """
            QPushButton {
                font-family: Arial;
                font-size: 22px;
                font-weight: 900;
                min-width: 46px;
                min-height: 40px;
                padding: 0px;
            }
        """
        for button in (self.up, self.down, self.left_btn, self.right_btn):
            button.setStyleSheet(arrow_style)

        jog.addWidget(self.up, 0, 1)
        jog.addWidget(self.left_btn, 1, 0)
        jog.addWidget(self.right_btn, 1, 2)
        jog.addWidget(self.down, 2, 1)

        self.jog_step = QDoubleSpinBox()
        self.jog_step.setRange(0.01, 10.0)
        self.jog_step.setDecimals(2)
        self.jog_step.setSingleStep(1.00)
        self.jog_step.setValue(2.00)
        self.jog_step.setSuffix(" mm")
        self.jog_step.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)

        step_control = QWidget()
        step_layout = QHBoxLayout(step_control)
        step_layout.setContentsMargins(0, 0, 0, 0)
        step_layout.setSpacing(4)
        step_layout.addWidget(self.jog_step, 1)

        self.jog_step_up = QPushButton("▲")
        self.jog_step_down = QPushButton("▼")
        for button in (self.jog_step_up, self.jog_step_down):
            button.setFixedSize(30, 30)
            button.setStyleSheet("""
                QPushButton {
                    padding: 0px;
                    font-size: 12px;
                    font-weight: 700;
                    background-color: #eeeeee;
                    border: 1px solid #9a9a9a;
                    border-radius: 3px;
                }
                QPushButton:hover { background-color: #dddddd; }
                QPushButton:pressed { background-color: #cccccc; }
            """)
        _connect_spin_buttons(self.jog_step_up, self.jog_step_down, self.jog_step)
        step_layout.addWidget(self.jog_step_up)
        step_layout.addWidget(self.jog_step_down)

        jog.addWidget(QLabel("Jog:"), 3, 0)
        jog.addWidget(step_control, 3, 1, 1, 2)
        layout.addLayout(jog)

        self.current_position = QLabel()
        self.current_position.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.current_position.setStyleSheet(
            "font-size: 14px; font-weight: 600; padding: 8px;"
        )
        layout.addWidget(self.current_position)
        self.set_current_position(current_x, current_y)

        self.up.clicked.connect(lambda: self._emit_jog("Y", +1))
        self.down.clicked.connect(lambda: self._emit_jog("Y", -1))
        self.left_btn.clicked.connect(lambda: self._emit_jog("X", -1))
        self.right_btn.clicked.connect(lambda: self._emit_jog("X", +1))

    def _build_manual_controls(self, layout: QVBoxLayout) -> None:
        message = QLabel(
            "Enter the map coordinate, move the manual stage to that position, "
            "then select Measure Point."
        )
        message.setWordWrap(True)
        layout.addWidget(message)

        form = QFormLayout()
        self._manual_step_buttons: list[QPushButton] = []

        def coordinate_control(label: str) -> tuple[QDoubleSpinBox, QWidget]:
            control = QDoubleSpinBox()
            control.setRange(-1000.0, 1000.0)
            control.setDecimals(2)
            control.setSingleStep(1.0)
            control.setSuffix(" mm")
            control.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)

            wrapper = QWidget()
            row = QHBoxLayout(wrapper)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(4)
            row.addWidget(control, 1)

            up = QPushButton("▲")
            down = QPushButton("▼")
            for button in (up, down):
                button.setFixedSize(30, 30)
                button.setStyleSheet("""
                    QPushButton {
                        padding: 0px;
                        font-size: 12px;
                        font-weight: 700;
                        background-color: #eeeeee;
                        border: 1px solid #9a9a9a;
                        border-radius: 3px;
                    }
                    QPushButton:hover { background-color: #dddddd; }
                    QPushButton:pressed { background-color: #cccccc; }
                """)
            _connect_spin_buttons(up, down, control)
            row.addWidget(up)
            row.addWidget(down)
            self._manual_step_buttons.extend((up, down))
            return control, wrapper

        self.relative_x, relative_x_widget = coordinate_control("Relative X")
        self.relative_y, relative_y_widget = coordinate_control("Relative Y")
        form.addRow("Relative X:", relative_x_widget)
        form.addRow("Relative Y:", relative_y_widget)

        self.absolute_x = None
        self.absolute_y = None
        if self.track_absolute:
            self.absolute_x, absolute_x_widget = coordinate_control("Absolute X")
            self.absolute_y, absolute_y_widget = coordinate_control("Absolute Y")
            form.addRow("Absolute X:", absolute_x_widget)
            form.addRow("Absolute Y:", absolute_y_widget)

            self.relative_x.valueChanged.connect(self._relative_changed)
            self.relative_y.valueChanged.connect(self._relative_changed)
            self.absolute_x.valueChanged.connect(self._absolute_changed)
            self.absolute_y.valueChanged.connect(self._absolute_changed)
            self._relative_changed()

        layout.addLayout(form)

    def _relative_changed(self) -> None:
        if self._syncing_coordinates or self.absolute_x is None or self.absolute_y is None:
            return
        self._syncing_coordinates = True
        try:
            self.absolute_x.setValue(self.center_x + self.relative_x.value())
            self.absolute_y.setValue(self.center_y + self.relative_y.value())
        finally:
            self._syncing_coordinates = False

    def _absolute_changed(self) -> None:
        if self._syncing_coordinates or self.absolute_x is None or self.absolute_y is None:
            return
        self._syncing_coordinates = True
        try:
            self.relative_x.setValue(self.absolute_x.value() - self.center_x)
            self.relative_y.setValue(self.absolute_y.value() - self.center_y)
        finally:
            self._syncing_coordinates = False

    def _emit_jog(self, axis: str, direction: int) -> None:
        self.jog_requested.emit(axis, direction, float(self.jog_step.value()))

    def _emit_measure(self) -> None:
        if self.automatic:
            x, y = self.current_coordinates()
        else:
            x = float(self.relative_x.value())
            y = float(self.relative_y.value())
        self.measure_requested.emit(x, y)

    def current_coordinates(self) -> tuple[float, float]:
        if self.automatic:
            return self._current_x, self._current_y
        return float(self.relative_x.value()), float(self.relative_y.value())

    def set_current_position(self, x: float, y: float) -> None:
        self._current_x = float(x)
        self._current_y = float(y)
        if self.automatic:
            self.current_position.setText(
                f"Current position:  X = {self._current_x:.2f} mm    "
                f"Y = {self._current_y:.2f} mm"
            )

    def set_busy(self, busy: bool) -> None:
        self.measure_button.setEnabled(not busy)
        if self.automatic:
            for widget in (
                self.up, self.down, self.left_btn, self.right_btn,
                self.jog_step, self.jog_step_up, self.jog_step_down,
            ):
                widget.setEnabled(not busy)
        else:
            self.relative_x.setEnabled(not busy)
            self.relative_y.setEnabled(not busy)
            for button in getattr(self, "_manual_step_buttons", []):
                button.setEnabled(not busy)
            if self.absolute_x is not None:
                self.absolute_x.setEnabled(not busy)
                self.absolute_y.setEnabled(not busy)

    def closeEvent(self, event) -> None:
        self.closed.emit()
        super().closeEvent(event)


class ProgressGrid(QWidget):
    """
    Interactive mapping/progress grid.

    The point selection is linked bidirectionally with the Measurement table.
    Coordinate ticks are drawn directly from the requested integer-mm grid.
    """

    point_clicked = pyqtSignal(int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.points: list[dict] = []
        self.selected_index: int | None = None
        self.stage_position: tuple[float, float] | None = None
        self._screen_positions: dict[int, QPointF] = {}
        self.setMinimumHeight(340)

    def set_points(self, points: list[dict]) -> None:
        self.points = points
        self.selected_index = None
        self.update()

    def set_selected(self, index: int | None) -> None:
        self.selected_index = index
        self.update()

    def set_stage_position(
        self,
        x: float | None,
        y: float | None = None,
    ) -> None:
        """Show or hide the automatic-stage relative position marker."""
        if x is None or y is None:
            self.stage_position = None
        else:
            self.stage_position = (float(x), float(y))
        self.update()

    def paintEvent(self, event) -> None:
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        if not self.points and self.stage_position is None:
            painter.drawText(
                self.rect(),
                Qt.AlignmentFlag.AlignCenter,
                "No measurements in the current dataset",
            )
            return

        # Extra left/bottom space is reserved for coordinate tick labels.
        area = self.rect().adjusted(96, 22, -24, -60)

        measurement_xs = sorted(set(float(p["X"]) for p in self.points))
        measurement_ys = sorted(set(float(p["Y"]) for p in self.points))

        extent_xs = list(measurement_xs)
        extent_ys = list(measurement_ys)
        if self.stage_position is not None:
            extent_xs.append(float(self.stage_position[0]))
            extent_ys.append(float(self.stage_position[1]))

        xs = extent_xs or [0.0]
        ys = extent_ys or [0.0]
        tick_xs = measurement_xs or xs
        tick_ys = measurement_ys or ys

        xmin, xmax = min(xs), max(xs)
        ymin, ymax = min(ys), max(ys)
        if math.isclose(xmin, xmax):
            xmin -= 1.0
            xmax += 1.0
        if math.isclose(ymin, ymax):
            ymin -= 1.0
            ymax += 1.0
        xspan = xmax - xmin
        yspan = ymax - ymin

        def screen_x(x):
            return area.left() + (x - xmin) / xspan * area.width()

        def screen_y(y):
            return area.bottom() - (y - ymin) / yspan * area.height()

        # Axes.
        painter.setPen(QPen(QColor("#777777"), 1))
        painter.drawLine(area.left(), area.bottom(), area.right(), area.bottom())
        painter.drawLine(area.left(), area.top(), area.left(), area.bottom())

        # Integer coordinate ticks/labels.
        painter.setPen(QPen(QColor("#444444"), 1))
        for x in tick_xs:
            sx = screen_x(x)
            painter.drawLine(int(sx), area.bottom(), int(sx), area.bottom() + 5)
            painter.drawText(
                int(sx) - 22,
                area.bottom() + 13,
                44,
                22,
                Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
                f"{float(x):g}",
            )

        for y in tick_ys:
            sy = screen_y(y)
            painter.drawLine(area.left() - 5, int(sy), area.left(), int(sy))
            painter.drawText(
                30,
                int(sy) - 10,
                area.left() - 58,
                20,
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                f"{float(y):g}",
            )

        self._screen_positions.clear()

        for index, point in enumerate(self.points):
            center = QPointF(screen_x(point["X"]), screen_y(point["Y"]))
            self._screen_positions[index] = center

            status = point.get("status", "PENDING")
            fill = {
                "PENDING": QColor("#b7b7b7"),
                "NO_DATA": QColor("#b7b7b7"),
                "CURRENT": QColor("#e1b84a"),
                "MEASURED": QColor("#61a875"),
                "WARNING": QColor("#df8b38"),
            }.get(status, QColor("#b7b7b7"))

            if status == "FAILED":
                # No usable TXT result exists for this coordinate.
                painter.setPen(QPen(QColor("#d13c3c"), 3))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawLine(
                    int(center.x() - 8), int(center.y() - 8),
                    int(center.x() + 8), int(center.y() + 8),
                )
                painter.drawLine(
                    int(center.x() - 8), int(center.y() + 8),
                    int(center.x() + 8), int(center.y() - 8),
                )
            else:
                # Draw the normal status-colored measurement point.
                painter.setPen(QPen(QColor("#202020"), 1.2))
                painter.setBrush(QBrush(fill))
                painter.drawEllipse(center, 9, 9)

            # Selection is only a GUI aid, so make it intentionally obvious:
            # a thick blue halo is drawn around the status-colored point.
            if index == self.selected_index:
                painter.setPen(QPen(QColor("#1565C0"), 4))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawEllipse(center, 15, 15)

        # Stage-position marker. Keep the filled red "laser dot" smaller than
        # the measurement-status point so the underlying gray/green/orange
        # color remains visible when the stage is sitting exactly on a point.
        if self.stage_position is not None:
            stage_center = QPointF(
                screen_x(self.stage_position[0]),
                screen_y(self.stage_position[1]),
            )
            painter.setPen(QPen(QColor("#8b0000"), 1))
            painter.setBrush(QBrush(QColor("#ff1744")))
            painter.drawEllipse(stage_center, 4, 4)

        # Axis titles.
        painter.setPen(QPen(QColor("#404040"), 1))
        painter.drawText(area.center().x() - 24, self.height() - 8, "X (mm)")
        painter.save()
        painter.translate(14, area.center().y() + 22)
        painter.rotate(-90)
        painter.drawText(0, 0, "Y (mm)")
        painter.restore()

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return

        pos = event.position()
        best = None
        for index, center in self._screen_positions.items():
            distance = math.hypot(pos.x() - center.x(), pos.y() - center.y())
            if distance <= 16 and (best is None or distance < best[1]):
                best = (index, distance)

        if best is not None:
            self.selected_index = best[0]
            self.point_clicked.emit(best[0])
            self.update()


class MeasurementPage(QWidget):
    """Combined Setup + Measurement workspace."""

    dataframe_updated = pyqtSignal(object)
    failed_points_updated = pyqtSignal(object)
    experiment_results_folder_updated = pyqtSignal(str)

    GRID_OPTIONS = [3, 5, 7, 9]
    SPACING_OPTIONS = [1, 2, 3, 4, 5]
    MAX_MAP_SPAN_MM = 20

    def __init__(self, config: AppConfig, parent=None) -> None:
        super().__init__(parent)

        self.config = config

        # Measurement backend is selected at runtime from the GUI. Both the
        # simulator and real CompleteEASE driver expose the same public API.
        self.completeease = None
        self.mapping_runner = None

        self.stage = None
        self.points: list[dict] = []
        self.data = pd.DataFrame()
        self.current_sequence_index = 0
        self.selected_point_index: int | None = None

        self.current_experiment = None
        self.mapping_active = False
        self.abort_requested = False
        self.worker: PointAcquisitionThread | None = None
        self._worker_action = None
        self._retake_index: int | None = None
        self._retake_previous_status: str | None = None
        self._completeease_ready = False
        self._dataset_kind = "planned_grid"
        self._experiment_recipe_name: str | None = None
        self.single_point_dialog: SinglePointDialog | None = None

        self.quality_dialog = MeasurementQualityDialog(self)

        self._build_ui()
        self._refresh_ports()
        self._rebuild_grid()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(splitter, 1)

        # --------------------------- LEFT: setup ------------------------
        left = QWidget()
        left_layout = QVBoxLayout(left)

        self.setup_group = QGroupBox("Measurement Setup")
        form = QFormLayout(self.setup_group)
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(9)

        source_row = QHBoxLayout()
        source_row.setSpacing(10)

        self.completeease_source = QComboBox()
        self.completeease_source.addItems(["SIMULATOR", "CompleteEASE"])
        self.completeease_source.setCurrentText("SIMULATOR")
        self.completeease_source.currentTextChanged.connect(
            self._completeease_source_changed
        )

        self.completeease_connect_button = QPushButton("Connect")
        self.completeease_connect_button.setMaximumWidth(100)
        self.completeease_connect_button.clicked.connect(
            self._toggle_completeease_connection
        )

        source_row.addWidget(self.completeease_source, 1)
        source_row.addWidget(self.completeease_connect_button)
        form.addRow("Measurement source:", source_row)

        self.recipe_combo = QComboBox()
        self.recipe_combo.addItem("Connect measurement source to load recipes")
        self.recipe_combo.setEnabled(False)
        # Recipe names may intentionally contain the full measurement setup.
        # Keep the selected name readable and always expose the full text as a
        # tooltip when the closed combo box cannot display all of it.
        self.recipe_combo.setMinimumWidth(320)
        self.recipe_combo.currentIndexChanged.connect(self._validate_setup)
        self.recipe_combo.currentTextChanged.connect(
            lambda text: self.recipe_combo.setToolTip(text)
        )
        form.addRow("Recipe:", self.recipe_combo)

        self.map_name = QLineEdit()
        self.map_name.setPlaceholderText("Optional (for example Sample01)")
        self.map_name.textChanged.connect(self._validate_setup)
        form.addRow("Map name:", self.map_name)

        save_row = QHBoxLayout()
        save_row.setSpacing(10)

        self.save_location = QLineEdit(str(self.config.default_experiment_parent))
        self.save_location.setPlaceholderText("Choose experiment parent folder")
        self.save_location.textChanged.connect(self._validate_setup)

        self.browse_button = QPushButton("Browse…")
        self.browse_button.setMaximumWidth(82)
        self.browse_button.clicked.connect(self._choose_save_location)

        save_row.addWidget(self.save_location, 1)
        save_row.addWidget(self.browse_button)
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
        # Automatic stage is the default for this instrument.
        self.stage_type.setCurrentIndex(1)
        self.stage_type.currentTextChanged.connect(self._stage_type_changed)
        form.addRow("Stage:", self.stage_type)

        # Optional aid for manual micrometer stages. Mapping coordinates remain
        # relative to the chosen map center; these controls only calculate the
        # absolute stage reading shown in the manual acquisition popup.
        self.track_absolute = QCheckBox("Track absolute coordinates")

        # Match the clearly visible checkbox style used in Measurement Quality.
        self.track_absolute.setStyleSheet("""
            QCheckBox::indicator {
                width: 18px;
                height: 18px;
                border: 1.5px solid #303030;
                border-radius: 3px;
                background-color: white;
            }
            QCheckBox::indicator:checked {
                border: 1.5px solid #303030;
                background-color: #1976D2;
            }
        """)

        self.track_absolute.toggled.connect(self._absolute_tracking_changed)
        form.addRow("", self.track_absolute)

        self.absolute_center_label = QLabel("Center:")

        self.absolute_center_widget = QWidget()
        center_layout = QHBoxLayout(self.absolute_center_widget)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.setSpacing(8)

        center_layout.addWidget(QLabel("X"))

        self.manual_center_x = QDoubleSpinBox()
        self.manual_center_x.setRange(-1000.0, 1000.0)
        self.manual_center_x.setDecimals(2)
        self.manual_center_x.setSingleStep(0.10)
        self.manual_center_x.setValue(0.0)
        self.manual_center_x.setSuffix(" mm")
        self.manual_center_x.setButtonSymbols(
            QAbstractSpinBox.ButtonSymbols.NoButtons
        )
        center_layout.addWidget(self.manual_center_x)

        self.manual_center_x_up = QPushButton("▲")
        self.manual_center_x_down = QPushButton("▼")

        center_layout.addWidget(QLabel("Y"))

        self.manual_center_y = QDoubleSpinBox()
        self.manual_center_y.setRange(-1000.0, 1000.0)
        self.manual_center_y.setDecimals(2)
        self.manual_center_y.setSingleStep(0.10)
        self.manual_center_y.setValue(0.0)
        self.manual_center_y.setSuffix(" mm")
        self.manual_center_y.setButtonSymbols(
            QAbstractSpinBox.ButtonSymbols.NoButtons
        )
        center_layout.addWidget(self.manual_center_y)

        self.manual_center_y_up = QPushButton("▲")
        self.manual_center_y_down = QPushButton("▼")

        # Explicit buttons avoid the same Windows/Qt issue seen previously
        # in the Measurement Quality spinbox, where the upper native arrow
        # was visible but did not reliably respond.
        arrow_style = """
            QPushButton {
                padding: 0px;
                font-size: 11px;
                font-weight: 700;
                background-color: #eeeeee;
                border: 1px solid #9a9a9a;
                border-radius: 3px;
            }
            QPushButton:hover {
                background-color: #dddddd;
            }
            QPushButton:pressed {
                background-color: #cccccc;
            }
        """

        for button in (
            self.manual_center_x_up, self.manual_center_x_down,
            self.manual_center_y_up, self.manual_center_y_down,
        ):
            button.setFixedSize(26, 26)
            button.setStyleSheet(arrow_style)

        _connect_spin_buttons(
            self.manual_center_x_up, self.manual_center_x_down, self.manual_center_x
        )
        _connect_spin_buttons(
            self.manual_center_y_up, self.manual_center_y_down, self.manual_center_y
        )

        # Keep each pair next to its corresponding coordinate field.
        x_buttons = QVBoxLayout()
        x_buttons.setContentsMargins(0, 0, 0, 0)
        x_buttons.setSpacing(2)
        x_buttons.addWidget(self.manual_center_x_up)
        x_buttons.addWidget(self.manual_center_x_down)

        y_buttons = QVBoxLayout()
        y_buttons.setContentsMargins(0, 0, 0, 0)
        y_buttons.setSpacing(2)
        y_buttons.addWidget(self.manual_center_y_up)
        y_buttons.addWidget(self.manual_center_y_down)

        # Rebuild the row in the intended order:
        # X [value] [▲▼]   Y [value] [▲▼]
        while center_layout.count():
            item = center_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)

        center_layout.addWidget(QLabel("X"))
        center_layout.addWidget(self.manual_center_x)
        center_layout.addLayout(x_buttons)
        center_layout.addSpacing(8)
        center_layout.addWidget(QLabel("Y"))
        center_layout.addWidget(self.manual_center_y)
        center_layout.addLayout(y_buttons)

        form.addRow(self.absolute_center_label, self.absolute_center_widget)

        self.span_label = QLabel()
        form.addRow("Mapping span:", self.span_label)

        self.points_label = QLabel()
        form.addRow("Points:", self.points_label)

        self.quality_button = QPushButton("Measurement Quality…")
        self.quality_button.setMaximumWidth(165)
        self.quality_button.clicked.connect(self.quality_dialog.exec)
        form.addRow("", self.quality_button)

        # Hidden unless a real setup problem exists.
        self.validation_label = QLabel()
        self.validation_label.setWordWrap(True)
        self.validation_label.hide()
        form.addRow("", self.validation_label)

        left_layout.addWidget(self.setup_group)

        # Automatic-stage section appears only when automatic stage is selected.
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

        # Jog arrows are intentionally bold and large because they are used
        # for visual/manual alignment rather than as ordinary text buttons.
        arrow_style = """
            QPushButton {
                font-family: Arial;
                font-size: 22px;
                font-weight: 900;
                min-width: 46px;
                min-height: 40px;
                padding: 0px;
            }
        """
        for arrow_button in (
            self.up,
            self.down,
            self.left_btn,
            self.right_btn,
        ):
            arrow_button.setStyleSheet(arrow_style)

        jog.addWidget(self.up, 0, 1)
        jog.addWidget(self.left_btn, 1, 0)
        jog.addWidget(self.right_btn, 1, 2)
        jog.addWidget(self.down, 2, 1)

        self.jog_step = QDoubleSpinBox()
        self.jog_step.setRange(0.01, 10.0)
        self.jog_step.setDecimals(2)
        self.jog_step.setSingleStep(1.00)
        self.jog_step.setValue(2.00)
        self.jog_step.setSuffix(" mm")
        self.jog_step.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)

        jog_step_control = QWidget()
        jog_step_layout = QHBoxLayout(jog_step_control)
        jog_step_layout.setContentsMargins(0, 0, 0, 0)
        jog_step_layout.setSpacing(4)
        jog_step_layout.addWidget(self.jog_step, 1)

        self.jog_step_up = QPushButton("▲")
        self.jog_step_down = QPushButton("▼")
        for button in (self.jog_step_up, self.jog_step_down):
            button.setFixedSize(30, 30)
            button.setStyleSheet("""
                QPushButton {
                    padding: 0px;
                    font-size: 12px;
                    font-weight: 700;
                    background-color: #eeeeee;
                    border: 1px solid #9a9a9a;
                    border-radius: 3px;
                }
                QPushButton:hover { background-color: #dddddd; }
                QPushButton:pressed { background-color: #cccccc; }
            """)
        _connect_spin_buttons(self.jog_step_up, self.jog_step_down, self.jog_step)
        jog_step_layout.addWidget(self.jog_step_up)
        jog_step_layout.addWidget(self.jog_step_down)

        jog.addWidget(QLabel("Step:"), 3, 0)
        jog.addWidget(jog_step_control, 3, 1, 1, 2)
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

        # Primary acquisition controls are intentionally larger and strongly
        # differentiated because they are the main run/stop actions.
        self.start_button.setMinimumSize(170, 44)
        self.abort_button.setMinimumSize(110, 44)

        self.start_button.setStyleSheet("""
            QPushButton {
                background-color: #4FAE67;
                color: white;
                font-weight: 700;
                font-size: 14px;
                border: 1px solid #3F9255;
                border-radius: 5px;
            }
            QPushButton:hover {
                background-color: #449D5B;
            }
            QPushButton:disabled {
                background-color: #A9CFB3;
                color: #F3F3F3;
                border-color: #A9CFB3;
            }
        """)

        self.abort_button.setStyleSheet("""
            QPushButton {
                background-color: #D9534F;
                color: white;
                font-weight: 700;
                font-size: 14px;
                border: 1px solid #BD4541;
                border-radius: 5px;
            }
            QPushButton:hover {
                background-color: #C74743;
            }
            QPushButton:disabled {
                background-color: #D9A5A3;
                color: #F3F3F3;
                border-color: #D9A5A3;
            }
        """)

        self.abort_button.setEnabled(False)
        self.start_button.clicked.connect(self._start_measurement)
        self.abort_button.clicked.connect(self._abort_measurement)

        self.start_more_button = QPushButton("▼")
        self.start_more_button.setFixedSize(38, 44)
        self.start_more_button.setToolTip("Additional acquisition options")
        self.start_more_button.setStyleSheet(self.start_button.styleSheet())
        self.start_more_button.clicked.connect(self._show_measurement_menu)

        self.clear_data_button = QPushButton("Clear Current Data")
        self.clear_data_button.setMinimumHeight(38)
        self.clear_data_button.setToolTip(
            "Clear the current Measurement/Results dataset without deleting "
            "the saved experiment folder from disk."
        )
        self.clear_data_button.clicked.connect(self._clear_current_data)

        run_row.addWidget(self.start_button)
        run_row.addWidget(self.start_more_button)
        run_row.addWidget(self.abort_button)
        run_row.addWidget(self.clear_data_button)
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
        self.retake_button = QPushButton("Measure Selected Point")
        self.retake_button.setToolTip(
            "Measure or remeasure the selected map point."
        )
        self.retake_button.setEnabled(False)
        self.retake_button.clicked.connect(self._measure_selected)
        retake_row.addWidget(self.retake_button)

        self.selected_coordinate = QLabel("Selected: —")
        self.selected_coordinate.setStyleSheet("color: #555555;")
        retake_row.addWidget(self.selected_coordinate)
        retake_row.addStretch()
        right_layout.addLayout(retake_row)

        self.table = QTableWidget()
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)

        # Use a strong selection color so a row selected from either the table
        # or the mapping grid is immediately obvious.
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
        self.table.setMinimumHeight(210)
        right_layout.addWidget(self.table, 2)

        splitter.addWidget(right)
        splitter.setSizes([370, 720])

        self._stage_type_changed()
        self._update_experiment_lock()

    # ------------------------------------------------------------------
    # Mapping definition
    # ------------------------------------------------------------------
    def _rebuild_grid(self) -> None:
        n = int(self.grid_combo.currentData())
        spacing = int(self.spacing_combo.currentData())
        half = (n - 1) // 2

        points: list[dict] = []
        # Snake path starts at the top-left: right across the first row, then
        # down one row and left, alternating direction for each row.
        for row in range(n):
            y = (half - row) * spacing
            columns = range(n) if row % 2 == 0 else range(n - 1, -1, -1)
            for column in columns:
                x = (column - half) * spacing
                points.append({"X": x, "Y": y, "status": "PENDING"})

        self.points = points
        if self.current_experiment is None:
            self._dataset_kind = "planned_grid"
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
        if name and INVALID_WINDOWS_CHARS.search(name):
            problems.append("Map name contains an invalid Windows filename character.")

        if not self.save_location.text().strip():
            problems.append("Save location cannot be empty.")

        if span > self.MAX_MAP_SPAN_MM:
            problems.append(
                f"Mapping span exceeds the {self.MAX_MAP_SPAN_MM} × "
                f"{self.MAX_MAP_SPAN_MM} mm V1 limit."
            )

        if problems:
            self.validation_label.setText("✕ " + " ".join(problems))
            self.validation_label.setStyleSheet("color: #b33939;")
            self.validation_label.show()
            self.start_button.setEnabled(False)
            self.start_more_button.setEnabled(False)
            self.clear_data_button.setEnabled(
                not self.mapping_active and self.worker is None
            )
            self._update_experiment_lock()
            self.start_button.setEnabled(False)
            self.start_more_button.setEnabled(False)
            return False

        # Valid setup stays visually quiet.
        self.validation_label.clear()
        self.validation_label.hide()
        self._update_experiment_lock()
        return True

    def _choose_save_location(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Choose experiment parent folder")
        if folder:
            self.save_location.setText(folder)

    # ------------------------------------------------------------------
    # CompleteEASE / simulator black-box connection
    # ------------------------------------------------------------------
    def _completeease_source_changed(self) -> None:
        """Refresh the disconnected placeholder when the backend is changed."""
        if self.completeease is not None and self.completeease.connected:
            return

        self.recipe_combo.clear()
        self.recipe_combo.addItem("Connect measurement source to load recipes")
        self.recipe_combo.setEnabled(False)
        self._completeease_ready = False
        self._validate_setup()

    def _toggle_completeease_connection(self) -> None:
        """Connect/disconnect the selected measurement backend."""
        if self.completeease is not None and self.completeease.connected:
            try:
                self.completeease.disconnect()
            finally:
                self.completeease = None
                self.mapping_runner = None
                self._completeease_ready = False
                self.completeease_connect_button.setText("Connect")
                self.completeease_source.setEnabled(True)
                self.recipe_combo.clear()
                self.recipe_combo.addItem(
                    "Connect measurement source to load recipes"
                )
                self.recipe_combo.setEnabled(False)
                self._validate_setup()
            return

        source = self.completeease_source.currentText()
        if source == "SIMULATOR":
            backend = CompleteEASESimulator(self.config.staging_folder)
        else:
            backend = CompleteEASEClient()

        try:
            backend.connect()
            recipes = backend.list_recipes()
            if not recipes:
                raise RuntimeError("No CompleteEASE recipes were returned.")
        except Exception as exc:
            try:
                backend.disconnect()
            except Exception:
                pass
            self.completeease = None
            self.mapping_runner = None
            self._completeease_ready = False
            self.recipe_combo.clear()
            self.recipe_combo.addItem("Measurement source unavailable")
            self.recipe_combo.setEnabled(False)
            self.completeease_connect_button.setText("Connect")
            self.completeease_source.setEnabled(True)
            self._validate_setup()
            message = str(exc)
            if source == "CompleteEASE":
                message += (
                    "\n\nMake sure CompleteEASE is open and running on this "
                    "computer before connecting AutoMapper."
                )
            QMessageBox.warning(self, "Measurement Source", message)
            return

        self.completeease = backend
        self.mapping_runner = MappingRunner(
            completeease=self.completeease,
            staging_folder=self.config.staging_folder,
        )
        self._completeease_ready = True
        self.completeease_connect_button.setText("Disconnect")
        self.completeease_source.setEnabled(False)
        self.recipe_combo.clear()
        self.recipe_combo.addItems(recipes)
        self.recipe_combo.setEnabled(True)
        if self._experiment_recipe_name:
            if self._experiment_recipe_name in recipes:
                self.recipe_combo.setCurrentText(self._experiment_recipe_name)
            else:
                self._completeease_ready = False
                QMessageBox.warning(
                    self,
                    "Locked Recipe Unavailable",
                    "The recipe used by the current experiment is not available "
                    "from the reconnected measurement source. Clear Current Data "
                    "before changing the recipe or starting a different experiment.",
                )
        # The popup is allowed to be much wider than the closed control so a
        # descriptive recipe name can be read before selection.
        longest_recipe = max(recipes, key=len)
        popup_width = (
            self.recipe_combo.fontMetrics().horizontalAdvance(longest_recipe) + 50
        )
        self.recipe_combo.view().setMinimumWidth(
            min(max(popup_width, 500), 1100)
        )
        self.recipe_combo.setToolTip(self.recipe_combo.currentText())
        self._validate_setup()

    def _ensure_completeease_connected(self) -> bool:
        if (
            self.completeease is not None
            and self.completeease.connected
            and self._completeease_ready
            and self.mapping_runner is not None
        ):
            return True

        QMessageBox.information(
            self,
            "Measurement Source",
            "Connect the CompleteEASE simulator or real CompleteEASE before "
            "starting the measurement.",
        )
        return False

    def _mark_completeease_disconnected(self) -> None:
        """Update connection controls after a fatal communication failure."""
        try:
            if self.completeease is not None and self.completeease.connected:
                self.completeease.disconnect()
        except Exception:
            pass

        self.completeease = None
        self.mapping_runner = None
        self._completeease_ready = False
        self.completeease_connect_button.setText("Connect")
        self.completeease_source.setEnabled(True)
        self.recipe_combo.clear()
        self.recipe_combo.addItem("Connect measurement source to load recipes")
        self.recipe_combo.setEnabled(False)

    # ------------------------------------------------------------------
    # Stage controls
    # ------------------------------------------------------------------
    def _stage_type_changed(self) -> None:
        automatic = self.stage_type.currentText().startswith("Low-Profile")
        self.stage_group.setVisible(automatic)

        # Absolute-coordinate tracking is only a manual-stage convenience.
        self.track_absolute.setVisible(not automatic)
        self._absolute_tracking_changed()
        if hasattr(self, "set_center"):
            self._update_origin_controls()
        self._refresh_stage_position_marker()

    def _refresh_stage_position_marker(self) -> None:
        """Refresh the progress-map marker from the connected automatic stage."""
        if not hasattr(self, "progress"):
            return

        automatic = self.stage_type.currentText().startswith("Low-Profile")
        if (
            not automatic
            or self.stage is None
            or not getattr(self.stage, "connected", False)
        ):
            self.progress.set_stage_position(None)
            return

        try:
            x, y = self.stage.position
            self.progress.set_stage_position(float(x), float(y))
        except Exception:
            self.progress.set_stage_position(None)

    def _absolute_tracking_changed(self) -> None:
        """Show manual center fields only when absolute tracking is enabled."""
        manual = not self.stage_type.currentText().startswith("Low-Profile")
        show_center = manual and self.track_absolute.isChecked()

        self.absolute_center_label.setVisible(show_center)
        self.absolute_center_widget.setVisible(show_center)

    def _origin_is_locked(self) -> bool:
        return self.current_experiment is not None

    def _update_experiment_lock(self) -> None:
        """Lock acquisition-defining settings while an experiment exists.

        A current experiment may still accept single points or remeasurements,
        but its recipe, map definition, stage type, save location, quality
        settings, and coordinate origin must remain unchanged. Clear Current
        Data is the explicit boundary between coordinate/acquisition setups.
        """
        if not hasattr(self, "setup_group"):
            return

        locked = self._origin_is_locked()
        busy = self.mapping_active or self.worker is not None
        tooltip = (
            "Clear Current Data before changing measurement setup. All "
            "measurements in one experiment must use the same acquisition "
            "settings and coordinate origin."
            if locked else ""
        )

        # During an active worker/sequence, temporarily disable the complete
        # setup group. When idle inside an existing experiment, the group is
        # enabled but all acquisition-defining fields remain locked.
        self.setup_group.setEnabled(not busy)

        setup_widgets = (
            self.completeease_source,
            self.recipe_combo,
            self.map_name,
            self.save_location,
            self.browse_button,
            self.grid_combo,
            self.spacing_combo,
            self.stage_type,
            self.track_absolute,
            self.quality_button,
        )
        for widget in setup_widgets:
            widget.setEnabled(not locked and not busy)
            widget.setToolTip(tooltip)

        # Recipe availability also depends on the measurement-source state.
        if not locked and not busy:
            self.recipe_combo.setEnabled(self._completeease_ready)
            # Connected sources cannot be switched without disconnecting first.
            source_connected = (
                self.completeease is not None and self.completeease.connected
            )
            self.completeease_source.setEnabled(not source_connected)

        # Do not allow an intentional disconnect during a locked experiment,
        # because that could silently change the recipe/source state. If a
        # communication failure already disconnected the backend, reconnecting
        # the same locked source is allowed and the original recipe is restored.
        source_connected = (
            self.completeease is not None and self.completeease.connected
        )
        self.completeease_connect_button.setEnabled(
            not busy and (not locked or not source_connected)
        )
        self.completeease_connect_button.setToolTip(
            tooltip if locked and source_connected else ""
        )

        # A new automatic grid sequence always starts a new experiment and is
        # therefore unavailable until the current data are cleared. Single
        # Point remains available through the adjacent menu.
        ready = (not busy and self._completeease_ready)
        self.start_button.setEnabled(ready and not locked)
        self.start_button.setToolTip(
            "Clear Current Data before starting a new grid measurement."
            if locked else ""
        )
        self.start_more_button.setEnabled(ready)

        self.clear_data_button.setEnabled(not busy)

        # The stage connection and mapping origin define the coordinate frame.
        # Jogging remains available for single-point acquisition, but changing
        # the connected stage or redefining zero is blocked until data clear.
        if hasattr(self, "port_combo"):
            self.port_combo.setEnabled(not locked and not busy)
            self.port_combo.setToolTip(tooltip)
            self.connect_button.setEnabled(not locked and not busy)
            self.connect_button.setToolTip(tooltip)

        self._update_origin_controls()

    def _update_origin_controls(self) -> None:
        """Lock coordinate-origin controls while a current experiment exists."""
        locked = self._origin_is_locked()
        tooltip = (
            "Clear Current Data before changing the mapping center. All "
            "measurements in one experiment must use the same origin."
            if locked else ""
        )

        self.set_center.setEnabled(not locked)
        self.set_center.setToolTip(tooltip)

        for widget in (
            self.manual_center_x,
            self.manual_center_y,
            self.manual_center_x_up,
            self.manual_center_x_down,
            self.manual_center_y_up,
            self.manual_center_y_down,
        ):
            widget.setEnabled(not locked)
            widget.setToolTip(tooltip)

    def _show_measurement_menu(self) -> None:
        menu = QMenu(self)
        add_single = menu.addAction("Add Single Point…")
        chosen = menu.exec(
            self.start_more_button.mapToGlobal(
                self.start_more_button.rect().bottomLeft()
            )
        )
        if chosen is add_single:
            self._open_single_point_dialog()

    def _ensure_current_experiment(self, *, for_single_point: bool = False) -> bool:
        """Create a current experiment only when one does not already exist."""
        if self.current_experiment is not None:
            return True

        try:
            self.current_experiment = create_experiment_folder(
                self.save_location.text().strip(),
                self.map_name.text().strip(),
            )
        except Exception as exc:
            QMessageBox.critical(self, "Experiment folder", str(exc))
            return False

        self._experiment_recipe_name = self.recipe_combo.currentText()
        self.experiment_results_folder_updated.emit(
            str(self.current_experiment.results)
        )
        self._update_experiment_lock()

        if for_single_point and self._dataset_kind == "planned_grid":
            self.points = []
            self.progress.set_points(self.points)
            self._dataset_kind = "single_points"
            self.selected_point_index = None
            self.selected_coordinate.setText("Selected: —")
        return True

    def _open_single_point_dialog(self) -> None:
        if not self._ensure_completeease_connected() or not self._validate_setup():
            return
        if self.worker is not None or self.mapping_active:
            return

        automatic = self.stage_type.currentText().startswith("Low-Profile")
        if automatic and (self.stage is None or not self.stage.connected):
            QMessageBox.information(
                self, "Stage", "Connect the automatic XY stage first."
            )
            return

        if self.single_point_dialog is not None:
            self.single_point_dialog.show()
            self.single_point_dialog.raise_()
            self.single_point_dialog.activateWindow()
            return

        current_x = current_y = 0.0
        if automatic:
            current_x, current_y = self.stage.position

        dialog = SinglePointDialog(
            automatic=automatic,
            track_absolute=(not automatic and self.track_absolute.isChecked()),
            center_x=self.manual_center_x.value(),
            center_y=self.manual_center_y.value(),
            current_x=current_x,
            current_y=current_y,
            parent=self,
        )
        dialog.jog_requested.connect(self._single_point_jog)
        dialog.measure_requested.connect(self._single_point_measure_requested)
        dialog.closed.connect(self._single_point_dialog_closed)
        self.single_point_dialog = dialog
        dialog.show()

    def _single_point_dialog_closed(self) -> None:
        dialog = self.single_point_dialog
        self.single_point_dialog = None
        if dialog is not None:
            dialog.deleteLater()

    def _single_point_jog(self, axis: str, direction: int, step: float) -> None:
        if self.stage is None or not self.stage.connected:
            QMessageBox.information(self, "Stage", "Connect the stage first.")
            return
        try:
            self.stage.move_relative(axis, direction * step)
            self.stage.wait_until_idle()
            self.stage_status.setText(self.stage.status)
            x, y = self.stage.position
            self.progress.set_stage_position(x, y)
            if self.single_point_dialog is not None:
                self.single_point_dialog.set_current_position(x, y)
        except Exception as exc:
            QMessageBox.warning(self, "Stage", str(exc))

    @staticmethod
    def _whole_mm_coordinate(value: float) -> int | None:
        rounded = int(round(value))
        return rounded if math.isclose(value, rounded, abs_tol=1e-6) else None

    def _single_point_measure_requested(self, x: float, y: float) -> None:
        if self.worker is not None or self.mapping_active:
            return
        if not self._ensure_completeease_connected() or not self._validate_setup():
            return

        x_mm = self._whole_mm_coordinate(x)
        y_mm = self._whole_mm_coordinate(y)
        if x_mm is None or y_mm is None:
            QMessageBox.information(
                self,
                "Single Point Coordinate",
                "AutoMapper currently uses whole-millimeter map coordinates for "
                "measurement filenames. Jog or enter a whole-millimeter X/Y "
                "coordinate before measuring.",
            )
            return

        if not self._ensure_current_experiment(for_single_point=True):
            return

        if not self.stage_type.currentText().startswith("Low-Profile"):
            # Manual stage position is operator-reported rather than measured.
            self.progress.set_stage_position(float(x_mm), float(y_mm))

        point_index = self._point_index_for_coordinate(x_mm, y_mm)
        if point_index is None:
            self.points.append({"X": x_mm, "Y": y_mm, "status": "PENDING"})
            point_index = len(self.points) - 1
            self.progress.set_points(self.points)

        if self.single_point_dialog is not None:
            self.single_point_dialog.set_busy(True)
        self._launch_point_worker(point_index, retake=True)

    def _clear_current_data(self) -> None:
        if self.mapping_active or self.worker is not None:
            return

        has_current = self.current_experiment is not None or not self.data.empty
        if has_current:
            response = QMessageBox.question(
                self,
                "Clear Current Data",
                "Clear the current Measurement and Results data?\n\n"
                "Previously saved experiment files will remain on disk. "
                "The next measurement will create a new experiment folder.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if response != QMessageBox.StandardButton.Yes:
                return

        if self.single_point_dialog is not None:
            self.single_point_dialog.close()

        self.current_experiment = None
        self._experiment_recipe_name = None
        self.data = pd.DataFrame()
        self.current_sequence_index = 0
        self.selected_point_index = None
        self._retake_index = None
        self._retake_previous_status = None
        self.measurement_status.setText("READY")
        self.current_label.setText("No measurement running.")
        self.selected_coordinate.setText("Selected: —")
        self._rebuild_grid()
        self._refresh_table()
        self.dataframe_updated.emit(self.data.copy())
        self.failed_points_updated.emit([])
        if not self.stage_type.currentText().startswith("Low-Profile"):
            self.progress.set_stage_position(None)
        self._update_experiment_lock()
        self._validate_setup()

    def _manual_absolute_position(
        self,
        x: float,
        y: float,
    ) -> tuple[float | None, float | None]:
        """
        Return the stage's absolute coordinates for a manual map point.

        These values are display-only. They never replace the relative map
        coordinates used for filenames, parsing, plots, or statistics.
        """
        if (
            self.stage_type.currentText().startswith("Low-Profile")
            or not self.track_absolute.isChecked()
        ):
            return None, None

        return (
            self.manual_center_x.value() + x,
            self.manual_center_y.value() + y,
        )

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
                self.progress.set_stage_position(None)
                return

            port = self.port_combo.currentText()
            self.stage = StageSimulator() if port == "SIMULATOR" else StageController()
            self.stage.connect(port)
            self.connect_button.setText("Disconnect")
            self.stage_status.setText("IDLE")
            self._refresh_stage_position_marker()
            if port != "SIMULATOR":
                QMessageBox.information(
                    self,
                    "Stage Connected",
                    "Stage communication connected successfully.\n\n"
                    "Make sure the mapping stage power is ON before moving "
                    "the stage or starting a map.",
                )
        except Exception as exc:
            QMessageBox.critical(self, "Stage connection", str(exc))

    def _jog(self, axis: str, direction: int) -> None:
        if self.stage is None or not self.stage.connected:
            QMessageBox.information(self, "Stage", "Connect the stage first.")
            return
        try:
            step = direction * self.jog_step.value()
            marker_position = self.progress.stage_position
            if marker_position is None:
                marker_position = self.stage.position
            target_x, target_y = marker_position
            if axis == "X":
                target_x += step
            else:
                target_y += step

            self.stage.move_relative(axis, step)
            self.stage_status.setText(self.stage.status)
            self.progress.set_stage_position(target_x, target_y)
        except Exception as exc:
            QMessageBox.warning(self, "Stage", str(exc))

    def _set_mapping_center(self) -> None:
        if self._origin_is_locked():
            QMessageBox.information(
                self,
                "Mapping Center Locked",
                "Clear Current Data before changing the mapping center. All "
                "measurements in one experiment must use the same origin.",
            )
            return
        if self.stage is None or not self.stage.connected:
            QMessageBox.information(self, "Stage", "Connect the stage first.")
            return
        try:
            self.stage.set_origin()
            self.stage_status.setText("MAPPING CENTER SET")
            self.progress.set_stage_position(0.0, 0.0)
            if self.single_point_dialog is not None:
                self.single_point_dialog.set_current_position(0.0, 0.0)
        except Exception as exc:
            QMessageBox.warning(self, "Stage", str(exc))

    def _return_automatic_stage_to_center(self) -> bool:
        """Return the connected automatic stage to mapping coordinate (0, 0).

        This is used only after a normal automatic grid completion.
        Single points, remeasurements, Abort, and fatal-error paths intentionally
        do not command extra motion.
        """
        automatic = self.stage_type.currentText().startswith("Low-Profile")
        if not automatic or self.stage is None or not self.stage.connected:
            return True

        try:
            self.stage_status.setText("RETURNING TO CENTER")
            self.stage.move("X", 0.0)
            self.stage.wait_until_idle()
            self.stage.move("Y", 0.0)
            self.stage.wait_until_idle()
            self.stage_status.setText("IDLE")
            self.progress.set_stage_position(0.0, 0.0)
            return True
        except Exception as exc:
            self.stage_status.setText("ERROR")
            QMessageBox.warning(
                self,
                "Stage Return",
                "The measurement operation finished, but the automatic stage "
                f"could not return to the mapping center (0, 0).\n\n{exc}",
            )
            return False

    # ------------------------------------------------------------------
    # Mapping acquisition
    # ------------------------------------------------------------------
    def _start_measurement(self) -> None:
        if self.current_experiment is not None:
            QMessageBox.information(
                self,
                "Current Experiment Active",
                "Clear Current Data before starting a new grid measurement. "
                "The current experiment remains available for single-point "
                "measurements and Measure Selected Point.",
            )
            return

        if not self._ensure_completeease_connected():
            return

        if not self._validate_setup():
            return

        automatic = self.stage_type.currentText().startswith("Low-Profile")
        if automatic and (self.stage is None or not self.stage.connected):
            QMessageBox.information(
                self,
                "Stage",
                "Connect the automatic XY stage before starting the measurement.",
            )
            return

        # A grid sequence always starts a new experiment folder. The origin
        # remains locked until Clear Current Data is used.
        try:
            self.current_experiment = create_experiment_folder(
                self.save_location.text().strip(),
                self.map_name.text().strip(),
            )
        except Exception as exc:
            QMessageBox.critical(self, "Experiment folder", str(exc))
            return

        self._experiment_recipe_name = self.recipe_combo.currentText()
        self.experiment_results_folder_updated.emit(
            str(self.current_experiment.results)
        )
        self._update_experiment_lock()
        self._dataset_kind = "grid"
        self._rebuild_grid()

        for point in self.points:
            point["status"] = "PENDING"

        self.data = pd.DataFrame()
        self.current_sequence_index = 0
        self.selected_point_index = None
        self.abort_requested = False
        self.mapping_active = True
        self._retake_index = None
        self._retake_previous_status = None
        self.selected_coordinate.setText("Selected: —")

        self._update_experiment_lock()
        self.start_button.setEnabled(False)
        self.start_more_button.setEnabled(False)
        self.clear_data_button.setEnabled(False)
        self.abort_button.setEnabled(True)
        self.retake_button.setEnabled(False)

        # Clear the previous experiment from Results immediately.
        self._refresh_table()
        self.dataframe_updated.emit(self.data.copy())
        self._emit_failed_points()

        self._start_next_point()

    def _start_next_point(self) -> None:
        if self.abort_requested:
            self._finish_aborted()
            return

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

        if not self.stage_type.currentText().startswith("Low-Profile"):
            absolute_x, absolute_y = self._manual_absolute_position(
                float(point["X"]),
                float(point["Y"]),
            )

            prompt = ManualStageDialog(
                int(point["X"]),
                int(point["Y"]),
                absolute_x=absolute_x,
                absolute_y=absolute_y,
                allow_skip=True,
                retake=False,
                parent=self,
            )
            action = prompt.exec()

            if action == ManualStageDialog.SKIP:
                # A skipped point has no acquired measurement file, so it stays
                # gray rather than being marked as a failed Results point.
                point["status"] = "NO_DATA"
                self._emit_failed_points()
                self.progress.update()
                self.current_sequence_index += 1
                QTimer.singleShot(0, self._start_next_point)
                return

            if action != ManualStageDialog.MEASURE:
                point["status"] = "PENDING"
                self.abort_requested = True
                self._finish_aborted()
                return

            # Manual stages have no position feedback. Once the operator chooses
            # Measure, AutoMapper treats the requested coordinate as the current
            # stage position and shows the same laser marker used for auto stage.
            self.progress.set_stage_position(float(point["X"]), float(point["Y"]))

        self._launch_point_worker(self.current_sequence_index, retake=False)

    def _launch_point_worker(self, point_index: int, retake: bool) -> None:
        if self.current_experiment is None or self.mapping_runner is None:
            return

        point = self.points[point_index]
        x, y = int(point["X"]), int(point["Y"])
        file_name = f"X{x}_Y{y}"
        automatic = self.stage_type.currentText().startswith("Low-Profile")
        stage = self.stage if automatic else None
        if automatic and stage is not None:
            self.progress.set_stage_position(float(x), float(y))

        self._retake_index = point_index if retake else None
        self._retake_previous_status = point["status"] if retake else None

        if retake:
            previous_status = point["status"]
            point["status"] = "CURRENT"
            self.measurement_status.setText("MEASURING")
            action_text = (
                "Remeasuring" if previous_status in ("MEASURED", "WARNING")
                else "Measuring"
            )
            self.current_label.setText(f"{action_text} X = {x} mm, Y = {y} mm")
            self.retake_button.setEnabled(False)
            self.progress.update()

        worker = PointAcquisitionThread(
            runner=self.mapping_runner,
            recipe_name=self.recipe_combo.currentText(),
            file_name=file_name,
            experiment=self.current_experiment,
            stage=stage,
            x=x,
            y=y,
            parent=self,
        )
        worker.finished_result.connect(self._point_finished)
        worker.communication_error.connect(self._communication_failed)
        worker.unexpected_error.connect(self._unexpected_acquisition_error)
        worker.finished.connect(self._worker_finished)
        self.worker = worker
        self._worker_action = None
        worker.start()

    def _point_finished(self, result) -> None:
        retake = self._retake_index is not None
        point_index = self._retake_index if retake else self.current_sequence_index
        point = self.points[point_index]

        if result.success and result.dataframe is not None:
            self.data = result.dataframe.copy()
            point["status"] = self._status_for_coordinate(point["X"], point["Y"])
            self._refresh_table()
            self.dataframe_updated.emit(self.data.copy())
        else:
            if retake and self._retake_previous_status in ("MEASURED", "WARNING"):
                # A failed remeasurement never destroys a previously valid
                # canonical result. Any newly produced raw files are preserved
                # separately by MappingRunner for later inspection/reanalysis.
                point["status"] = self._retake_previous_status
                QMessageBox.warning(
                    self,
                    "Point Measurement",
                    "The new measurement did not produce usable Results data. "
                    "The previous valid result was kept, and any new raw files "
                    "were preserved.",
                )
            elif getattr(result, "measurement_acquired", False):
                # A new SE exists, so measurement data were acquired, but there
                # is no usable TXT result for Results. Red X communicates that
                # distinction from a gray/no-data point.
                point["status"] = "FAILED"
            else:
                # No new SE measurement file: leave the point gray.
                point["status"] = "NO_DATA"

        self._emit_failed_points()
        self.progress.update()

        if retake:
            self.measurement_status.setText("COMPLETE")
            self.current_label.setText(
                f"Point measurement finished: X = {point['X']} mm, "
                f"Y = {point['Y']} mm, status = {point['status']}"
            )
            self._worker_action = "retake_done"
            return

        self.current_sequence_index += 1
        self._worker_action = "abort" if self.abort_requested else "next"

    def _worker_finished(self) -> None:
        worker = self.worker
        self.worker = None
        if worker is not None:
            worker.deleteLater()

        action = self._worker_action
        self._worker_action = None

        if action == "next":
            self._start_next_point()
        elif action == "abort":
            self._finish_aborted()
        elif action == "retake_done":
            self._retake_index = None
            self._retake_previous_status = None
            if self.single_point_dialog is not None:
                self.single_point_dialog.set_busy(False)
                if self.stage is not None and self.stage.connected:
                    x, y = self.stage.position
                    self.single_point_dialog.set_current_position(x, y)
            if self.selected_point_index is not None:
                self._select_point(self.selected_point_index, select_table=True)
            self._validate_setup()

    def _status_for_coordinate(self, x: int, y: int) -> str:
        if self.data.empty:
            return "FAILED"

        matches = self.data[(self.data["X"] == x) & (self.data["Y"] == y)]
        if matches.empty:
            return "FAILED"

        if self.quality_dialog.flag_mse.isChecked() and "MSE" in matches.columns:
            try:
                mse = float(matches.iloc[0]["MSE"])
                if mse > self.quality_dialog.max_mse.value():
                    return "WARNING"
            except (TypeError, ValueError):
                pass
        return "MEASURED"

    def _finish_mapping(self) -> None:
        self.mapping_active = False
        self._return_automatic_stage_to_center()
        measured = sum(
            point["status"] in ("MEASURED", "WARNING") for point in self.points
        )
        failed = sum(point["status"] == "FAILED" for point in self.points)
        no_data = sum(point["status"] == "NO_DATA" for point in self.points)

        self.measurement_status.setText("COMPLETE")
        self.current_label.setText(
            f"Complete: {measured} results, {failed} without usable results, "
            f"{no_data} without acquired data."
        )
        self._update_experiment_lock()
        self.abort_button.setEnabled(False)
        self._validate_setup()
        self.dataframe_updated.emit(self.data.copy())

    def _abort_measurement(self) -> None:
        if not self.mapping_active:
            return

        # Abort deliberately does not cancel CompleteEASE or the current
        # recipe. The current point finishes, then no new point is requested.
        self.abort_requested = True
        self.abort_button.setEnabled(False)
        self.measurement_status.setText("STOPPING")
        self.current_label.setText("Stopping after the current point finishes…")

        if self.worker is None:
            self._finish_aborted()

    def _finish_aborted(self) -> None:
        self.mapping_active = False
        if self.current_sequence_index < len(self.points):
            point = self.points[self.current_sequence_index]
            if point["status"] == "CURRENT":
                point["status"] = "PENDING"

        self.measurement_status.setText("ABORTED")
        self.current_label.setText("Mapping sequence stopped.")
        self._update_experiment_lock()
        self.abort_button.setEnabled(False)
        self.progress.update()
        self._validate_setup()

    def _communication_failed(self, message: str) -> None:
        # Communication failure is fatal to the mapping sequence, unlike a
        # single point that simply does not produce a readable TXT file.
        if self._retake_index is not None:
            point = self.points[self._retake_index]
            point["status"] = self._retake_previous_status or "NO_DATA"
        elif self.current_sequence_index < len(self.points):
            point = self.points[self.current_sequence_index]
            if point["status"] == "CURRENT":
                point["status"] = "PENDING"

        self.mapping_active = False
        self._mark_completeease_disconnected()
        self.measurement_status.setText("ERROR")
        self.current_label.setText("CompleteEASE communication error. Mapping stopped.")
        self._update_experiment_lock()
        self.abort_button.setEnabled(False)
        self.progress.update()
        self._worker_action = "error"
        self._validate_setup()
        if self.single_point_dialog is not None:
            self.single_point_dialog.set_busy(False)
        QMessageBox.critical(self, "CompleteEASE Communication Error", message)

    def _unexpected_acquisition_error(self, message: str) -> None:
        if self._retake_index is not None:
            point = self.points[self._retake_index]
            point["status"] = self._retake_previous_status or "NO_DATA"
        elif self.current_sequence_index < len(self.points):
            point = self.points[self.current_sequence_index]
            if point["status"] == "CURRENT":
                point["status"] = "PENDING"

        self.mapping_active = False
        self.measurement_status.setText("ERROR")
        self.current_label.setText("Acquisition error. Mapping stopped.")
        self._update_experiment_lock()
        self.abort_button.setEnabled(False)
        self.progress.update()
        self._worker_action = "error"
        self._validate_setup()
        if self.single_point_dialog is not None:
            self.single_point_dialog.set_busy(False)
        QMessageBox.critical(self, "Acquisition Error", message)

    def _emit_failed_points(self) -> None:
        failed = [
            (int(point["X"]), int(point["Y"]))
            for point in self.points
            if point["status"] == "FAILED"
        ]
        self.failed_points_updated.emit(failed)

    # ------------------------------------------------------------------
    # Bidirectional map <-> table selection
    # ------------------------------------------------------------------
    def _progress_point_clicked(self, index: int) -> None:
        # Clicking the selected point again clears the selection.
        if self.selected_point_index == index:
            self._clear_selection()
            return
        self._select_point(index, select_table=True)

    def _table_row_clicked(self, table_row: int, column: int) -> None:
        del column

        if table_row >= len(self.data):
            return

        row = self.data.iloc[table_row]
        point_index = self._point_index_for_coordinate(int(row["X"]), int(row["Y"]))
        if point_index is None:
            return

        # Clicking the selected row again clears the selection.
        if self.selected_point_index == point_index:
            self._clear_selection()
            return

        self._select_point(point_index, select_table=False)

    def _clear_selection(self) -> None:
        """Clear the temporary map/table selection without changing any data."""
        self.selected_point_index = None
        self.progress.set_selected(None)

        self.table.blockSignals(True)
        self.table.clearSelection()
        self.table.blockSignals(False)

        self.selected_coordinate.setText("Selected: —")
        self.retake_button.setEnabled(False)

        if self.measurement_status.text() == "COMPLETE":
            measured = sum(
                point["status"] in ("MEASURED", "WARNING") for point in self.points
            )
            failed = sum(point["status"] == "FAILED" for point in self.points)
            no_data = sum(point["status"] == "NO_DATA" for point in self.points)
            self.current_label.setText(
                f"Complete: {measured} results, {failed} without usable results, "
                f"{no_data} without acquired data."
            )
        elif not self.mapping_active and self.worker is None:
            self.current_label.setText("No measurement selected.")

    def _select_point(self, point_index: int, select_table: bool) -> None:
        self.selected_point_index = point_index
        self.progress.set_selected(point_index)

        point = self.points[point_index]
        x, y = point["X"], point["Y"]

        self.selected_coordinate.setText(f"Selected: X = {x} mm, Y = {y} mm")
        self.current_label.setText(
            f"Selected measurement: X = {x} mm, Y = {y} mm, "
            f"status = {point['status']}"
        )

        self.retake_button.setEnabled(
            not self.mapping_active
            and self.worker is None
            and point["status"] in ("PENDING", "NO_DATA", "MEASURED", "WARNING", "FAILED")
        )

        if select_table and not self.data.empty:
            matches = self.data.index[(self.data["X"] == x) & (self.data["Y"] == y)]
            if len(matches):
                row_index = int(matches[0])
                self.table.blockSignals(True)
                self.table.selectRow(row_index)
                first_item = self.table.item(row_index, 0)
                if first_item:
                    self.table.scrollToItem(first_item)
                self.table.blockSignals(False)

    def _point_index_for_coordinate(self, x: int, y: int) -> int | None:
        for index, point in enumerate(self.points):
            if point["X"] == x and point["Y"] == y:
                return index
        return None

    def _measure_selected(self) -> None:
        if self.selected_point_index is None or self.worker is not None:
            return

        point = self.points[self.selected_point_index]
        if point["status"] not in ("PENDING", "NO_DATA", "MEASURED", "WARNING", "FAILED"):
            return

        if not self._ensure_completeease_connected() or not self._validate_setup():
            return

        automatic = self.stage_type.currentText().startswith("Low-Profile")
        if automatic and (self.stage is None or not self.stage.connected):
            QMessageBox.information(self, "Stage", "Connect the stage first.")
            return

        if not automatic:
            absolute_x, absolute_y = self._manual_absolute_position(
                float(point["X"]),
                float(point["Y"]),
            )

            prompt = ManualStageDialog(
                int(point["X"]),
                int(point["Y"]),
                absolute_x=absolute_x,
                absolute_y=absolute_y,
                allow_skip=False,
                retake=point["status"] in ("MEASURED", "WARNING"),
                parent=self,
            )
            if prompt.exec() != ManualStageDialog.MEASURE:
                return
            self.progress.set_stage_position(float(point["X"]), float(point["Y"]))

        if not self._ensure_current_experiment():
            return
        if self._dataset_kind == "planned_grid":
            self._dataset_kind = "grid"
        self._launch_point_worker(self.selected_point_index, retake=True)

    def _refresh_table(self) -> None:
        self.table.clear()
        if self.data.empty:
            self.table.setRowCount(0)
            self.table.setColumnCount(0)
            return

        # Included is analysis metadata and therefore hidden in Measurement.
        columns = [column for column in self.data.columns if column != "Included"]
        display_names = {"X": "X (mm)", "Y": "Y (mm)"}

        self.table.setColumnCount(len(columns))
        self.table.setHorizontalHeaderLabels(
            [display_names.get(column, column) for column in columns]
        )
        self.table.setRowCount(len(self.data))

        for row_i, (_, row) in enumerate(self.data.iterrows()):
            for col_i, column in enumerate(columns):
                value = row[column]
                if isinstance(value, float):
                    text = f"{value:.6g}"
                else:
                    text = str(value)
                self.table.setItem(row_i, col_i, QTableWidgetItem(text))

        self.table.resizeColumnsToContents()

    def shutdown(self) -> None:
        """Close hardware/software connections when the application exits."""
        try:
            if self.stage is not None and self.stage.connected:
                self.stage.disconnect()
        except Exception:
            pass
        try:
            if self.completeease is not None and self.completeease.connected:
                self.completeease.disconnect()
        except Exception:
            pass
