V1.30 automatic-stage coordinate correction

Replace:
- measurement_page.py
- mapping_runner.py
- help_dialog.py
- README.md

Scientific/map coordinates remain intuitive:
+X = measurement position to the right on the sample
+Y = measurement position upward on the sample

For the low-profile automatic stage only:
physical stage X = -map X
physical stage Y = -map Y

This applies to:
- automatic grid mapping
- automatic single-point measurements
- Measure Selected Point / remeasurements
- automatic-stage jog arrows
- live stage/laser position marker

The standalone stage driver, Arduino firmware, and standalone XY Stage
Controller are unchanged and continue to represent true physical stage motion.

Manual-stage coordinate calculations are unchanged.
