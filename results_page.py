"""Results page skeleton."""

import statistics

from PyQt6.QtWidgets import (
    QFileDialog, QGroupBox, QHBoxLayout, QLabel, QPushButton, QScrollArea,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from mapping_model import MappingModel


class ResultsPage(QWidget):
    def __init__(self, model: MappingModel, parent=None) -> None:
        super().__init__(parent)
        self.model = model
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        controls = QHBoxLayout()

        self.refresh_button = QPushButton("Refresh Plots")
        self.refresh_button.clicked.connect(self.refresh_results)
        controls.addWidget(self.refresh_button)

        self.import_txt_button = QPushButton("Import TXT Folder")
        self.import_txt_button.clicked.connect(self.import_txt_folder)
        controls.addWidget(self.import_txt_button)

        self.import_table_button = QPushButton("Import Table")
        self.import_table_button.clicked.connect(self.import_table)
        controls.addWidget(self.import_table_button)
        controls.addStretch()
        root.addLayout(controls)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        layout = QVBoxLayout(content)

        thickness = QGroupBox("Thickness Map")
        t_layout = QVBoxLayout(thickness)
        self.thickness_label = QLabel("No plot generated yet.")
        self.thickness_label.setMinimumHeight(190)
        self.thickness_label.setWordWrap(True)
        t_layout.addWidget(self.thickness_label)
        layout.addWidget(thickness)

        mse = QGroupBox("MSE Map")
        m_layout = QVBoxLayout(mse)
        self.mse_label = QLabel("No plot generated yet.")
        self.mse_label.setMinimumHeight(150)
        m_layout.addWidget(self.mse_label)
        layout.addWidget(mse)

        stats = QGroupBox("Statistics")
        s_layout = QVBoxLayout(stats)
        self.stats_table = QTableWidget(0, 3)
        self.stats_table.setHorizontalHeaderLabels(["Parameter", "Average", "Std. dev."])
        s_layout.addWidget(self.stats_table)
        layout.addWidget(stats)
        layout.addStretch()

        scroll.setWidget(content)
        root.addWidget(scroll)

    def refresh_results(self) -> None:
        thickness = [p.results["Thickness"] for p in self.model.points if "Thickness" in p.results]
        mse = [p.results["MSE"] for p in self.model.points if "MSE" in p.results]

        self.thickness_label.setText(
            f"Thickness map placeholder\n{len(thickness)} measured points available."
            if thickness else "No Thickness data available."
        )
        self.mse_label.setText(
            f"MSE map placeholder\n{len(mse)} measured points available."
            if mse else "No MSE data available."
        )

        rows = []
        if thickness:
            rows.append(("Thickness", statistics.mean(thickness), statistics.stdev(thickness) if len(thickness) > 1 else 0.0))
        if mse:
            rows.append(("MSE", statistics.mean(mse), statistics.stdev(mse) if len(mse) > 1 else 0.0))

        self.stats_table.setRowCount(len(rows))
        for row, (name, avg, std) in enumerate(rows):
            self.stats_table.setItem(row, 0, QTableWidgetItem(name))
            self.stats_table.setItem(row, 1, QTableWidgetItem(f"{avg:.3f}"))
            self.stats_table.setItem(row, 2, QTableWidgetItem(f"{std:.3f}"))
        self.stats_table.resizeColumnsToContents()

    def import_txt_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Select TXT data folder")
        if folder:
            self.thickness_label.setText(
                f"Selected TXT folder:\n{folder}\n\nTXT parsing will be connected later."
            )

    def import_table(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(
            self, "Import mapping table", "", "CSV files (*.csv);;All files (*.*)"
        )
        if filename:
            self.thickness_label.setText(
                f"Selected table:\n{filename}\n\nCSV importing will be connected later."
            )
