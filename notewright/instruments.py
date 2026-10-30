"""Which instruments play, and regrouping Demucs' six stems into those instruments.

Demucs always returns piano, vocals, guitar, bass, drums and other, and every stem
carries some of the rest. Two things turn that into the real line-up:

1. Detection. PANNs (Kong et al. 2020), an AudioSet sound-event model, listens to the
   mix and to each stem. Its guesses, together with the stems' loudness, give a
   suggested line-up that the player can correct.
2. Regrouping. A stem whose instrument is not in the line-up is not shown. Its audio
   goes, one second at a time, to the instrument it sounds most like, judged by which
   other stem plays the same notes at that moment (chroma similarity). Piano that
   Demucs put in "guitar" returns to the piano, and horns in "vocals" go to the horns.
   The parts still add up to the whole recording, so muting one leaves nothing of it.
"""

import urllib.request
from pathlib import Path

import librosa
import numpy as np

from .config import MODEL_DIR

SED_URL = "https://zenodo.org/record/3987831/files/Cnn14_DecisionLevelMax_mAP%3D0.385.pth?download=1"

# Instruments a line-up can hold, in display order. "horns" have kinds (below).
FAMILIES = ["piano", "horns", "guitar", "vocals", "bass", "drums", "other"]
HORNS = ["saxophone", "trumpet", "trombone", "clarinet", "flute"]
NAMES = {
    "piano": "Piano", "guitar": "Guitar", "vocals": "Vocals", "bass": "Bass", "drums": "Drums",
    "other": "Other (strings, keys, synths)", "saxophone": "Saxophone", "trumpet": "Trumpet",
    "trombone": "Trombone", "clarinet": "Clarinet", "flute": "Flute",
}

_LABELS = {
    "piano": ["Piano", "Keyboard (musical)", "Electric piano"],
    "guitar": ["Guitar", "Electric guitar", "Acoustic guitar", "Steel guitar, slide guitar", "Plucked string instrument"],
    "singing": ["Singing", "Male singing", "Female singing", "Choir", "Vocal music", "Rapping", "Child singing"],
    "saxophone": ["Saxophone"],
    "trumpet": ["Trumpet"],
    "trombone": ["Trombone"],
    "clarinet": ["Clarinet"],
    "flute": ["Flute"],
    "horns": ["Brass instrument", "Wind instrument, woodwind instrument", "Saxophone", "Trumpet", "Trombone"],
    "drums": ["Drum kit", "Drum", "Snare drum", "Bass drum", "Cymbal", "Hi-hat"],
    "double_bass": ["Double bass"],
    "bass_guitar": ["Bass guitar"],
}

_sed = None


# AudioSet's label list (CC BY 4.0), shipped with Notewright. panns_inference reads
# it from ~/panns_data on import and would otherwise try to fetch it with wget.
LABELS_CSV = Path(__file__).with_name("audioset_labels.csv")


def _labels() -> list[str]:
    import csv

    with open(LABELS_CSV, newline="") as f:
        return [row[2] for row in list(csv.reader(f))[1:]]


def _labels_file() -> None:
    import shutil

    path = Path.home() / "panns_data" / "class_labels_indices.csv"
    if not path.exists() or path.stat().st_size < 10_000:
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(LABELS_CSV, path)


def _model(device: str):
    global _sed
    if _sed is None:
        import torch

        _labels_file()
        from panns_inference.models import Cnn14_DecisionLevelMax

        path = MODEL_DIR / "instruments_sed.pth"
        if not path.exists() or path.stat().st_size < 3e8:
            MODEL_DIR.mkdir(parents=True, exist_ok=True)
            urllib.request.urlretrieve(SED_URL, path.with_suffix(".part"))
            path.with_suffix(".part").rename(path)
        net = Cnn14_DecisionLevelMax(
            sample_rate=32000, window_size=1024, hop_size=320, mel_bins=64, fmin=50, fmax=14000, classes_num=527
        )
        net.load_state_dict(torch.load(str(path), map_location="cpu", weights_only=True)["model"])
        net.to(device if device == "cuda" else "cpu").eval()
        _sed = net
    return _sed


def listen(y32k: np.ndarray, device: str) -> dict[str, np.ndarray]:
    """Per-frame (100 per second) probability for each label group in _LABELS."""
    import torch

    net = _model(device)
    labels = _labels()
    peak = np.abs(y32k).max() or 1.0
    x = torch.tensor((y32k / peak * 0.9)[None, :], dtype=torch.float32, device=next(net.parameters()).device)
    with torch.no_grad():
        frames = net(x)["framewise_output"][0].cpu().numpy()
    return {g: frames[:, [labels.index(n) for n in names]].max(axis=1) for g, names in _LABELS.items()}


