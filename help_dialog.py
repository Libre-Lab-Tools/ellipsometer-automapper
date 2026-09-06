"""
ABSTRACT
--------
Pop-out Help window containing concise AutoMapper operating guidance.

Help is intentionally separate from the two main working screens so it does
not consume permanent GUI space.
"""

from PyQt6.QtWidgets import QDialog, QLabel, QScrollArea, QVBoxLayout, QWidget


class HelpDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("AutoMapper Help")
        self.resize(620, 650)

        root = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)

        content = QWidget()
        layout = QVBoxLayout(content)

        text = QLabel(
            "<h2>Before Mapping</h2>"
            "<p><b>1. Test the sample at the intended mapping center.</b><br>"
            "Confirm that the ellipsometer can align and acquire useful data.</p>"

            "<p><b>2. Test the selected CompleteEASE recipe manually.</b><br>"
            "Verify that the optical model represents the sample adequately and "
            "that fitted parameters/MSE are reasonable.</p>"

            "<p><b>3. Configure alignment behavior before the mapping run.</b><br>"
            "If the recipe performs alignment at every point, no separate "
            "pre-alignment is normally needed. If alignment steps are skipped, "
            "perform the required alignment beforehand and save the appropriate "
            "alignment/Z information in the recipe.</p>"

            "<p><b>4. Automatic XY stage:</b><br>"
            "Jog the sample until the ellipsometer beam is over the desired "
            "center of the sampling area. Then select <b>Set Mapping Center</b>. "
            "Make sure the selected mapping span fits on the sample.</p>"

            "<h2>Measurement</h2>"
            "<p>The mapping grid shows pending, current, completed, and "
            "high-MSE measurements. A measured point can later be selected and "
            "retaken.</p>"

            "<h2>Results</h2>"
            "<p>Results can come from the current measurement, an imported TXT "
            "folder, or an imported CSV table. Move the mouse across the map to "
            "read X, Y, and the interpolated parameter value. Click an actual "
            "measured point to select it and Ignore/Enable it. Ignored points "
            "remain visible but do not contribute to interpolation or statistics.</p>"

            "<h2>Raw Data</h2>"
            "<p>Future AutoMapper measurements use integer-mm filenames such as "
            "<b>X-4_Y2.txt</b>. Legacy files named like <b>(-1,0).txt</b> can "
            "also be imported by the parser.</p>"
        )
        text.setWordWrap(True)
        layout.addWidget(text)
        layout.addStretch()

        scroll.setWidget(content)
        root.addWidget(scroll)
