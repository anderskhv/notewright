// Playback engine: the original recording, separated stems and sampled instruments
// playing the transcription, all locked to one clock and one playback speed.
import { Reverb, Smolken, Soundfont, SplendidGrandPiano, Versilian } from "./vendor/smplr.mjs";

// Recorded instruments where a good free one exists; General MIDI soundfonts otherwise.
// VCSL is the Versilian Community Sample Library (CC0).
const VCSL = {
  tenor_sax: "Aerophones/Reed Aerophones/Tenor Saxophone - Vibrato",
  kick: "Membranophones/Struck Membranophones/Bass Drum 1",
  snare: "Membranophones/Struck Membranophones/Snare Drum, Modern 1",
  hihat: "Idiophones/Struck Idiophones/Hi-Hat Cymbal",
  ride: "Idiophones/Struck Idiophones/Suspended Cymbal 1",
};
// Reverb send per voice: a small room puts the parts in one space.
const ROOM = { piano: 0.18, tenor_sax: 0.3, alto_sax: 0.3, trumpet: 0.3, acoustic_bass: 0.08, drums: 0.12 };

// A drum kit from single recorded drums, played by General MIDI drum numbers.
class DrumKit {
  constructor(ctx, jazz) {
    const make = (name) => Versilian(ctx, { instrument: VCSL[name] });
    this.parts = { kick: make("kick"), snare: make("snare"), cymbal: make(jazz ? "ride" : "hihat") };
    this.keys = { kick: 60, snare: 60, cymbal: jazz ? 70 : 42 }; // keys in each sample map (stick ride: 70)
    this.ready = Promise.all(Object.values(this.parts).map((p) => p.ready));
    const parts = Object.values(this.parts);
    this.output = { set volume(v) { for (const p of parts) p.output.volume = v; }, addEffect: (...a) => parts.forEach((p) => p.output.addEffect(...a)) };
  }
  start({ note, time, velocity }) {
    const part = note === 36 ? "kick" : note === 38 ? "snare" : "cymbal";
    this.parts[part].start({ note: this.keys[part], time, velocity });
  }
  stop() { for (const p of Object.values(this.parts)) p.stop(); }
  dispose() { for (const p of Object.values(this.parts)) p.dispose(); }
}

// Sync model.
// - The full recording is the master: the clock follows the element's reported
//   position (only fresh reports count, since some browsers update it coarsely).
// - That report is not what reaches the speakers: the media pipeline adds its own
//   delay, which differs per browser. So the player measures it. A tap behind the
//   recording records its loudness every 10 ms; every couple of seconds that is
//   cross-correlated with the same loudness curve computed on the server from the
//   audio the notes were transcribed from. The lag found is the pipeline delay, and
//   the clock is corrected by it. Sampled notes, cursor and piano roll run on that clock.
// - Stems follow the recording by nudging their speed (pitch is preserved).
// - All media plays through the AudioContext, so it shares one output latency.
const LOOKAHEAD = 0.25; // seconds of song time scheduled ahead
const SLEW = 0.1; // fraction of the clock error corrected per fresh position report
const HARD = 0.25; // past this, jump instead of slewing
const FPS = 100; // loudness frames per second, as on the server
const MAX_LAG = 40; // frames searched either way (±0.4 s)

const TAP_SOURCE = `
class Tap extends AudioWorkletProcessor {
  constructor() { super(); this.sum = 0; this.n = 0; this.t = null; }
  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (ch) {
      if (this.t === null) this.t = currentTime;
      for (let i = 0; i < ch.length; i++) this.sum += ch[i] * ch[i];
      this.n += ch.length;
      if (this.n >= sampleRate / 100) {
        this.port.postMessage([this.t, this.sum / this.n]);
        this.sum = 0; this.n = 0; this.t = null;
      }
    }
    return true;
  }
}
registerProcessor("notewright-tap", Tap);`;

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

// Rise in loudness per frame: onsets, which line up sharply.
function onsets(log) {
  const out = new Float32Array(log.length);
  for (let i = 1; i < log.length; i++) out[i] = Math.max(0, log[i] - log[i - 1]);
  return out;
}

