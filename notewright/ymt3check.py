"""Does YourMT3+ give the same notes on this Mac's GPU as on its processor, and how much faster?

    python -m notewright.ymt3check "Song title"

Takes one minute of a finished song, transcribes it with YourMT3+ on the processor and
on the GPU, and compares the notes. Nothing in your library changes.
"""

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from . import yourmt3
from .pipeline import jobs_dir, list_jobs


def run(wav: Path, device: str) -> tuple[list, float]:
    env = {**os.environ, "NOTEWRIGHT_YOURMT3_DEVICE": device, "PYTORCH_ENABLE_MPS_FALLBACK": "1"}
    t = time.time()
    out = subprocess.run([str(yourmt3.python()), str(yourmt3.ADDON / "runner.py"), str(wav)],
                         capture_output=True, text=True, env=env)
    if out.returncode:
        raise RuntimeError(out.stderr.strip().splitlines()[-1] if out.stderr.strip() else "failed")
    return json.loads(out.stdout)["notes"], time.time() - t


def main(text: str) -> None:
    if not yourmt3.available():
        sys.exit("The YourMT3+ add-on is not installed.")
    found = [j for j in list_jobs() if text.lower() in j["title"].lower() and j["status"] == "done"]
    if not found:
        sys.exit(f'No finished song with "{text}" in the title.')
    d = jobs_dir() / found[0]["id"]
    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "minute.wav"
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", "60", "-t", "60",
                        "-i", str(d / "audio.m4a"), "-ac", "1", "-ar", "44100", str(wav)], check=True)
        print("One minute of", found[0]["title"])
        cpu, t_cpu = run(wav, "cpu")
        print(f"  processor: {len(cpu)} notes in {t_cpu:.0f} s")
        try:
            gpu, t_gpu = run(wav, "mps")
        except RuntimeError as err:
            print(f"  GPU: did not run ({err})")
            return
        print(f"  GPU:       {len(gpu)} notes in {t_gpu:.0f} s")
    key = lambda n: (round(n[0], 2), n[2], n[4], n[5])  # noqa: E731
    a, b = {key(n) for n in cpu}, {key(n) for n in gpu}
    same = len(a & b) / max(1, len(a | b))
    print(f"  notes identical (start to 10 ms, pitch, instrument): {same:.1%}, {t_cpu / t_gpu:.1f}x the speed")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
