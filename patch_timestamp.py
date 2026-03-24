#!/usr/bin/env python3
"""
patch_timestamp.py - Adds a live timestamp overlay to camera_pi.py (raspiCamSrv).

Automatically re-applies after git pull updates overwrite the file.

What this script does:
  1. Finds the venv used by raspiCamSrv and checks if opencv is installed.
     If not, installs opencv-python-headless into that venv automatically.
  2. Patches camera_pi.py with a safe apply_timestamp() callback:
     - No module-level cv2 constants (safe when cv2 missing at import time)
     - Tries "lores" stream first, falls back to "main" (RPi Zero compat)
     - Guards against cv2 being unavailable at runtime

Usage:
    python3 patch_timestamp.py
    python3 patch_timestamp.py --path /custom/path/to/camera_pi.py
    python3 patch_timestamp.py --force         # re-apply without prompting
    python3 patch_timestamp.py --no-cv2-check  # skip opencv install check

One-liner from GitHub:
    curl -sL https://raw.githubusercontent.com/1519105/patch-timestamp/main/patch_timestamp.py | python3
"""

import sys
import shutil
import subprocess
from pathlib import Path
from datetime import datetime

# ── Auto-discovery ────────────────────────────────────────────────────────────

def find_target() -> Path:
    """
    Searches for camera_pi.py in this order:
    1. Same directory as this script (if placed inside the repo)
    2. ~/prg/raspiCamSrv/raspiCamSrv/camera_pi.py (current user home)
    3. /home/*/prg/raspiCamSrv/raspiCamSrv/camera_pi.py (all home dirs)
    4. Glob fallback across all of /home (slow, last resort)
    """
    rel = Path("prg/raspiCamSrv/raspiCamSrv/camera_pi.py")

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

# ── cv2 / opencv detection and install ───────────────────────────────────────

def find_venv_pip(target: Path):
    """
    Finds the pip executable of the venv used by raspiCamSrv.
    Walks up from camera_pi.py's location, then tries common /home paths.
    Returns a Path to pip, or None if not found.
    """
    for parent in [target.parent, target.parent.parent, target.parent.parent.parent]:
        for venv_name in (".venv", "venv", "env"):
            pip = parent / venv_name / "bin" / "pip"
            if pip.exists():
                return pip

    # Fallback: glob common locations
    for pattern in [
        "home/*/prg/raspi-cam-srv/.venv/bin/pip",
        "home/*/prg/raspiCamSrv/.venv/bin/pip",
    ]:
        results = list(Path("/").glob(pattern))
        if results:
            return results[0]

    return None


def check_and_install_cv2(target: Path, skip: bool = False):
    """
    Checks whether cv2 is importable in the raspiCamSrv venv.
    If missing and skip=False, offers to install opencv-python-headless.
    """
    if skip:
        print("\ncv2 check skipped (--no-cv2-check).")
        return

    print("\nChecking cv2 (opencv) in raspiCamSrv venv...")

    pip = find_venv_pip(target)
    if pip is None:
        print("  ! Could not locate venv pip — skipping.")
        print("    Install manually: pip install opencv-python-headless")
        return

    python = pip.parent / "python3"
    if not python.exists():
        python = pip.parent / "python"

    # Test import
    result = subprocess.run(
        [str(python), "-c", "import cv2"],
        capture_output=True,
    )
    if result.returncode == 0:
        ver = subprocess.run(
            [str(python), "-c", "import cv2; print(cv2.__version__)"],
            capture_output=True, text=True,
        )
        print(f"  ~ cv2 already available: version {ver.stdout.strip()}")
        return

    # Not found — ask user (or auto-install when no TTY, e.g. curl | python3)
    print(f"  ! cv2 not found in venv at: {pip.parent}")
    try:
        with open("/dev/tty") as tty:
            sys.stdout.write("  Install opencv-python-headless now? [Y/n]: ")
            sys.stdout.flush()
            answer = tty.readline().strip().lower()
    except OSError:
        answer = "y"
        print("  No TTY detected — installing automatically.")

    if answer in ("", "y"):
        print("  Installing opencv-python-headless (may take a minute on Zero)...")
        ret = subprocess.run([str(pip), "install", "opencv-python-headless"])
        if ret.returncode == 0:
            print("  + opencv-python-headless installed successfully.")
        else:
            print("  ! Installation failed. Try manually:")
            print(f"    sudo {pip} install opencv-python-headless")
    else:
        print("  Skipped. Timestamp overlay will be inactive until cv2 is installed.")


# ── Patch content ─────────────────────────────────────────────────────────────

IMPORT_CV2    = "import cv2"
IMPORT_MAPPED = "from picamera2 import MappedArray"