export class Player {
  constructor() {
    this.ctx = new AudioContext({ latencyHint: "interactive" });
    this.rate = 1;
    this.playing = false;
    this.position = 0;
    this.duration = 0;
    this.media = new Map(); // stem id -> { el, gain, node }
    this.synths = new Map(); // track id -> { notes, instrument, volume, pointer, ready }
    // Sampled instruments are slow to load (their samples are decoded on the page), so
    // they load only when a transcription is turned up, and are kept for the next song.
    this._instruments = new Map(); // "voice|track id|jazz" -> Promise<{ instrument, oneShot }>
    this._loading = 0;
    this.loop = { a: null, b: null, on: false };
    // Measured media pipeline delay, seconds of real time. It belongs to this browser
    // and computer, so the last measurement is where the next song starts.
    this.delay = 0;
    try { this.delay = Number(localStorage.getItem("notewright.delay")) || 0; } catch {}
    this.sync = { measured: false, confidence: 0 };
    this.onEnd = () => {};
    this.onLoading = () => {};
    this.onSync = () => {};
    this.onSlow = () => {};
    this._timer = null;
    this._capture = [];
    this._history = [];
    this._tapReady = this.ctx.audioWorklet
      .addModule(URL.createObjectURL(new Blob([TAP_SOURCE], { type: "application/javascript" })))
      .catch((err) => console.warn("Sync measurement unavailable:", err));
  }

  get time() {
    if (!this.playing) return this.position;
    return this.anchorSong + Math.max(0, this.ctx.currentTime - this.anchorCtx) * this.rate;
  }

  // The song position you are hearing right now: what was rendered one output latency ago.
  get heardTime() {
    if (!this.playing) return this.position;
    const latency = (this.ctx.outputLatency || 0) + (this.ctx.baseLatency || 0);
    return Math.max(0, this.time - latency * this.rate);
  }

  _songAt(ctxTime) {
    return this.anchorSong + (ctxTime - this.anchorCtx) * this.rate;
  }

  load(result, base, envelopeUrl) {
    this.pause();
    this.position = 0;
    for (const m of this.media.values()) { m.el.pause(); m.el.removeAttribute("src"); m.el.load(); m.node.disconnect(); }
    for (const s of this.synths.values()) s.instrument?.stop();
    this.media.clear();
    this.synths.clear();
    this.duration = result.duration;
    this.jobId = result.id;
    this._slowToken = null;
    this.loop = { a: null, b: null, on: false };
    // Jazz (horns or a double bass in the line-up) gets a ride cymbal instead of a hi-hat.
    const l = result.lineup;
    this.jazz = !!(l && (l.bassKind === "double" || (l.horns && l.horns.length)));
    this.reference = null;
    this._history = [];
    this.sync = { measured: false, confidence: 0 };
    if (envelopeUrl) {
      fetch(envelopeUrl).then((r) => r.json()).then((env) => {
        this.reference = onsets(Float32Array.from(env.log));
      }).catch((err) => console.warn("No loudness reference:", err));
    }

    this._addMedia("original", base + result.audio, 1);
    this._attachTap();
    const stems = new Map();
    for (const t of result.tracks) if (t.stem) stems.set(t.stemId || t.id, t.stem);
    for (const [id, src] of stems) this._addMedia(id, base + src, 0);

    for (const track of result.tracks) {
      if (!track.notes.length) continue;
      // Velocities come from the recording's level: soft comping would play almost
      // silently. Scale each part so its typical note plays at a healthy velocity.
      const vels = track.notes.map((n) => n[3]).sort((a, b) => a - b);
      const typical = vels[Math.floor(vels.length * 0.75)] || 80;
      const boost = Math.max(1, Math.min(3, 95 / typical));
      const notes = track.notes.map(([start, end, pitch, vel]) => (
        { start, end, pitch, vel: Math.max(30, Math.min(127, Math.round(vel * boost))) }
      ));
      const sound = track.id === "piano" ? withPedal(notes, track.pedals || []) : notes.map((n) => n.end);
      notes.forEach((n, i) => (n.sound = Math.max(sound[i], n.start + 0.05)));
      const synth = { notes, volume: 0, pointer: 0, ready: false, instrument: null, voice: track.voice, key: `${track.voice}|${track.id}|${this.jazz}` };
      this.synths.set(track.id, synth);
    }
    this.onLoading(this._loading);
  }

  async _attachTap() {
    await this._tapReady;
    const master = this.media.get("original");
    if (!master || !this.ctx.audioWorklet) return;
    try {
      const tap = new AudioWorkletNode(this.ctx, "notewright-tap");
      const sink = this.ctx.createGain();
      sink.gain.value = 0;
      master.node.connect(tap).connect(sink).connect(this.ctx.destination);
      tap.port.onmessage = (e) => this._onTap(e.data);
      master.tap = tap;
    } catch (err) {
      console.warn("Sync measurement unavailable:", err);
    }
  }

