# Orca First-Layer Calibration

Author: Zachary Jones

Version 0.3.6.  Requires Orca Slicer 2.5.0-rc or later, with the plugin system enabled.  Tested with Voron Trident 300 and Voron Switchwire 250, in Klipper setups.

## What it does

- The plugin reads the printer, filament, and process profiles currently active in Orca.
- It writes a new 3MF.  It does not modify saved presets or the current project.
- The new 3MF contains 9 patches, 40 x 40 mm, pitch 60 mm, footprint 160 x 160 mm, centred on the bed.
- Patches print in order, multipliers -4 to +4 of the step.  With the default step of 0.1 mm: -0.40, -0.30, -0.20, -0.10, 0.00, +0.10, +0.20, +0.30, +0.40 mm.
- Each patch label, as raised text in the centre, is the Z offset for that patch.
- print_sequence is set to "by object".
- machine_start_gcode: the existing start G-code, unchanged, plus one appended line at the end: SET_GCODE_OFFSET Z_ADJUST=-0.400 MOVE=1 (at step 0.1).
- printing_by_object_gcode: the existing between-objects G-code, unchanged, plus one appended line: SET_GCODE_OFFSET Z_ADJUST=0.100 MOVE=1 (at step 0.1).

## Mechanism

Orca emits between-objects G-code only after an object finishes, not before the first object.  The final G-code therefore has 1 start line and 8 step lines for 9 objects, cumulatively -0.40 to +0.40.  Z_ADJUST is relative, not Z=, so it does not override the probe offset.  It does not replace a START_PRINT macro.

## Install

- Copy `first_layer_calibration.py` to the Orca plugins folder, under `first_layer_calibration/`.  Example: `orca_plugins/first_layer_calibration/first_layer_calibration.py`.
- Restart Orca, open File, Plugins, and activate "Generate First-Layer Calibration 3MF".

## Workflow

- In Orca, set the printer, filament, and process profile on the plate, the way you normally would.  You do not have to save first.
- Run the plugin script.  In Prepare, press Space, type Generate, and run "Generate First-Layer Calibration 3MF" from Speed Dial.
- Step size is in mm, default 0.1.  Change `step_mm` in the plugin config before running, if you want a different step.
- The plugin writes `first-layer-calibration.3mf` to plugin storage, and shows the full path.  Open it on an empty plate, as a project, slice, and print.
- Pick the best-looking patch.  Its centre text is its Z offset.  That is the value to use for that printer.

## Save directory

- In the Plugins dialog, Config tab, set `output_dir` to an absolute path to change where the 3MF is written.  Empty means plugin storage, the old default.
- There is no file picker in the Orca plugin host UI, so this is a typed path, not a picker.
- The directory must already exist.  The plugin does not create it, because os.makedirs is an audited FsCreate event, and FsCreate has no persistable permission list in Orca, so its prompt can never stick.
- The first run to a new directory fails with a permission prompt for the file write (FsReadWrite, which is persistable).  Accept it, then run again: the second run writes the file.

## Calibration menu

- A plugin cannot be added to Orca's Calibration menu in 2.5.0-rc.  The plugin API has no menu capability, and the built-in Calibration menu is hard-coded, not extensible by plugins.
- Speed Dial, above, is the route used instead.

## Why a 3MF, not a direct plate add

- Orca's Python plugin API has no add, import, or load call for models.  A plugin cannot drop models onto the current plate.
- This plugin writes a new 3MF for Orca to open, the same workaround other Orca plugins use.

## How the profile changes survive a GUI load

- Orca's preset load resets every printer or print key NOT in `different_settings_to_system` to its base preset value.
- The plugin adds `print_sequence` to the print diff entry, and `machine_start_gcode` plus `printing_by_object_gcode` to the printer diff entry, preserving every existing diff entry.
- If no diff list is present at all, it leaves it absent, because with an empty diff list Orca skips the reset.
- Settings IDs are left exactly as they were when the plugin ran.

## Checks before you print

- Open the generated file as a project, not as geometry only.  Geometry-only discards the project preset, including between-objects G-code and print-by-object.
- Do not change printer, filament, or process after opening.  That replaces the project preset with the system preset, which has no between-objects G-code.
- Check the exported G-code, not the preview, for 1 start line plus 8 step lines.

## Assumptions

- Step unit is mm.
- Text method is raised text, not cut-out text.  Layer 1 is the patch, layer 2 is the text.
- Patch order, front to back, left to right: -4, -3, -2, -1, 0, +1, +2, +3, +4, where front is offset Y = -60.

## Not yet verified

- A physical print.  The G-code offsets and patch labels are verified in exported files, not on a printer.
