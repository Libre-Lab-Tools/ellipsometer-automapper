"""
ABSTRACT
--------
This module converts a folder of CompleteEASE exported TXT result files into a
single pandas DataFrame.

The parser is intentionally independent from the ellipsometer, XY stage, GUI,
and experiment folder logic.  It only needs a folder.  Coordinates are read
from each filename and fitted parameter/value pairs are read from the TXT file.

New AutoMapper filenames:
    X-4_Y2.txt
    X0_Y0.txt

Legacy filenames from the user's previous mapping workflow are also accepted:
    (-1,0).txt
    (0,-2).txt

The CompleteEASE header and ERROR BAR column are ignored.  Every parameter
actually present in the TXT files is retained as its own table column.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd


# Strict new filename format.  V1 intentionally supports integer millimeters.
_NEW_NAME = re.compile(r"^X(?P<x>-?\d+)_Y(?P<y>-?\d+)\.txt$", re.IGNORECASE)

# Legacy format, retained so old mapping folders can still be imported.
_LEGACY_NAME = re.compile(r"^\((?P<x>-?\d+),(?P<y>-?\d+)\)\.txt$", re.IGNORECASE)


def coordinate_from_filename(filename: str) -> tuple[int, int] | None:
    """Return (X, Y) from a supported TXT filename, or None if unsupported."""
    for pattern in (_NEW_NAME, _LEGACY_NAME):
        match = pattern.match(filename)
        if match:
            return int(match.group("x")), int(match.group("y"))
    return None


def parse_completeease_txt(path: str | Path) -> dict[str, float]:
    """
    Read fitted parameter values from one CompleteEASE TXT export.

    Everything before the line beginning with PARAMETER is ignored.
    Only the first two tab-separated columns after that line are used:
    PARAMETER and VALUE.  Error bars are intentionally ignored for V1.
    """
    path = Path(path)
    parameters: dict[str, float] = {}
    in_parameter_section = False

    with path.open("r", encoding="utf-8", errors="replace") as file:
        for raw_line in file:
            line = raw_line.rstrip("\r\n")

            if not in_parameter_section:
                if line.upper().startswith("PARAMETER\tVALUE"):
                    in_parameter_section = True
                continue

            parts = line.split("\t")
            if len(parts) < 2:
                continue

            name = parts[0].strip()
            value_text = parts[1].strip()

            if not name or not value_text:
                continue

            try:
                parameters[name] = float(value_text)
            except ValueError:
                # A non-numeric fitted result is not useful for mapping.
                continue

    return parameters


def parse_txt_folder(folder: str | Path) -> pd.DataFrame:
    """
    Parse every supported CompleteEASE TXT file in a folder.

    Returns
    -------
    pandas.DataFrame
        Columns begin with X and Y, followed by every fitted parameter found
        anywhere in the folder.  Missing values are NaN.  An Included column
        is added for Results-page enable/disable processing.
    """
    folder = Path(folder)
    if not folder.is_dir():
        raise FileNotFoundError(f"TXT folder does not exist: {folder}")

    rows: list[dict[str, object]] = []
    seen_coordinates: set[tuple[int, int]] = set()

    for path in sorted(folder.glob("*.txt")):
        coordinate = coordinate_from_filename(path.name)
        if coordinate is None:
            # Ignore unrelated TXT files rather than guessing coordinates.
            continue

        x, y = coordinate
        if coordinate in seen_coordinates:
            raise ValueError(
                f"Duplicate mapping coordinate X={x}, Y={y} in folder: {folder}"
            )
        seen_coordinates.add(coordinate)

        row: dict[str, object] = {"X": x, "Y": y}
        row.update(parse_completeease_txt(path))
        rows.append(row)

    if not rows:
        raise ValueError(
            "No supported mapping TXT files were found. "
            "Expected names such as X-4_Y2.txt or legacy (-1,0).txt."
        )

    df = pd.DataFrame(rows)

    # Keep coordinate columns first. Parameter columns remain exactly as
    # CompleteEASE reported them.
    parameter_columns = [c for c in df.columns if c not in ("X", "Y")]
    df = df[["X", "Y", *parameter_columns]]

    # A mapping-style order is convenient when viewing the table:
    # top row first (largest Y), then left-to-right X.
    df = df.sort_values(["Y", "X"], ascending=[False, True]).reset_index(drop=True)

    # Inclusion is analysis metadata, not raw CompleteEASE data.
    df["Included"] = True
    return df


def load_csv_table(path: str | Path) -> pd.DataFrame:
    """
    Load a previously saved/edited mapping CSV into the common table format.

    V1 requires explicit X and Y columns.  Included is added automatically if
    the CSV does not already contain it.
    """
    path = Path(path)
    df = pd.read_csv(path)

    if "X" not in df.columns or "Y" not in df.columns:
        raise ValueError("Imported table must contain X and Y columns.")

    # Coordinates are intentionally integer millimeters in V1.
    df["X"] = pd.to_numeric(df["X"], errors="raise").astype(int)
    df["Y"] = pd.to_numeric(df["Y"], errors="raise").astype(int)

    if "Included" not in df.columns:
        df["Included"] = True
    else:
        # Be tolerant of CSV text values such as TRUE/FALSE.
        df["Included"] = (
            df["Included"]
            .astype(str)
            .str.strip()
            .str.lower()
            .map({"true": True, "1": True, "yes": True,
                  "false": False, "0": False, "no": False})
            .fillna(True)
            .astype(bool)
        )

    return df.reset_index(drop=True)
