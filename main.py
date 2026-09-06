"""
ABSTRACT
--------
Main entry point for Ellipsometer AutoMapper V1.

The application intentionally has only two main working screens:
    Measurement | Results

Help is a pop-out window at the upper-right, following the workflow discussed
for the user's DAQ-style applications.

This V1 focuses on the final GUI architecture, real TXT-folder parsing, real
interactive result maps, stage connection/jog controls, and simulated
acquisition. CompleteEASE recipe communication and permanent experiment-folder
management will be connected in the next implementation stage.
"""

from __future__ import annotations

import sys

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QMainWindow,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from help_dialog import HelpDialog
from measurement_page import MeasurementPage
from results_page import ResultsPage


class AutoMapperWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Ellipsometer AutoMapper")
        self.resize(1220, 800)

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        # ------------------------------------------------------------------
        # Top navigation: only Measurement and Results on the left.
        # Help stays on the upper-right as a pop-out.
        # ------------------------------------------------------------------
        header = QHBoxLayout()

        self.measurement_button = QPushButton("Measurement")
        self.results_button = QPushButton("Results")
        self.help_button = QPushButton("Help")

        self.measurement_button.setCheckable(True)
        self.results_button.setCheckable(True)
        self.measurement_button.setChecked(True)

        header.addWidget(self.measurement_button)
        header.addWidget(self.results_button)
        header.addStretch()
        header.addWidget(self.help_button)
        root.addLayout(header)

        self.stack = QStackedWidget()
        self.measurement_page = MeasurementPage()
        self.results_page = ResultsPage()
        self.stack.addWidget(self.measurement_page)
        self.stack.addWidget(self.results_page)
        root.addWidget(self.stack, 1)

        self.measurement_button.clicked.connect(lambda: self._show_page(0))
        self.results_button.clicked.connect(lambda: self._show_page(1))
        self.help_button.clicked.connect(self._show_help)

        # The current table is shared between acquisition and Results.
        self.measurement_page.dataframe_updated.connect(
            self.results_page.set_dataframe
        )

        self.help_dialog = None

        self.setStyleSheet("""
            QWidget {
                color: #202020;
                background-color: #f7f7f7;
            }

            QGroupBox {
                font-weight: 600;
                border: 1px solid #c9c9c9;
                border-radius: 5px;
                margin-top: 8px;
                padding-top: 8px;
                background-color: white;
                color: #202020;
            }

            QGroupBox::title {
                subcontrol-origin: margin;
                left: 8px;
                padding: 0 4px;
                color: #202020;
            }

            QPushButton {
                padding: 6px 10px;
                min-height: 24px;
                color: #202020;
                background-color: #eeeeee;
                border: 1px solid #bcbcbc;
                border-radius: 4px;
            }

            QPushButton:checked {
                background-color: #42aee8;
                color: white;
            }

            QPushButton:disabled {
                color: #999999;
                background-color: #eeeeee;
            }

            QLineEdit,
            QComboBox,
            QSpinBox,
            QDoubleSpinBox {
                min-height: 26px;
                color: #202020;
                background-color: white;
                border: 1px solid #bcbcbc;
            }

            QTableWidget {
                color: #202020;
                background-color: white;
                gridline-color: #d0d0d0;
            }

            QHeaderView::section {
                color: #202020;
                background-color: #e9e9e9;
                border: 1px solid #cccccc;
                padding: 4px;
            }

            QLabel {
                color: #202020;
            }
        """)

    def _show_page(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        self.measurement_button.setChecked(index == 0)
        self.results_button.setChecked(index == 1)

    def _show_help(self) -> None:
        if self.help_dialog is None:
            self.help_dialog = HelpDialog(self)
        self.help_dialog.show()
        self.help_dialog.raise_()
        self.help_dialog.activateWindow()


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Ellipsometer AutoMapper")

    window = AutoMapperWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
