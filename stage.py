"""
ABSTRACT
--------
Reusable serial interface for the Low-Profile Automatic XY Stage.

This module contains no GUI or ellipsometer logic.  It sends the generic
MOVE/MOVEREL/SETORIGIN/POS?/STATUS? protocol to the Arduino UNO R4 stage
controller.  Other applications can reuse this class independently.
"""

from __future__ import annotations

import threading
import time
from typing import List, Tuple

import serial
from serial.tools import list_ports


class StageController:
    """Python interface to the Arduino XY-stage firmware."""

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

    @staticmethod
    def available_ports() -> List[str]:
        return [port.device for port in list_ports.comports()]

    @property
    def connected(self) -> bool:
        return self._serial is not None and self._serial.is_open

    def connect(self, port: str) -> None:
        if self.connected:
            return

        self._serial = serial.Serial(port, self.baudrate, timeout=0.1)
        time.sleep(1.5)  # UNO R4 may reset when its serial port opens.
        self._serial.reset_input_buffer()

        self._stop_reader.clear()
        self._reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
        self._reader_thread.start()

        with self._lock:
            self._status = "IDLE"
            self._last_response = "CONNECTED"
            self._last_error = ""

        self.request_position()
        self.request_status()

    def disconnect(self) -> None:
        self._stop_reader.set()

        serial_port = self._serial
        self._serial = None
        if serial_port is not None and serial_port.is_open:
            serial_port.close()

        if self._reader_thread and self._reader_thread.is_alive():
            self._reader_thread.join(timeout=0.5)

        with self._lock:
            self._status = "DISCONNECTED"

    def move(self, axis: str, position_mm: float) -> None:
        self._send(f"MOVE {self._validate_axis(axis)} {position_mm:.6f}")

    def move_relative(self, axis: str, distance_mm: float) -> None:
        self._send(f"MOVEREL {self._validate_axis(axis)} {distance_mm:.6f}")

    def set_origin(self) -> None:
        self._send("SETORIGIN")

    def request_position(self) -> None:
        self._send("POS?")

    def request_status(self) -> None:
        self._send("STATUS?")

    @property
    def position(self) -> Tuple[float, float]:
        with self._lock:
            return self._x, self._y

    @property
    def status(self) -> str:
        with self._lock:
            return self._status

    @property
    def last_error(self) -> str:
        with self._lock:
            return self._last_error

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
                    self._last_error = str(exc)
                    self._status = "DISCONNECTED"
                break

            if raw:
                line = raw.decode("ascii", errors="replace").strip()
                if line:
                    self._process_response(line)

    def _process_response(self, line: str) -> None:
        with self._lock:
            if line.startswith("POS "):
                parts = line.split()
                if len(parts) == 3:
                    try:
                        self._x = float(parts[1])
                        self._y = float(parts[2])
                    except ValueError:
                        self._last_error = f"Invalid position response: {line}"
                return

            if line == "STATUS IDLE" or line == "DONE":
                self._status = "IDLE"
                return

            if line.startswith("STATUS MOVING "):
                self._status = " ".join(line.split()[1:])
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
            raise ValueError("Axis must be X or Y.")
        return axis
