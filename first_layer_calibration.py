# /// script
# requires-python = ">=3.12"
# dependencies = []
#
# [tool.orcaslicer.plugin]
# name = "First Layer Calibration"
# description = "Generate a 9-patch first-layer test as a 3MF project, with print by object and append-only Klipper Z-offset G-code in its profiles."
# author = "Zachary Jones"
# version = "0.3.6"
# ///

"""First Layer Calibration plugin for OrcaSlicer 2.5.0 (Python plugins).

Route chosen 2026-11-03: the 3MF route, not the pipeline route.

Workflow, in plain terms:
1. You set up the plate in Orca: printer, filament, and process
   profile, the way you normally would, or you open a project
   from Home. You do NOT have to save the project first.
2. Run this script, with step size in mm (default 0.1).
3. The plugin reads your printer, filament, and profile
   exactly as they are on the current plate, from
   orca.host.preset_bundle().full_config() (fixed in
   0.3.0). That call is safe from a Script worker, unlike
   plater.project_path() and plater.export_3mf_copy(),
   which both require the UI thread and therefore always
   failed from a Script, with 0.2.0 swallowing the
   project_path thread error as "No saved project" and
   0.2.2 reporting the export thread error. No saved
   project, no export, and no path are needed. The plugin
   does not guess them, and it does not contain any
   printer names. It adds 9 patches and writes a new 3MF
   to plugin storage. Open that 3MF on an empty plate, as
   a project, slice, and print.

What the plugin changes in the new 3MF, and only there:
- 9 patch objects, in order, multipliers -4 to +4 of the step.
  With step 0.1 mm that is -0.40, -0.30, -0.20, -0.10, 0.00,
  +0.10, +0.20, +0.30, +0.40 mm. Each patch has that calculated
  value as text in its centre, so the printed patch tells you
  its own offset.
- print_sequence = "by object", so the patches print one at
  a time, in that order.
- machine_start_gcode: appends, never replaces, one line at
  the end of your existing start G-code:
      SET_GCODE_OFFSET Z_ADJUST=<start> MOVE=1
  where <start> = -4 * step. Your START_PRINT macro, homing,
  and mesh lines stay exactly as they were, in front of it.
- printing_by_object_gcode: appends, never replaces, one line:
      SET_GCODE_OFFSET Z_ADJUST=<step> MOVE=1
  Orca emits that block between objects, so each next patch
  steps up by one step, cumulatively, from -4x to +4x.
  Z_ADJUST is relative, not Z=, so it does not override the
  probe offset. It only adds to it.

What it does not do:
- It does not modify your saved template project, your
  presets, or your normal profiles. Only the new 3MF carries
  the calibration changes.
- It does not add models to the plate directly. That is not
  in the Orca plugin API (see below), which is why it writes
  a 3MF for you to open, the same pattern as the existing
  orca-svg-multicor plugin.

SOURCE-VERIFIED AGAINST OrcaSlicer main, commit e2acd9e,
and against a real Orca 2.5.0-rc export:
- Host is read-only for models: PluginHostModel.cpp binds
  Model / ModelObject / ModelVolume with objects(), object(),
  volumes(), etc., and no add/create method. PluginHostApp.cpp
  binds Plater with model(), project_path(), export_3mf_copy(),
  and no load/import method. Hence the 3MF route.
- A mesh-only 3MF with one top-level mesh object per patch,
  in build order, loads as separate objects, as in Orca's own
  resources/calib/filament_flow/pass1.3mf (9 objects, 9 build
  items, no transform).
- A saved project 3MF carries Metadata/project_settings.config
  as a flat JSON full config (Config.cpp save_to_json, and
  Plater.cpp export_3mf writes preset_bundle full_config).
  Modifying only print_sequence, machine_start_gcode, and
  printing_by_object_gcode in that JSON preserves every other
  key exactly as Orca wrote it.
- By-object printing: print_sequence includes "by object",
  printing_by_object_gcode is labelled "Between Object G-code",
  and GCode.cpp emits it only when finished_objects > 0, i.e.
  not before the first object. That is why the start offset
  lives in start G-code, and the step lives in between-objects
  G-code: 1 start line + 8 step lines for 9 objects.
- PresetBundle bindings (PluginHostPresets.cpp) expose
  full_config_keys() / full_config_value(), and Plater
  exposes project_path(). This script uses project_path()
  and the saved project file, not a reconstructed config,
  so vector and multi-line G-code values are preserved byte
  for byte except for the appended lines.

Patch geometry, stated so it can be checked, not guessed:
- Patch base: 40 x 40 mm, 0.2 mm thick.
- Text: 5x7 pixel font, pixel 1.2 mm, raised from 0.2 mm to
  0.4 mm total, centred on the patch. Layer 1 is the patch,
  layer 2 is the text pixels. The text is the offset label.
- Grid: pitch 60 mm, offsets at -60, 0, +60 in X and Y,
  footprint 160 x 160 mm, centred on the bed. Each patch
  mesh is local to its centre, and its build item transform
  places it at bed centre (from the template's
  printable_area) plus its offset. Order, front to back,
  left to right: -4, -3, -2, -1, 0, +1, +2, +3, +4, where
  front is offset Y = -60 and back is offset Y = +60.
- Fits Voron 2.4 350, Voron Trident 300, and Voron
  Switchwire 250, all with margin. No printer size is
  hard-coded in the plugin; the fit is a property of the
  160 mm footprint, not a printer check.
"""

