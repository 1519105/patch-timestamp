#!/usr/bin/env python3
"""
patch_timestamp.py — přidá časovou značku zpět do camera_pi.py po aktualizaci

Použití:
    python3 patch_timestamp.py

Nebo stáhnout a spustit jedním příkazem:
    curl -sL https://raw.githubusercontent.com/TVUJ_GITHUB/REPO/main/patch_timestamp.py | python3
"""

import sys
import shutil
from pathlib import Path
from datetime import datetime

TARGET = Path("/home/cam/prg/raspi-cam-srv/raspiCamSrv/camera_pi.py")

# ── Co přidat ────────────────────────────────────────────────────────────────

IMPORT_CV2        = "import cv2"
IMPORT_MAPPED     = "from picamera2 import MappedArray"

TIMESTAMP_CODE = '''
# === TIMESTAMP OVERLAY ===
_ts_font      = cv2.FONT_HERSHEY_SIMPLEX
_ts_origin    = (40, 60)
_ts_color     = (255, 255, 255)
_ts_scale     = 1.0
_ts_thickness = 3

def apply_timestamp(request):
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    with MappedArray(request, "lores") as m:
        (text_width, text_height), baseline = cv2.getTextSize(
            timestamp, _ts_font, _ts_scale, _ts_thickness
        )
        bg_rect_start = (_ts_origin[0] - 5, _ts_origin[1] - text_height - 10)
        bg_rect_end   = (_ts_origin[0] + text_width + 5, _ts_origin[1] + baseline + 5)
        cv2.rectangle(m.array, bg_rect_start, bg_rect_end, (0, 0, 0), -1)
        cv2.putText(m.array, timestamp, _ts_origin, _ts_font,
                    _ts_scale, _ts_color, _ts_thickness, cv2.LINE_AA)
# === END TIMESTAMP OVERLAY ===
'''

CALLBACK_LINE     = "                        cam.pre_callback = apply_timestamp"

# ── Kotvy pro vložení ─────────────────────────────────────────────────────────

# Import cv2 vložit za první blok importů (hledáme "import time" nebo "import os")
ANCHOR_IMPORT_CV2     = "import time"

# MappedArray import vložit za ostatní picamera2 importy
ANCHOR_IMPORT_MAPPED  = "from picamera2 import"

# Timestamp funkci vložit za definici konstant (hledáme konec importů — prázdný řádek po posledním importu)
ANCHOR_TIMESTAMP_FUNC = "# ── Hlavní funkce ──"   # fallback — viz logika níže

# Callback vložit za "cam.start()"
ANCHOR_CALLBACK       = "if not isUsb:"

# ── Pomocné funkce ────────────────────────────────────────────────────────────

def backup(path: Path) -> Path:
    stamp  = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = path.with_suffix(f".py.bak_{stamp}")
    shutil.copy2(path, backup)
    print(f"  Záloha uložena: {backup}")
    return backup

def already_patched(lines: list[str]) -> bool:
    return any("apply_timestamp" in l for l in lines)

def insert_after_last(lines, anchor_substr, new_lines, deduplicate=True):
    """Vloží new_lines za poslední výskyt řádku obsahujícího anchor_substr."""
    idx = None
    for i, l in enumerate(lines):
        if anchor_substr in l:
            idx = i
    if idx is None:
        return False, lines
    if deduplicate and any(nl.strip() in l for nl in new_lines for l in lines):
        return False, lines
    lines = lines[:idx+1] + new_lines + lines[idx+1:]
    return True, lines

def insert_after_first(lines, anchor_substr, new_lines):
    """Vloží new_lines za první výskyt řádku obsahujícího anchor_substr."""
    for i, l in enumerate(lines):
        if anchor_substr in l:
            lines = lines[:i+1] + new_lines + lines[i+1:]
            return True, lines
    return False, lines

def insert_before_first(lines, anchor_substr, new_lines):
    """Vloží new_lines před první výskyt řádku obsahujícího anchor_substr."""
    for i, l in enumerate(lines):
        if anchor_substr in l:
            lines = lines[:i] + new_lines + lines[i:]
            return True, lines
    return False, lines

# ── Hlavní logika ─────────────────────────────────────────────────────────────

def patch(path: Path):
    print(f"\ncamera_pi.py patch — časová značka")
    print(f"Soubor: {path}")

    if not path.exists():
        print(f"  CHYBA: soubor nenalezen: {path}")
        sys.exit(1)

    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)

    if already_patched(lines):
        print("  Soubor je již patchovaný (apply_timestamp nalezena). Přeskakuji.")
        sys.exit(0)

    backup(path)

    changed = False

    # 1. Přidat "import cv2" za "import time"
    ok, lines = insert_after_first(lines, ANCHOR_IMPORT_CV2, [IMPORT_CV2 + "\n"])
    if ok:
        print("  + import cv2 přidán")
        changed = True
    else:
        print("  ! import cv2 — kotva nenalezena, vkládám na řádek 3")
        lines = lines[:2] + [IMPORT_CV2 + "\n"] + lines[2:]
        changed = True

    # 2. Přidat "from picamera2 import MappedArray" za poslední "from picamera2 import"
    ok, lines = insert_after_last(lines, ANCHOR_IMPORT_MAPPED, [IMPORT_MAPPED + "\n"])
    if ok:
        print("  + from picamera2 import MappedArray přidán")
        changed = True
    else:
        print("  ! MappedArray import — kotva nenalezena, vkládám za cv2 import")
        ok2, lines = insert_after_first(lines, IMPORT_CV2, [IMPORT_MAPPED + "\n"])
        changed = changed or ok2

    # 3. Vložit funkci apply_timestamp před první "def " po importech
    #    (= před první definici funkce v souboru)
    ok, lines = insert_before_first(lines, "\ndef ", [TIMESTAMP_CODE + "\n"])
    if ok:
        print("  + apply_timestamp funkce přidána")
        changed = True
    else:
        print("  ! Funkce — kotva nenalezena")

    # 4. Registrovat callback: vložit za "if not isUsb:"
    #    Najdeme blok kde je "if not isUsb:" a přidáme callback jako další řádek
    inserted_callback = False
    for i, l in enumerate(lines):
        if ANCHOR_CALLBACK in l and "cam.pre_callback" not in l:
            indent = len(l) - len(l.lstrip())
            cb_line = " " * (indent + 4) + CALLBACK_LINE.strip() + "\n"
            lines = lines[:i+1] + [cb_line] + lines[i+1:]
            print("  + cam.pre_callback = apply_timestamp registrován")
            inserted_callback = True
            changed = True
            break
    if not inserted_callback:
        print("  ! Callback — kotva 'if not isUsb:' nenalezena, nutná manuální kontrola")

    if changed:
        path.write_text("".join(lines), encoding="utf-8")
        print(f"\n  Hotovo. Restartuj picamera2 server:")
        print(f"  sudo systemctl restart camera.service")
        print(f"  (nebo jak se jmenuje tvoje service — zkontroluj: systemctl list-units | grep cam)")
    else:
        print("  Žádné změny neprovedeny.")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Patch camera_pi.py — přidat časovou značku")
    parser.add_argument("--path", default=str(TARGET),
                        help=f"Cesta k camera_pi.py (výchozí: {TARGET})")
    args = parser.parse_args()
    patch(Path(args.path))