  _ensureInstrument(synth) {
    if (synth.instrument || synth.loading) return;
    synth.loading = true;
    if (!this._instruments.has(synth.key)) this._instruments.set(synth.key, this._makeInstrument(synth.voice, this.jazz));
    this.onLoading(++this._loading);
    this._instruments.get(synth.key).then(({ instrument, oneShot }) => {
      synth.instrument = instrument;
      synth.oneShot = oneShot;
      synth.ready = true;
      this._applyVolume(synth);
    }).catch((err) => {
      console.error("Could not load sounds for", synth.key, err);
      this._instruments.delete(synth.key);
    }).finally(() => {
      synth.loading = false;
      this.onLoading(--this._loading);
    });
  }

  async _makeInstrument(voice, jazz) {
    const ctx = this.ctx;
    const make = () => {
      if (voice === "piano") return SplendidGrandPiano(ctx);
      if (voice === "drums") return new DrumKit(ctx, jazz);
      if (voice === "acoustic_bass") return Smolken(ctx, { instrument: "Pizzicato" });
      if (VCSL[voice]) return Versilian(ctx, { instrument: VCSL[voice] });
      return Soundfont(ctx, { instrument: voice });
    };
    let instrument = make();
    try {
      await instrument.ready;
    } catch (err) {
      // A recorded instrument that fails to load falls back to its soundfont.
      if (voice === "piano" || voice === "drums") throw err;
      console.warn("Falling back to the soundfont for", voice, err);
      instrument = Soundfont(ctx, { instrument: voice });
      await instrument.ready;
    }
    try {
      this.reverb ||= Reverb(ctx);
      instrument.output.addEffect("room", this.reverb, ROOM[voice] ?? 0.2);
    } catch (err) {
      console.warn("No reverb:", err);
    }
    return { instrument, oneShot: voice === "drums" };
  }

  _addMedia(id, src, volume) {
    const el = new Audio();
    el.src = src;
    el.preload = "auto";
    el.preservesPitch = true;
    const gain = this.ctx.createGain();
    gain.gain.value = volume;
    const node = this.ctx.createMediaElementSource(el);
    node.connect(gain).connect(this.ctx.destination);
    // scale: song seconds per second of this file (1, or the speed of a pre-slowed copy)
    this.media.set(id, { el, gain, node, scale: 1, src, original: src });
  }

  _applyVolume(synth) {
    if (synth.instrument) synth.instrument.output.volume = Math.round(synth.volume * 115);
  }

  setMediaVolume(id, v) {
    const m = this.media.get(id);
    if (m) m.gain.gain.value = Math.max(0, Math.min(1, v));
  }

  setSynthVolume(id, v) {
    const s = this.synths.get(id);
    if (!s) return;
    s.volume = v;
    if (v > 0) this._ensureInstrument(s);
    this._applyVolume(s);
    if (v === 0) s.instrument?.stop();
  }

  async play() {
    if (this.playing || this._starting) return;
    if (this.position >= this.duration - 0.05) this.position = 0;
    const token = (this._starting = {});
    await this.ctx.resume();
    const els = [...this.media.values()].map((m) => m.el);
    for (const m of this.media.values()) {
      m.el.playbackRate = this._baseRate(m);
      m.el.currentTime = this.position / m.scale;
    }
    // Start the clock only once the recording is really playing.
    await Promise.all(els.map((el) => el.play().catch(() => {})));
    if (this._starting !== token) return; // paused or seeked meanwhile
    this._starting = null;
    const master = this.media.get("original");
    this.anchorSong = this._songPos(master) - this.delay * this.rate;
    this.anchorCtx = this.ctx.currentTime;
    this._lastReport = master.el.currentTime;
    this._capture = [];
    for (const s of this.synths.values()) s.pointer = lowerBound(s.notes, this.anchorSong);
    this.playing = true;
    this._timer = setInterval(() => this._tick(), 25);
    this._tick();
  }

  pause() {
    if (this._starting) {
      this._starting = null;
      for (const m of this.media.values()) m.el.pause();
      return;
    }
    if (!this.playing) return;
    this.position = Math.max(0, Math.min(this.time, this.duration));
    this.playing = false;
    clearInterval(this._timer);
    for (const m of this.media.values()) m.el.pause();
    for (const s of this.synths.values()) s.instrument?.stop();
  }

