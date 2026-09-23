# Ellipsometer AutoMapper

## Purpose

Ellipsometer AutoMapper provides predefined XY grid mapping and user-added
single-point acquisition for a J.A. Woollam M-2000 workflow, plus a separate
Results workspace for mapping analysis and export.

The application has two main tabs:

- **Measurement** — setup, grid acquisition, single-point acquisition, progress, remeasurement
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

**Enable Export Measurement Results in every AutoMapper recipe.** This is not a
default CompleteEASE behavior. The exported TXT file is AutoMapper's source of
truth for deciding whether a point succeeded and for loading fitted parameters.
CompleteEASE must already be open and running before the real backend connects.

Long, descriptive recipe names are recommended, for example:
`Purpose - Angle(s) - Acquisition time - Alignment - Model - Model restrictions`.

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
  mapping points are requested and no automatic return-to-center motion occurs.
- A normally completed automatic grid sequence returns the stage to `(0, 0)`.
- Single-point acquisition and Measure Selected Point do not automatically
  return the stage to center.


## Measurement workflow additions

- Grid mapping uses a top-left snake path: right, down, left, down, right.
- The automatic-stage jog step defaults to 2 mm and uses explicit `▲` / `▼`
  increment buttons to avoid unreliable native Windows spin-box arrows.
- `Add Single Point...` adds arbitrary user-chosen points to the current
  experiment. The Measurement progress area acts as a live XY scatter display
  for single-point datasets.
- `Measure Selected Point` can acquire a pending/failed point or remeasure an
  existing valid point.
- `Clear Current Data` clears the current GUI dataset without deleting the
  saved experiment folder. The next acquisition creates a new timestamp folder.
- Once an experiment begins, the acquisition definition is locked: recipe, map
  name, save location, grid, spacing, stage type, quality settings, stage
  connection/origin, and manual-stage center coordinates cannot be changed.
  Clear Current Data to start a new setup. Jogging remains available for adding
  single points to the current experiment.
- A new grid sequence cannot start while a current experiment exists; Add Single
  Point and Measure Selected Point continue to use the current experiment.
- Manual single-point coordinate fields and jog-step controls use explicit
  `▲` / `▼` buttons with direct value changes rather than native spin-box arrows.

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
- Temporary Point Statistics mode for one or more clicked measured points;
  Ctrl+click multi-selection uses Qt keyboard modifiers for Windows reliability
- Statistics CSV export with calculation metadata
- Qualitative interpolated Thickness line profile

See **Help** inside the application for operating instructions.


## V1.16 Windows launch/setup helpers

- `Setup AutoMapper.bat` creates a computer-local `.venv` and installs
  `requirements.txt`.
- `Run AutoMapper.bat` launches `main.py` using that local environment.
- `LAB_PC_INSTALL.txt` contains step-by-step lab-computer installation
  instructions.
- The batch files use paths relative to the project directory and are therefore
  not computer-specific.


## V1.17 launchers

- `Run AutoMapper.bat` launches the GUI with `pythonw.exe`; no persistent
  command window remains open.
- `Run AutoMapper Debug.bat` launches with `python.exe` and keeps the terminal
  visible so Python errors can be inspected.


## V1.21 stage-position marker

When the automatic XY stage (or simulator) is connected, the Measurement
progress map shows a small red stage-position marker in relative X/Y
coordinates. Jogging and automatic moves update the marker. `Set Mapping
Center` redefines the current physical position as `(0, 0)`, so the marker
moves to the map origin without commanding physical motion.

Point Statistics instructions now explicitly describe Ctrl+click
multi-selection and note that closing the window clears that selection.


## V1.22 raw-data and operator-display refinements

- Newly produced `.SE` and `.txt` files are preserved before AutoMapper tries
  to interpret the TXT result. Raw data are therefore not discarded because of
  a parser/model/export problem.
- `.SE` represents acquired measurement data. A point with no new `.SE` remains
  gray; a point with acquired data but no usable TXT Results is shown as a red X.
- Repeated coordinate attempts are preserved under `raw_data/repeated_attempts`
  when needed so a failed remeasurement cannot destroy a previously valid pair.
- Live experiment parsing ignores unusable TXT files and requires a matching `.SE`;
  standalone TXT-folder import remains supported without requiring `.SE` files.
- The stage-position marker is a smaller filled red laser dot so the underlying
  measurement-status color remains visible. Manual-stage measurements use the
  operator-confirmed coordinate as the displayed stage position.
- Manual-stage mapping prompts show the requested map/absolute coordinates in
  large text for easier viewing while operating through a glovebox.