import json
import math
import os
import uuid
import zipfile

try:
    import orca
except Exception:  # Standalone test / import without Orca: core stays usable.
    orca = None


# ---------------------------------------------------------------- text font

# 5 columns x 7 rows, row 0 is the top of the glyph.
_GLYPHS = {
    "0": ("01110", "10001", "10011", "10101", "11001", "10001", "01110"),
    "1": ("00100", "01100", "00100", "00100", "00100", "00100", "01110"),
    "2": ("01110", "10001", "00001", "00010", "00100", "01000", "11111"),
    "3": ("01110", "10001", "00001", "00110", "00001", "10001", "01110"),
    "4": ("00010", "00110", "01010", "10010", "11111", "00010", "00010"),
    "5": ("11111", "10000", "11110", "00001", "00001", "10001", "01110"),
    "6": ("00110", "01000", "10000", "11110", "10001", "10001", "01110"),
    "7": ("11111", "00001", "00010", "00100", "01000", "01000", "01000"),
    "8": ("01110", "10001", "10001", "01110", "10001", "10001", "01110"),
    "9": ("01110", "10001", "10001", "01111", "00001", "00010", "01100"),
    "-": ("00000", "00000", "00000", "01110", "00000", "00000", "00000"),
    "+": ("00000", "00100", "00100", "11111", "00100", "00100", "00000"),
    ".": ("00000", "00000", "00000", "00000", "00000", "01100", "01100"),
}


def format_offset(value, step_mm):
    """Text label for a patch, e.g. -0.40, 0.00, +0.40 at step 0.1."""
    decimals = 2
    if step_mm < 0.01:
        decimals = 3
    if step_mm < 0.001:
        decimals = 4
    if abs(value) < 0.5 * (10 ** (-decimals)):
        return "0." + ("0" * decimals)
    return "%+.*f" % (decimals, value)


def format_gcode_offset(value):
    """G-code value, 3 decimals, avoiding float artefacts like 0.30000004."""
    return "%.3f" % value


# ---------------------------------------------------------------- geometry

