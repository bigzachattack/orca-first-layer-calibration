"""Tests for first_layer_calibration.py, stdlib only.

Run from the repository root:
  python3 -m unittest discover -s tests -v

No Orca installation is needed.  The plugin imports with
orca = None outside Orca, and these tests exercise only the
core writer and config logic.  GUI slicing is a separate,
manual check, not covered here, because it needs a running
Orca GUI and a real preset bundle.

Cases are synthetic, with no printer names hardcoded as
the only case.  The diff-list cases reproduce the bug
found in November 2026 from a real Trident file: a live
config whose printer diff listed machine_start_gcode but
not printing_by_object_gcode had its between-objects
G-code reset to empty on GUI load, while start G-code
survived.
"""

import json
import os
import re
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import first_layer_calibration as flc


def base_config(**overrides):
    cfg = {
        "printer_settings_id": "Voron Trident 300 0.4 nozzle",
        "print_settings_id": "0.20mm Standard @Voron",
        "filament_settings_id": '"Generic PLA @System"',
        "printable_area": "0x0,300x0,300x300,0x300",
        "machine_start_gcode": "START_PRINT",
        "printing_by_object_gcode": "",
        "print_sequence": "by layer",
    }
    cfg.update(overrides)
    return cfg


def read_output(path):
    with zipfile.ZipFile(path) as zf:
        cfg = json.loads(zf.read("Metadata/project_settings.config"))
        model = zf.read("3D/3dmodel.model").decode("utf-8")
    return cfg, model


class TestOffsets(unittest.TestCase):
    def test_step_01_labels_and_gcode(self):
        cfg = base_config()
        changes = flc.modify_config(cfg, 0.1)
        self.assertEqual(
            changes["labels"],
            ["-0.40", "-0.30", "-0.20", "-0.10", "0.00",
             "+0.10", "+0.20", "+0.30", "+0.40"])
        self.assertIn("SET_GCODE_OFFSET Z_ADJUST=-0.400 MOVE=1",
                      cfg["machine_start_gcode"])
        self.assertIn("SET_GCODE_OFFSET Z_ADJUST=0.100 MOVE=1",
                      cfg["printing_by_object_gcode"])
        self.assertEqual(cfg["print_sequence"], "by object")

    def test_step_001(self):
        cfg = base_config()
        flc.modify_config(cfg, 0.01)
        self.assertIn("SET_GCODE_OFFSET Z_ADJUST=-0.040 MOVE=1",
                      cfg["machine_start_gcode"])
        self.assertIn("SET_GCODE_OFFSET Z_ADJUST=0.010 MOVE=1",
                      cfg["printing_by_object_gcode"])

    def test_append_only_and_rerun_does_not_duplicate(self):
        cfg = base_config(machine_start_gcode="START_PRINT\nG28\n")
        flc.modify_config(cfg, 0.1)
        first = cfg["machine_start_gcode"]
        self.assertTrue(first.startswith("START_PRINT\nG28\n"))
        self.assertEqual(first.count("SET_GCODE_OFFSET"), 1)
        flc.modify_config(cfg, 0.1)
        self.assertEqual(cfg["machine_start_gcode"], first)
        self.assertEqual(cfg["printing_by_object_gcode"].count("SET_GCODE_OFFSET"), 1)

    def test_changing_step_replaces_not_stacks(self):
        cfg = base_config()
        flc.modify_config(cfg, 0.1)
        flc.modify_config(cfg, 0.05)
        self.assertIn("SET_GCODE_OFFSET Z_ADJUST=-0.200 MOVE=1",
                      cfg["machine_start_gcode"])
        self.assertNotIn("-0.400", cfg["machine_start_gcode"])
        self.assertEqual(cfg["machine_start_gcode"].count("SET_GCODE_OFFSET"), 1)

    def test_literal_backslash_n_is_normalized(self):
        # preset_bundle values use literal backslash-n, not real newlines.
        cfg = base_config(
            machine_start_gcode="START_PRINT\\n; First-Layer Calibration\\nSET_GCODE_OFFSET Z_ADJUST=-0.400 MOVE=1\\n")
        flc.modify_config(cfg, 0.1)
        self.assertEqual(cfg["machine_start_gcode"].count("SET_GCODE_OFFSET"), 1)


