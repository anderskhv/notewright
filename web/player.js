// Playback engine: the original recording, separated stems and sampled instruments
// playing the transcription, all locked to one clock and one playback speed.
import { DrumMachine, Soundfont, SplendidGrandPiano } from "./vendor/smplr.mjs";

const LOOKAHEAD = 0.25; // seconds of song time scheduled ahead
const DRIFT = 0.12; // re-sync a media element once it is this far off

function lowerBound(notes, t) {
  let lo = 0, hi = notes.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (notes[mid].start < t) lo = mid + 1; else hi = mid;
  }
  return lo;
}

// Sustain pedal: a note released while the pedal is down keeps sounding until the
// pedal lifts, or until the same key is struck again.
function withPedal(notes, pedals) {
  const nextSame = new Map();
  const sound = new Array(notes.length);
  for (let i = notes.length - 1; i >= 0; i--) {
    const n = notes[i];
    let end = n.end;
    for (const [ps, pe] of pedals) {
      if (ps <= n.end && n.end < pe) { end = pe; break; }
    }
    const next = nextSame.get(n.pitch);
    sound[i] = next !== undefined ? Math.min(end, next) : end;
    nextSame.set(n.pitch, n.start);
  }
  return sound;
}

function drumName(groups, gm) {
  const want = { 36: ["kick", "bd", "bass"], 38: ["snare", "sd", "clap"], 42: ["hihat", "hh", "closed", "hat"] }[gm] || ["kick"];
  for (const w of want) {
    const hit = groups.find((g) => g.toLowerCase().includes(w));
    if (hit) return hit;
  }
  return groups[0];
}

export class Player {
  constructor() {
    this.ctx = new AudioContext({ latencyHint: "interactive" });
    this.rate = 1;
    this.playing = false;
    this.position = 0;
    this.duration = 0;
    this.media = new Map(); // id -> HTMLAudioElement
    this.synths = new Map(); // id -> { notes, instrument, volume, pointer, ready }
    this.loop = { a: null, b: null, on: false };
    this.onEnd = () => {};
    this.onLoading = () => {};
    this._timer = null;
  }

  get time() {
    if (!this.playing) return this.position;
    return this.anchorSong + Math.max(0, this.ctx.currentTime - this.anchorCtx) * this.rate;
  }

  load(result, base) {
    this.pause();
    this.position = 0;
    for (const el of this.media.values()) { el.pause(); el.removeAttribute("src"); el.load(); }
    for (const s of this.synths.values()) s.instrument?.dispose();
    this.media.clear();
    this.synths.clear();
    this.duration = result.duration;
    this.loop = { a: null, b: null, on: false };

    this._addMedia("original", base + result.audio, 1);
    let pending = 0;
    for (const track of result.tracks) {
      if (track.stem) this._addMedia(track.id, base + track.stem, 0);
      if (!track.notes.length) continue;
      const notes = track.notes.map(([start, end, pitch, vel]) => ({ start, end, pitch, vel }));
      const sound = track.id === "piano" ? withPedal(notes, track.pedals || []) : notes.map((n) => n.end);
      notes.forEach((n, i) => (n.sound = Math.max(sound[i], n.start + 0.05)));
      const synth = { notes, volume: 0, pointer: 0, ready: false, instrument: null, voice: track.voice };
      this.synths.set(track.id, synth);
      pending++;
      this._makeInstrument(synth).then(() => {
        synth.ready = true;
        this.onLoading(--pending);
      }).catch((err) => {
        console.error("Could not load sounds for", track.id, err);
        this.onLoading(--pending);
      });
    }
    this.onLoading(pending);
  }

  async _makeInstrument(synth) {
    const ctx = this.ctx;
    if (synth.voice === "piano") synth.instrument = SplendidGrandPiano(ctx);
    else if (synth.voice === "drums") synth.instrument = DrumMachine(ctx, { instrument: "TR-808" });
    else synth.instrument = Soundfont(ctx, { instrument: synth.voice });
    await synth.instrument.ready;
    if (synth.voice === "drums") {
      const groups = synth.instrument.getGroupNames();
      synth.drumMap = { 36: drumName(groups, 36), 38: drumName(groups, 38), 42: drumName(groups, 42) };
    }
    this._applyVolume(synth);
  }

  _addMedia(id, src, volume) {
    const el = new Audio();
    el.src = src;
    el.preload = "auto";
    el.preservesPitch = true;
    el.volume = volume;
    this.media.set(id, el);
  }

  _applyVolume(synth) {
    if (synth.instrument) synth.instrument.output.volume = Math.round(synth.volume * 115);
  }

  setMediaVolume(id, v) {
    const el = this.media.get(id);
    if (el) el.volume = Math.max(0, Math.min(1, v));
  }

  setSynthVolume(id, v) {
    const s = this.synths.get(id);
    if (!s) return;
    s.volume = v;
    this._applyVolume(s);
    if (v === 0) s.instrument?.stop();
  }

  async play() {
    if (this.playing) return;
    if (this.position >= this.duration - 0.05) this.position = 0;
    await this.ctx.resume();
    this.anchorSong = this.position;
    this.anchorCtx = this.ctx.currentTime + 0.06;
    for (const s of this.synths.values()) s.pointer = lowerBound(s.notes, this.position);
    for (const el of this.media.values()) {
      el.playbackRate = this.rate;
      el.currentTime = this.position;
      el.play().catch(() => {});
    }
    this.playing = true;
    this._timer = setInterval(() => this._tick(), 25);
    this._tick();
  }

  pause() {
    if (!this.playing) return;
    this.position = Math.min(this.time, this.duration);
    this.playing = false;
    clearInterval(this._timer);
    for (const el of this.media.values()) el.pause();
    for (const s of this.synths.values()) s.instrument?.stop();
  }

  toggle() { return this.playing ? this.pause() : this.play(); }

  seek(t) {
    t = Math.max(0, Math.min(this.duration, t));
    const wasPlaying = this.playing;
    this.pause();
    this.position = t;
    for (const el of this.media.values()) el.currentTime = t;
    if (wasPlaying) this.play();
  }

  setRate(rate) {
    const wasPlaying = this.playing;
    this.pause();
    this.rate = rate;
    for (const el of this.media.values()) el.playbackRate = rate;
    if (wasPlaying) this.play();
  }

  _tick() {
    const t = this.time;
    const { loop } = this;
    if (loop.on && loop.a !== null && loop.b !== null && t >= loop.b) {
      this.seek(loop.a);
      return;
    }
    if (t >= this.duration) {
      this.pause();
      this.position = this.duration;
      this.onEnd();
      return;
    }
    for (const el of this.media.values()) {
      if (!el.paused && Math.abs(el.currentTime - t) > DRIFT) el.currentTime = t;
    }
    const horizon = t + LOOKAHEAD * this.rate;
    const now = this.ctx.currentTime;
    for (const s of this.synths.values()) {
      const { notes } = s;
      while (s.pointer < notes.length && notes[s.pointer].start < horizon) {
        const n = notes[s.pointer++];
        if (!s.ready || s.volume === 0 || n.start < t - 0.05) continue;
        const when = this.anchorCtx + (n.start - this.anchorSong) / this.rate;
        const note = s.drumMap ? s.drumMap[n.pitch] || s.drumMap[36] : n.pitch;
        s.instrument.start({
          note,
          velocity: n.vel,
          time: Math.max(when, now),
          duration: s.drumMap ? undefined : (n.sound - n.start) / this.rate,
        });
      }
    }
  }
}
