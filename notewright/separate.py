"""Source separation with Demucs (htdemucs_6s: drums, bass, other, vocals, guitar, piano)."""

from pathlib import Path
from typing import Callable

import numpy as np
import soundfile as sf

STEMS = ["piano", "vocals", "guitar", "bass", "drums", "other"]


def separate(wav_path: Path, out_dir: Path, device: str, progress: Callable[[float], None]) -> dict[str, Path]:
    import torch
    from demucs.api import Separator

    def on_progress(info: dict) -> None:
        if info.get("state") == "end" and info.get("audio_length"):
            within = info["segment_offset"] / info["audio_length"]
            progress(min(1.0, (info.get("model_idx_in_bag", 0) + within) / info.get("models", 1)))

    separator = Separator(model="htdemucs_6s", device=device, callback=on_progress)
    audio, sr = sf.read(str(wav_path), dtype="float32", always_2d=True)
    _, stems = separator.separate_tensor(torch.from_numpy(audio.T.copy()), sr)

    out_dir.mkdir(exist_ok=True)
    paths = {}
    for name, tensor in stems.items():
        path = out_dir / f"{name}.wav"
        sf.write(str(path), tensor.cpu().numpy().T.astype(np.float32), separator.samplerate)
        paths[name] = path
    return paths
