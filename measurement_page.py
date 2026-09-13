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
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app_config import AppConfig
from mapping_runner import (
    MappingRunner,
    PointAcquisitionThread,
    create_experiment_folder,
)
from stage import StageController
from stage_simulator import StageSimulator


INVALID_WINDOWS_CHARS = re.compile(r'[\\/:*?"<>|]')


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

        up_button.clicked.connect(self.max_mse.stepUp)
        down_button.clicked.connect(self.max_mse.stepDown)

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
            message_text = (
                f"<b>Map position:</b> X = {x} mm, Y = {y} mm<br>"
                f"<b>Absolute stage position:</b> "
                f"X = {absolute_x:.2f} mm, Y = {absolute_y:.2f} mm<br><br>"
                f"Move the stage to the absolute position above, then select "
                f"Measure to {action_word} this point."
            )
        else:
            message_text = (
                f"Move the sample to X = {x} mm, Y = {y} mm, "
                f"then select Measure to {action_word} this point."
            )

        message = QLabel(message_text)
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
        self._screen_positions: dict[int, QPointF] = {}
        self.setMinimumHeight(340)

    def set_points(self, points: list[dict]) -> None:
        self.points = points
        self.selected_index = None
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

        # Extra left/bottom space is reserved for coordinate tick labels.
        area = self.rect().adjusted(96, 22, -24, -60)

        xs = sorted(set(p["X"] for p in self.points))
        ys = sorted(set(p["Y"] for p in self.points))
        xmin, xmax = min(xs), max(xs)
        ymin, ymax = min(ys), max(ys)
        xspan = max(xmax - xmin, 1)
        yspan = max(ymax - ymin, 1)

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
        for x in xs:
            sx = screen_x(x)
            painter.drawLine(int(sx), area.bottom(), int(sx), area.bottom() + 5)
            painter.drawText(
                int(sx) - 22,
                area.bottom() + 13,
                44,
                22,
                Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
                str(int(x)),
            )

        for y in ys:
            sy = screen_y(y)
            painter.drawLine(area.left() - 5, int(sy), area.left(), int(sy))
            painter.drawText(
                30,
                int(sy) - 10,
                area.left() - 58,
                20,
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                str(int(y)),
            )

        self._screen_positions.clear()

        for index, point in enumerate(self.points):
            center = QPointF(screen_x(point["X"]), screen_y(point["Y"]))
            self._screen_positions[index] = center

            status = point.get("status", "PENDING")
            fill = {
                "PENDING": QColor("#b7b7b7"),
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

    def __init__(self, config: AppConfig, completeease, parent=None) -> None:
        super().__init__(parent)

        self.config = config
        self.completeease = completeease
        self.mapping_runner = MappingRunner(
            completeease=self.completeease,
            staging_folder=self.config.staging_folder,
        )

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

        self.quality_dialog = MeasurementQualityDialog(self)

        self._build_ui()
        self._refresh_ports()
        self._rebuild_grid()

        # Initialize the CompleteEASE black box after the window is created so
        # connection problems can be reported through the GUI rather than at
        # import time.
        QTimer.singleShot(0, self._initialize_completeease)

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

        self.recipe_combo = QComboBox()
        self.recipe_combo.addItem("Connecting to CompleteEASE…")
        self.recipe_combo.setEnabled(False)
        self.recipe_combo.currentIndexChanged.connect(self._validate_setup)
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

        browse = QPushButton("Browse…")
        browse.setMaximumWidth(82)
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

        x_up = QPushButton("▲")
        x_down = QPushButton("▼")

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

        y_up = QPushButton("▲")
        y_down = QPushButton("▼")

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

        for button in (x_up, x_down, y_up, y_down):
            button.setFixedSize(26, 26)
            button.setStyleSheet(arrow_style)

        x_up.clicked.connect(self.manual_center_x.stepUp)
        x_down.clicked.connect(self.manual_center_x.stepDown)
        y_up.clicked.connect(self.manual_center_y.stepUp)
        y_down.clicked.connect(self.manual_center_y.stepDown)

        # Keep each pair next to its corresponding coordinate field.
        x_buttons = QVBoxLayout()
        x_buttons.setContentsMargins(0, 0, 0, 0)
        x_buttons.setSpacing(2)
        x_buttons.addWidget(x_up)
        x_buttons.addWidget(x_down)

        y_buttons = QVBoxLayout()
        y_buttons.setContentsMargins(0, 0, 0, 0)
        y_buttons.setSpacing(2)
        y_buttons.addWidget(y_up)
        y_buttons.addWidget(y_down)

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
        self.retake_button.setToolTip(
            "Select a measured point from the map or live table to retake that measurement."
        )
        self.retake_button.setEnabled(False)
        self.retake_button.clicked.connect(self._retake_selected)
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
            return False

        # Valid setup stays visually quiet.
        self.validation_label.clear()
        self.validation_label.hide()
        self.start_button.setEnabled(not self.mapping_active and self.worker is None)
        return True

    def _choose_save_location(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Choose experiment parent folder")
        if folder:
            self.save_location.setText(folder)

    # ------------------------------------------------------------------
    # CompleteEASE / simulator black-box connection
    # ------------------------------------------------------------------
    def _initialize_completeease(self) -> None:
        try:
            if not self.completeease.connected:
                self.completeease.connect()
            recipes = self.completeease.list_recipes()
        except Exception as exc:
            self._completeease_ready = False
            self.recipe_combo.clear()
            self.recipe_combo.addItem("CompleteEASE unavailable")
            self.recipe_combo.setEnabled(False)
            self._validate_setup()
            QMessageBox.warning(self, "CompleteEASE", str(exc))
            return

        self._completeease_ready = True
        self.recipe_combo.clear()
        self.recipe_combo.addItems(recipes)
        self.recipe_combo.setEnabled(True)
        self._validate_setup()

    def _ensure_completeease_connected(self) -> bool:
        if self.completeease.connected and self._completeease_ready:
            return True
        self._initialize_completeease()
        return self._completeease_ready

    # ------------------------------------------------------------------
    # Stage controls
    # ------------------------------------------------------------------
    def _stage_type_changed(self) -> None:
        automatic = self.stage_type.currentText().startswith("Low-Profile")
        self.stage_group.setVisible(automatic)

        # Absolute-coordinate tracking is only a manual-stage convenience.
        self.track_absolute.setVisible(not automatic)
        self._absolute_tracking_changed()

    def _absolute_tracking_changed(self) -> None:
        """Show manual center fields only when absolute tracking is enabled."""
        manual = not self.stage_type.currentText().startswith("Low-Profile")
        show_center = manual and self.track_absolute.isChecked()

        self.absolute_center_label.setVisible(show_center)
        self.absolute_center_widget.setVisible(show_center)

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
    # Mapping acquisition
    # ------------------------------------------------------------------
    def _start_measurement(self) -> None:
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

        try:
            self.current_experiment = create_experiment_folder(
                self.save_location.text().strip(),
                self.map_name.text().strip(),
            )
        except Exception as exc:
            QMessageBox.critical(self, "Experiment folder", str(exc))
            return

        # Results uses this only as the preferred destination when the user
        # later exports figures. Nothing is saved there automatically.
        self.experiment_results_folder_updated.emit(
            str(self.current_experiment.results)
        )

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

        self.setup_group.setEnabled(False)
        self.start_button.setEnabled(False)
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
                # A skipped point intentionally produces no measurement file.
                # Downstream it behaves exactly like any other failed/missing
                # measurement: red in Measurement and absent from Results.
                point["status"] = "FAILED"
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

        self._launch_point_worker(self.current_sequence_index, retake=False)

    def _launch_point_worker(self, point_index: int, retake: bool) -> None:
        if self.current_experiment is None:
            return

        point = self.points[point_index]
        x, y = int(point["X"]), int(point["Y"])
        file_name = f"X{x}_Y{y}"
        automatic = self.stage_type.currentText().startswith("Low-Profile")
        stage = self.stage if automatic else None

        self._retake_index = point_index if retake else None
        self._retake_previous_status = point["status"] if retake else None

        if retake:
            point["status"] = "CURRENT"
            self.measurement_status.setText("RETAKING")
            self.current_label.setText(f"Retaking X = {x} mm, Y = {y} mm")
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
                # A failed retake never destroys a previously valid permanent
                # result. The existing row/files remain the source of truth.
                point["status"] = self._retake_previous_status
                QMessageBox.warning(
                    self,
                    "Retake",
                    "The retake did not produce a readable TXT result. "
                    "The previous valid measurement was kept.",
                )
            else:
                point["status"] = "FAILED"

        self._emit_failed_points()
        self.progress.update()

        if retake:
            self.measurement_status.setText("COMPLETE")
            self.current_label.setText(
                f"Retake finished: X = {point['X']} mm, Y = {point['Y']} mm, "
                f"status = {point['status']}"
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
        measured = sum(
            point["status"] in ("MEASURED", "WARNING") for point in self.points
        )
        failed = sum(point["status"] == "FAILED" for point in self.points)

        self.measurement_status.setText("COMPLETE")
        self.current_label.setText(
            f"Complete: {measured} measured, {failed} failed."
        )
        self.setup_group.setEnabled(True)
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
        self.setup_group.setEnabled(True)
        self.abort_button.setEnabled(False)
        self.progress.update()
        self._validate_setup()

    def _communication_failed(self, message: str) -> None:
        # Communication failure is fatal to the mapping sequence, unlike a
        # single point that simply does not produce a readable TXT file.
        if self._retake_index is not None:
            point = self.points[self._retake_index]
            point["status"] = self._retake_previous_status or "FAILED"
        elif self.current_sequence_index < len(self.points):
            point = self.points[self.current_sequence_index]
            if point["status"] == "CURRENT":
                point["status"] = "PENDING"

        self.mapping_active = False
        self._completeease_ready = False
        self.measurement_status.setText("ERROR")
        self.current_label.setText("CompleteEASE communication error. Mapping stopped.")
        self.setup_group.setEnabled(True)
        self.abort_button.setEnabled(False)
        self.progress.update()
        self._worker_action = "error"
        self._validate_setup()
        QMessageBox.critical(self, "CompleteEASE Communication Error", message)

    def _unexpected_acquisition_error(self, message: str) -> None:
        if self._retake_index is not None:
            point = self.points[self._retake_index]
            point["status"] = self._retake_previous_status or "FAILED"
        elif self.current_sequence_index < len(self.points):
            point = self.points[self.current_sequence_index]
            if point["status"] == "CURRENT":
                point["status"] = "PENDING"

        self.mapping_active = False
        self.measurement_status.setText("ERROR")
        self.current_label.setText("Acquisition error. Mapping stopped.")
        self.setup_group.setEnabled(True)
        self.abort_button.setEnabled(False)
        self.progress.update()
        self._worker_action = "error"
        self._validate_setup()
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
            self.current_label.setText(
                f"Complete: {measured} measured, {failed} failed."
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
            and point["status"] in ("MEASURED", "WARNING", "FAILED")
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

    def _retake_selected(self) -> None:
        if self.selected_point_index is None or self.worker is not None:
            return

        point = self.points[self.selected_point_index]
        if point["status"] not in ("MEASURED", "WARNING", "FAILED"):
            return

        if not self._ensure_completeease_connected():
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
                retake=True,
                parent=self,
            )
            if prompt.exec() != ManualStageDialog.MEASURE:
                return

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
            if self.completeease.connected:
                self.completeease.disconnect()
        except Exception:
            pass
