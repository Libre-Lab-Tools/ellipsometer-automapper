# Ellipsometer AutoMapper — V1 GUI Prototype

## Abstract

This is the first integrated AutoMapper prototype for the J.A. Woollam M-2000
mapping workflow discussed for the Low-Profile Automatic XY Stage.

The purpose of V1 is to lock down the user interface and reusable data-analysis
architecture before connecting the CompleteEASE acquisition loop.

## Main interface

The application intentionally has only two main screens:

- **Measurement** — setup + acquisition + progress + live table + retake
- **Results** — imported/current data + statistics + interactive maps

**Help** is a pop-out window in the upper-right.

## What works in V1

### Measurement
- Odd square grids: 3×3, 5×5, 7×7, 9×9
- Integer spacing: 1–5 mm
- Mapping-span calculation and 20 mm range validation
- Windows-safe map-name validation
- Manual Stage / Low-Profile Automatic XY Stage selection
- Real COM-port discovery plus simulator
- Simplified automatic-stage controls:
  - connect
  - jog
  - jog step
  - Set Mapping Center
- Optional high-MSE warning threshold
- Interactive progress grid
- Live dynamic table
- Retake selected point
- Simulated Thickness/MSE acquisition so the GUI can be tested without CompleteEASE

### Results
- Receives the current table from Measurement
- **Import TXT Folder**
- **Import Table** (CSV)
- Dynamic parameter selector
- One interpolated map at a time
- Actual measured XY points plotted over the colored map
- Mouse-position readout below the graph:
  - X
  - Y
  - interpolated parameter value
- Click an actual measured point:
  - select it
  - highlight its table row
  - show the measured parameter value and MSE
- Ignore / Enable selected point
- Ignored points are red X markers and are excluded from interpolation/statistics
- Vertical statistics table
- Standard Matplotlib toolbar for zoom/pan/save-image

### TXT parser
The parser reads parameter/value pairs only after:

`PARAMETER    VALUE    ERROR BAR`

The CompleteEASE header and error bars are ignored.

Supported coordinate filenames:

**New AutoMapper format**
- `X-4_Y2.txt`
- `X0_Y0.txt`

**Legacy format**
- `(-1,0).txt`
- `(0,-2).txt`

All fitted parameters found in the TXT files are retained as dynamic columns.

## What is intentionally not connected yet

- CompleteEASE `RunRecipe(...)`
- Common Recipe list
- Temporary Staging Data copying
- Permanent experiment-folder creation
- Automatic raw-data archiving
- Automatic baseline result export
- Manual-stage coordinate prompts during real acquisition

These are intentionally left for the next stage so the GUI/data behavior can be
tested first.

## Install

From the project folder:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python main.py
```

Each computer should have its own local `.venv`. Do not sync `.venv`.

## Intended future acquisition flow

1. User selects experiment destination.
2. AutoMapper creates the experiment folder.
3. CompleteEASE writes the current point into **Temporary Staging Data**.
4. AutoMapper copies the exact `X#_Y#.SE` and `X#_Y#.txt` pair into the
   experiment `raw_data` folder.
5. `data_parser.py` rebuilds the table from that permanent raw-data folder.
6. Measurement GUI updates.
7. Repeat.
8. At completion, baseline CSV/statistics/default maps are saved automatically.

## V1.1 GUI revision

This revision applies the first round of hands-on GUI feedback.

### Measurement
- Low-Profile Automatic XY Stage is now the default.
- Measurement Quality is a pop-out dialog.
- Valid maps no longer show a redundant "mapping definition is valid" message.
- Progress grid shows integer-mm coordinate tick labels.
- Live table displays `X (mm)` and `Y (mm)`.
- Analysis-only `Included` is hidden from the Measurement table.
- Progress-map points and table rows select each other.
- Retake has a usage tooltip and selected-coordinate readout.
- Combo-box/input text has additional left padding.

### Results
- The data section is now named **Mapping Data**.
- **Parameter Statistics** shows all numeric parameters simultaneously as columns.
- Plot and colorbar use a fixed width ratio when the Results splitter is resized.
- The default Matplotlib toolbar is removed.
- `Plot Options…` contains Save Current Plot, Save All Plots, and Reset Plot Settings.
- Plot type selector supports:
  - Interpolated Map
  - Measured Points + Values
  - Pixel / Cell Map
