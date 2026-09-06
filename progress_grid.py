"""Interactive progress grid for mapping measurements."""

from PyQt6.QtCore import QPointF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPen, QBrush
from PyQt6.QtWidgets import QWidget

from mapping_model import MappingModel


class ProgressGrid(QWidget):
    point_selected = pyqtSignal(int)

    def __init__(self, model: MappingModel, parent=None) -> None:
        super().__init__(parent)
        self.model = model
        self.selected_index: int | None = None
        self._screen_points: dict[int, QPointF] = {}
        self.setMinimumSize(360, 320)

    def refresh(self) -> None:
        self.update()

    def paintEvent(self, event) -> None:
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(45, 25, -25, -45)

        painter.setPen(QPen(QColor("#808080"), 1))
        painter.drawLine(rect.left(), rect.bottom(), rect.right(), rect.bottom())
        painter.drawLine(rect.left(), rect.top(), rect.left(), rect.bottom())

        if not self.model.points:
            return

        xs = [p.x_mm for p in self.model.points]
        ys = [p.y_mm for p in self.model.points]
        xmin, xmax = min(xs), max(xs)
        ymin, ymax = min(ys), max(ys)
        if xmax == xmin:
            xmax += 1
            xmin -= 1
        if ymax == ymin:
            ymax += 1
            ymin -= 1

        self._screen_points.clear()
        colors = {
            "PENDING": QColor("#b8b8b8"),
            "CURRENT": QColor("#f0c84b"),
            "MEASURED": QColor("#56a66f"),
            "ERROR": QColor("#c95c5c"),
        }

        for point in self.model.points:
            sx = rect.left() + (point.x_mm - xmin) / (xmax - xmin) * rect.width()
            sy = rect.bottom() - (point.y_mm - ymin) / (ymax - ymin) * rect.height()
            center = QPointF(sx, sy)
            self._screen_points[point.index] = center

            pen = QPen(QColor("#202020"), 1)
            if point.index == self.selected_index:
                pen = QPen(QColor("#2769b0"), 3)

            painter.setPen(pen)
            painter.setBrush(QBrush(colors.get(point.status, colors["PENDING"])))
            painter.drawEllipse(center, 9, 9)

        painter.setPen(QPen(QColor("#404040"), 1))
        painter.drawText(int(rect.center().x() - 20), self.height() - 10, "X (mm)")
        painter.save()
        painter.translate(12, rect.center().y() + 20)
        painter.rotate(-90)
        painter.drawText(0, 0, "Y (mm)")
        painter.restore()

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        pos = event.position()
        nearest = None
        nearest_distance = None

        for index, center in self._screen_points.items():
            distance = ((pos.x() - center.x()) ** 2 + (pos.y() - center.y()) ** 2) ** 0.5
            if distance <= 16 and (nearest_distance is None or distance < nearest_distance):
                nearest = index
                nearest_distance = distance

        if nearest is not None:
            self.selected_index = nearest
            self.point_selected.emit(nearest)
            self.update()
