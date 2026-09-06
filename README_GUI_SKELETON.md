# Ellipsometer XY Mapping GUI Skeleton

First user-interface prototype for the mapping software.

This version intentionally does **not** communicate with CompleteEASE. It uses the existing `stage_simulator.py` and dummy measurement values so the workflow can be evaluated first.

## Structure

- `mapping_gui.py` — main application with horizontal tabs.
- `mapping_model.py` — one shared mapping definition and point list.
- `setup_page.py` — experiment setup and simulated automatic-stage alignment controls.
- `measurement_page.py` — Start, Abort, interactive progress grid, dummy live table, retake.
- `progress_grid.py` — clickable mapping-progress grid.
- `results_page.py` — Refresh Plots / Import TXT / Import Table placeholders and basic dummy statistics.
- `help_page.py` — short operating instructions.

## Existing file required

Keep your existing `stage_simulator.py` in the same folder.

## Run

Activate the project virtual environment and run:

```powershell
python mapping_gui.py
```

## Current prototype behavior

### Setup
- Placeholder recipe list.
- Map name.
- Odd square grids only.
- Spacing options 1–5 mm.
- Manual Stage or Low-Profile Automatic XY Stage.
- Mapping span `(N-1) × spacing`.
- Point count.
- Warning when the span exceeds 20 × 20 mm.
- Automatic-stage controls use the simulator for now.

### Measurement
- Start / Abort.
- Snake-order progress grid.
- Pending/current/measured state.
- Manual-stage coordinate prompt before each point.
- Dummy Thickness and MSE values populate the table.
- Click a measured point and retake it.

### Results
- Refresh Plots placeholder.
- Import TXT Folder placeholder.
- Import Table placeholder.
- Basic statistics from the dummy table.

## Next steps after the GUI feels right

1. Replace placeholder recipe names with recipes from the CompleteEASE Common Recipes folder.
2. Add a CompleteEASE communication module.
3. Create the experiment folder at measurement start.
4. Generate filenames from map name + coordinate.
5. Run the recipe and wait for completion.
6. Copy the generated `.SE` and `.txt` pair into the experiment `raw_data` folder.
7. Parse the TXT file and update the live table.
8. Save the table as CSV.
9. Replace placeholder plots with the previous interpolation/statistics plotting logic.
10. Connect the real automatic stage before each automatic measurement.