- Mouse movement reports X, Y, and the interpolated parameter value below the graph.
- Clicking a measured point selects/highlights the matching Mapping Data row.
- Clicking a Mapping Data row highlights the matching point on the graph.
- Ignore / Enable applies globally to all parameters and statistics.
- Click the plot title to edit it.
- Click an axis tick label to choose Auto / 1 / 2 / 3 / 5 mm tick spacing.

### Navigation
- Measurement and Results now use real application tabs.
- Help remains a pop-out control at the upper-right of the tab bar.


## V1.2 visual cleanup

- Removed the prototype-development banner from Measurement.
- Start Measurement is larger and green; Abort is larger and red.
- X/Y coordinate labels on the Measurement grid have more space from the axes.
- Browse is smaller and separated from the save-location field.
- Measurement Quality is a compact button.
- Measurement and Results tabs are larger and styled as primary application workspaces.


## V1.5 interaction revision

### Measurement
- Jog arrows are larger and visually heavier.
- Clicking an already-selected map point deselects it.
- Clicking an already-selected live-table row deselects it.

### Results
- Ignore / Enable is now a small secondary button beside the selected-point information.
- Mapping Data uses the same strong blue full-row selection as Measurement.
- Clicking an already-selected plot point or table row deselects it.
- Parameter selector is wider for long CompleteEASE parameter names.
- Interpolated maps end exactly at the outer measurement coordinates.
- Colorbar labels can be edited by clicking the colorbar label.
- Exported figures omit the temporary blue selected-point ring.

## V1.6 - real file-pipeline simulator

This revision replaces the old in-memory fake Thickness/MSE generator with a
black-box CompleteEASE simulator that uses the same interface planned for the
real `completeease.py` driver.

### Portable configuration

`config.json` contains machine-specific settings. Relative paths are resolved
from the folder containing the AutoMapper code, so the default development
configuration works anywhere the project folder is copied:

```json
{
  "staging_folder": "data/staging",
  "default_experiment_parent": "data/experiments",
  "completeease_backend": "simulator"
}
```

With the project located at:

`C:\Users\raulm\Desktop\LibreLab Tools\Ellipsometer AutoMapper`

the default folders therefore resolve to:

- `C:\Users\raulm\Desktop\LibreLab Tools\Ellipsometer AutoMapper\data\staging`
- `C:\Users\raulm\Desktop\LibreLab Tools\Ellipsometer AutoMapper\data\experiments`

On the ellipsometer computer, `staging_folder` can instead be changed to the
actual folder already configured inside the CompleteEASE recipes, and
`completeease_backend` can be changed from `simulator` to `real`.

### Experiment folders

The Save Location field is a parent directory only. Each Start Measurement
creates a unique folder:

`YYYYMMDD_HHMMSS_OptionalMapName`

with:

- `raw_data/`
- `results/`
- `mapping_results.csv`

The map name is optional because the timestamp guarantees uniqueness.

### CompleteEASE simulator

The simulator exposes:

- `connect()`
- `disconnect()`
- `list_recipes()`
- `run_recipe(recipe_name, file_name)`

It returns three fake recipes and writes realistic CompleteEASE-style TXT files
plus dummy SE files into the configured staging folder. AutoMapper then copies
the exact expected files into the current experiment `raw_data` folder, parses
the permanent folder, and updates Measurement and Results from the real parser.

### Point failure behavior

A point is successful only when the requested recipe produces a new readable
TXT file with the expected coordinate filename. If no usable TXT is produced,
the point is marked Failed (red X) in the Measurement progress map and mapping
continues. Failed points have no Results-table row and no Results-plot marker. A
later successful retake creates the normal data row.

A CompleteEASE communication failure is different: it stops the mapping
sequence and notifies the user. Abort also stops the mapping sequence, but does
not attempt to cancel the recipe currently running.


## V1.8 Results/export and failure-simulation revision

- The simulator deliberately makes acquisition #3 produce no TXT and #5 produce an unreadable TXT on each fresh application run. Later calls succeed, so failed points can be retaken.
- Failed/missing measurements remain visible only in the Measurement progress map. Results contains only real readable measurements.
- Ignored valid points remain red X markers while analyzing Results, but are omitted from exported figures.
- `Plot Options…` replaces the single Save Plot button. It contains Save Current Plot, Save All Plots, and Reset Plot Settings.
- Save All creates a timestamped `plots_YYYYMMDD_HHMMSS` folder inside the user-selected parent and exports every available parameter/plot-type combination.
- Reset Plot Settings enables all valid points and restores default title, colorbar title, automatic ticks, default parameter/plot, and clears selection.
- For live AutoMapper experiments, the experiment `results` folder is used as the preferred plot-export destination.
