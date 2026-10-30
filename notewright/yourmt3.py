"""Optional YourMT3+ add-on: a multi-instrument transcriber trained on band recordings.

On a test ballad with known notes it found the piano far better than the solo-piano
model (F 0.84 vs 0.59-0.73), so when it is installed (addons/yourmt3/install.sh)
Notewright takes the piano notes from it. It runs in its own Python environment as a
separate program; Notewright only reads the notes it prints. Without it, nothing changes.
"""

import json
import os
import subprocess
from pathlib import Path
from typing import Callable

ADDON = Path(os.environ.get("NOTEWRIGHT_YOURMT3", Path(__file__).resolve().parent.parent / "addons" / "yourmt3"))
CHECKPOINT = "space/amt/logs/2024/mc13_256_g4_all_v7_mt3f_sqr_rms_moe_wf4_n8k2_silu_rope_rp_b36_nops/checkpoints/last.ckpt"

# YourMT3+ names instrument classes by a representative General MIDI program.
PIANO_CLASSES = {0, 8, 16}  # piano, chromatic percussion (vibes etc.), organ
GUITAR = 24


def python() -> Path:
    return ADDON / "venv" / "bin" / "python"


def available() -> bool:
    return python().exists() and (ADDON / CHECKPOINT).exists()


def transcribe(wav: Path, progress: Callable[[float], None]) -> list:
    """All notes YourMT3+ hears: [start, end, pitch, velocity, program, is_drum]."""
    proc = subprocess.Popen(
        [str(python()), str(ADDON / "runner.py"), str(wav)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    tail = []
    for line in proc.stderr:
        if line.startswith("progress "):
            progress(float(line.split()[1]))
        else:
            tail = (tail + [line])[-20:]
    out = proc.stdout.read()
    if proc.wait() != 0:
        raise RuntimeError("YourMT3+ add-on failed: " + "".join(tail)[-500:])
    return json.loads(out)["notes"]


def piano_notes(notes: list, guitar_in_lineup: bool) -> list:
    """Its piano notes. Without a guitar in the band, what it calls guitar is piano."""
    classes = PIANO_CLASSES | (set() if guitar_in_lineup else {GUITAR})
    return sorted([n[0], n[1], n[2], n[3]] for n in notes if not n[5] and n[4] in classes)