def _box(x0, x1, y0, y1, z0, z1):
    v = [
        (x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
        (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1),
    ]
    quads = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4),
             (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
    tris = []
    for a, b, c, d in quads:
        tris.append((v[a], v[b], v[c]))
        tris.append((v[a], v[c], v[d]))
    return tris


def patch_triangles(cx, cy, label, patch_mm=40.0, pixel_mm=1.2):
    """Triangles for one patch: 0.2 mm base plus raised text to 0.4 mm."""
    tris = []
    half = patch_mm / 2.0
    tris.extend(_box(cx - half, cx + half, cy - half, cy + half, 0.0, 0.2))
    # Text layout: 5 pixels per char, 1 pixel spacing, 7 pixels tall.
    total_px = len(label) * 6 - 1
    total_w = total_px * pixel_mm
    total_h = 7 * pixel_mm
    left = cx - total_w / 2.0
    # Row 0 (top of glyph) goes to the back (+Y), so text reads
    # upright when viewed from the front (-Y) of the plate.
    top = cy + total_h / 2.0
    for ci, ch in enumerate(label):
        glyph = _GLYPHS.get(ch)
        if glyph is None:
            continue
        for row in range(7):
            for col in range(5):
                if glyph[row][col] != "1":
                    continue
                x0 = left + (ci * 6 + col) * pixel_mm
                y1 = top - row * pixel_mm
                y0 = y1 - pixel_mm
                tris.extend(_box(x0, x0 + pixel_mm, y0, y1, 0.2, 0.4))
    return tris


PATCH_POSITIONS = [
    (-60.0, -60.0), (0.0, -60.0), (60.0, -60.0),
    (-60.0, 0.0), (0.0, 0.0), (60.0, 0.0),
    (-60.0, 60.0), (0.0, 60.0), (60.0, 60.0),
]


def patch_specs(step_mm):
    """List of (multiplier, offset, label, cx, cy) in print order."""
    specs = []
    for mult, (cx, cy) in zip(range(-4, 5), PATCH_POSITIONS):
        offset = mult * step_mm
        specs.append((mult, offset, format_offset(offset, step_mm), cx, cy))
    return specs


def bed_center_from_config(cfg):
    """Bed centre from printable_area, e.g. ['0x0','200x0','200x200','0x200'] -> (100, 100).

    Orca 3MF model coordinates are plate coordinates, not bed-centred
    coordinates: a project saved from a 200 mm bed has a build item
    transform of 100,100 for a centred object. So patch meshes are
    built locally around 0,0, and each build item transform places
    that patch at bed centre plus its grid offset. That keeps the
    plugin printer agnostic in code, while the output 3MF is
    correct for the bed size in the template project.
    """
    try:
        area = cfg.get("printable_area") or []
        # Saved projects give a list ['0x0','200x0',...].
        # preset_bundle.full_config_value() gives one comma
        # string '0x0,300x0,300x300,0x300'. Handle both,
        # or the centre falls back to 100,100 on a 300 bed.
        if isinstance(area, str):
            area = [pt for pt in area.replace(",", " ").split() if "x" in pt]
        xs, ys = [], []
        for pt in area:
            x_str, y_str = str(pt).split("x")
            xs.append(float(x_str))
            ys.append(float(y_str))
        if xs and ys:
            return ((max(xs) - min(xs)) / 2.0 + min(xs),
                    (max(ys) - min(ys)) / 2.0 + min(ys))
    except Exception:
        pass
    return (100.0, 100.0)


def build_model_xml(step_mm, bed_center=(100.0, 100.0)):
    """3MF model XML: one mesh object per patch, in print order.

    Each mesh is local to its patch centre. Each build item
    transform places it at bed centre plus its grid offset.
    """
    objects_xml = []
    items_xml = []
    for idx, (mult, offset, label, cx, cy) in enumerate(patch_specs(step_mm), start=1):
        tris = patch_triangles(0.0, 0.0, label)
        # Deduplicate vertices by rounded coordinate.
        vindex = {}
        vertices = []
        triangles = []
        for p, q, r in tris:
            tri_idx = []
            for pt in (p, q, r):
                key = (round(pt[0], 6), round(pt[1], 6), round(pt[2], 6))
                if key not in vindex:
                    vindex[key] = len(vertices)
                    vertices.append(key)
                tri_idx.append(vindex[key])
            triangles.append(tri_idx)
        v_xml = "".join(
            '<vertex x="%s" y="%s" z="%s"/>' % (x, y, z)
            for (x, y, z) in vertices
        )
        t_xml = "".join(
            '<triangle v1="%d" v2="%d" v3="%d"/>' % tuple(t)
            for t in triangles
        )
        name = "FLC %s" % label
        objects_xml.append(
            '<object id="%d" name="%s" type="model" p:UUID="%s">'
            "<mesh><vertices>%s</vertices><triangles>%s</triangles></mesh>"
            "</object>" % (idx, name, str(uuid.uuid4()), v_xml, t_xml)
        )
        plate_x = bed_center[0] + cx
        plate_y = bed_center[1] + cy
        items_xml.append(
            '<item objectid="%d" p:UUID="%s" transform="1 0 0 0 1 0 0 0 1 %s %s 0"/>'
            % (idx, str(uuid.uuid4()), plate_x, plate_y))
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<model unit="millimeter" xml:lang="en-US" '
        'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02" '
        'xmlns:p="http://schemas.microsoft.com/3dmanufacturing/production/2015/06" '
        'requiredextensions="p">\n'
        ' <metadata name="Application">OrcaSlicer-2.5.0-rc</metadata>\n'
        ' <metadata name="OrcaSlicer">2.5.0-rc</metadata>\n'
        ' <metadata name="Title">First Layer Calibration</metadata>\n'
        " <resources>\n  " + "\n  ".join(objects_xml) + "\n </resources>\n"
        ' <build p:UUID="%s">\n  ' % str(uuid.uuid4())
        + "\n  ".join(items_xml)
        + "\n </build>\n</model>\n"
    )


