import os
from pathlib import Path
from typing import Callable, TypeVar

T = TypeVar("T")

DATA_DIR = Path(os.environ.get("NOTEWRIGHT_DATA", Path(__file__).resolve().parent.parent / "data"))
MODEL_DIR = Path(os.environ.get("NOTEWRIGHT_MODELS", Path.home() / ".cache" / "notewright"))
PIANO_CHECKPOINT_URL = (
    "https://zenodo.org/record/4034264/files/"
    "CRNN_note_F1%3D0.9677_pedal_F1%3D0.9186.pth?download=1"
)


def device() -> str:
    """The fastest place to run the models: an NVIDIA GPU, the Apple Silicon GPU, or the CPU."""
    forced = os.environ.get("NOTEWRIGHT_DEVICE")
    if forced:
        return forced
    import torch

    if torch.cuda.is_available():
        return "cuda"
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


class JobDeleted(Exception):
    """The song was deleted while it was being worked on."""


def gpu_or_cpu(dev: str, step: Callable[[str], T], what: str) -> T:
    """Run a step on the GPU; if the GPU can't run it, run it again on the CPU.

    Same model, same weights: the notes don't depend on where it runs, only the speed does.
    """
    if dev == "cpu":
        return step("cpu")
    try:
        return step(dev)
    except JobDeleted:
        raise
    except Exception as exc:  # noqa: BLE001 - whatever the GPU can't do, the CPU can
        print(f"Notewright: {what} failed on {dev} ({type(exc).__name__}: {exc}); running it on the CPU instead.",
              flush=True)
        return step("cpu")
