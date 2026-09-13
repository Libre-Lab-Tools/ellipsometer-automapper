"""
ABSTRACT
--------
Real CompleteEASE TCP/IP communication driver for Ellipsometer AutoMapper.

This file intentionally contains only instrument-software communication. It
shares the same public interface as completeease_simulator.py so AutoMapper can
switch between them without changing acquisition logic.
"""

from __future__ import annotations

import socket

from completeease_common import CompleteEASECommunicationError


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 4444


class CompleteEASEClient:
    def __init__(
        self,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        connect_timeout: float = 5.0,
        command_timeout: float = 3600.0,
    ) -> None:
        self.host = host
        self.port = port
        self.connect_timeout = connect_timeout
        self.command_timeout = command_timeout
        self._socket: socket.socket | None = None

    @property
    def connected(self) -> bool:
        return self._socket is not None

    def connect(self) -> None:
        if self.connected:
            return

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(self.connect_timeout)
        try:
            sock.connect((self.host, self.port))
        except OSError as exc:
            sock.close()
            raise CompleteEASECommunicationError(
                f"Could not connect to CompleteEASE at {self.host}:{self.port}: {exc}"
            ) from exc

        sock.settimeout(self.command_timeout)
        self._socket = sock

    def disconnect(self) -> None:
        if self._socket is None:
            return
        try:
            self._socket.close()
        finally:
            self._socket = None

    def list_recipes(self) -> list[str]:
        reply = self._send_command("ListRecipes()")
        return [
            item.strip()
            for item in reply.replace("\r", "").replace("\n", "").split("/")
            if item.strip()
        ]

    def run_recipe(self, recipe_name: str, file_name: str) -> str:
        recipe_name = recipe_name.strip()
        file_name = file_name.strip()

        if not recipe_name or not file_name:
            raise ValueError("recipe_name and file_name cannot be empty.")
        if "/" in file_name or "\\" in file_name:
            raise ValueError("file_name must be a base name, not a path.")

        return self._send_command(f"RunRecipe({recipe_name}/{file_name})")

    def _send_command(self, command: str) -> str:
        if self._socket is None:
            raise CompleteEASECommunicationError(
                "CompleteEASE is not connected. Call connect() first."
            )

        try:
            self._socket.sendall((command + "\r\n").encode("utf-8"))
            data = self._socket.recv(65536)
        except (OSError, socket.timeout) as exc:
            self.disconnect()
            raise CompleteEASECommunicationError(
                f"CompleteEASE communication failed while sending {command!r}: {exc}"
            ) from exc

        if not data:
            self.disconnect()
            raise CompleteEASECommunicationError(
                "CompleteEASE closed the connection without returning a reply."
            )

        return data.decode("latin-1").replace("±", "+-").strip()