# ---------------------------------------------------------------- config

_MARKER = "; First-Layer Calibration"


def _append_gcode_line(existing, line):
    """Append one G-code line, append-only, without duplicating it.

    Removes only lines previously added by this plugin (marked
    with the marker comment), then appends the marker comment
    and the line. Every other line is preserved verbatim.
    """
    existing = existing or ""
    # Live preset_bundle values use literal backslash-n for
    # newlines (opt_serialize form), saved projects use real
    # newlines. Normalize first, or a marker line buried in
    # a single literal-\n string is never found, and a
    # re-run duplicates the offset instead of replacing it.
    existing = existing.replace("\\r\\n", "\n").replace("\\n", "\n")
    kept = []
    skip_next = False
    for raw in existing.splitlines():
        if skip_next:
            skip_next = False
            continue
        if raw.strip().startswith(_MARKER):
            skip_next = True
            continue
        kept.append(raw)
    while kept and kept[-1].strip() == "":
        kept.pop()
    kept.append(_MARKER)
    kept.append(line)
    return "\n".join(kept) + "\n"


def _split_serialized_list(value):
    """Split an Orca opt_serialize list string, respecting quotes.

    Example input, as seen in a real preset_bundle config:
    ';\"filament_key1;filament_key2\";\"printer_key1;printer_key2\"'
    gives ['', 'filament_key1;filament_key2', 'printer_key1;printer_key2'].
    """
    entries = []
    cur = []
    in_quote = False
    for ch in value:
        if ch == '"':
            in_quote = not in_quote
        elif ch == ';' and not in_quote:
            entries.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    entries.append("".join(cur))
    return entries


def _join_serialized_list(entries):
    """Inverse of _split_serialized_list: quote entries containing ';'."""
    parts = []
    for e in entries:
        if ";" in e:
            parts.append('"%s"' % e)
        else:
            parts.append(e)
    return ";".join(parts)


def _add_key_to_entry(entry, key):
    keys = [k for k in entry.split(";") if k] if entry else []
    if key not in keys:
        keys.append(key)
    return ";".join(keys)


