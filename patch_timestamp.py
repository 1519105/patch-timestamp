#!/usr/bin/env python3
"""
patch_timestamp.py - Adds a live timestamp overlay to camera_pi.py (raspi-cam-srv).

Automatically re-applies after git pull updates overwrite the file.

Usage:
    python3 patch_timestamp.py
    python3 patch_timestamp.py --path /custom/path/to/camera_pi.py

One-liner from GitHub:
    curl -sL https://raw.githubusercontent.com/1519105/patch-timestamp/main/patch_timestamp.py | python3
"""

import sys
import shutil
from pathlib import Path
from datetime import datetime

# ── Auto-discovery ────────────────────────────────────────────────────────────

def find_target() -> Path:
    """
    Searches for camera_pi.py in this order:
    1. Same directory as this script (if placed inside the repo)
    2. ~/prg/raspi-cam-srv/raspiCamSrv/camera_pi.py (current user home)
    3. /home/*/prg/raspi-cam-srv/raspiCamSrv/camera_pi.py (all home dirs)
    4. Glob fallback across all of /home (slow, last resort)
    """
    rel = Path("prg/raspi-cam-srv/raspiCamSrv/camera_pi.py")

    # 1. Next to this script
    try:
        local = Path(__file__).resolve().parent / "camera_pi.py"
        if local.exists():
            print(f"  Found next to script: {local}")
            return local
    except NameError:
        pass  # __file__ unavailable when piped via curl | python3

    # 2. Current user home
    candidate = Path.home() / rel
    if candidate.exists():
        print(f"  Found in home: {candidate}")
        return candidate

    # 3. All /home/*/
    if Path("/home").exists():
        for h in sorted(Path("/home").iterdir()):
            if not h.is_dir():
                continue
            candidate = h / rel
            if candidate.exists():
                print(f"  Found in /home/{h.name}: {candidate}")
                return candidate

    # 4. Glob fallback
    print("  Searching via glob (may take a moment)...")
    results = list(Path("/home").glob("**/raspiCamSrv/camera_pi.py"))
    if results:
        print(f"  Found via glob: {results[0]}")
        return results[0]

    return Path("__NOT_FOUND__")

TARGET = find_target()

# ── Patch content ─────────────────────────────────────────────────────────────

IMPORT_CV2    = "import cv2"
IMPORT_MAPPED = "from picamera2 import MappedArray"

TIMESTAMP_CODE = '''
# === TIMESTAMP OVERLAY (patch_timestamp.py) ===
_ts_font      = cv2.FONT_HERSHEY_SIMPLEX
_ts_origin    = (40, 60)
_ts_color     = (255, 255, 255)
_ts_scale     = 1.0
_ts_thickness = 3

def apply_timestamp(request):
    """Pre-callback: burns current time into the lores stream frame."""
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    with MappedArray(request, "lores") as m:
        (text_width, text_height), baseline = cv2.getTextSize(
            timestamp, _ts_font, _ts_scale, _ts_thickness
        )
        bg_start = (_ts_origin[0] - 5, _ts_origin[1] - text_height - 10)
        bg_end   = (_ts_origin[0] + text_width + 5, _ts_origin[1] + baseline + 5)
        cv2.rectangle(m.array, bg_start, bg_end, (0, 0, 0), -1)
        cv2.putText(m.array, timestamp, _ts_origin, _ts_font,
                    _ts_scale, _ts_color, _ts_thickness, cv2.LINE_AA)
# === END TIMESTAMP OVERLAY ===
'''

# ── Helpers ───────────────────────────────────────────────────────────────────

def backup(path: Path) -> Path:
    stamp  = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak    = path.with_suffix(f".py.bak_{stamp}")
    shutil.copy2(path, bak)
    print(f"  Backup saved: {bak}")
    return bak

def already_patched(lines: list[str]) -> bool:
    return any("apply_timestamp" in l for l in lines)

def line_contains(lines, substr):
    """Returns True if any line contains substr."""
    return any(substr in l for l in lines)

