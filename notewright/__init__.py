"""Notewright: local, open-source music transcription."""

import os
import sys
from pathlib import Path

# Tools the installer puts inside Notewright's own environment (ffmpeg, deno) are found
# even when that environment isn't "activated".
_bin = str(Path(sys.executable).parent)
if _bin not in os.environ.get("PATH", "").split(os.pathsep):
    os.environ["PATH"] = _bin + os.pathsep + os.environ.get("PATH", "")

# On Apple Silicon a few PyTorch operations have no GPU version yet; run those on the CPU
# instead of failing. Must be set before torch is imported.
os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")

__version__ = "0.12.0"
