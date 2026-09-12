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
- `Save Plot` is the only permanent plot toolbar action.
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