  toggle() { return this.playing ? this.pause() : this.play(); }

  seek(t) {
    t = Math.max(0, Math.min(this.duration, t));
    const wasPlaying = this.playing;
    const starting = !!this._starting;
    this.pause();
    this.position = t;
    for (const m of this.media.values()) m.el.currentTime = t / m.scale;
    if (wasPlaying || starting) this.play();
  }

  setRate(rate) {
    // Still starting counts as playing: dragging the slider changes speed again before
    // the previous change has finished restarting playback.
    const wasPlaying = this.playing || !!this._starting;
    this.pause();
    this.rate = rate;
    for (const m of this.media.values()) m.el.playbackRate = this._baseRate(m);
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
    this._follow();
    const horizon = this.time + LOOKAHEAD * this.rate;
    const now = this.ctx.currentTime;
    for (const s of this.synths.values()) {
      const { notes } = s;
      while (s.pointer < notes.length && notes[s.pointer].start < horizon) {
        const n = notes[s.pointer++];
        if (!s.ready || s.volume === 0 || n.start < this.time - 0.05) continue;
        const when = this.anchorCtx + (n.start - this.anchorSong) / this.rate;
        s.instrument.start({
          note: n.pitch,
          velocity: n.vel,
          time: Math.max(when, now),
          duration: s.oneShot ? undefined : (n.sound - n.start) / this.rate,
        });
      }
    }
  }

  // Keep the clock on the recording, and the stems on the recording.
  _follow() {
    const entry = this.media.get("original");
    const master = entry.el;
    if (master.paused || master.seeking) return;
    const m = this._songPos(entry);
    if (master.currentTime !== this._lastReport) {
      // A fresh report: the recording is at m now, minus what its pipeline holds back.
      this._lastReport = master.currentTime;
      const err = m - this.delay * this.rate - this.time;
      if (Math.abs(err) > HARD) this._jump(err);
      else this.anchorSong += err * SLEW;
    }
    for (const [id, part] of this.media) {
      const { el } = part;
      if (id === "original" || el.paused || el.seeking) continue;
      const diff = this._songPos(part) - m;
      if (Math.abs(diff) > HARD) {
        el.currentTime = m / part.scale;
        el.playbackRate = this._baseRate(part);
      } else {
        // Pull the stem in by playing it up to 5% faster or slower; inaudible with pitch kept.
        const nudge = Math.abs(diff) > 0.01 ? Math.max(-0.05, Math.min(0.05, -diff * 2)) : 0;
        el.playbackRate = this._baseRate(part) * (1 + nudge);
      }
    }
  }

  _jump(err) {
    this.anchorSong += err;
    for (const s of this.synths.values()) {
      s.instrument?.stop();
      s.pointer = lowerBound(s.notes, this.time);
    }
  }

  _songPos(part) {
    return part.el.currentTime * part.scale;
  }

  _baseRate(part) {
    return this.rate / part.scale; // a copy pre-slowed to exactly this speed plays at 1
  }

  // ---------- High-quality slow motion ----------

  // Below 95% speed, ask the server for copies slowed with Signalsmith Stretch and swap
  // them in (the recording first, then each part) at the same position. Until they are
  // ready, and at full speed, the browser's own time-stretch plays the normal files.
  async prepareSlow() {
    const token = (this._slowToken = {});
    const percent = Math.round(this.rate * 100);
    const parts = [...this.media.keys()];
    if (percent >= 95) {
      for (const id of parts) await this._swap(id, this.media.get(id).original, 1);
      this.onSlow(null);
      return;
    }
    this.onSlow("preparing");
    const one = async (id) => {
      const url = `/api/jobs/${this.jobId}/slow/${id}/${percent}`;
      try {
        const res = await fetch(url + "?prepare=1");
        if (!res.ok) throw new Error(res.status);
      } catch (err) {
        console.warn("No high-quality slow copy of", id, err);
        return;
      }
      if (this._slowToken === token) await this._swap(id, url, percent / 100);
    };
    // The recording first (it is what you hear in most mixes), then the parts two at a time.
    await one("original");
    const rest = parts.filter((id) => id !== "original");
    const lanes = [0, 1].map(async (lane) => {
      for (let i = lane; i < rest.length; i += 2) if (this._slowToken === token) await one(rest[i]);
    });
    await Promise.all(lanes);
    if (this._slowToken === token) this.onSlow("ready");
  }

