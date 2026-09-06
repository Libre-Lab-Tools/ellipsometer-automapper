"""
ABSTRACT
--------
This module contains data-analysis functions used by the Results page.

It knows nothing about CompleteEASE, recipes, stage motion, or acquisition.
Its only input is the current mapping DataFrame.  It determines plottable
numeric parameters, filters ignored points, creates an interpolated surface,
and calculates statistics.

Because it is independent from acquisition, the same functions can be used
after a live map, after importing a TXT folder, or after importing a CSV table.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.interpolate import griddata


@dataclass
class MapData:
    """Container returned to the GUI for one selected plotting parameter."""
    x_axis: np.ndarray
    y_axis: np.ndarray
    z_grid: np.ndarray
    included: pd.DataFrame
    excluded: pd.DataFrame


def available_parameters(df: pd.DataFrame) -> list[str]:
    """Return numeric columns that make sense as mapped parameters."""
    if df is None or df.empty:
        return []

    parameters: list[str] = []
    for column in df.columns:
        if column in ("X", "Y", "Included"):
            continue

        numeric = pd.to_numeric(df[column], errors="coerce")
        if numeric.notna().any():
            parameters.append(column)

    # Prefer a Thickness parameter by default, then MSE, then everything else.
    thickness = [p for p in parameters if "thickness" in p.lower()
                 and "non-uniformity" not in p.lower()]
    mse = [p for p in parameters if p.strip().lower() == "mse"]
    rest = [p for p in parameters if p not in thickness and p not in mse]
    return thickness + mse + rest


def build_map(df: pd.DataFrame, parameter: str, resolution: int = 180) -> MapData:
    """
    Build an interpolated map using only Included=True measurements.

    Cubic interpolation is attempted first for normal square mapping grids.
    If the available data are insufficient for cubic interpolation, the
    function falls back to linear and finally nearest-neighbor interpolation.
    """
    work = df.copy()
    work[parameter] = pd.to_numeric(work[parameter], errors="coerce")
    work["X"] = pd.to_numeric(work["X"], errors="coerce")
    work["Y"] = pd.to_numeric(work["Y"], errors="coerce")

    included_mask = work["Included"].astype(bool) & work[parameter].notna()
    excluded_mask = (~work["Included"].astype(bool)) & work[parameter].notna()

    included = work.loc[included_mask].copy()
    excluded = work.loc[excluded_mask].copy()

    if included.empty:
        raise ValueError(f"No included values are available for {parameter}.")

    xmin, xmax = included["X"].min(), included["X"].max()
    ymin, ymax = included["Y"].min(), included["Y"].max()

    # A map needs finite area. Single-line datasets can still display measured
    # points, but interpolation is not meaningful.
    if xmin == xmax or ymin == ymax:
        x_axis = np.linspace(xmin - 0.5, xmax + 0.5, resolution)
        y_axis = np.linspace(ymin - 0.5, ymax + 0.5, resolution)
        z_grid = np.full((resolution, resolution), np.nan)
        return MapData(x_axis, y_axis, z_grid, included, excluded)

    x_axis = np.linspace(xmin, xmax, resolution)
    y_axis = np.linspace(ymin, ymax, resolution)
    grid_x, grid_y = np.meshgrid(x_axis, y_axis)

    points = included[["X", "Y"]].to_numpy(dtype=float)
    values = included[parameter].to_numpy(dtype=float)

    z_grid = None
    for method in ("cubic", "linear", "nearest"):
        try:
            z_grid = griddata(points, values, (grid_x, grid_y), method=method)
            if z_grid is not None and np.isfinite(z_grid).any():
                break
        except Exception:
            z_grid = None

    if z_grid is None:
        z_grid = np.full_like(grid_x, np.nan, dtype=float)

    return MapData(x_axis, y_axis, z_grid, included, excluded)


def statistics_for(df: pd.DataFrame, parameter: str) -> dict[str, float]:
    """Calculate statistics using only included, numeric measurements."""
    work = df.loc[df["Included"].astype(bool)].copy()
    values = pd.to_numeric(work[parameter], errors="coerce").dropna().to_numpy()

    if len(values) == 0:
        return {}

    mean = float(np.mean(values))
    minimum = float(np.min(values))
    maximum = float(np.max(values))
    std = float(np.std(values, ddof=1)) if len(values) > 1 else 0.0
    range_percent = (
        100.0 * (maximum - minimum) / mean if mean != 0 else float("nan")
    )

    return {
        "Mean": mean,
        "Std. Dev.": std,
        "Minimum": minimum,
        "Maximum": maximum,
        "Range (%)": range_percent,
    }


def interpolated_value(map_data: MapData, x: float, y: float) -> float | None:
    """
    Return the interpolated grid value nearest the cursor position.

    The map is already densely interpolated for display, so this is fast enough
    for continuous mouse-position readout without recomputing interpolation.
    """
    if map_data.z_grid.size == 0:
        return None

    ix = int(np.argmin(np.abs(map_data.x_axis - x)))
    iy = int(np.argmin(np.abs(map_data.y_axis - y)))
    value = map_data.z_grid[iy, ix]

    if not np.isfinite(value):
        return None
    return float(value)
