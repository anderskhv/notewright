// Falling-notes piano roll: notes drop onto a keyboard and light the keys they play.
//
// The look follows Rousseau's piano videos: all 88 keys of a real-looking keyboard in
// dim light, crisp notes with a soft glow, and light, smoke and sparks where notes land.
// It stays cheap: the keyboard is drawn once per resize and copied each frame; a note's
// glow is two translucent rounded rectangles behind it; smoke is one pre-drawn sprite.

const BLACK = new Set([1, 3, 6, 8, 10]);
const isBlack = (p) => BLACK.has(p % 12);
const LO = 21, HI = 108; // A0..C8
// Black keys sit off-centre in their groups, as on a real keyboard.
const OFFSET = { 1: 0.58, 3: 0.74, 6: 0.56, 8: 0.66, 10: 0.78 };

export class PianoRoll {
  constructor(canvas) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d");
    this.tracks = [];
    this.bars = [];
    this.chords = [];
    this.pps = 140; // pixels per second
    this.particles = [];
    this.keyboard = document.createElement("canvas"); // the keyboard at rest
    this.smoke = sprite();
    this._tints = new Map();
    new ResizeObserver(() => this._resize()).observe(canvas);
  }

  setData(tracks, beats, beatsPerBar, chords = []) {
    this.chords = chords;
    this.tracks = tracks.filter((t) => t.notes.length && t.id !== "drums");
    this.bars = beats.filter((_, i) => i % beatsPerBar === 0);
    this.particles = [];
  }

  _resize() {
    const dpr = window.devicePixelRatio || 1;
    const { clientWidth: w, clientHeight: h } = this.canvas;
    this.dpr = dpr;
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
    for (let p = LO; p <= HI; p++) if (!isBlack(p)) whites.push(p);
    const ww = this.w / whites.length;
    this.ww = ww;
    this.whites = whites;
    this.keyX = new Map();
    whites.forEach((p, i) => this.keyX.set(p, { x: i * ww, w: ww }));
    for (let p = LO; p <= HI; p++) {
      if (isBlack(p)) this.keyX.set(p, { x: this.keyX.get(p - 1).x + ww * OFFSET[p % 12], w: ww * 0.56 });
    }
    // A real white key is about six times as long as it is wide.
    this.kbH = Math.round(Math.max(48, Math.min(ww * 6.2, this.h * 0.24)));
    this._drawKeyboard();
  }

  _drawKeyboard() {
    const { w, kbH, ww } = this;
    const dpr = this.dpr || 1;
    const kb = this.keyboard;
    kb.width = Math.max(1, w * dpr);
    kb.height = Math.max(1, kbH * dpr);
    const c = kb.getContext("2d");
    c.setTransform(dpr, 0, 0, dpr, 0, 0);
    c.fillStyle = "#0b0b10";
    c.fillRect(0, 0, w, kbH);
    const lip = Math.max(3, Math.round(kbH * 0.06));
    const ambient = c.createLinearGradient(0, 5, 0, kbH);
    ambient.addColorStop(0, "#5d6175");
    ambient.addColorStop(0.08, "#aeb3c6");
    ambient.addColorStop(0.6, "#c9cddb");
    ambient.addColorStop(1, "#b9bdcc");
    for (const p of this.whites) {
      const k = this.keyX.get(p);
      c.fillStyle = ambient;
      c.fillRect(k.x + 0.5, 5, k.w - 1, kbH - 5 - lip);
      c.fillStyle = "#8f93a5"; // the key's front face
      c.fillRect(k.x + 0.5, kbH - lip, k.w - 1, lip);
      c.fillStyle = "rgba(0,0,0,0.35)";
      c.fillRect(k.x + k.w - 0.5, 5, 1, kbH - 5);
    }
    const bk = kbH * 0.64;
    for (let p = LO; p <= HI; p++) {
      if (!isBlack(p)) continue;
      const k = this.keyX.get(p);
      const shadow = c.createLinearGradient(k.x + k.w, 0, k.x + k.w + ww * 0.25, 0);
      shadow.addColorStop(0, "rgba(0,0,0,0.35)");
      shadow.addColorStop(1, "rgba(0,0,0,0)");
      c.fillStyle = shadow;
      c.fillRect(k.x + k.w, 5, ww * 0.25, bk);
      this._blackKey(c, k, 5, bk, null);
    }
    c.fillStyle = "rgba(160,170,255,0.55)"; // the light line along the key slip
    c.fillRect(0, 0, w, 1);
    this.bk = bk;
  }

  _blackKey(c, k, top, len, rgb) {
    const body = c.createLinearGradient(k.x, 0, k.x + k.w, 0);
    if (rgb) {
      const [r, g, b] = rgb;
      body.addColorStop(0, `rgb(${r * 0.6 | 0},${g * 0.6 | 0},${b * 0.7 | 0})`);
      body.addColorStop(0.5, `rgb(${r},${g},${b})`);
      body.addColorStop(1, `rgb(${r * 0.6 | 0},${g * 0.6 | 0},${b * 0.7 | 0})`);
    } else {
      body.addColorStop(0, "#050507");
      body.addColorStop(0.45, "#1b1c22");
      body.addColorStop(1, "#060608");
    }
    c.fillStyle = body;
    c.beginPath();
    c.roundRect(k.x, top, k.w, len, [0, 0, 2, 2]);
    c.fill();
    c.fillStyle = rgb ? "rgba(255,255,255,0.35)" : "rgba(160,170,220,0.18)"; // gloss
    c.fillRect(k.x + k.w * 0.32, top + 3, k.w * 0.18, Math.max(0, len - 17));
    const bevel = c.createLinearGradient(0, top + len - 12, 0, top + len);
    bevel.addColorStop(0, rgb ? "rgba(255,255,255,0.5)" : "#3d3f4b");
    bevel.addColorStop(1, rgb ? "rgba(255,255,255,0.15)" : "#15161b");
    c.fillStyle = bevel;
    c.fillRect(k.x + 1, top + len - 12, k.w - 2, 11);
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

    ctx.fillStyle = css.getPropertyValue("--roll-bg") || "#000";
    ctx.fillRect(0, 0, w, h);

    // Octave lines at every C, then bar lines.
    ctx.lineWidth = 1;
    ctx.strokeStyle = css.getPropertyValue("--roll-guide");
    for (const [p, k] of this.keyX) {
      if (p % 12 !== 0) continue;
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

    // Notes, each with a soft halo: two larger translucent copies behind the crisp one.
    // (A full-screen blurred glow layer looked the same and cost far more per frame.)
    const sounding = [];
    for (const track of this.tracks) {
      if (!track.visible) continue;
      const rgb = hexRgb(track.color);
      const [r, g, b] = rgb;
      const halo1 = `rgba(${r},${g},${b},0.10)`, halo2 = `rgba(${r},${g},${b},0.22)`;
      for (const n of track.notes) {
        if (n.start > windowEnd) break;
        if (n.end < t) continue;
        const k = this.keyX.get(n.pitch);
        if (!k) continue;
        const top = Math.max(0, fall - (n.end - t) * this.pps);
        const bottom = Math.min(fall, fall - (n.start - t) * this.pps);
        if (bottom - top < 1) continue;
        const x = k.x + 1.5, width = Math.max(2, k.w - 3), height = Math.max(3, bottom - top);
        const radius = Math.min(6, width / 2, height / 2);
        ctx.fillStyle = halo1;
        ctx.beginPath(); ctx.roundRect(x - 5, top - 5, width + 10, height + 10, radius + 5); ctx.fill();
        ctx.fillStyle = halo2;
        ctx.beginPath(); ctx.roundRect(x - 2, top - 2, width + 4, height + 4, radius + 2); ctx.fill();
        ctx.fillStyle = track.color;
        ctx.beginPath(); ctx.roundRect(x, top, width, height, radius); ctx.fill();
        if (n.start <= t) sounding.push({ k, rgb, pitch: n.pitch });
      }
    }

    // Where notes land: a column of light, smoke and sparks, a small flare.
    ctx.save();
    ctx.globalCompositeOperation = "lighter";
    for (const { k, rgb } of sounding) {
      const [r, g, b] = rgb, cx = k.x + k.w / 2;
      const beam = ctx.createLinearGradient(0, fall, 0, fall - 150);
      beam.addColorStop(0, `rgba(${r},${g},${b},0.2)`);
      beam.addColorStop(1, `rgba(${r},${g},${b},0)`);
      ctx.fillStyle = beam;
      ctx.fillRect(cx - k.w, fall - 150, k.w * 2, 150);
      if (moving) this._emit(k, fall, rgb, dt);
    }
    this._drawParticles(dt);
    for (const { k } of sounding) {
      const cx = k.x + k.w / 2;
      ctx.globalAlpha = 0.75;
      ctx.drawImage(this.smoke, cx - 26, fall - 20, 52, 40);
    }
    ctx.restore();

    // The keyboard, then the keys being played, lit from above.
    ctx.drawImage(this.keyboard, 0, fall, w, this.kbH);
    const litWhite = new Map(), litBlack = new Map();
    for (const s of sounding) (isBlack(s.pitch) ? litBlack : litWhite).set(s.pitch, s);
    for (const { k, rgb } of litWhite.values()) {
      const [r, g, b] = rgb;
      const face = ctx.createLinearGradient(0, fall + 5, 0, fall + this.kbH);
      face.addColorStop(0, "#ffffff");
      face.addColorStop(0.35, `rgb(${Math.min(255, r + 120)},${Math.min(255, g + 120)},${Math.min(255, b + 40)})`);
      face.addColorStop(1, `rgb(${r},${g},${b})`);
      ctx.fillStyle = face;
      ctx.fillRect(k.x + 0.5, fall + 5, k.w - 1, this.kbH - 5);
    }
    // Black keys are drawn again on top so a lit white key does not cover them.
    if (litWhite.size) {
      for (let p = LO; p <= HI; p++) {
        if (!isBlack(p) || litBlack.has(p)) continue;
        const k = this.keyX.get(p);
        if (litWhite.has(p - 1) || litWhite.has(p + 1)) this._blackKey(ctx, { x: k.x, w: k.w }, fall + 5, this.bk, null);
      }
    }
    for (const { k, rgb } of litBlack.values()) this._blackKey(ctx, { x: k.x, w: k.w }, fall + 5, this.bk, rgb);
    // Light spilling from each lit key onto its neighbours.
    ctx.save();
    ctx.globalCompositeOperation = "lighter";
    for (const { k, rgb } of sounding) {
      const [r, g, b] = rgb, cx = k.x + k.w / 2;
      ctx.save();
      ctx.translate(cx, fall + 6);
      ctx.scale(2.2, 1);
      const pool = ctx.createRadialGradient(0, 0, 0, 0, 0, this.kbH * 0.5);
      pool.addColorStop(0, `rgba(${r},${g},${b},0.3)`);
      pool.addColorStop(0.4, `rgba(${r},${g},${b},0.12)`);
      pool.addColorStop(1, `rgba(${r},${g},${b},0)`);
      ctx.fillStyle = pool;
      ctx.fillRect(-this.kbH * 0.5, 0, this.kbH, this.kbH * 0.5);
      ctx.restore();
      const glow = ctx.createRadialGradient(cx, fall + 3, 0, cx, fall + 3, k.w * 1.4);
      glow.addColorStop(0, "rgba(255,255,255,0.6)");
      glow.addColorStop(0.5, `rgba(${r},${g},${b},0.3)`);
      glow.addColorStop(1, `rgba(${r},${g},${b},0)`);
      ctx.fillStyle = glow;
      ctx.fillRect(cx - k.w * 1.4, fall - 8, k.w * 2.8, 22);
    }
    ctx.restore();

    this._drawChords(t, windowEnd, fall, css);
  }

  _drawChords(t, windowEnd, fall, css) {
    const { ctx, w } = this;
    let now = null;
    const next = [];
    ctx.textAlign = "right";
    ctx.font = "500 14px system-ui, sans-serif";
    for (const [start, end, name] of this.chords || []) {
      if (start <= t && t < end) now = name;
      else if (start > t && next.length < 2) next.push(name);
      if (start <= t || start > windowEnd) continue;
      const y = Math.round(fall - (start - t) * this.pps) + 0.5;
      ctx.strokeStyle = css.getPropertyValue("--roll-chord-line");
      ctx.beginPath(); ctx.moveTo(w - 70, y); ctx.lineTo(w, y); ctx.stroke();
      ctx.fillStyle = css.getPropertyValue("--roll-chord");
      ctx.fillText(name, w - 8, y - 5);
    }
    ctx.textAlign = "start";
    if (!now) return;
    ctx.save();
    ctx.font = "300 32px system-ui, sans-serif";
    ctx.shadowColor = "rgba(91,108,255,0.7)";
    ctx.shadowBlur = 20;
    ctx.fillStyle = css.getPropertyValue("--roll-chord-now");
    ctx.fillText(now, 22, 50);
    const width = ctx.measureText(now).width;
    ctx.restore();
    if (next.length) {
      ctx.font = "14px system-ui, sans-serif";
      ctx.fillStyle = css.getPropertyValue("--roll-chord");
      ctx.fillText(`→ ${next.join("  ")}`, 32 + width, 48);
    }
  }

  _tint(rgb) {
    const key = rgb.join();
    if (!this._tints.has(key)) {
      const c = document.createElement("canvas");
      c.width = c.height = 64;
      const x = c.getContext("2d");
      x.drawImage(this.smoke, 0, 0);
      x.globalCompositeOperation = "source-in";
      x.fillStyle = `rgb(${rgb})`;
      x.fillRect(0, 0, 64, 64);
      this._tints.set(key, c);
    }
    return this._tints.get(key);
  }

  _emit(k, y, rgb, dt) {
    const parts = this.particles;
    if (parts.length > 600) return;
    // About 60 sparks and 8 puffs of smoke a second per sounding key.
    if (Math.random() < 30 * dt) {
      for (let i = 0; i < 2; i++) {
        parts.push({ kind: 0, x: k.x + Math.random() * k.w, y: y - 2, vx: (Math.random() - 0.5) * 40, vy: -40 - Math.random() * 90,
          life: 0, max: 0.5 + Math.random() * 0.7, rgb });
      }
    }
    if (Math.random() < 8 * dt) {
      parts.push({ kind: 1, x: k.x + k.w / 2 + (Math.random() - 0.5) * k.w, y: y - 6, vx: (Math.random() - 0.5) * 12, vy: -18 - Math.random() * 25,
        life: 0, max: 1.2 + Math.random() * 1.2, size: 16 + Math.random() * 22, rgb });
    }
  }

  _drawParticles(dt) {
    const { ctx } = this;
    const parts = this.particles;
    let alive = 0;
    for (const p of parts) {
      p.life += dt;
      if (p.life >= p.max) continue;
      p.x += p.vx * dt;
      p.y += p.vy * dt;
      const a = 1 - p.life / p.max;
      if (p.kind === 0) {
        p.vy += 30 * dt;
        const [r, g, b] = p.rgb;
        ctx.fillStyle = `rgba(${Math.min(255, r + 120)},${Math.min(255, g + 120)},${Math.min(255, b + 120)},${a})`;
        ctx.fillRect(p.x, p.y, 1.6, 1.6);
      } else {
        const s = p.size * (1 + p.life * 0.6);
        ctx.globalAlpha = 0.14 * a;
        ctx.drawImage(this._tint(p.rgb), p.x - s / 2, p.y - s / 2, s, s);
        ctx.globalAlpha = 1;
      }
      parts[alive++] = p;
    }
    parts.length = alive;
  }

  // Pixels of scroll to seconds, for wheel scrubbing.
  pxToSeconds(px) {
    return px / this.pps;
  }
}

function sprite() {
  const c = document.createElement("canvas");
  c.width = c.height = 64;
  const x = c.getContext("2d");
  const g = x.createRadialGradient(32, 32, 0, 32, 32, 32);
  g.addColorStop(0, "rgba(255,255,255,1)");
  g.addColorStop(0.3, "rgba(255,255,255,0.35)");
  g.addColorStop(1, "rgba(255,255,255,0)");
  x.fillStyle = g;
  x.fillRect(0, 0, 64, 64);
  return c;
}

function hexRgb(hex) {
  const v = parseInt(hex.slice(1), 16);
  return [(v >> 16) & 255, (v >> 8) & 255, v & 255];
}
