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

  setData(tracks, beats, beatsPerBar, chords = []) {
    this.chords = chords;
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
    const nowMs = performance.now();
    const dt = Math.min(0.05, (nowMs - (this._frameMs || nowMs)) / 1000);
    this._frameMs = nowMs;
    const moving = t !== this._prevT;
    this._prevT = t;

    ctx.clearRect(0, 0, w, h);
    ctx.fillStyle = css.getPropertyValue("--roll-bg");
    ctx.fillRect(0, 0, w, fall);

    // Guide lines at every C and F, as on a real keyboard's landmarks; then bar lines.
    ctx.lineWidth = 1;
    ctx.strokeStyle = css.getPropertyValue("--roll-guide");
    for (const [p, k] of this.keyX) {
      if (p % 12 !== 0 && p % 12 !== 5) continue;
      const x = Math.round(k.x) + 0.5;
      ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, fall); ctx.stroke();
    }
    ctx.strokeStyle = css.getPropertyValue("--roll-bar");
    ctx.fillStyle = css.getPropertyValue("--roll-text");
    ctx.font = "11px system-ui, sans-serif";
    this.bars.forEach((bt, i) => {
      if (bt < t || bt > windowEnd) return;
      const y = Math.round(fall - (bt - t) * this.pps) + 0.5;
      ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(w, y); ctx.stroke();
      ctx.fillText(String(i + 1), 4, y - 3);
    });

    // Notes: a soft gradient each; the ones sounding glow.
    const active = new Map();
    const glowing = [];
    for (const track of this.tracks) {
      if (!track.visible) continue;
      const [r, g, b] = rgb(track.color);
      for (const n of track.notes) {
        if (n.start > windowEnd) break;
        if (n.end < t) continue;
        const k = this.keyX.get(n.pitch);
        if (!k) continue;
        const y1 = fall - (n.start - t) * this.pps;
        const top = Math.max(0, fall - (n.end - t) * this.pps);
        const height = Math.max(4, Math.min(fall, y1) - top);
        const x = k.x + 1.5, width = k.w - 3, radius = Math.min(7, width / 2.2);
        const grad = ctx.createLinearGradient(0, top, 0, top + height);
        grad.addColorStop(0, `rgba(${r},${g},${b},0.75)`);
        grad.addColorStop(1, `rgb(${Math.min(255, r + 40)},${Math.min(255, g + 40)},${Math.min(255, b + 40)})`);
        ctx.fillStyle = grad;
        roundRect(ctx, x, top, width, height, radius);
        if (n.start <= t) {
          active.set(n.pitch, [r, g, b]);
          glowing.push([x, top, width, height, radius, r, g, b, k]);
        }
      }
    }
    ctx.save();
    for (const [x, top, width, height, radius, r, g, b] of glowing) {
      ctx.shadowColor = `rgba(${r},${g},${b},0.9)`;
      ctx.shadowBlur = 18;
      ctx.fillStyle = `rgb(${Math.min(255, r + 70)},${Math.min(255, g + 70)},${Math.min(255, b + 70)})`;
      roundRect(ctx, x, top, width, height, radius);
    }
    ctx.restore();

    // Light rising from pressed keys, and sparks where the notes land.
    ctx.save();
    ctx.globalCompositeOperation = "lighter";
    for (const [, , , , , r, g, b, k] of glowing) {
      const beam = ctx.createLinearGradient(0, fall, 0, fall - 110);
      beam.addColorStop(0, `rgba(${r},${g},${b},0.35)`);
      beam.addColorStop(1, `rgba(${r},${g},${b},0)`);
      ctx.fillStyle = beam;
      ctx.fillRect(k.x - k.w * 0.3, fall - 110, k.w * 1.6, 110);
      if (moving) this._emit(k, fall, r, g, b, dt);
    }
    this._sparks(dt);
    ctx.restore();

    // Chords: upcoming ones fall along the right edge, the current one sits top left.
    let now = null;
    ctx.textAlign = "right";
    ctx.font = "600 15px system-ui, sans-serif";
    for (const [start, end, name] of this.chords || []) {
      if (start <= t && t < end) now = name;
      if (start <= t || start > windowEnd) continue;
      const y = Math.round(fall - (start - t) * this.pps) + 0.5;
      ctx.strokeStyle = css.getPropertyValue("--roll-chord-line");
      ctx.beginPath(); ctx.moveTo(w - 70, y); ctx.lineTo(w, y); ctx.stroke();
      ctx.fillStyle = css.getPropertyValue("--roll-chord");
      ctx.fillText(name, w - 8, y - 5);
    }
    ctx.textAlign = "start";
    if (now) {
      ctx.font = "600 26px system-ui, sans-serif";
      const pad = 10, width = ctx.measureText(now).width + pad * 2;
      ctx.fillStyle = css.getPropertyValue("--roll-chord-bg");
      roundRect(ctx, 12, 12, width, 42, 8);
      ctx.fillStyle = css.getPropertyValue("--roll-chord-now");
      ctx.fillText(now, 12 + pad, 43);
    }

    // Keyboard: a lit rail, shaded keys, pressed keys in the part's colour.
    const ky = fall;
    const rail = ctx.createLinearGradient(0, ky - 4, 0, ky + 2);
    rail.addColorStop(0, css.getPropertyValue("--roll-edge"));
    rail.addColorStop(1, "#1a1a20");
    ctx.fillStyle = rail;
    ctx.fillRect(0, ky - 4, w, 6);
    const whiteGrad = ctx.createLinearGradient(0, ky, 0, ky + this.kbH);
    whiteGrad.addColorStop(0, "#d9d9de");
    whiteGrad.addColorStop(0.12, "#f7f7f5");
    whiteGrad.addColorStop(1, "#e9e9e6");
    for (const [p, k] of this.keyX) {
      if (isBlack(p)) continue;
      const lit = active.get(p);
      if (lit) {
        const g2 = ctx.createLinearGradient(0, ky, 0, ky + this.kbH);
        g2.addColorStop(0, `rgb(${lit[0]},${lit[1]},${lit[2]})`);
        g2.addColorStop(1, `rgba(${lit[0]},${lit[1]},${lit[2]},0.55)`);
        ctx.fillStyle = g2;
      } else ctx.fillStyle = whiteGrad;
      ctx.fillRect(k.x + 0.5, ky + 2, k.w - 1.5, this.kbH - 2);
      if (p % 12 === 0 && k.w > 14) {
        ctx.fillStyle = lit ? "#fff" : "#8a8a85";
        ctx.font = "10px system-ui, sans-serif";
        ctx.textAlign = "center";
        ctx.fillText(`C${p / 12 - 1}`, k.x + k.w / 2, ky + this.kbH - 6);
        ctx.textAlign = "start";
      }
    }
    const blackGrad = ctx.createLinearGradient(0, ky, 0, ky + this.kbH * 0.62);
    blackGrad.addColorStop(0, "#050506");
    blackGrad.addColorStop(0.85, "#2b2b30");
    blackGrad.addColorStop(1, "#111114");
    for (const [p, k] of this.keyX) {
      if (!isBlack(p)) continue;
      const lit = active.get(p);
      ctx.fillStyle = lit ? `rgb(${lit[0]},${lit[1]},${lit[2]})` : blackGrad;
      ctx.fillRect(k.x, ky + 2, k.w, this.kbH * 0.62);
    }
  }

  _emit(k, y, r, g, b, dt) {
    const sparks = (this.particles ||= []);
    const count = Math.random() < 30 * dt ? 2 : 0; // about 60 sparks a second per sounding key
    for (let i = 0; i < count && sparks.length < 500; i++) {
      sparks.push({
        x: k.x + Math.random() * k.w, y: y - 2,
        vx: (Math.random() - 0.5) * 40, vy: -40 - Math.random() * 90,
        life: 0, max: 0.5 + Math.random() * 0.7, r, g, b,
      });
    }
  }

  _sparks(dt) {
    const ctx = this.ctx;
    const sparks = this.particles || [];
    let alive = 0;
    for (const s of sparks) {
      s.life += dt;
      if (s.life >= s.max) continue;
      s.x += s.vx * dt; s.y += s.vy * dt; s.vy += 30 * dt;
      const a = 1 - s.life / s.max;
      ctx.fillStyle = `rgba(${Math.min(255, s.r + 120)},${Math.min(255, s.g + 120)},${Math.min(255, s.b + 120)},${a})`;
      ctx.fillRect(s.x, s.y, 1.6, 1.6);
      sparks[alive++] = s;
    }
    sparks.length = alive;
  }

  // Pixels of scroll to seconds, for wheel scrubbing.
  pxToSeconds(px) {
    return px / this.pps;
  }
}

function rgb(hex) {
  const v = parseInt(hex.slice(1), 16);
  return [(v >> 16) & 255, (v >> 8) & 255, v & 255];
}

function roundRect(ctx, x, y, w, h, r) {
  r = Math.max(0, Math.min(r, w / 2, h / 2)); // a corner larger than half the box draws a comma
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.fill();
}
