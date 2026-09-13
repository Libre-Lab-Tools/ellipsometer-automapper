"""
ABSTRACT
--------
Software-only implementation of the same public interface as StageController.

The simulator lets the Measurement GUI be developed and tested on a laptop
without an Arduino or ellipsometer.  It is intentionally simple: position
changes are immediate because the V1 GUI prototype is focused on layout and
workflow rather than stage-motion timing.
"""

from __future__ import annotations

from typing import List, Tuple


class StageSimulator:
    def __init__(self) -> None:
        self._connected = False
        self._x = 0.0
        self._y = 0.0
        self._status = "DISCONNECTED"
        self._last_error = ""

    @staticmethod
    def available_ports() -> List[str]:
        return ["SIMULATOR"]

    @property
    def connected(self) -> bool:
        return self._connected

    def connect(self, port: str = "SIMULATOR") -> None:
        del port
        self._connected = True
        self._status = "IDLE"
        self._last_error = ""

    def disconnect(self) -> None:
        self._connected = False
        self._status = "DISCONNECTED"

    def move(self, axis: str, position_mm: float) -> None:
        self._require_connection()
        axis = self._validate_axis(axis)
        if axis == "X":
            self._x = float(position_mm)
        else:
            self._y = float(position_mm)

    def move_relative(self, axis: str, distance_mm: float) -> None:
        self._require_connection()
        axis = self._validate_axis(axis)
        if axis == "X":
            self._x += float(distance_mm)
        else:
            self._y += float(distance_mm)

    def wait_until_idle(self, timeout: float = 30.0) -> None:
        del timeout
        self._require_connection()

    def set_origin(self) -> None:
        self._require_connection()
        self._x = 0.0
        self._y = 0.0

    def request_position(self) -> None:
        self._require_connection()

    def request_status(self) -> None:
        self._require_connection()

    @property
    def position(self) -> Tuple[float, float]:
        return self._x, self._y

    @property
    def status(self) -> str:
        return self._status

    @property
    def last_error(self) -> str:
        return self._last_error

    def _require_connection(self) -> None:
        if not self._connected:
            raise RuntimeError("Stage simulator is not connected.")

    @staticmethod
    def _validate_axis(axis: str) -> str:
        axis = axis.strip().upper()
        if axis not in ("X", "Y"):
            raise ValueError("Axis must be X or Y.")
        return axis