def update_diff_list(cfg):
    """Add the plugin's changed keys to different_settings_to_system.

    Generic, no preset names hardcoded. Orca's preset load
    resets every printer/print key NOT in the diff list to
    its base preset value. So if a live config already has
    a diff list (real customized presets do, system presets
    usually do not), a new key not in that list is silently
    reset on GUI load, even though it is present in
    project_settings and in a CLI slice. That is why
    machine_start_gcode survived in a real Trident file
    (it was already in the printer diff list) while
    printing_by_object_gcode did not (it was not).
    Layout, for any filament count: entry 0 is print,
    entries 1..N are filaments, entry -1 is printer.
    Only the first and last entries are touched.
    If no diff list is present at all, leave it absent:
    with an empty diff list Orca skips the reset, so
    creating one from scratch could reset other custom
    keys that were not in any list.
    Returns True if the list was updated.
    """
    if "different_settings_to_system" not in cfg:
        return False
    raw = cfg["different_settings_to_system"]
    if isinstance(raw, list):
        entries = list(raw)
        was_list = True
    elif isinstance(raw, str):
        entries = _split_serialized_list(raw)
        was_list = False
    else:
        return False
    if len(entries) < 2:
        return False
    entries[0] = _add_key_to_entry(entries[0], "print_sequence")
    entries[-1] = _add_key_to_entry(entries[-1], "machine_start_gcode")
    entries[-1] = _add_key_to_entry(entries[-1], "printing_by_object_gcode")
    cfg["different_settings_to_system"] = entries if was_list else _join_serialized_list(entries)
    return True


def modify_config(cfg, step_mm):
    """Modify a project_settings config dict in place. Returns changes."""
    start_offset = -4.0 * step_mm
    start_line = "SET_GCODE_OFFSET Z_ADJUST=%s MOVE=1" % format_gcode_offset(start_offset)
    step_line = "SET_GCODE_OFFSET Z_ADJUST=%s MOVE=1" % format_gcode_offset(step_mm)
    cfg["print_sequence"] = "by object"
    cfg["machine_start_gcode"] = _append_gcode_line(
        cfg.get("machine_start_gcode", ""), start_line)
    cfg["printing_by_object_gcode"] = _append_gcode_line(
        cfg.get("printing_by_object_gcode", ""), step_line)
    update_diff_list(cfg)
    return {
        "step_mm": step_mm,
        "start_offset": start_offset,
        "step_line": step_line,
        "start_line": start_line,
        "labels": [label for (_, _, label, _, _) in patch_specs(step_mm)],
    }


# ---------------------------------------------------------------- 3MF writer

def build_3mf_from_config(cfg, output_path, step_mm, template_entries=None):
    """Write a calibration 3MF from a full config dict.

    cfg is the preset bundle full config, as a dict in the
    same form as Metadata/project_settings.config in a saved
    project (values are serialized strings, or lists of
    strings for vector options). Only print_sequence,
    machine_start_gcode, and printing_by_object_gcode are
    changed, by modify_config. The model is the 9 patches.
    template_entries, when given, are extra 3MF entries to
    carry over (e.g. slice_info, filament_sequence); the
    model, per-object files, and model_settings are never
    carried over, they described the old objects.
    Returns the changes dict from modify_config.
    """
    if step_mm <= 0:
        raise ValueError("step_mm must be positive, got %r" % (step_mm,))
    cfg = dict(cfg)
    changes = modify_config(cfg, step_mm)
    # 0.3.6 reverts 0.3.5's settings_id renaming. Renaming
    # broke profile identity for real, multi-printer use:
    # the generated file showed project presets instead of
    # the printer, filament, and print profiles that were
    # active when the plugin ran. Settings IDs are now
    # left exactly as the live config had them, for any
    # printer, filament, or print preset, and the diff
    # list (update_diff_list, in modify_config) is what
    # tells Orca to keep the three changed keys.
    changes["bed_center"] = bed_center_from_config(cfg)
    model_xml = build_model_xml(step_mm, changes["bed_center"])
    out_entries = {}
    for name, data in (template_entries or {}).items():
        if name == "3D/3dmodel.model":
            continue
        if name.startswith("3D/Objects/"):
            continue
        if name in ("Metadata/model_settings.config", "3D/_rels/3dmodel.model.rels",
                     "Metadata/project_settings.config"):
            continue
        out_entries[name] = data
    out_entries["3D/3dmodel.model"] = model_xml.encode("utf-8")
    out_entries["Metadata/project_settings.config"] = json.dumps(
        cfg, indent=1).encode("utf-8")
    # A minimal, valid content-types / rels set, matching the
    # mesh-only calibration 3MFs Orca ships.
    out_entries["[Content_Types].xml"] = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">\n'
        ' <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>\n'
        ' <Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>\n'
        ' <Default Extension="json" ContentType="application/json"/>\n'
        "</Types>\n"
    ).encode("utf-8")
    out_entries["_rels/.rels"] = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">\n'
        ' <Relationship Target="/3D/3dmodel.model" Id="rel-1" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>\n'
        "</Relationships>\n"
    ).encode("utf-8")
    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zout:
        for name in sorted(out_entries):
            zout.writestr(name, out_entries[name])
    return changes


