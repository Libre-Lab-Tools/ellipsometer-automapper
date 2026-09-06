"""First GUI prototype for ellipsometer XY mapping."""

import sys

from PyQt6.QtWidgets import QApplication, QMainWindow, QTabWidget

from help_page import HelpPage
from mapping_model import MappingModel
from measurement_page import MeasurementPage
from results_page import ResultsPage
from setup_page import SetupPage
from stage_simulator import StageSimulator


class MappingWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Ellipsometer XY Mapping")
        self.resize(980, 760)

        self.model = MappingModel()
        self.stage_simulator = StageSimulator()

        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)

        self.setup_page = SetupPage(self.model, self.stage_simulator)
        self.measurement_page = MeasurementPage(self.model)
        self.results_page = ResultsPage(self.model)
        self.help_page = HelpPage()

        self.tabs.addTab(self.setup_page, "Setup")
        self.tabs.addTab(self.measurement_page, "Measurement")
        self.tabs.addTab(self.results_page, "Results")
        self.tabs.addTab(self.help_page, "Help")

        self.setup_page.configuration_changed.connect(self.measurement_page.refresh_from_model)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MappingWindow()
    window.show()
    sys.exit(app.exec())
