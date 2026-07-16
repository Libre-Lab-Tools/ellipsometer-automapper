# Ellipsometer AutoMapper

An open-source application for automated spatial mapping using J.A. Woollam M2000 ellipsometer through CompleteEASE remote commands.

The software was developed as a compact alternative for automated ellipsometry mapping in space-constrained environments. It is designed to operate with a custom low-profile automatic XY stage that mounts directly on the standard horizontal sample stage while remaining within the instrument's maximum sample-height limit, enabling automated mapping inside gloveboxes and other confined experimental setups.

Similar to commercially available automated mapping systems, the application allows users to define a measurement grid, select a CompleteEASE recipe, and automatically execute the complete mapping sequence. The software controls stage motion, performs measurements, extracts selected parameters, saves the experimental data, and generates spatial maps through an open-source Python interface.

## Features

- Automatic XY mapping
- CompleteEASE remote communication
- User-selectable measurement recipes
- Automated data collection
- CSV export
- Automatic map generation
- Measurement history
- Point remeasurement
- PyQt graphical user interface

## Dependencies

This project uses the **Low-Profile Automatic XY Stage** project for stage motion and positioning.

## Compatibility

Currently developed for J.A. Woollam M-2000 ellipsometers using CompleteEASE remote commands.

## Disclaimer

This project is an independent open-source project and is not affiliated with or endorsed by J.A. Woollam Co., Inc. Product names are used solely to indicate equipment compatibility.

## Project Status

This project is under active development.
