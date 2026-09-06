"""Quick operating instructions."""

from PyQt6.QtWidgets import QLabel, QScrollArea, QVBoxLayout, QWidget


class HelpPage(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        root = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        layout = QVBoxLayout(content)

        text = QLabel(
            "<h2>XY Mapping – Quick Guide</h2>"
            "<h3>Setup</h3>"
            "<p>Select recipe, map name, odd square grid, spacing, and stage type.</p>"
            "<p>For the automatic stage, align the ellipsometer beam with the center "
            "of the desired sampling area and select <b>Set Origin</b>.</p>"
            "<h3>Measurement</h3>"
            "<p>Start the mapping from the Measurement page. The grid shows progress "
            "and the table fills as points are measured.</p>"
            "<p>For a manual stage, follow the coordinate prompt for each point.</p>"
            "<h3>Retake</h3>"
            "<p>After a run, click a measured point in the progress grid and choose "
            "<b>Retake Selected Point</b>.</p>"
            "<h3>Results</h3>"
            "<p>Refresh plots from the current table, or later import a TXT folder or CSV table.</p>"
            "<h3>Mapping span</h3>"
            "<p>The displayed mapping span is (N−1) × spacing, corresponding to the "
            "outer measurement-center positions. Version 1 uses odd square grids and "
            "a maximum span of 20 × 20 mm.</p>"
        )
        text.setWordWrap(True)
        layout.addWidget(text)
        layout.addStretch()
        scroll.setWidget(content)
        root.addWidget(scroll)
