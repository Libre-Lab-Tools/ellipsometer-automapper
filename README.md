# Ellipsometer AutoMapper

## Purpose

Ellipsometer AutoMapper provides predefined XY mapping acquisition for a
J.A. Woollam M-2000 workflow plus a separate Results workspace for mapping
analysis and export.

The application has two main tabs:

- **Measurement** — setup, stage control, acquisition, progress, retakes
- **Results** — import/current data, maps, statistics, ignoring points, exports

The **Help** button opens the in-application user manual.

## Installation

From the project folder:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python main.py
```

Each computer should keep its own local `.venv`.

## Machine configuration

`config.json` contains only machine-specific folders:

```json
{
  "staging_folder": "data/staging",
  "default_experiment_parent": "data/experiments"
}
```

Paths may be relative to the AutoMapper folder or absolute Windows paths.

- `staging_folder` must match the folder where the real CompleteEASE recipes
  save their generated `.SE` and `.txt` files.
- `default_experiment_parent` is the default parent directory where AutoMapper
  creates timestamped experiment folders.

## Measurement backends

The Measurement tab selects the measurement source at runtime:

- **SIMULATOR** — black-box CompleteEASE/recipe/ellipsometer simulator
- **CompleteEASE** — real TCP/IP driver (`127.0.0.1:4444`)

Both expose the same interface:

- `connect()`
- `disconnect()`
- `list_recipes()`
- `run_recipe(recipe_name, file_name)`

This allows development/testing with any combination of simulated or real stage
and simulated or real CompleteEASE.

## Real CompleteEASE setup

Recipes must be available in the normal/common recipe location recognized by
CompleteEASE. Configure each recipe to save its point output into the same
folder configured as `staging_folder` in `config.json`.

AutoMapper supplies filenames such as `X-4_Y2`. The recipe/instrument handles
acquisition; after `RunRecipe(...)` returns, AutoMapper validates the expected
TXT file, copies the available point files into permanent `raw_data`, reparses
the experiment, and continues.

Refer to the CompleteEASE manual for recipe creation, alignment, models,
fitting, and instrument-specific configuration.

## Experiment structure

Each new mapping run creates a unique timestamped folder:

```text
YYYYMMDD_HHMMSS_OptionalMapName/
├── raw_data/
│   ├── X-4_Y2.SE
│   ├── X-4_Y2.txt
│   └── ...
├── results/
└── mapping_results.csv
```

The `results` folder is a convenient destination for user-exported plots and
statistics; processed outputs are not automatically saved.

## Failure behavior

- New readable TXT result → point measured successfully.
- No usable TXT result → point marked failed/missing; mapping continues.
- CompleteEASE communication failure → mapping sequence stops and user is
  notified.
- Abort → current CompleteEASE request is allowed to finish; no additional
  mapping points are requested.

## Current Results features

- Interpolated Map
- Contour Map
- Measured Points + Values
- Pixel / Cell Map
- 3D Surface
- Histogram
- Ignore / Enable valid measurements
- Plot title, colorbar title, axis range and tick controls
- Current-plot export and selected-type batch export
- Optional exported measured-point markers
- Statistics for full included dataset or current plot range
- Statistics CSV export with calculation metadata
- Qualitative interpolated Thickness line profile

See **Help** inside the application for operating instructions.
