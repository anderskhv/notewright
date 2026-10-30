// Sheet music view: OpenSheetMusicDisplay with a cursor that follows playback.
/* global opensheetmusicdisplay */

// OSMD's visible cursor redraws itself on every step, which locks the page up on a long
// score. So the score is indexed with an invisible copy of the cursor's iterator, and
// the visible cursor is moved in one redraw. The score is laid out only while it is on
// screen (laid out while hidden it has no width), and again whenever its width changes.
export class SheetView {
  constructor(container) {
    this.container = container;
    this.osmd = null;
    this.steps = [];
    this.index = 0;
    this.zoom = 1;
    this._width = 0;
    this._token = 0;
  }

  async load(url, beats, beatQuarters) {
    const token = ++this._token;
    this.beats = beats;
    this.beatQuarters = beatQuarters;
    this.container.innerHTML = "";
    this.osmd = null;
    this.steps = [];
    this._width = 0;
    const osmd = new opensheetmusicdisplay.OpenSheetMusicDisplay(this.container, {
      autoResize: false, // re-laid out from update() instead, only when visible
      backend: "svg",
      drawTitle: false,
      drawComposer: false,
      drawPartNames: false,
      followCursor: false,
      cursorsOptions: [{ type: 0, color: "#3f6fe0", alpha: 0.45, follow: false }],
    });
    osmd.zoom = this.zoom;
    await osmd.load(url);
    if (token !== this._token) return; // another song or part was opened meanwhile
    this.osmd = osmd;
    this._layout();
  }

  setZoom(z) {
    if (!this.osmd) return;
    this.zoom = Math.max(0.4, Math.min(2, z));
    this.osmd.zoom = this.zoom;
    this._width = 0;
    this._layout();
  }

  get visible() {
    return !!this.container.offsetParent && this.container.clientWidth > 0;
  }

  _layout() {
    if (!this.osmd || !this.visible) return false;
    const width = this.container.clientWidth;
    if (width === this._width) return true;
    this._width = width;
    this.osmd.render();
    this._indexCursor();
    return true;
  }

  // Record the score position (in quarter notes) of every cursor step once, using an
  // invisible iterator, so following playback is a lookup.
  _indexCursor() {
    const cursor = this.osmd.cursor;
    cursor.show();
    cursor.reset();
    const it = cursor.Iterator.clone();
    this.steps = [];
    let guard = 0;
    while (!it.EndReached && guard++ < 200000) {
      this.steps.push(it.currentTimeStamp.RealValue * 4);
      it.moveToNextVisibleVoiceEntry(false);
    }
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

  update(t) {
    if (!this._layout() || !this.steps.length) return;
    const q = this.quarterAt(t) + 1e-6;
    let target = 0, lo = 0, hi = this.steps.length - 1;
    while (lo <= hi) {
      const mid = (lo + hi) >> 1;
      if (this.steps[mid] <= q) { target = mid; lo = mid + 1; } else hi = mid - 1;
    }
    if (target === this.index) return;
    const cursor = this.osmd.cursor;
    if (target < this.index) {
      cursor.reset();
      this.index = 0;
    }
    // Move the iterator without drawing, then draw the cursor once.
    while (this.index < target && !cursor.Iterator.EndReached) {
      cursor.Iterator.moveToNextVisibleVoiceEntry(false);
      this.index++;
    }
    cursor.update();
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
