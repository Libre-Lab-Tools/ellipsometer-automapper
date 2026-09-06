"""Shared mapping experiment state."""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class MappingPoint:
    index: int
    x_mm: float
    y_mm: float
    status: str = "PENDING"  # PENDING, CURRENT, MEASURED, ERROR
    results: dict[str, Any] = field(default_factory=dict)


class MappingModel:
    MAX_MAP_SPAN_MM = 20.0

    def __init__(self) -> None:
        self.recipe = ""
        self.map_name = "Sample01"
        self.grid_size = 5
        self.spacing_mm = 2.0
        self.stage_type = "Manual Stage"
        self.points: list[MappingPoint] = []
        self.current_index: int | None = None
        self.running = False
        self.generate_points()

    @property
    def mapping_span_mm(self) -> float:
        return (self.grid_size - 1) * self.spacing_mm

    @property
    def point_count(self) -> int:
        return self.grid_size * self.grid_size

    @property
    def valid_range(self) -> bool:
        return self.mapping_span_mm <= self.MAX_MAP_SPAN_MM

    def generate_points(self) -> None:
        """Generate a centered square grid in snake order."""
        n = self.grid_size
        d = self.spacing_mm
        half = (n - 1) / 2.0

        points: list[MappingPoint] = []
        index = 1

        for row in range(n):
            y = (row - half) * d
            x_indices = range(n) if row % 2 == 0 else range(n - 1, -1, -1)
            for col in x_indices:
                x = (col - half) * d
                points.append(MappingPoint(index=index, x_mm=x, y_mm=y))
                index += 1

        self.points = points
        self.current_index = None
        self.running = False

    def reset_progress(self) -> None:
        for point in self.points:
            point.status = "PENDING"
            point.results.clear()
        self.current_index = None
        self.running = False

    def point_by_index(self, point_index: int) -> MappingPoint | None:
        return next((p for p in self.points if p.index == point_index), None)
