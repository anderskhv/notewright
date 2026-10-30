"""Transcribe one audio file with YourMT3+ and print its notes as JSON.

Runs inside the add-on's own environment (see install.sh), as a separate program:
Notewright starts it and reads its output. YourMT3+ (Chang et al. 2024) is not part of
Notewright; its code and checkpoint are downloaded by install.sh under their own licence.

Usage: python runner.py AUDIO_FILE > notes.json
Output: {"notes": [[start, end, pitch, velocity, program, is_drum], ...]}
"""

import importlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SPACE = os.path.join(HERE, "space")
CHECKPOINT = "mc13_256_g4_all_v7_mt3f_sqr_rms_moe_wf4_n8k2_silu_rope_rp_b36_nops@last.ckpt"
ARGS = [CHECKPOINT, "-p", "2024", "-tk", "mc13_full_plus_256", "-dec", "multi-t5", "-nl", "26", "-enc", "perceiver-tf",
        "-sqr", "1", "-ff", "moe", "-wf", "4", "-nmoe", "8", "-kmoe", "2", "-act", "silu", "-epe", "rope", "-rp", "1",
        "-ac", "spec", "-hop", "300", "-atc", "1", "-pr", "32"]
# The checkpoint stores a few of YourMT3's own data classes next to the weights. Allow
# exactly those, so it can be read weights-only rather than as arbitrary pickled code.
SAFE = ["utils.task_manager.TaskManager", "numpy.core.multiarray._reconstruct", "numpy.ndarray", "numpy.dtype",
        "numpy.dtypes.Int64DType", "numpy.core.multiarray.scalar", "utils.tokenizer.NoteEventTokenizer",
        "utils.event_codec.FastCodec", "utils.note_event_dataclasses.EventRange", "utils.note_event_dataclasses.Event"]
CHUNK_SEGMENTS = 32  # ~65 s of audio per pass keeps memory flat on long songs


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def main(path: str) -> None:
    os.chdir(SPACE)
    sys.path.insert(0, os.path.join(SPACE, "amt", "src"))
    sys.path.insert(0, SPACE)
    os.environ.setdefault("WANDB_MODE", "disabled")
    import numpy as np
    import soundfile as sf
    import torch
    import torchaudio

    allowed = [getattr(importlib.import_module(n.rsplit(".", 1)[0]), n.rsplit(".", 1)[1]) for n in SAFE]
    original_load = torch.load

    def safe_load(*a, **k):
        k["weights_only"] = True
        with torch.serialization.safe_globals(allowed):
            return original_load(*a, **k)

    torch.load = safe_load
    stdout = sys.stdout
    sys.stdout = sys.stderr  # their loader prints its configuration
    import model_helper
    from utils.audio import slice_padded_array
    from utils.event2note import merge_zipped_note_events_and_ties_to_notes
    from utils.note2event import mix_notes

    model = model_helper.load_model_checkpoint(args=ARGS, device="cpu")
    sys.stdout = stdout
    y, sr = sf.read(path, dtype="float32", always_2d=True)
    audio = torch.from_numpy(y.mean(axis=1)[None, :].copy())
    audio = torchaudio.functional.resample(audio, sr, model.audio_cfg["sample_rate"])
    frames = model.audio_cfg["input_frames"]
    segments = slice_padded_array(audio, frames, frames)
    segments = torch.from_numpy(segments.astype("float32")).unsqueeze(1)
    n = segments.shape[0]
    channels = model.task_manager.num_decoding_channels
    starts = [frames * i / model.audio_cfg["sample_rate"] for i in range(n)]
    per_channel = [[] for _ in range(channels)]
    with torch.inference_mode():
        for c0 in range(0, n, CHUNK_SEGMENTS):
            tokens, _ = model.inference_file(bsz=8, audio_segments=segments[c0:c0 + CHUNK_SEGMENTS])
            for ch in range(channels):
                zipped, _, _ = model.task_manager.detokenize_list_batches(
                    [arr[:, ch, :] for arr in tokens], starts[c0:c0 + CHUNK_SEGMENTS], return_events=True)
                notes, _ = merge_zipped_note_events_and_ties_to_notes(zipped)
                per_channel[ch].extend(notes)
            log(f"progress {min(n, c0 + CHUNK_SEGMENTS) / n:.3f}")
    notes = mix_notes(per_channel)
    out = [[round(float(x.onset), 4), round(float(x.offset), 4), int(x.pitch), int(x.velocity) or 80, int(x.program), bool(x.is_drum)]
           for x in notes]
    json.dump({"notes": out}, stdout)


if __name__ == "__main__":
    main(sys.argv[1])