def insert_after_first(lines, anchor, new_lines):
    """Insert new_lines after the first line containing anchor."""
    for i, l in enumerate(lines):
        if anchor in l:
            return True, lines[:i+1] + new_lines + lines[i+1:]
    return False, lines

def insert_before_first_toplevel(lines, keyword):
    """
    Insert TIMESTAMP_CODE before the first top-level class or def.
    Handles both LF and CRLF line endings.
    """
    for i, l in enumerate(lines):
        stripped = l.lstrip("\r\n")
        if stripped.startswith(keyword):
            return True, lines[:i] + [TIMESTAMP_CODE + "\n"] + lines[i:]
    return False, lines

# ── Main ──────────────────────────────────────────────────────────────────────

def patch(path: Path, force: bool = False):
    print(f"\ncamera_pi.py timestamp patch")
    print(f"Target: {path}")

    if not path.exists():
        print(f"  ERROR: file not found: {path}")
        sys.exit(1)

    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)

    if already_patched(lines):
        print("  apply_timestamp already found in file.")
        if force:
            print("  --force set, re-applying...")
        else:
            try:
                answer = input("  Re-apply patch anyway? [y/N]: ").strip().lower()
            except EOFError:
                # Non-interactive (e.g. curl | python3) — default to no
                answer = "n"
            if answer != "y":
                print("  Skipping. Run with --force to skip this prompt.")
                sys.exit(0)
        print("  Re-applying patch...")

    backup(path)
    changed = False

    # 1. Add "import cv2" after "import time"
    if not line_contains(lines, IMPORT_CV2):
        ok, lines = insert_after_first(lines, "import time", [IMPORT_CV2 + "\n"])
        if ok:
            print("  + import cv2 added")
            changed = True
        else:
            # fallback: insert at line 3
            lines = lines[:2] + [IMPORT_CV2 + "\n"] + lines[2:]
            print("  + import cv2 added (fallback: line 3)")
            changed = True
    else:
        print("  ~ import cv2 already present, skipping")

    # 2. Add "from picamera2 import MappedArray" if not already imported
    if not line_contains(lines, "MappedArray"):
        ok, lines = insert_after_first(lines, IMPORT_CV2, [IMPORT_MAPPED + "\n"])
        if ok:
            print("  + from picamera2 import MappedArray added")
            changed = True
        else:
            print("  ! MappedArray import: anchor not found, skipping")
    else:
        print("  ~ MappedArray already imported, skipping")

    # 3. Insert apply_timestamp function before first top-level class
    ok, lines = insert_before_first_toplevel(lines, "class ")
    if ok:
        print("  + apply_timestamp function added")
        changed = True
    else:
        # fallback: before first top-level def
        ok, lines = insert_before_first_toplevel(lines, "def ")
        if ok:
            print("  + apply_timestamp function added (fallback: before first def)")
            changed = True
        else:
            print("  ! Function: anchor not found")

    # 4. Register callback immediately before cam.start(show_preview=False)
    inserted_callback = False
    for i, l in enumerate(lines):
        if "cam.start(show_preview=False)" in l and "cam.pre_callback" not in l:
            indent  = len(l) - len(l.lstrip())
            cb_line = " " * indent + "cam.pre_callback = apply_timestamp\n"
            lines   = lines[:i] + [cb_line] + lines[i:]
            print("  + cam.pre_callback = apply_timestamp registered (before cam.start)")
            inserted_callback = True
            changed = True
            break
    if not inserted_callback:
        print("  ! Callback: anchor 'cam.start(show_preview=False)' not found")

    if changed:
        path.write_text("".join(lines), encoding="utf-8")
        print(f"\n  Done. Restart the picamera2 service:")
        print(f"  sudo systemctl restart raspi-cam-srv")
        print(f"  (check name with: systemctl list-units | grep cam)")
    else:
        print("  No changes made.")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(
        description="Patch camera_pi.py - add live timestamp overlay"
    )
    parser.add_argument(
        "--path", default=None,
        help="Path to camera_pi.py (default: auto-detect)"
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Re-apply patch without prompting even if already patched"
    )
    args   = parser.parse_args()
    target = Path(args.path) if args.path else TARGET
    patch(target, force=args.force)