  async _swap(id, src, scale) {
    const part = this.media.get(id);
    if (!part || part.src === src) return;
    const el = new Audio();
    el.preload = "auto";
    el.preservesPitch = true;
    el.src = src;
    const ok = await new Promise((resolve) => {
      el.oncanplaythrough = () => resolve(true);
      el.onerror = () => resolve(false);
    });
    if (!ok || this.media.get(id) !== part) return;
    const node = this.ctx.createMediaElementSource(el);
    node.connect(part.gain);
    if (part.tap) node.connect(part.tap);
    const old = part.el, oldNode = part.node;
    Object.assign(part, { el, node, scale, src });
    el.playbackRate = this._baseRate(part);
    el.currentTime = this.time / scale;
    if (this.playing) await el.play().catch(() => {});
    old.pause();
    oldNode.disconnect();
    old.removeAttribute("src");
    old.load();
  }

  // ---------- Measuring the real delay ----------

  _onTap([ctxTime, power]) {
    if (!this.playing) return;
    this._capture.push([this._songAt(ctxTime), Math.log10(power + 1e-10)]);
    const first = this._capture[0][0];
    const last = this._capture[this._capture.length - 1][0];
    if (last - first > 8 * this.rate) this._capture.shift();
    if (last - first >= 4 * this.rate && (!this._lastMeasure || ctxTime - this._lastMeasure > 2)) {
      this._lastMeasure = ctxTime;
      this._measure();
    }
  }

  _measure() {
    const ref = this.reference;
    const cap = this._capture;
    if (!ref || cap.length < 100) return;
    // Resample the capture onto the song's 10 ms grid.
    const start = Math.ceil(cap[0][0] * FPS) + MAX_LAG + 1;
    const end = Math.floor(cap[cap.length - 1][0] * FPS) - 1;
    if (end - start < 200 || end + MAX_LAG >= ref.length) return;
    const log = new Float32Array(end - start + 2);
    let j = 0;
    for (let f = start - 1; f <= end; f++) {
      const t = f / FPS;
      while (j < cap.length - 2 && cap[j + 1][0] < t) j++;
      const [t0, v0] = cap[j], [t1, v1] = cap[j + 1];
      log[f - start + 1] = v0 + (v1 - v0) * Math.min(1, Math.max(0, (t - t0) / (t1 - t0 || 1)));
    }
    const got = onsets(log).subarray(1);
    // Captured frame f holds reference frame f - lag, if the audio runs lag frames behind.
    const scores = [];
    for (let lag = -MAX_LAG; lag <= MAX_LAG; lag++) {
      let dot = 0, a2 = 0, b2 = 0;
      for (let i = 0; i < got.length; i++) {
        const r = ref[start + i - lag] || 0;
        dot += got[i] * r; a2 += got[i] * got[i]; b2 += r * r;
      }
      scores.push(dot / (Math.sqrt(a2 * b2) || 1));
    }
    let best = 0;
    for (let i = 1; i < scores.length; i++) if (scores[i] > scores[best]) best = i;
    let runnerUp = 0;
    for (let i = 0; i < scores.length; i++) if (Math.abs(i - best) > 3) runnerUp = Math.max(runnerUp, scores[i]);
    const confidence = scores[best];
    // Accept only a clear, distinct peak (quiet or repetitive passages are skipped).
    if (confidence < 0.35 || confidence - runnerUp < 0.08) return;
    const lagSeconds = (best - MAX_LAG) / FPS / this.rate;
    this._history.push(lagSeconds);
    if (this._history.length > 3) this._history.shift();
    const sorted = [...this._history].sort((a, b) => a - b);
    const median = sorted[sorted.length >> 1];
    if (this._history.length >= 2 && Math.abs(median) > 0.004) {
      const step = median * 0.8;
      this.delay += step;
      this._history = this._history.map((v) => v - step);
      // Small corrections shift the clock quietly; big ones restart the sampled notes.
      if (Math.abs(step) > 0.03) this._jump(-step * this.rate);
      else this.anchorSong -= step * this.rate;
      this._capture = [];
    }
    try { localStorage.setItem("notewright.delay", String(this.delay)); } catch {}
    this.sync = { measured: true, confidence, delay: this.delay };
    this.onSync(this.sync);
  }
}
