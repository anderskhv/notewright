// Sheet music view: OpenSheetMusicDisplay with a cursor that follows playback.
/* global opensheetmusicdisplay */

export class SheetView {
  constructor(container) {
    this.container = container;
    this.osmd = null;
    this.steps = [];
    this.index = 0;
    this.zoom = 1;
  }

  async load(url, beats, beatQuarters) {
    this.beats = beats;
    this.beatQuarters = beatQuarters;
    this.container.innerHTML = "";
    this.osmd = null;
    const osmd = new opensheetmusicdisplay.OpenSheetMusicDisplay(this.container, {
      autoResize: true,
      backend: "svg",
      drawTitle: false,
      drawComposer: false,
      drawPartNames: false,
      followCursor: false,
      cursorsOptions: [{ type: 0, color: "#3f6fe0", alpha: 0.45, follow: false }],
    });
    osmd.zoom = this.zoom;
    await osmd.load(url);
    osmd.render();
    this.osmd = osmd;
    this._indexCursor();
  }

  setZoom(z) {
    if (!this.osmd) return;
    this.zoom = Math.max(0.4, Math.min(2, z));
    this.osmd.zoom = this.zoom;
    this.osmd.render();
    this._indexCursor();
  }

  // Record the score position (in quarter notes) of every cursor step once, so
  // following playback is a lookup rather than a walk through the score.
  _indexCursor() {
    const cursor = this.osmd.cursor;
    cursor.show();
    cursor.reset();
    this.steps = [];
    while (!cursor.iterator.EndReached) {
      this.steps.push(cursor.iterator.currentTimeStamp.RealValue * 4);
      cursor.next();
    }
    cursor.reset();
    this.index = 0;
    this._lastScroll = null;
  }

  // Seconds to score quarter notes via the beat grid the score was written on.
  quarterAt(t) {
    const b = this.beats;
    if (!b || b.length < 2) return 0;
    let lo = 0, hi = b.length - 1;
    if (t <= b[0]) return 0;
    if (t >= b[hi]) return hi * this.beatQuarters;
    while (hi - lo > 1) {
      const mid = (lo + hi) >> 1;
      if (b[mid] <= t) lo = mid; else hi = mid;
    }
    return (lo + (t - b[lo]) / (b[hi] - b[lo])) * this.beatQuarters;
  }

  timeAtQuarter(q) {
    const i = q / this.beatQuarters;
    const lo = Math.max(0, Math.min(this.beats.length - 2, Math.floor(i)));
    return this.beats[lo] + (i - lo) * (this.beats[lo + 1] - this.beats[lo]);
  }

  update(t) {
    if (!this.osmd || !this.steps.length || !this.container.offsetParent) return;
    const q = this.quarterAt(t) + 1e-6;
    let target = 0, lo = 0, hi = this.steps.length - 1;
    while (lo <= hi) {
      const mid = (lo + hi) >> 1;
      if (this.steps[mid] <= q) { target = mid; lo = mid + 1; } else hi = mid - 1;
    }
    const cursor = this.osmd.cursor;
    if (target < this.index) { cursor.reset(); this.index = 0; }
    while (this.index < target) { cursor.next(); this.index++; }
    this._follow(cursor.cursorElement);
  }

  _follow(el) {
    if (!el) return;
    const box = this.container;
    const top = el.offsetTop;
    if (top === this._lastScroll) return;
    this._lastScroll = top;
    const view = box.scrollTop;
    if (top < view || top > view + box.clientHeight * 0.6) {
      box.scrollTo({ top: Math.max(0, top - box.clientHeight * 0.15), behavior: "smooth" });
    }
  }
}