def _load32(path: Path) -> np.ndarray:
    y, _ = librosa.load(str(path), sr=32000, mono=True)
    return y


def _rms(y: np.ndarray) -> float:
    return float(np.sqrt(np.mean(y**2))) if y.size else 0.0


def detect(mix: Path, stems: dict[str, Path], device: str) -> dict:
    """Suggest a line-up: {"families": [...], "horns": [...], "bassKind": ...}."""
    y_mix = _load32(mix)
    mix_rms = _rms(y_mix) or 1.0
    on_mix = listen(y_mix, device)
    level, heard = {}, {}
    for name, path in stems.items():
        y = _load32(path)
        level[name] = _rms(y) / mix_rms
        heard[name] = listen(y, device)

    p90 = lambda a: float(np.percentile(a, 90))  # noqa: E731 - "plays for at least a tenth of the song"
    families = []
    # Piano is hard to recognise on old or dull recordings, so its stem's level counts too.
    if p90(heard["piano"]["piano"]) > 0.05 or (level["piano"] > 0.15 and p90(heard["guitar"]["guitar"]) < 0.1):
        families.append("piano")
    horn_score = max(p90(on_mix["horns"]), p90(heard["other"]["horns"]), p90(heard["vocals"]["horns"]))
    horns = []
    if horn_score > 0.12:
        families.append("horns")
        # The model tells "a horn" far better than which horn: suggest only the clearest one.
        kinds = {k: max(p90(on_mix[k]), p90(heard["other"][k])) for k in HORNS}
        horns = [max(kinds, key=kinds.get)]
    if p90(heard["guitar"]["guitar"]) > 0.15 or p90(on_mix["guitar"]) > 0.2:
        families.append("guitar")
    if p90(heard["vocals"]["singing"]) > 0.15 or p90(on_mix["singing"]) > 0.2:
        families.append("vocals")
    if level["bass"] > 0.02:
        families.append("bass")
    if level["drums"] > 0.05 or p90(heard["drums"]["drums"]) > 0.1:
        families.append("drums")
    if not families:
        families = ["other"]
    double = p90(heard["bass"]["double_bass"]) + (0.05 if horns else 0)
    return {
        "families": families,
        "horns": horns,
        "bassKind": "double" if double >= p90(heard["bass"]["bass_guitar"]) else "electric",
    }


def lineup_by_level(mix: Path, stems: dict[str, Path]) -> dict:
    """Fallback line-up from stem loudness alone, if the recogniser cannot run."""
    mix_rms = _rms(_load32(mix)) or 1.0
    level = {name: _rms(_load32(path)) / mix_rms for name, path in stems.items()}
    families = [f for f, need in (("piano", 0.15), ("guitar", 0.25), ("vocals", 0.25), ("bass", 0.02),
                                  ("drums", 0.05), ("other", 0.2)) if level.get(f, 0) >= need]
    return {"families": families or ["other"], "horns": [], "bassKind": "electric"}


def normalise_lineup(lineup: dict) -> dict:
    families = [f for f in FAMILIES if f in lineup.get("families", [])]
    horns = [h for h in HORNS if h in lineup.get("horns", [])]
    if "horns" in families and not horns:
        horns = ["saxophone"]
    if horns and "horns" not in families:
        families.insert(1, "horns")
    return {"families": families or ["other"], "horns": horns, "bassKind": lineup.get("bassKind", "electric")}


# Where each Demucs stem goes when its own instrument is in the line-up.
_HOME = {"piano": "piano", "guitar": "guitar", "vocals": "vocals", "bass": "bass", "drums": "drums", "other": "other"}
# Where it goes otherwise, in order of preference ("route" = second by second, by sound).
_FALLBACK = {
    "piano": ["route"],
    "guitar": ["route"],
    "vocals": ["horns", "route"],
    "other": ["horns", "route"],
    "bass": ["other", "route"],
    "drums": ["other", "route"],
}


def arrange(stems: dict[str, np.ndarray], sr: int, lineup: dict) -> dict[str, np.ndarray]:
    """Regroup stem audio (name -> channels x samples) into the line-up's families."""
    families = lineup["families"]
    out = {f: np.zeros_like(next(iter(stems.values()))) for f in families}
    homeless = {}
    for name, y in stems.items():
        home = _HOME[name]
        if home in families:
            out[home] += y
            continue
        for target in _FALLBACK[name]:
            if target in families:
                out[target] += y
                break
            if target == "route":
                homeless[name] = y
                break
    if homeless:
        _route(homeless, out, sr)
    return out


