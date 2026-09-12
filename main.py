"""
ABSTRACT
--------
Main entry point for Ellipsometer AutoMapper V1.1.

The application has two true working tabs:
    Measurement | Results

They are presented using a real QTabWidget so they visually behave like
application workspaces rather than pop-out buttons. Help remains an auxiliary
pop-out control placed at the upper-right corner of the tab bar.
"""

from __future__ import annotations

import sys

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QMainWindow, QPushButton, QTabWidget

from help_dialog import HelpDialog
from measurement_page import MeasurementPage
from results_page import ResultsPage


class AutoMapperWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Ellipsometer AutoMapper")
        self.resize(1240, 820)

        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)

        self.measurement_page = MeasurementPage()
        self.results_page = ResultsPage()

        self.tabs.addTab(self.measurement_page, "Measurement")
        self.tabs.addTab(self.results_page, "Results")

        # Help is deliberately not another workspace tab.
        self.help_button = QPushButton("Help")
        self.help_button.setMaximumWidth(80)
        self.help_button.clicked.connect(self._show_help)
        self.tabs.setCornerWidget(self.help_button, Qt.Corner.TopRightCorner)

        # The same current DataFrame feeds both acquisition and Results.
        self.measurement_page.dataframe_updated.connect(
            self.results_page.set_dataframe
        )

        self.help_dialog = None

        # Explicit colors prevent Windows/Qt dark-palette text from becoming
        # unreadable on the light scientific-interface backgrounds.
        self.setStyleSheet("""
            QWidget {
                color: #202020;
                background-color: #f7f7f7;
            }

            QTabWidget::pane {
                border: 1px solid #b8b8b8;
                background-color: #f7f7f7;
                top: -1px;
            }

            QTabBar::tab {
                color: #202020;
                background-color: #e4e4e4;
                border: 1px solid #a9a9a9;
                border-bottom: none;
                min-width: 145px;
                min-height: 34px;
                padding: 10px 22px;
                margin-right: 2px;
                font-size: 15px;
                font-weight: 600;
            }

            QTabBar::tab:selected {
                background-color: white;
                border-top: 3px solid #3A8DC2;
                font-weight: 700;
            }

            QTabBar::tab:!selected {
                margin-top: 3px;
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

            QPushButton:hover {
                background-color: #e2e2e2;
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
                border-radius: 3px;
                padding-left: 7px;
            }

            QComboBox {
                padding-right: 20px;
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
