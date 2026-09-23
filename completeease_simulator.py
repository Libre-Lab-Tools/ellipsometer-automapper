"""
ABSTRACT
--------
Black-box CompleteEASE + recipe + ellipsometer simulator for AutoMapper.

The simulator exposes the same public interface expected from completeease.py:
    connect()
    disconnect()
    list_recipes()
    run_recipe(recipe_name, file_name)

A simulated recipe writes a dummy .SE file and a realistic CompleteEASE-style
TXT result file into the configured staging folder. Everything after that point
(copying, parsing, plotting, statistics, retakes) is handled by the real
AutoMapper workflow.
"""

from __future__ import annotations

from pathlib import Path
import math
import re
import time

from completeease_common import CompleteEASECommunicationError


_FILE_NAME = re.compile(r"^X(?P<x>-?\d+)_Y(?P<y>-?\d+)$", re.IGNORECASE)


class CompleteEASESimulator:
    """Software-only replacement for CompleteEASE during development."""

    RECIPES = [
        "Thin Film Mapping",
        "Cauchy Mapping",
        "Roughness Mapping",
    ]

    def __init__(self, staging_folder: str | Path, acquisition_delay: float = 0.7) -> None:
        self.staging_folder = Path(staging_folder)
        self.acquisition_delay = float(acquisition_delay)
        self._connected = False

        # Development-only repeating failure cycle:
        #   1 success
        #   2 success
        #   3 SE only / no TXT
        #   4 success
        #   5 success
        #   6 SE + unreadable TXT
        #   7 success
        #   8 success
        #   9 no files
        # then repeat from 1.
        #
        # Coordinates that fail because of this artificial cycle are remembered;
        # the next acquisition of that same coordinate is forced to succeed so
        # retake/recovery behavior can be tested naturally.
        self._run_count = 0
        self._failed_coordinates: set[str] = set()

        self.staging_folder.mkdir(parents=True, exist_ok=True)

    @property
    def connected(self) -> bool:
        return self._connected

    def connect(self) -> None:
        # Reconnecting starts a fresh deterministic simulator test cycle.
        if not self._connected:
            self._run_count = 0
            self._failed_coordinates.clear()
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False

    def list_recipes(self) -> list[str]:
        self._require_connection()
        return list(self.RECIPES)

    def run_recipe(self, recipe_name: str, file_name: str) -> str:
        """
        Simulate one synchronous CompleteEASE recipe measurement.

        The function returns only after the fake files have been written, which
        mirrors the behavior AutoMapper relies on from synchronous RunRecipe().
        """
        self._require_connection()

        if recipe_name not in self.RECIPES:
            raise CompleteEASECommunicationError(
                f"Simulator does not recognize recipe: {recipe_name}"
            )

        match = _FILE_NAME.match(file_name.strip())
        if not match:
            raise ValueError(
                "Simulator file names must use the AutoMapper format X#_Y#."
            )

        x = int(match.group("x"))
        y = int(match.group("y"))

        time.sleep(self.acquisition_delay)

        # Artificially failed coordinates succeed on their next acquisition.
        # The retry does not consume a cycle position, so the repeating failure
        # pattern remains predictable for subsequent new points.
        if file_name in self._failed_coordinates:
            self._failed_coordinates.remove(file_name)
            self._write_se(file_name, recipe_name, x, y)
            values = self._simulated_values(recipe_name, x, y)
            self._write_txt(file_name, recipe_name, values)
            return "; ".join(
                f"{name}={value:.6g}" for name, value in values.items()
            )

        self._run_count += 1
        cycle_position = ((self._run_count - 1) % 9) + 1

        # Position 3: a measurement file exists, but no results TXT is produced.
        # AutoMapper should preserve the SE and show the point as results-unusable.
        if cycle_position == 3:
            self._write_se(file_name, recipe_name, x, y)
            self._failed_coordinates.add(file_name)
            return "SIMULATED_SE_ONLY_NO_TXT"

        # Position 6: both files exist, but the TXT has no readable parameter
        # section. AutoMapper should preserve both raw files and reject Results.
        if cycle_position == 6:
            self._write_se(file_name, recipe_name, x, y)
            self._write_corrupt_txt(file_name, recipe_name)
            self._failed_coordinates.add(file_name)
            return "SIMULATED_UNREADABLE_TXT_RESULT"

        # Position 9: no new SE or TXT is written at all. AutoMapper should treat
        # this as no acquired measurement and leave the point gray.
        if cycle_position == 9:
            self._failed_coordinates.add(file_name)
            return "SIMULATED_NO_FILES"

        # All other cycle positions are successful.
        self._write_se(file_name, recipe_name, x, y)
        values = self._simulated_values(recipe_name, x, y)
        self._write_txt(file_name, recipe_name, values)

        # Real CompleteEASE returns a results string. AutoMapper deliberately
        # ignores its scientific contents and reads the TXT file instead.
        return "; ".join(f"{name}={value:.6g}" for name, value in values.items())

    def _simulated_values(self, recipe_name: str, x: int, y: int) -> dict[str, float]:
        # Smooth coordinate-dependent values make the live maps meaningful and
        # repeatable instead of producing featureless random noise.
        radial = math.sqrt(x * x + y * y)
        thickness = 850.0 + 3.2 * x - 2.1 * y + 8.0 * math.sin((x + y) / 4.0)
        roughness = 7.5 + 0.45 * radial + 0.5 * math.cos(x / 3.0)
        mse = 2.0 + 0.10 * radial + 0.35 * abs(math.sin(y / 2.0))

        if recipe_name == "Roughness Mapping":
            return {
                "MSE": mse,
                "Roughness (nm)": roughness,
                "Thickness # 1 (nm)": thickness,
            }

        if recipe_name == "Cauchy Mapping":
            return {
                "MSE": mse,
                "Roughness (nm)": roughness,
                "Thickness # 1 (nm)": thickness,
                "A": 1.728 + 0.0004 * x,
                "B": 0.00239 + 0.00001 * y,
                "% Thickness Non-uniformity": 5.0 + 0.3 * radial,
                "n of Cauchy @ 632.8 nm": 1.7338 + 0.0003 * x - 0.0002 * y,
            }

        return {
            "MSE": mse,
            "Roughness (nm)": roughness,
            "Thickness # 1 (nm)": thickness,
            "% Thickness Non-uniformity": 4.0 + 0.25 * radial,
        }

    def _write_se(self, file_name: str, recipe_name: str, x: int, y: int) -> None:
        path = self.staging_folder / f"{file_name}.SE"
        path.write_text(
            "SIMULATED COMPLETEEASE DATA\n"
            f"Recipe: {recipe_name}\n"
            f"Coordinate: X={x} mm, Y={y} mm\n",
            encoding="utf-8",
        )

    def _write_txt(
        self,
        file_name: str,
        recipe_name: str,
        values: dict[str, float],
    ) -> None:
        path = self.staging_folder / f"{file_name}.txt"

        lines = [
            "HEADER_START",
            "Software\tCompleteEASE Simulator",
            f"Recipe\t{recipe_name}",
            "HEADER_END",
            "PARAMETER\tVALUE\tERROR BAR",
        ]

        for name, value in values.items():
            lines.append(f"{name}\t{value:.8g}\t0")

        path.write_text("\n".join(lines) + "\n", encoding="utf-8")


    def _write_corrupt_txt(self, file_name: str, recipe_name: str) -> None:
        """Write a TXT file with no readable CompleteEASE parameter section."""
        path = self.staging_folder / f"{file_name}.txt"
        path.write_text(
            "HEADER_START\n"
            "Software\tCompleteEASE Simulator\n"
            f"Recipe\t{recipe_name}\n"
            "HEADER_END\n"
            "SIMULATED RESULT FILE WITHOUT READABLE PARAMETERS\n",
            encoding="utf-8",
        )

    def _require_connection(self) -> None:
        if not self._connected:
            raise CompleteEASECommunicationError(
                "CompleteEASE simulator is not connected."
            )
