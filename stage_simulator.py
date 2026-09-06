"""Software-only simulator for the XY stage.

The simulator exposes the same public interface as StageController, allowing
stage_panel.py and future applications to run without an Arduino or motors.
"""

from __future__ import annotations

import math
import threading
import time
from typing import List, Tuple


class StageSimulator:
    """Simple one-axis-at-a-time stage simulator with live motion."""

    def __init__(
        self,
        max_speed_mm_s: float = 4.0,
        acceleration_mm_s2: float = 7.5,
        update_period_s: float = 0.02,
    ) -> None:
        self.max_speed = max_speed_mm_s
        self.acceleration = acceleration_mm_s2
        self.update_period = update_period_s

        self._connected = False
        self._x = 0.0
        self._y = 0.0
        self._status = "DISCONNECTED"
        self._last_response = ""
        self._last_error = ""

        self._lock = threading.Lock()
        self._motion_thread: threading.Thread | None = None

    # ------------------------------------------------------------------
    # Connection - mirrors StageController
    # ------------------------------------------------------------------
    @staticmethod
    def available_ports() -> List[str]:
        return ["SIMULATOR"]

    @property
    def connected(self) -> bool:
        return self._connected

    def connect(self, port: str = "SIMULATOR") -> None:
        del port
        with self._lock:
            self._connected = True
            self._status = "IDLE"
            self._last_response = ""
            self._last_error = ""

    def disconnect(self) -> None:
        with self._lock:
            self._connected = False
            self._status = "DISCONNECTED"
            self._last_response = ""
            self._last_error = ""

    # ------------------------------------------------------------------
    # Stage commands - same names as StageController
    # ------------------------------------------------------------------
    def move(self, axis: str, position_mm: float) -> None:
        axis = self._validate_axis(axis)
        self._require_connection()

        with self._lock:
            current = self._x if axis == "X" else self._y
        self._start_move(axis, current, float(position_mm))

    def move_relative(self, axis: str, distance_mm: float) -> None:
        axis = self._validate_axis(axis)
        self._require_connection()

        with self._lock:
            current = self._x if axis == "X" else self._y
        self._start_move(axis, current, current + float(distance_mm))

    def set_origin(self) -> None:
        self._require_connection()

        with self._lock:
            if self._status.startswith("MOVING"):
                self._last_response = "ERROR BUSY"
                self._last_error = "ERROR BUSY"
                return

            self._x = 0.0
            self._y = 0.0
            self._last_response = "OK"
            self._last_error = ""

    def request_position(self) -> None:
        self._require_connection()
        with self._lock:
            self._last_response = f"POS {self._x:.3f} {self._y:.3f}"

    def request_status(self) -> None:
        self._require_connection()
        with self._lock:
            self._last_response = f"STATUS {self._status}"
            if self._status == "IDLE" and self._last_error == "ERROR BUSY":
                self._last_error = ""

    # ------------------------------------------------------------------
    # Read-only state
    # ------------------------------------------------------------------
    @property
    def position(self) -> Tuple[float, float]:
        with self._lock:
            return self._x, self._y

    @property
    def status(self) -> str:
        with self._lock:
            return self._status

    @property
    def last_response(self) -> str:
        with self._lock:
            return self._last_response

    @property
    def last_error(self) -> str:
        with self._lock:
            return self._last_error

    # ------------------------------------------------------------------
    # Simulated motion
    # ------------------------------------------------------------------
    def _start_move(self, axis: str, start: float, target: float) -> None:
        with self._lock:
            if self._status.startswith("MOVING"):
                self._last_response = "ERROR BUSY"
                self._last_error = "ERROR BUSY"
                return

            self._last_response = "OK"
            self._last_error = ""

            if math.isclose(start, target, abs_tol=1e-12):
                self._last_response = "DONE"
                self._status = "IDLE"
                return

            self._status = f"MOVING {axis}"

        self._motion_thread = threading.Thread(
            target=self._simulate_move,
            args=(axis, start, target),
            daemon=True,
        )
        self._motion_thread.start()

    def _simulate_move(self, axis: str, start: float, target: float) -> None:
        distance = target - start
        direction = 1.0 if distance > 0 else -1.0
        total_distance = abs(distance)

        # Trapezoidal profile. If the move is short, it becomes triangular.
        accel_distance = (self.max_speed ** 2) / (2.0 * self.acceleration)
        triangular = 2.0 * accel_distance >= total_distance

        if triangular:
            peak_speed = math.sqrt(total_distance * self.acceleration)
            accel_time = peak_speed / self.acceleration
            cruise_time = 0.0
        else:
            peak_speed = self.max_speed
            accel_time = peak_speed / self.acceleration
            cruise_distance = total_distance - 2.0 * accel_distance
            cruise_time = cruise_distance / peak_speed

        total_time = 2.0 * accel_time + cruise_time
        start_time = time.monotonic()

        while True:
            elapsed = time.monotonic() - start_time
            if elapsed >= total_time:
                travelled = total_distance
            elif elapsed < accel_time:
                travelled = 0.5 * self.acceleration * elapsed * elapsed
            elif elapsed < accel_time + cruise_time:
                accel_part = 0.5 * self.acceleration * accel_time * accel_time
                travelled = accel_part + peak_speed * (elapsed - accel_time)
            else:
                decel_elapsed = elapsed - accel_time - cruise_time
                remaining_time = total_time - elapsed
                # Position from the target during the deceleration phase.
                remaining_distance = 0.5 * self.acceleration * remaining_time * remaining_time
                travelled = total_distance - remaining_distance

            new_position = start + direction * min(travelled, total_distance)

            with self._lock:
                if not self._connected:
                    return
                if axis == "X":
                    self._x = new_position
                else:
                    self._y = new_position

            if elapsed >= total_time:
                break

            time.sleep(self.update_period)

        with self._lock:
            if axis == "X":
                self._x = target
            else:
                self._y = target
            self._status = "IDLE"
            self._last_response = "DONE"
            if self._last_error == "ERROR BUSY":
                self._last_error = ""

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _require_connection(self) -> None:
        if not self.connected:
            raise RuntimeError("Stage is not connected.")

    @staticmethod
    def _validate_axis(axis: str) -> str:
        axis = axis.strip().upper()
        if axis not in ("X", "Y"):
            raise ValueError("Axis must be 'X' or 'Y'.")
        return axis
