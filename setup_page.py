"""Experiment setup page for the first mapping GUI prototype."""

from PyQt6.QtCore import QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox, QDoubleSpinBox, QFormLayout, QGridLayout, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget,
)

from mapping_model import MappingModel


class SetupPage(QWidget):
    configuration_changed = pyqtSignal()

    GRID_OPTIONS = [3, 5, 7, 9, 11, 13, 15]
    SPACING_OPTIONS = [1, 2, 3, 4, 5]

    def __init__(self, model: MappingModel, stage_simulator, parent=None) -> None:
        super().__init__(parent)
        self.model = model
        self.stage = stage_simulator
        self._build_ui()
        self._load_model()
        self._settings_changed()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        setup_group = QGroupBox("Mapping Setup")
        form = QFormLayout(setup_group)

        self.recipe_combo = QComboBox()
        self.recipe_combo.addItems(["Example_Recipe_1", "Example_Recipe_2"])
        form.addRow("Recipe:", self.recipe_combo)

        self.map_name_edit = QLineEdit()
        form.addRow("Map name:", self.map_name_edit)

        self.grid_combo = QComboBox()
        for n in self.GRID_OPTIONS:
            self.grid_combo.addItem(f"{n} × {n}", n)
        form.addRow("Grid:", self.grid_combo)

        self.spacing_combo = QComboBox()
        for spacing in self.SPACING_OPTIONS:
            self.spacing_combo.addItem(f"{spacing} mm", float(spacing))
        form.addRow("Spacing:", self.spacing_combo)

        self.stage_combo = QComboBox()
        self.stage_combo.addItems(["Manual Stage", "Low-Profile Automatic XY Stage"])
        form.addRow("Stage:", self.stage_combo)
        root.addWidget(setup_group)

        summary_group = QGroupBox("Mapping Summary")
        summary = QGridLayout(summary_group)
        summary.addWidget(QLabel("Mapping span:"), 0, 0)
        self.span_label = QLabel()
        summary.addWidget(self.span_label, 0, 1)
        summary.addWidget(QLabel("Points:"), 1, 0)
        self.points_label = QLabel()
        summary.addWidget(self.points_label, 1, 1)
        self.range_message = QLabel()
        self.range_message.setWordWrap(True)
        summary.addWidget(self.range_message, 2, 0, 1, 2)
        root.addWidget(summary_group)

        self.stage_group = QGroupBox("Automatic Stage")
        stage_layout = QVBoxLayout(self.stage_group)

        instruction = QLabel(
            "Before mapping: use the manual controls to align the ellipsometer "
            "beam with the center of the desired sampling area, then select Set Origin."
        )
        instruction.setWordWrap(True)
        stage_layout.addWidget(instruction)

        connection = QHBoxLayout()
        connection.addWidget(QLabel("Connection:"))
        connection.addWidget(QLabel("SIMULATOR"))
        connection.addStretch()
        self.connect_button = QPushButton("Connect")
        self.connect_button.clicked.connect(self._toggle_connection)
        connection.addWidget(self.connect_button)
        stage_layout.addLayout(connection)

        self.position_label = QLabel("X = 0.000 mm   Y = 0.000 mm")
        stage_layout.addWidget(self.position_label)

        jog = QGridLayout()
        self.up_button = QPushButton("↑")
        self.down_button = QPushButton("↓")
        self.left_button = QPushButton("←")
        self.right_button = QPushButton("→")
        jog.addWidget(self.up_button, 0, 1)
        jog.addWidget(self.left_button, 1, 0)
        jog.addWidget(self.right_button, 1, 2)
        jog.addWidget(self.down_button, 2, 1)

        self.jog_step = QDoubleSpinBox()
        self.jog_step.setRange(0.01, 10.0)
        self.jog_step.setDecimals(2)
        self.jog_step.setSingleStep(0.10)
        self.jog_step.setValue(0.10)
        self.jog_step.setSuffix(" mm")
        jog.addWidget(QLabel("Step:"), 3, 0)
        jog.addWidget(self.jog_step, 3, 1, 1, 2)
        stage_layout.addLayout(jog)

        bottom = QHBoxLayout()
        self.origin_button = QPushButton("Set Origin")
        bottom.addWidget(self.origin_button)
        bottom.addStretch()
        bottom.addWidget(QLabel("Status:"))
        self.stage_status = QLabel("DISCONNECTED")
        bottom.addWidget(self.stage_status)
        stage_layout.addLayout(bottom)
        root.addWidget(self.stage_group)
        root.addStretch()

        self.grid_combo.currentIndexChanged.connect(self._settings_changed)
        self.spacing_combo.currentIndexChanged.connect(self._settings_changed)
        self.stage_combo.currentIndexChanged.connect(self._settings_changed)
        self.recipe_combo.currentTextChanged.connect(self._settings_changed)
        self.map_name_edit.textChanged.connect(self._settings_changed)

        self.up_button.clicked.connect(lambda: self._jog("Y", +1))
        self.down_button.clicked.connect(lambda: self._jog("Y", -1))
        self.left_button.clicked.connect(lambda: self._jog("X", -1))
        self.right_button.clicked.connect(lambda: self._jog("X", +1))
        self.origin_button.clicked.connect(self._set_origin)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._refresh_stage)
        self.timer.start(100)

    def _load_model(self) -> None:
        self.map_name_edit.setText(self.model.map_name)
        i = self.grid_combo.findData(self.model.grid_size)
        if i >= 0:
            self.grid_combo.setCurrentIndex(i)
        i = self.spacing_combo.findData(self.model.spacing_mm)
        if i >= 0:
            self.spacing_combo.setCurrentIndex(i)

    def _settings_changed(self) -> None:
        self.model.recipe = self.recipe_combo.currentText()
        self.model.map_name = self.map_name_edit.text().strip() or "Untitled"
        self.model.grid_size = int(self.grid_combo.currentData())
        self.model.spacing_mm = float(self.spacing_combo.currentData())
        self.model.stage_type = self.stage_combo.currentText()
        self.model.generate_points()

        span = self.model.mapping_span_mm
        self.span_label.setText(f"{span:.0f} × {span:.0f} mm")
        self.points_label.setText(str(self.model.point_count))

        if self.model.valid_range:
            self.span_label.setStyleSheet("color: #2769b0;")
            self.range_message.setText(
                f"✓ Within the {self.model.MAX_MAP_SPAN_MM:.0f} × "
                f"{self.model.MAX_MAP_SPAN_MM:.0f} mm allowed mapping span."
            )
            self.range_message.setStyleSheet("color: #2769b0;")
        else:
            self.span_label.setStyleSheet("color: #b33939;")
            self.range_message.setText(
                f"✕ Exceeds the {self.model.MAX_MAP_SPAN_MM:.0f} × "
                f"{self.model.MAX_MAP_SPAN_MM:.0f} mm allowed mapping span."
            )
            self.range_message.setStyleSheet("color: #b33939;")

        self.stage_group.setVisible(
            self.model.stage_type == "Low-Profile Automatic XY Stage"
        )
        self.configuration_changed.emit()

    def _toggle_connection(self) -> None:
        if self.stage.connected:
            self.stage.disconnect()
        else:
            self.stage.connect()
        self._refresh_stage()

    def _jog(self, axis: str, sign: int) -> None:
        if self.stage.connected:
            self.stage.move_relative(axis, sign * self.jog_step.value())

    def _set_origin(self) -> None:
        if self.stage.connected:
            self.stage.set_origin()

    def _refresh_stage(self) -> None:
        if not self.stage.connected:
            self.connect_button.setText("Connect")
            self.position_label.setText("X = 0.000 mm   Y = 0.000 mm")
            self.stage_status.setText("DISCONNECTED")
            return
        self.connect_button.setText("Disconnect")
        x, y = self.stage.position
        self.position_label.setText(f"X = {x:.3f} mm   Y = {y:.3f} mm")
        self.stage_status.setText(self.stage.status)
