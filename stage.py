"""Real serial interface for the XY stage controller.

This module contains no GUI code.  Any Python application can reuse
StageController to talk to the Arduino firmware over USB serial.
"""

from __future__ import annotations

import threading
import time
from typing import List, Tuple

import serial
from serial.tools import list_ports


class StageController:
    """Python interface to stage_controller_v2.ino."""

    def __init__(self, baudrate: int = 9600) -> None:
        self.baudrate = baudrate
        self._serial: serial.Serial | None = None
        self._reader_thread: threading.Thread | None = None
        self._stop_reader = threading.Event()
        self._lock = threading.Lock()

        self._x = 0.0
        self._y = 0.0
        self._status = "DISCONNECTED"
        self._last_response = ""
        self._last_error = ""

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------
    @staticmethod
    def available_ports() -> List[str]:
        """Return currently available serial port names."""
        return [port.device for port in list_ports.comports()]

    @property
    def connected(self) -> bool:
        return self._serial is not None and self._serial.is_open

    def connect(self, port: str) -> None:
        """Open the Arduino serial port and start the background reader."""
        if self.connected:
            return

        self._serial = serial.Serial(port, self.baudrate, timeout=0.1)

        # UNO R4 may reset when the serial port opens. Give it a moment.
        time.sleep(1.5)
        self._serial.reset_input_buffer()

        self._stop_reader.clear()
        self._reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
        self._reader_thread.start()

        with self._lock:
            self._status = "IDLE"
            self._last_response = ""
            self._last_error = ""

        self.request_position()
        self.request_status()

    def disconnect(self) -> None:
        """Stop the reader and close the serial port."""
        self._stop_reader.set()

        serial_port = self._serial
        self._serial = None

        if serial_port is not None and serial_port.is_open:
            serial_port.close()

        if self._reader_thread and self._reader_thread.is_alive():
            self._reader_thread.join(timeout=0.5)

        with self._lock:
            self._status = "DISCONNECTED"
            self._last_response = ""
            self._last_error = ""

    # ------------------------------------------------------------------
    # Stage commands
    # ------------------------------------------------------------------
    def move(self, axis: str, position_mm: float) -> None:
        axis = self._validate_axis(axis)
        self._send(f"MOVE {axis} {position_mm:.6f}")

    def move_relative(self, axis: str, distance_mm: float) -> None:
        axis = self._validate_axis(axis)
        self._send(f"MOVEREL {axis} {distance_mm:.6f}")

    def set_origin(self) -> None:
        self._send("SETORIGIN")

    def request_position(self) -> None:
        self._send("POS?")

    def request_status(self) -> None:
        self._send("STATUS?")

    # ------------------------------------------------------------------
    # Read-only state used by GUIs/applications
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
    # Internal serial handling
    # ------------------------------------------------------------------
    def _send(self, command: str) -> None:
        if not self.connected or self._serial is None:
            raise RuntimeError("Stage is not connected.")

        self._serial.write((command + "\n").encode("ascii"))

    def _reader_loop(self) -> None:
        while not self._stop_reader.is_set():
            serial_port = self._serial
            if serial_port is None or not serial_port.is_open:
                break

            try:
                raw = serial_port.readline()
            except (serial.SerialException, OSError) as exc:
                with self._lock:
                    self._last_error = f"Connection lost: {exc}"
                    self._status = "DISCONNECTED"

                try:
                    serial_port.close()
                except (serial.SerialException, OSError):
                    pass

                self._serial = None
                break

            if not raw:
                continue

            line = raw.decode("ascii", errors="replace").strip()
            if line:
                self._process_response(line)

    def _process_response(self, line: str) -> None:
        with self._lock:
            self._last_response = line

            if line.startswith("POS "):
                parts = line.split()
                if len(parts) == 3:
                    try:
                        self._x = float(parts[1])
                        self._y = float(parts[2])
                    except ValueError:
                        self._last_error = f"Invalid position response: {line}"
                return

            if line == "STATUS IDLE":
                self._status = "IDLE"
                if self._last_error == "ERROR BUSY":
                    self._last_error = ""
                return

            if line.startswith("STATUS MOVING "):
                parts = line.split()
                if len(parts) == 3:
                    self._status = f"MOVING {parts[2]}"
                return

            if line == "DONE":
                self._status = "IDLE"
                if self._last_error == "ERROR BUSY":
                    self._last_error = ""
                return

            if line.startswith("ERROR"):
                self._last_error = line
                return

            if line == "OK":
                self._last_error = ""

    @staticmethod
    def _validate_axis(axis: str) -> str:
        axis = axis.strip().upper()
        if axis not in ("X", "Y"):
            raise ValueError("Axis must be 'X' or 'Y'.")
        return axis