def _route(homeless: dict[str, np.ndarray], out: dict[str, np.ndarray], sr: int) -> None:
    """Send each second of a homeless stem to the family that plays the same notes then."""
    hop = 2048
    win = int(sr)  # one-second decisions
    targets = list(out)
    chroma = {f: librosa.feature.chroma_stft(y=_mono(out[f]), sr=sr, hop_length=hop) for f in targets}
    energy = {f: librosa.feature.rms(y=_mono(out[f]), hop_length=hop)[0] for f in targets}
    for y in homeless.values():
        c = librosa.feature.chroma_stft(y=_mono(y), sr=sr, hop_length=hop)
        n_frames = c.shape[1]
        per_win = max(1, win // hop)
        weights = np.zeros((len(targets), n_frames))
        for start in range(0, n_frames, per_win):
            sl = slice(start, min(n_frames, start + per_win))
            a = c[:, sl].mean(axis=1)
            scores = []
            for f in targets:
                b = chroma[f][:, sl].mean(axis=1)
                sim = float(a @ b / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-9))
                # A family that is silent here cannot be what the stem is hearing.
                scores.append(sim * min(1.0, energy[f][sl].mean() / (energy[f].mean() + 1e-9)))
            weights[int(np.argmax(scores)), sl] = 1.0
        # Smooth the switches over ~0.3 s so no seams can be heard.
        k = np.hanning(max(3, int(0.3 * sr / hop)))
        weights = np.array([np.convolve(w, k / k.sum(), mode="same") for w in weights])
        weights /= weights.sum(axis=0, keepdims=True) + 1e-9
        samples = np.arange(y.shape[-1]) / hop
        for i, f in enumerate(targets):
            gain = np.interp(samples, np.arange(n_frames), weights[i])
            out[f] += y * gain[None, :]


def _mono(y: np.ndarray) -> np.ndarray:
    return y.mean(axis=0) if y.ndim == 2 else y


# ---------- Telling horns apart ----------

# Usual sounding range, high to low; used to name the pitch clusters.
_HORN_RANK = ["flute", "trumpet", "clarinet", "saxophone", "trombone"]


def split_horns(notes: list, kinds: list[str]) -> dict[str, list]:
    """Divide one horn line between the horns in the line-up, phrase by phrase.

    Demucs cannot separate sax from trumpet, but solos change hands and each horn
    has its own register: trumpet sits well above tenor sax, trombone below. Phrases
    are clustered by their median pitch, and the labels are smoothed over ~12 s,
    because a solo lasts a while. Clusters are named in register order. It is an
    estimate: in unison passages the line goes to whichever horn its register suggests.
    """
    if len(kinds) < 2 or len(notes) < 8:
        return {kinds[0]: notes} if kinds else {}
    phrases, current = [], [notes[0]]
    for n in notes[1:]:
        if n[0] - current[-1][1] > 0.6:
            phrases.append(current)
            current = []
        current.append(n)
    phrases.append(current)

    pitch = np.array([np.median([n[2] for n in ph]) for ph in phrases], dtype=float)
    centre = np.array([(ph[0][0] + ph[-1][1]) / 2 for ph in phrases])
    weight = np.array([len(ph) for ph in phrases], dtype=float)
    k = len(kinds)
    labels = _kmeans_1d(pitch, k)
    # Smooth: each phrase takes the note-weighted majority label of its neighbourhood.
    smoothed = labels.copy()
    for i, c in enumerate(centre):
        near = np.abs(centre - c) <= 6
        votes = np.bincount(labels[near], weights=weight[near], minlength=k)
        smoothed[i] = int(np.argmax(votes))
    order = sorted(kinds, key=lambda h: _HORN_RANK.index(h) if h in _HORN_RANK else 99)
    by_pitch = sorted(range(k), key=lambda c: -pitch[labels == c].mean() if np.any(labels == c) else 0)
    names = {c: order[i] for i, c in enumerate(by_pitch)}
    out = {h: [] for h in kinds}
    for ph, lab in zip(phrases, smoothed):
        out[names[int(lab)]].extend(ph)
    return out


def _kmeans_1d(x: np.ndarray, k: int) -> np.ndarray:
    centres = np.quantile(x, np.linspace(0.1, 0.9, k))
    for _ in range(30):
        labels = np.argmin(np.abs(x[:, None] - centres[None, :]), axis=1)
        new = np.array([x[labels == c].mean() if np.any(labels == c) else centres[c] for c in range(k)])
        if np.allclose(new, centres):
            break
        centres = new
    return labels
