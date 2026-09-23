"""
ABSTRACT
--------
Real CompleteEASE TCP/IP communication driver for Ellipsometer AutoMapper.

This module is the real replacement for completeease_simulator.py. It exposes
exactly the same public interface used by the Measurement workspace:

    connect()
    disconnect()
    list_recipes()
    run_recipe(recipe_name, file_name)

The driver only translates AutoMapper requests into the CompleteEASE remote
command language. It does not know about mapping grids, stage motion, staging
file parsing, experiment folders, plots, or statistics.

For synchronous RunRecipe(), a normal return means CompleteEASE returned a
reply after handling the recipe request. AutoMapper intentionally ignores the
scientific contents of that reply and validates the expected TXT file instead.
TCP/socket failures are translated into CompleteEASECommunicationError so the
mapping sequence can stop cleanly.
"""

from __future__ import annotations

import socket

from completeease_common import CompleteEASECommunicationError


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 4444


class CompleteEASEClient:
    """Small synchronous TCP/IP driver for CompleteEASE remote commands."""

    def __init__(
        self,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        connect_timeout: float = 5.0,
        command_timeout: float = 3600.0,
    ) -> None:
        self.host = host
        self.port = int(port)
        self.connect_timeout = float(connect_timeout)
        self.command_timeout = float(command_timeout)
        self._socket: socket.socket | None = None

    @property
    def connected(self) -> bool:
        """True while the local TCP socket to CompleteEASE is open."""
        return self._socket is not None

    def connect(self) -> None:
        """Open the local TCP connection to CompleteEASE."""
        if self.connected:
            return

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(self.connect_timeout)

        try:
            sock.connect((self.host, self.port))
        except (OSError, socket.timeout) as exc:
            sock.close()
            raise CompleteEASECommunicationError(
                f"Could not connect to CompleteEASE at "
                f"{self.host}:{self.port}: {exc}. "
                "Make sure CompleteEASE is open and running on this computer."
            ) from exc

        # A recipe may take much longer than the initial TCP connection.
        sock.settimeout(self.command_timeout)
        self._socket = sock

    def disconnect(self) -> None:
        """Close the TCP socket. Safe to call when already disconnected."""
        if self._socket is None:
            return

        try:
            self._socket.close()
        finally:
            self._socket = None

    def list_recipes(self) -> list[str]:
        """
        Ask CompleteEASE for the recipes it recognizes in its common recipe
        location and return them as a Python list.
        """
        reply = self._send_command("ListRecipes()")

        return [
            item.strip()
            for item in reply.replace("\r", "").replace("\n", "").split("/")
            if item.strip()
        ]

    def run_recipe(self, recipe_name: str, file_name: str) -> str:
        """
        Run one CompleteEASE recipe synchronously.

        Parameters
        ----------
        recipe_name:
            Recipe name exactly as returned by list_recipes().
        file_name:
            Base file name supplied by AutoMapper, for example X-4_Y2.
            No extension or directory is supplied; the recipe controls where
            CompleteEASE saves its data.

        Returns
        -------
        str
            The raw CompleteEASE reply. AutoMapper does not use this reply for
            scientific values; it reads the recipe-generated TXT file instead.
        """
        recipe_name = recipe_name.strip()
        file_name = file_name.strip()

        if not recipe_name:
            raise ValueError("recipe_name cannot be empty.")
        if not file_name:
            raise ValueError("file_name cannot be empty.")

        for label, value in (("recipe_name", recipe_name), ("file_name", file_name)):
            if "\r" in value or "\n" in value:
                raise ValueError(f"{label} cannot contain line breaks.")

        if "/" in file_name or "\\" in file_name:
            raise ValueError("file_name must be a base name, not a path.")

        return self._send_command(f"RunRecipe({recipe_name}/{file_name})")

    def _send_command(self, command: str) -> str:
        """
        Send one CRLF-terminated CompleteEASE command and return its reply.

        This follows the synchronous communication pattern previously used for
        AutoMapper development: send one command, then wait for CompleteEASE to
        return the command result. A socket failure is fatal to the mapping
        sequence and is translated into CompleteEASECommunicationError.
        """
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
                f"CompleteEASE communication failed while sending "
                f"{command!r}: {exc}. "
                "Make sure CompleteEASE is still open and running on this computer."
            ) from exc

        if not data:
            self.disconnect()
            raise CompleteEASECommunicationError(
                "CompleteEASE closed the connection without returning a reply. "
                "Make sure CompleteEASE is still open and running on this computer."
            )

        # latin-1 safely preserves the byte values returned by the legacy
        # interface; normalize ± for terminals/environments that dislike it.
        return data.decode("latin-1").replace("±", "+-").strip()