class TestBedCenter(unittest.TestCase):
    def test_comma_string_300(self):
        self.assertEqual(flc.bed_center_from_config(base_config()), (150.0, 150.0))

    def test_comma_string_250(self):
        cfg = base_config(printable_area="0x0,250x0,250x250,0x250")
        self.assertEqual(flc.bed_center_from_config(cfg), (125.0, 125.0))

    def test_list_form_350(self):
        cfg = base_config(printable_area=["0x0", "350x0", "350x350", "0x350"])
        self.assertEqual(flc.bed_center_from_config(cfg), (175.0, 175.0))


class TestDiffList(unittest.TestCase):
    def test_serialized_trident_like_diff(self):
        # Printer diff has start, but not between.  Filament diff
        # and print diff are preserved, not replaced.
        diff = ';"filament_flow_ratio";"machine_start_gcode;retraction_length"'
        cfg = base_config(different_settings_to_system=diff)
        flc.modify_config(cfg, 0.1)
        out = cfg["different_settings_to_system"]
        self.assertIn("printing_by_object_gcode", out)
        self.assertIn("machine_start_gcode", out)
        self.assertIn("filament_flow_ratio", out)
        self.assertIn("retraction_length", out)
        # Print entry (first) gains print_sequence.
        entries = flc._split_serialized_list(out)
        self.assertEqual(entries[0], "print_sequence")
        self.assertIn("printing_by_object_gcode", entries[-1])

    def test_list_diff_two_filaments(self):
        cfg = base_config(
            different_settings_to_system=["", "filament_flow_ratio", "machine_end_gcode"])
        flc.modify_config(cfg, 0.1)
        out = cfg["different_settings_to_system"]
        self.assertEqual(out[0], "print_sequence")
        self.assertEqual(out[1], "filament_flow_ratio")
        self.assertIn("printing_by_object_gcode", out[-1])
        self.assertIn("machine_start_gcode", out[-1])

    def test_no_diff_list_is_left_absent(self):
        cfg = base_config()
        self.assertNotIn("different_settings_to_system", cfg)
        flc.modify_config(cfg, 0.1)
        self.assertNotIn("different_settings_to_system", cfg)

    def test_no_duplicate_keys_on_rerun(self):
        cfg = base_config(
            different_settings_to_system=';"filament_flow_ratio";"machine_start_gcode;printing_by_object_gcode"')
        flc.modify_config(cfg, 0.1)
        flc.modify_config(cfg, 0.1)
        out = cfg["different_settings_to_system"]
        self.assertEqual(out.count("printing_by_object_gcode"), 1)


class TestWriter(unittest.TestCase):
    def test_nine_patches_settings_ids_preserved(self):
        with tempfile.TemporaryDirectory() as td:
            out = os.path.join(td, "out.3mf")
            flc.build_3mf_from_config(base_config(), out, 0.1)
            cfg, model = read_output(out)
            self.assertEqual(cfg["printer_settings_id"], "Voron Trident 300 0.4 nozzle")
            self.assertEqual(cfg["print_settings_id"], "0.20mm Standard @Voron")
            self.assertEqual(cfg["print_sequence"], "by object")
            items = re.findall(r'transform="1 0 0 0 1 0 0 0 1 ([0-9.]+) ([0-9.]+) 0"', model)
            self.assertEqual(len(items), 9)
            # 300 mm bed, centre 150, offsets -60, 0, +60.
            self.assertEqual(items[0], ("90.0", "90.0"))
            self.assertEqual(items[4], ("150.0", "150.0"))
            self.assertEqual(items[8], ("210.0", "210.0"))
            for label in ["FLC -0.40", "FLC 0.00", "FLC +0.40"]:
                self.assertIn(label, model)

    def test_switchwire_250_transforms(self):
        cfg = base_config(
            printer_settings_id="Voron Switchwire 250 0.4 nozzle",
            printable_area="0x0,250x0,250x250,0x250")
        with tempfile.TemporaryDirectory() as td:
            out = os.path.join(td, "out.3mf")
            flc.build_3mf_from_config(cfg, out, 0.1)
            _, model = read_output(out)
            items = re.findall(r'transform="1 0 0 0 1 0 0 0 1 ([0-9.]+) ([0-9.]+) 0"', model)
            self.assertEqual(items[0], ("65.0", "65.0"))
            self.assertEqual(items[8], ("185.0", "185.0"))

    def test_invalid_step_rejected(self):
        with self.assertRaises(ValueError):
            flc.build_3mf_from_config(base_config(), "/tmp/never.3mf", 0)


if __name__ == "__main__":
    unittest.main()