# Key fix 1: NO module-level cv2.FONT_* constants — all cv2 usage is inside
#            the function, guarded by a cv2Available check.
# Key fix 2: tries "lores" first, falls back to "main" (RPi Zero uses "main").
# Key fix 3: explicit break after success so only one stream is written.
TIMESTAMP_CODE = '''
# === TIMESTAMP OVERLAY (patch_timestamp.py) ===

def apply_timestamp(request):
    """Pre-callback: burns current date/time into the live camera stream.

    Safe on all Pi models:
      - Returns immediately if cv2 is not available.
      - Tries the "lores" stream first (RPi 4/5), then falls back to "main"
        (RPi Zero / USB cameras that only configure a main stream).
      - All cv2 constants are resolved inside the function, never at import
        time, so the module loads cleanly even without cv2 installed.
    """
    try:
        cv2Available  # noqa: F821  (defined by the existing try/except block)
    except NameError:
        return
    if not cv2Available:
        return

    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    font      = cv2.FONT_HERSHEY_SIMPLEX
    origin    = (40, 60)
    color     = (255, 255, 255)
    scale     = 1.0
    thickness = 3

    for _stream in ("lores", "main"):
        try:
            with MappedArray(request, _stream) as m:
                (tw, th), baseline = cv2.getTextSize(timestamp, font, scale, thickness)
                bg_tl = (origin[0] - 5,      origin[1] - th - 10)
                bg_br = (origin[0] + tw + 5,  origin[1] + baseline + 5)
                cv2.rectangle(m.array, bg_tl, bg_br, (0, 0, 0), -1)
                cv2.putText(m.array, timestamp, origin, font,
                            scale, color, thickness, cv2.LINE_AA)
            break  # success — don't touch the other stream
        except Exception:
            continue

# === END TIMESTAMP OVERLAY ===
'''

# ── Helpers ───────────────────────────────────────────────────────────────────

def backup(path: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak   = path.with_suffix(f".py.bak_{stamp}")
    shutil.copy2(path, bak)
    print(f"  Backup saved: {bak}")
    return bak

def already_patched(lines: list[str]) -> bool:
    return any("apply_timestamp" in l for l in lines)

def line_contains(lines, substr):
    return any(substr in l for l in lines)

def insert_after_first(lines, anchor, new_lines):
    """Insert new_lines after the first line containing anchor."""
    for i, l in enumerate(lines):
        if anchor in l:
            return True, lines[:i+1] + new_lines + lines[i+1:]
    return False, lines

def insert_before_first_toplevel(lines, keyword):
    """Insert TIMESTAMP_CODE before the first top-level class or def."""
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
                with open("/dev/tty") as tty:
                    sys.stdout.write("  Re-apply patch anyway? [y/N]: ")
                    sys.stdout.flush()
                    answer = tty.readline().strip().lower()
            except OSError:
                answer = "n"
                print("  No TTY available, defaulting to N. Use --force to override.")
            if answer != "y":
                print("  Skipping.")
                sys.exit(0)
        # Remove old patch block before re-inserting
        lines = remove_old_patch(lines)
        print("  Old patch block removed.")

    backup(path)
    changed = False

    # 1. Add "import cv2" after "import time"
    if not line_contains(lines, IMPORT_CV2):
        ok, lines = insert_after_first(lines, "import time", [IMPORT_CV2 + "\n"])
        if ok:
            print("  + import cv2 added")
            changed = True
        else:
            lines = lines[:2] + [IMPORT_CV2 + "\n"] + lines[2:]
            print("  + import cv2 added (fallback: line 3)")
            changed = True
    else:
        print("  ~ import cv2 already present, skipping")

    # 2. Add "from picamera2 import MappedArray" if missing
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
        print(f"  sudo systemctl restart raspiCamSrv")
        print(f"  (check name with: systemctl list-units | grep cam)")
    else:
        print("  No changes made.")


def remove_old_patch(lines: list[str]) -> list[str]:
    """Strip an existing patch block delimited by the marker comments."""
    start_marker = "# === TIMESTAMP OVERLAY"
    end_marker   = "# === END TIMESTAMP OVERLAY ==="
    result = []
    inside = False
    for l in lines:
        if start_marker in l:
            inside = True
        if not inside:
            result.append(l)
        if end_marker in l:
            inside = False
    # Also remove any cam.pre_callback = apply_timestamp lines added outside
    # the block (inserted just before cam.start).
    result = [l for l in result if "cam.pre_callback = apply_timestamp" not in l]
    return result


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
    parser.add_argument(
        "--no-cv2-check", action="store_true", dest="no_cv2_check",
        help="Skip opencv availability check and auto-install"
    )
    args   = parser.parse_args()
    target = Path(args.path) if args.path else TARGET

    check_and_install_cv2(target, skip=args.no_cv2_check)
    patch(target, force=args.force)
