"""Notewright: local, open-source music transcription."""

import os

# On Apple Silicon a few PyTorch operations have no GPU version yet; run those on the CPU
# instead of failing. Must be set before torch is imported.
os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")

__version__ = "0.9.0"
