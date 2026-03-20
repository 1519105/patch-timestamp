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

# ── Automatické nalezení camera_pi.py ────────────────────────────────────────

def find_target() -> Path:
    """
    Hledá camera_pi.py v tomto pořadí:
    1. Vedle tohoto skriptu (pokud je přibalený přímo v repo)
    2. ~/prg/raspi-cam-srv/raspiCamSrv/camera_pi.py (home aktuálního uživatele)
    3. /home/*/prg/raspi-cam-srv/raspiCamSrv/camera_pi.py (všechny home adresáře)
    4. Glob **/raspiCamSrv/camera_pi.py po celém /home (pomalý fallback)
    """
    rel = Path("prg/raspi-cam-srv/raspiCamSrv/camera_pi.py")

    # 1. Vedle skriptu
    try:
        local = Path(__file__).resolve().parent / "camera_pi.py"
        if local.exists():
            print(f"  Nalezeno vedle skriptu: {local}")
            return local
    except NameError:
        pass  # __file__ není dostupné při spuštění přes curl | python3

    # 2. Home aktuálního uživatele
    candidate = Path.home() / rel
    if candidate.exists():
        print(f"  Nalezeno v home: {candidate}")
        return candidate

    # 3. Všechny /home/*/
    if Path("/home").exists():
        for h in sorted(Path("/home").iterdir()):
            if not h.is_dir():
                continue
            candidate = h / rel
            if candidate.exists():
                print(f"  Nalezeno v /home/{h.name}: {candidate}")
                return candidate

    # 4. Glob fallback — prohledá celý /home
    print("  Hledám přes glob (může chvíli trvat)...")
    results = list(Path("/home").glob("**/raspiCamSrv/camera_pi.py"))
    if results:
        print(f"  Nalezeno přes glob: {results[0]}")
        return results[0]

    return Path("__NENALEZENO__")

TARGET = find_target()

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
ANCHOR_CALLBACK       = "cam.start(show_preview=False)"

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

    # 3. Vložit funkci apply_timestamp před první třídu v souboru
    #    Hledáme "class " na začátku řádku (funguje i pro \r\n soubory)
    inserted_func = False
    for i, l in enumerate(lines):
        if l.lstrip("\r\n").startswith("class "):
            lines = lines[:i] + [TIMESTAMP_CODE + "\n"] + lines[i:]
            print("  + apply_timestamp funkce přidána")
            inserted_func = True
            changed = True
            break
    if not inserted_func:
        # fallback: před první def na začátku řádku
        for i, l in enumerate(lines):
            if l.lstrip("\r\n").startswith("def "):
                lines = lines[:i] + [TIMESTAMP_CODE + "\n"] + lines[i:]
                print("  + apply_timestamp funkce přidána (fallback)")
                inserted_func = True
                changed = True
                break
    if not inserted_func:
        print("  ! Funkce — kotva nenalezena")

    # 4. Registrovat callback: vložit za "if not isUsb:"
    #    Najdeme blok kde je "if not isUsb:" a přidáme callback jako další řádek
    inserted_callback = False
    for i, l in enumerate(lines):
        if ANCHOR_CALLBACK in l and "cam.pre_callback" not in l:
            # Vložit PŘED cam.start() se stejným odsazením
            indent = len(l) - len(l.lstrip())
            cb_line = " " * indent + "cam.pre_callback = apply_timestamp\n"
            lines = lines[:i] + [cb_line] + lines[i:]
            print("  + cam.pre_callback = apply_timestamp registrován (před cam.start)")
            inserted_callback = True
            changed = True
            break
    if not inserted_callback:
        print("  ! Callback — kotva 'cam.start(show_preview=False)' nenalezena, nutná manuální kontrola")

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
    parser.add_argument("--path", default=None,
                        help="Cesta k camera_pi.py (výchozí: automatické hledání)")
    args = parser.parse_args()

    target = Path(args.path) if args.path else TARGET
    patch(target)
