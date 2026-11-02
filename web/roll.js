// Falling-notes piano roll: notes drop onto a keyboard and light the keys they play.
//
// The look follows Rousseau's piano videos: all 88 keys of a real piano (ivory and ebony keys,
// a wooden key slip with its red felt), crisp notes with a soft glow, and light where notes land.
// It stays cheap: the keyboard is drawn once per resize and copied each frame; a note's
// glow is two translucent rounded rectangles behind it; smoke is one pre-drawn sprite.

const BLACK = new Set([1, 3, 6, 8, 10]);
const isBlack = (p) => BLACK.has(p % 12);
const LO = 21, HI = 108; // A0..C8
const KEY_TOP = 7; // the key slip above the keys, in pixels
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
    // Behind and between the keys: the dark key bed.
    c.fillStyle = "#08080a";
    c.fillRect(0, 0, w, kbH);
    // The key slip a real piano has above the keys: dark wood with a red felt strip.
    const slip = KEY_TOP - 2;
    const wood = c.createLinearGradient(0, 0, 0, slip);
    wood.addColorStop(0, "#1c1714");
    wood.addColorStop(0.6, "#0f0c0b");
    wood.addColorStop(1, "#060505");
    c.fillStyle = wood;
    c.fillRect(0, 0, w, slip);
    c.fillStyle = "rgba(255,240,220,0.10)"; // its polished edge catching the light
    c.fillRect(0, 0, w, 1);
    c.fillStyle = "#5e1418"; // the felt
    c.fillRect(0, slip, w, 2);
    for (const p of this.whites) this._whiteKey(c, this.keyX.get(p), KEY_TOP, kbH - KEY_TOP, false);
    const bk = (kbH - KEY_TOP) * 0.63;
    for (let p = LO; p <= HI; p++) {
      if (!isBlack(p)) continue;
      const k = this.keyX.get(p);
      const shadow = c.createLinearGradient(k.x + k.w, 0, k.x + k.w + ww * 0.3, 0);
      shadow.addColorStop(0, "rgba(0,0,0,0.28)");
      shadow.addColorStop(1, "rgba(0,0,0,0)");
      c.fillStyle = shadow;
      c.fillRect(k.x + k.w, KEY_TOP, ww * 0.3, bk + 3);
      this._blackKey(c, k, KEY_TOP, bk, false);
    }
    this.bk = bk;
  }

  // An ivory key: warm white, a soft shadow under the key slip, a rounded front edge.
  // Pressed, it sits lower: a little shorter, shaded at its sides.
  _whiteKey(c, k, top, len, pressed) {
    const x = k.x + 0.6, wd = k.w - 1.2;
    const front = Math.max(3, Math.round(len * 0.055));
    const body = len - front - (pressed ? 1.5 : 0);
    const face = c.createLinearGradient(0, top, 0, top + body);
    face.addColorStop(0, pressed ? "#8d8a84" : "#9c9890");
    face.addColorStop(0.07, pressed ? "#d4d0c6" : "#e9e5db");
    face.addColorStop(0.7, pressed ? "#e2ded4" : "#f6f3eb");
    face.addColorStop(1, pressed ? "#dcd8ce" : "#efebe2");
    c.fillStyle = face;
    c.beginPath();
    c.roundRect(x, top, wd, body, [0, 0, 1.5, 1.5]);
    c.fill();
    c.fillStyle = pressed ? "#a7a399" : "#c9c4b8"; // the front of the key
    c.beginPath();
    c.roundRect(x, top + body, wd, front, [0, 0, 2.5, 2.5]);
    c.fill();
    if (pressed) {
      const side = c.createLinearGradient(x, 0, x + wd, 0);
      side.addColorStop(0, "rgba(0,0,0,0.22)");
      side.addColorStop(0.18, "rgba(0,0,0,0)");
      side.addColorStop(0.82, "rgba(0,0,0,0)");
      side.addColorStop(1, "rgba(0,0,0,0.22)");
      c.fillStyle = side;
      c.fillRect(x, top, wd, body);
    }
  }

  // An ebony key: a flat top, a sloped front catching the light, a soft sheen.
  _blackKey(c, k, top, len, pressed) {
    const slope = Math.max(4, Math.round(len * 0.13));
    const flat = len - slope - (pressed ? 2 : 0);
    const body = c.createLinearGradient(k.x, 0, k.x + k.w, 0);
    body.addColorStop(0, "#050505");
    body.addColorStop(0.5, pressed ? "#141313" : "#1d1c1b");
    body.addColorStop(1, "#060606");
    c.fillStyle = body;
    c.beginPath();
    c.roundRect(k.x, top, k.w, flat + slope, [0, 0, 1.5, 1.5]);
    c.fill();
    const sheen = c.createLinearGradient(0, top, 0, top + flat);
    sheen.addColorStop(0, "rgba(255,255,255,0)");
    sheen.addColorStop(1, pressed ? "rgba(255,255,255,0.05)" : "rgba(255,255,255,0.09)");
    c.fillStyle = sheen;
    c.fillRect(k.x + k.w * 0.18, top, k.w * 0.64, flat);
    const front = c.createLinearGradient(0, top + flat, 0, top + flat + slope);
    front.addColorStop(0, pressed ? "#2a2928" : "#3a3836");
    front.addColorStop(1, "#121212");
    c.fillStyle = front;
    c.fillRect(k.x + 1, top + flat, k.w - 2, slope - 1);
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
      // The column of light is a tall ellipse fading in every direction, so it has
      // no hard sides: a radial gradient squeezed sideways.
      ctx.save();
      ctx.translate(cx, fall);
      ctx.scale(k.w / 150, 1);
      const beam = ctx.createRadialGradient(0, 0, 0, 0, 0, 150);
      beam.addColorStop(0, `rgba(${r},${g},${b},0.22)`);
      beam.addColorStop(0.5, `rgba(${r},${g},${b},0.08)`);
      beam.addColorStop(1, `rgba(${r},${g},${b},0)`);
      ctx.fillStyle = beam;
      ctx.fillRect(-150, -150, 300, 150);
      ctx.restore();
      if (moving) this._emit(k, fall, rgb, dt);
    }
    this._drawParticles(dt);
    for (const { k } of sounding) {
      const cx = k.x + k.w / 2;
      ctx.globalAlpha = 0.75;
      ctx.drawImage(this.smoke, cx - 26, fall - 20, 52, 40);
    }
    ctx.restore();

    // The keyboard, then the keys being played: pressed down, lit by the falling note.
    const kbH = this.kbH, keyLen = kbH - KEY_TOP;
    ctx.drawImage(this.keyboard, 0, fall, w, kbH);
    const litWhite = new Map(), litBlack = new Map();
    for (const s of sounding) (isBlack(s.pitch) ? litBlack : litWhite).set(s.pitch, s);
    for (const { k } of litWhite.values()) this._whiteKey(ctx, k, fall + KEY_TOP, keyLen, true);
    // Black keys are drawn again on top so a pressed white key does not cover them.
    if (litWhite.size) {
      for (let p = LO; p <= HI; p++) {
        if (!isBlack(p) || litBlack.has(p)) continue;
        if (litWhite.has(p - 1) || litWhite.has(p + 1)) this._blackKey(ctx, this.keyX.get(p), fall + KEY_TOP, this.bk, false);
      }
    }
    for (const { k } of litBlack.values()) this._blackKey(ctx, k, fall + KEY_TOP, this.bk, true);
    // The note's colour falls on the key it plays, strongest where it lands, and spills a
    // little onto the neighbours, the way coloured light falls on a real keyboard.
    ctx.save();
    ctx.globalCompositeOperation = "lighter";
    for (const { k, rgb, pitch } of sounding) {
      const [r, g, b] = rgb, cx = k.x + k.w / 2;
      const len = isBlack(pitch) ? this.bk : keyLen;
      const wash = ctx.createLinearGradient(0, fall + KEY_TOP, 0, fall + KEY_TOP + len);
      const a = isBlack(pitch) ? 0.55 : 0.32;
      wash.addColorStop(0, `rgba(${r},${g},${b},${a})`);
      wash.addColorStop(0.55, `rgba(${r},${g},${b},${a * 0.45})`);
      wash.addColorStop(1, `rgba(${r},${g},${b},${a * 0.15})`);
      ctx.fillStyle = wash;
      ctx.fillRect(k.x + 1, fall + KEY_TOP, k.w - 2, len);
      ctx.save();
      ctx.translate(cx, fall + KEY_TOP);
      ctx.scale(2, 1);
      const pool = ctx.createRadialGradient(0, 0, 0, 0, 0, keyLen * 0.45);
      pool.addColorStop(0, `rgba(${r},${g},${b},0.16)`);
      pool.addColorStop(1, `rgba(${r},${g},${b},0)`);
      ctx.fillStyle = pool;
      ctx.fillRect(-keyLen * 0.45, 0, keyLen * 0.9, keyLen * 0.45);
      ctx.restore();
      // Where the note meets the key: a soft coloured glow, not a white flash.
      const glow = ctx.createRadialGradient(cx, fall + KEY_TOP - 1, 0, cx, fall + KEY_TOP - 1, k.w * 1.2);
      glow.addColorStop(0, `rgba(${Math.min(255, r + 90)},${Math.min(255, g + 90)},${Math.min(255, b + 90)},0.35)`);
      glow.addColorStop(1, `rgba(${r},${g},${b},0)`);
      ctx.fillStyle = glow;
      ctx.fillRect(cx - k.w * 1.2, fall + KEY_TOP - 1 - k.w * 1.2, k.w * 2.4, k.w * 2.4);
    }
    ctx.restore();
    this._drawChords(t, windowEnd, fall, css);
  }

  _drawChords(t, windowEnd, fall, css) {
    const { ctx, w } = this;
    ctx.textAlign = "right";
    ctx.font = "500 14px system-ui, sans-serif";
    for (const [start, , name] of this.chords || []) {
      if (start <= t || start > windowEnd) continue;
      const y = Math.round(fall - (start - t) * this.pps) + 0.5;
      ctx.strokeStyle = css.getPropertyValue("--roll-chord-line");
      ctx.beginPath(); ctx.moveTo(w - 70, y); ctx.lineTo(w, y); ctx.stroke();
      ctx.fillStyle = css.getPropertyValue("--roll-chord");
      ctx.fillText(name, w - 8, y - 5);
    }
    ctx.textAlign = "start";
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
    // About 12 fine sparks and 6 puffs of smoke a second per sounding key.
    if (Math.random() < 12 * dt) {
      for (let i = 0; i < 1; i++) {
        parts.push({ kind: 0, x: k.x + Math.random() * k.w, y: y - 2, vx: (Math.random() - 0.5) * 40, vy: -40 - Math.random() * 90,
          life: 0, max: 0.5 + Math.random() * 0.7, rgb });
      }
    }
    if (Math.random() < 6 * dt) {
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
        ctx.fillStyle = `rgba(${Math.min(255, r + 120)},${Math.min(255, g + 120)},${Math.min(255, b + 120)},${a * 0.7})`;
        ctx.fillRect(p.x, p.y, 1.2, 1.2);
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
