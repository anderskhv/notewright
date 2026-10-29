// Falling-notes piano roll: notes drop onto a keyboard and light the keys they play.

const BLACK = new Set([1, 3, 6, 8, 10]);
const isBlack = (p) => BLACK.has(p % 12);

export class PianoRoll {
  constructor(canvas) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d");
    this.tracks = [];
    this.bars = [];
    this.pps = 140; // pixels per second
    this.lo = 21;
    this.hi = 108;
    new ResizeObserver(() => this._resize()).observe(canvas);
  }

  setData(tracks, beats, beatsPerBar) {
    this.tracks = tracks.filter((t) => t.notes.length && t.id !== "drums");
    let lo = 127, hi = 0;
    for (const t of this.tracks) {
      for (const n of t.notes) { lo = Math.min(lo, n.pitch); hi = Math.max(hi, n.pitch); }
    }
    if (lo > hi) { lo = 48; hi = 84; }
    // Whole octaves, at least four of them, within the 88 keys.
    this.lo = Math.max(21, Math.floor((lo - 2) / 12) * 12);
    this.hi = Math.min(108, Math.max(this.lo + 47, Math.ceil((hi + 2) / 12) * 12 - 1));
    this.bars = beats.filter((_, i) => i % beatsPerBar === 0);
    this._layout();
  }

  _resize() {
    const dpr = window.devicePixelRatio || 1;
    const { clientWidth: w, clientHeight: h } = this.canvas;
    this.canvas.width = Math.max(1, w * dpr);
    this.canvas.height = Math.max(1, h * dpr);
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    this.w = w;
    this.h = h;
    this._layout();
    if (this._last !== undefined) this.draw(this._last);
  }

  _layout() {
    if (!this.w) return;
    const whites = [];
    for (let p = this.lo; p <= this.hi; p++) if (!isBlack(p)) whites.push(p);
    const ww = this.w / whites.length;
    this.keyX = new Map();
    whites.forEach((p, i) => this.keyX.set(p, { x: i * ww, w: ww }));
    for (let p = this.lo; p <= this.hi; p++) {
      if (isBlack(p) && this.keyX.has(p - 1)) {
        const left = this.keyX.get(p - 1);
        this.keyX.set(p, { x: left.x + ww * 0.68, w: ww * 0.64 });
      }
    }
    this.kbH = Math.min(120, Math.max(60, this.h * 0.2));
  }

  draw(t) {
    this._last = t;
    const { ctx, w, h } = this;
    if (!w || !this.keyX) return;
    const css = getComputedStyle(this.canvas);
    const fall = h - this.kbH;
    const windowEnd = t + fall / this.pps;
    ctx.clearRect(0, 0, w, h);
    ctx.fillStyle = css.getPropertyValue("--roll-bg");
    ctx.fillRect(0, 0, w, fall);

    // Lanes behind the black keys, then bar lines.
    ctx.fillStyle = css.getPropertyValue("--roll-lane");
    for (const [p, k] of this.keyX) if (isBlack(p)) ctx.fillRect(k.x, 0, k.w, fall);
    ctx.strokeStyle = css.getPropertyValue("--roll-bar");
    ctx.fillStyle = css.getPropertyValue("--roll-text");
    ctx.font = "11px system-ui, sans-serif";
    ctx.lineWidth = 1;
    this.bars.forEach((bt, i) => {
      if (bt < t || bt > windowEnd) return;
      const y = Math.round(fall - (bt - t) * this.pps) + 0.5;
      ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(w, y); ctx.stroke();
      ctx.fillText(String(i + 1), 4, y - 3);
    });

    const active = new Map();
    for (const track of this.tracks) {
      if (!track.visible) continue;
      ctx.fillStyle = track.color;
      for (const n of track.notes) {
        if (n.start > windowEnd) break;
        if (n.end < t) continue;
        const k = this.keyX.get(n.pitch);
        if (!k) continue;
        if (n.start <= t) active.set(n.pitch, track.color);
        const y1 = fall - (n.start - t) * this.pps;
        const y2 = fall - (n.end - t) * this.pps;
        const top = Math.max(0, y2);
        const height = Math.max(3, Math.min(fall, y1) - top);
        roundRect(ctx, k.x + 1, top, k.w - 2, height, Math.min(4, k.w / 3));
      }
    }

    // Keyboard.
    const ky = fall;
    ctx.fillStyle = css.getPropertyValue("--roll-edge");
    ctx.fillRect(0, ky - 3, w, 3);
    for (const [p, k] of this.keyX) {
      if (isBlack(p)) continue;
      ctx.fillStyle = active.get(p) || "#fbfbf8";
      ctx.fillRect(k.x, ky, k.w - 1, this.kbH);
      if (p % 12 === 0 && k.w > 14) {
        ctx.fillStyle = active.has(p) ? "#fff" : "#8a8a85";
        ctx.font = "10px system-ui, sans-serif";
        ctx.textAlign = "center";
        ctx.fillText(`C${p / 12 - 1}`, k.x + k.w / 2, ky + this.kbH - 6);
        ctx.textAlign = "start";
      }
    }
    for (const [p, k] of this.keyX) {
      if (!isBlack(p)) continue;
      ctx.fillStyle = active.get(p) || "#1d1d1f";
      ctx.fillRect(k.x, ky, k.w, this.kbH * 0.62);
    }
  }

  // Pixels of scroll to seconds, for wheel scrubbing.
  pxToSeconds(px) {
    return px / this.pps;
  }
}

function roundRect(ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.fill();
}
