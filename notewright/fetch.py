"""Download every model Notewright uses, so the first song doesn't wait for them.

    python -m notewright.fetch

Safe to run again: models already downloaded are skipped.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path


def main() -> None:
    steps = [
        ("Piano model (165 MB)", lambda: __import__("notewright.transcribe", fromlist=["x"])._piano_checkpoint()),
        ("Beat tracker (80 MB)", _beats),
        ("Instrument separator (80 MB)", _separator),
        ("Instrument recogniser (330 MB)", lambda: __import__("notewright.instruments", fromlist=["x"])._model("cpu")),
        ("Sheet music reader (about 300 MB)", _homr),
    ]
    for i, (name, step) in enumerate(steps, 1):
        print(f"  [{i}/{len(steps)}] {name}", flush=True)
        step()
    print("  All models are ready.")


def _beats() -> None:
    from beat_this.inference import Audio2Beats

    Audio2Beats(checkpoint_path="final0", device="cpu", dbn=False)


def _separator() -> None:
    from demucs.pretrained import get_model

    get_model("htdemucs_6s")


def _homr() -> None:
    beside = Path(sys.executable).with_name("homr")
    homr = str(beside) if beside.exists() else shutil.which("homr")
    # PYTHONWARNINGS: its libraries print alarming but harmless warnings the first time they load.
    subprocess.run([homr, "--init"], check=True, stdout=subprocess.DEVNULL,
                   env={**os.environ, "PYTHONWARNINGS": "ignore"})


if __name__ == "__main__":
    main()
