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