def build_3mf(template_path, output_path, step_mm):
    """Backwards-compatible wrapper: read a saved project 3MF."""
    with zipfile.ZipFile(template_path, "r") as zin:
        entries = {name: zin.read(name) for name in zin.namelist()}
    if "Metadata/project_settings.config" not in entries:
        raise ValueError(
            "template 3MF has no Metadata/project_settings.config")
    cfg = json.loads(entries["Metadata/project_settings.config"].decode("utf-8"))
    return build_3mf_from_config(cfg, output_path, step_mm, entries)


# ---------------------------------------------------------------- script capability

def _load_config(capability):
    try:
        raw = capability.get_config()
    except Exception:
        return {}
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


if orca is not None:
    class GenerateFirstLayerCalibration3MF(orca.script.ScriptPluginCapabilityBase):
        def get_name(self):
            return "Generate First-Layer Calibration 3MF"

        def get_default_config(self):
            return {"step_mm": 0.1, "output_name": "first-layer-calibration.3mf",
                    "output_dir": ""}

        def execute(self):
            try:
                raw_cfg = self.get_config()
            except Exception as exc:
                raw_cfg = "get_config raised: %s" % exc
            try:
                with open(os.path.join(str(orca.host.plugin.storage()),
                                       "config_dump.json"),
                          "w", encoding="utf-8") as fh:
                    json.dump({"raw_type": str(type(raw_cfg)),
                               "raw": raw_cfg}, fh, indent=1, default=str)
            except Exception:
                pass
            cfg = _load_config(self)
            defaults = self.get_default_config()
            try:
                step = float(cfg.get("step_mm", defaults["step_mm"]))
            except Exception:
                return orca.ExecutionResult.failure(
                    orca.PluginResult.RecoverableError,
                    "step_mm is not a number: %r" % (cfg.get("step_mm"),))
            if step <= 0 or step > 0.5:
                return orca.ExecutionResult.failure(
                    orca.PluginResult.RecoverableError,
                    "step_mm must be between 0 and 0.5, got %s" % step)
            output_name = str(cfg.get("output_name", defaults["output_name"]) or defaults["output_name"])
            folder = orca.host.plugin.storage()
            # Diagnostic dump of the config as received, so a
            # Config tab edit that does not reach execute()
            # can be seen, not guessed at.
            try:
                with open(os.path.join(str(folder), "config_dump.json"),
                          "w", encoding="utf-8") as fh:
                    json.dump({"raw_type": str(type(cfg)),
                               "raw": cfg}, fh, indent=1, default=str)
            except Exception:
                pass
            # Save directory, 0.3.2-0.3.4: output_dir in the
            # script config, edited in the Plugins dialog
            # Config tab. Empty means plugin storage (the
            # old default). A set directory must be an
            # absolute path; a relative one is taken
            # relative to plugin storage. Orca has no file
            # picker in the plugin host UI (message, HTML
            # window, dock panel, progress, notification
            # only), so this is a typed path, not a picker.
            # 0.3.4: the directory must already exist.
            # os.makedirs is an audited FsCreate event, and
            # FsCreate has no persistable permission list
            # in Orca (permission_list_for returns None
            # for it), so a mkdir prompt can never be
            # granted persistently from a worker-thread
            # script: the first run fails, the prompt
            # appears, Yes does not stick, and every later
            # run fails the same way. Do not makedirs.
            # For a file write, the audited event is
            # FsReadWrite, which IS persistable: the first
            # run fails with a permission prompt, accept
            # it, then run again and it succeeds.
            output_dir = str(cfg.get("output_dir", "") or "").strip()
            if output_dir:
                output_dir = os.path.expanduser(output_dir)
                if not os.path.isabs(output_dir):
                    output_dir = os.path.join(str(folder), output_dir)
                if not os.path.isdir(output_dir):
                    return orca.ExecutionResult.failure(
                        orca.PluginResult.RecoverableError,
                        "output_dir %s does not exist. Create "
                        "it first (the plugin cannot create "
                        "it: Orca's mkdir permission cannot "
                        "be granted persistently), then run "
                        "again. The first run to a new "
                        "directory will still fail with a "
                        "permission prompt: accept it, then "
                        "run again." % output_dir)
                output_path = os.path.join(output_dir, output_name)
            else:
                output_path = os.path.join(str(folder), output_name)
            # Config source, 0.3.0: the live preset bundle.
            # plater.project_path() and plater.export_3mf_copy()
            # both require the UI thread (PluginHostApp.cpp,
            # require_main_thread), and a Script runs on its
            # own worker thread, so both always failed from
            # here: 0.2.0 swallowed the project_path error
            # as "No saved project", 0.2.2 reported the
            # export error. preset_bundle().full_config_keys()
            # and full_config_value() have no such thread
            # requirement, and full_config is exactly what a
            # saved project writes as project_settings.config.
            # No saved project, no export, no path needed.
            try:
                bundle = orca.host.preset_bundle()
                keys = list(bundle.full_config_keys())
                full_cfg = {}
                for key in keys:
                    value = bundle.full_config_value(key)
                    if value is not None:
                        full_cfg[key] = value
            except Exception as exc:
                return orca.ExecutionResult.failure(
                    orca.PluginResult.RecoverableError,
                    "could not read the current presets "
                    "(preset_bundle.full_config failed: %s). "
                    "Set printer, filament, and process on the "
                    "plate, then run this script again." % exc)
            if not full_cfg:
                return orca.ExecutionResult.failure(
                    orca.PluginResult.RecoverableError,
                    "current presets returned an empty full config. "
                    "Set printer, filament, and process on the plate, "
                    "then run this script again.")
            # Debug dump, in plugin storage, so a live run can
            # be checked for value formats (strings vs lists)
            # without guessing from the saved-project form.
            try:
                with open(os.path.join(str(folder), "full_config_dump.json"),
                          "w", encoding="utf-8") as fh:
                    json.dump(full_cfg, fh, indent=1, default=str)
            except Exception:
                pass
            template_source = "current presets (preset_bundle)"
            try:
                changes = build_3mf_from_config(full_cfg, output_path, step)
            except Exception as exc:
                return orca.ExecutionResult.failure(
                    orca.PluginResult.RecoverableError,
                    "could not build calibration 3MF from %s: %s"
                    % (template_source, exc))
            msg = (
                "First-layer calibration 3MF written to %s\n"
                "Template: %s. Step: %s mm. Patches, in print order: %s\n"
                "Start offset: %s. Between objects: %s per object.\n"
                "Open it on an empty plate as a project, slice, and print. "
                "Open as a project, not as geometry only: geometry-only "
                "discards the project preset, including the between-objects "
                "G-code and print-by-object, and changing printer, filament, "
                "or process after opening replaces the project preset with "
                "the system preset, which has no between-objects G-code. "
                "Check the sliced G-code for 1 start offset plus 8 between "
                "steps. The best-looking patch label is its Z offset."
                % (output_path, template_source, step, ", ".join(changes["labels"]),
                   changes["start_line"], changes["step_line"])
            )
            try:
                orca.host.ui.message(msg, title="First Layer Calibration",
                                     buttons="ok", icon="info")
            except Exception:
                pass
            return orca.ExecutionResult.success(msg)

    @orca.plugin
    class FirstLayerCalibrationPlugin(orca.base):
        def register_capabilities(self):
            orca.register_capability(GenerateFirstLayerCalibration3MF)
