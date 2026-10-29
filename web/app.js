import { Player } from "./player.js";
import { PianoRoll } from "./roll.js";
import { SheetView } from "./sheet.js";

const COLORS = { piano: "#4f7cff", vocals: "#e4572e", guitar: "#f2a541", bass: "#3bb273", drums: "#9a9ba3", other: "#a463f2" };
const $ = (id) => document.getElementById(id);

const player = new Player();
window.notewright = { player }; // handy from the devtools console
const roll = new PianoRoll($("roll"));
const sheet = new SheetView($("sheet"));
let jobs = [];
let current = null; // { job, result }
let pollTimer = null;
let dragging = false;

// ---------- Library ----------

async function refreshJobs() {
  jobs = await (await fetch("/api/jobs")).json();
  renderJobs();
  const busy = jobs.some((j) => j.status === "queued" || j.status === "running");
  clearTimeout(pollTimer);
  if (busy) pollTimer = setTimeout(refreshJobs, 1500);
  const shown = jobs.find((j) => j.id === location.hash.slice(1));
  if (shown && (shown.status !== "done" || !current?.result || current.job.id !== shown.id)) showJob(shown);
}

function renderJobs() {
  const list = $("jobs");
  list.innerHTML = "";
  $("jobs-empty").hidden = jobs.length > 0;
  for (const job of jobs) {
    const li = document.createElement("li");
    li.classList.toggle("selected", job.id === location.hash.slice(1));
    const title = document.createElement("span");
    title.className = "title";
    title.textContent = job.title;
    const sub = document.createElement("span");
    sub.className = "sub";
    const mode = job.mode === "band" ? "Full band" : "Piano";
    sub.textContent = job.status === "done" ? mode
      : job.status === "error" ? `${mode} · failed`
      : `${mode} · ${job.stage}`;
    li.append(title, sub);
    if (job.status === "running" || job.status === "queued") {
      const mini = document.createElement("div");
      mini.className = "mini";
      mini.innerHTML = `<div style="width:${Math.round(job.progress * 100)}%"></div>`;
      li.append(mini);
    }
    const remove = document.createElement("button");
    remove.className = "remove";
    remove.title = "Delete";
    remove.setAttribute("aria-label", `Delete ${job.title}`);
    remove.textContent = "×";
    remove.onclick = async (e) => {
      e.stopPropagation();
      if (!confirm(`Delete "${job.title}"?`)) return;
      await fetch(`/api/jobs/${job.id}`, { method: "DELETE" });
      if (location.hash.slice(1) === job.id) { history.replaceState(null, "", "#"); showWelcome(); }
      refreshJobs();
    };
    li.append(remove);
    li.onclick = () => { location.hash = job.id; };
    list.append(li);
  }
}

function showWelcome() {
  player.pause();
  current = null;
  $("welcome").hidden = false;
  $("progress").hidden = true;
  $("song").hidden = true;
}

async function showJob(job) {
  $("welcome").hidden = true;
  if (job.status !== "done") {
    player.pause();
    current = { job, result: null };
    $("song").hidden = true;
    $("progress").hidden = false;
    $("progress-title").textContent = job.title;
    $("progress-stage").textContent = job.status === "queued" ? "Waiting for the previous song to finish" : job.stage;
    $("progress-bar").style.width = `${Math.round(job.progress * 100)}%`;
    $("progress-error").hidden = job.status !== "error";
    $("progress-error").textContent = job.error || "";
    if (job.status === "error") $("progress-stage").textContent = "Transcription failed.";
    return;
  }
  if (current && current.job.id === job.id && current.result) return;
  const base = `/files/${job.id}/`;
  const result = await (await fetch(base + "result.json")).json();
  current = { job, result, base };
  openSong(result, base);
}

window.addEventListener("hashchange", () => {
  const job = jobs.find((j) => j.id === location.hash.slice(1));
  renderJobs();
  if (job) showJob(job); else showWelcome();
});

$("new-job").addEventListener("submit", async (e) => {
  e.preventDefault();
  const res = await fetch("/api/jobs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url: $("url").value, mode: mode() }),
  });
  if (!res.ok) return alert((await res.json()).detail || "Could not start the transcription.");
  const job = await res.json();
  $("url").value = "";
  location.hash = job.id;
  refreshJobs();
});

$("file").addEventListener("change", async () => {
  const file = $("file").files[0];
  if (!file) return;
  const body = new FormData();
  body.append("file", file);
  body.append("mode", mode());
  const job = await (await fetch("/api/jobs/upload", { method: "POST", body })).json();
  $("file").value = "";
  location.hash = job.id;
  refreshJobs();
});

const mode = () => document.querySelector('input[name="mode"]:checked').value;

// ---------- Song view ----------

function fmt(t) {
  const m = Math.floor(t / 60), s = Math.floor(t % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}

function openSong(result, base) {
  $("progress").hidden = true;
  $("song").hidden = false;
  $("song-title").textContent = result.title;
  const parts = result.tracks.filter((t) => t.notes.length).length;
  $("song-meta").textContent = `${result.timeSignature} · ${Math.round(result.bpm)} bpm · ${fmt(result.duration)}`
    + (result.tracks.length > 1 ? ` · ${parts} parts transcribed` : "");
  $("duration").textContent = fmt(result.duration);
  $("dl-midi").href = base + result.midi;

  player.load(result, base);
  player.setRate($("speed").value / 100);

  const tracks = result.tracks.map((t) => ({
    ...t,
    color: COLORS[t.id] || "#888",
    visible: true,
    notes: t.notes.map(([start, end, pitch]) => ({ start, end, pitch })),
  }));
  current.tracks = tracks;
  roll.setData(tracks, result.beats, result.beatsPerBar);

  const sheetSelect = $("sheet-track");
  sheetSelect.innerHTML = "";
  for (const t of tracks.filter((t) => t.sheet)) sheetSelect.add(new Option(t.name, t.id));
  sheetSelect.onchange = () => loadSheet(sheetSelect.value);
  if (sheetSelect.options.length) loadSheet(sheetSelect.value);
  else $("sheet").innerHTML = '<p class="hint">No pitched notes were found, so there is no sheet music.</p>';

  buildMixer(result);
  applyPreset("original");
  setView(localStorage.getItem("notewright.view") || "both");
  updateLoopButtons();
}

async function loadSheet(trackId) {
  const t = current.tracks.find((x) => x.id === trackId);
  $("dl-xml").href = current.base + t.sheet;
  $("sheet").innerHTML = '<p class="hint">Rendering sheet music…</p>';
  try {
    await sheet.load(current.base + t.sheet, current.result.beats, current.result.beatQuarters);
    sheet.update(player.time);
  } catch (err) {
    console.error(err);
    $("sheet").innerHTML = '<p class="error">The sheet music could not be rendered. The MusicXML download still works in MuseScore.</p>';
  }
}

function setView(view) {
  $("stage").dataset.view = view;
  for (const b of document.querySelectorAll(".views .tab")) b.classList.toggle("active", b.dataset.view === view);
  try { localStorage.setItem("notewright.view", view); } catch {}
}
for (const b of document.querySelectorAll(".views .tab")) b.onclick = () => setView(b.dataset.view);

// ---------- Mixer ----------

const mix = { media: {}, synth: {} };

function slider(value, onInput) {
  const input = document.createElement("input");
  input.type = "range";
  input.min = 0;
  input.max = 100;
  input.value = Math.round(value * 100);
  input.oninput = () => onInput(input.value / 100);
  return input;
}

function buildMixer(result) {
  const rows = $("mix-rows");
  rows.innerHTML = "";
  mix.media = {};
  mix.synth = {};
  const addRow = (label, color, mediaId, synthId, track) => {
    const tr = document.createElement("tr");
    const name = document.createElement("td");
    if (color) name.innerHTML = `<span class="swatch" style="background:${color}"></span>`;
    name.append(label);
    const rec = document.createElement("td");
    const syn = document.createElement("td");
    const show = document.createElement("td");
    if (mediaId) {
      mix.media[mediaId] = slider(0, (v) => player.setMediaVolume(mediaId, v));
      rec.append(mix.media[mediaId]);
    } else rec.innerHTML = '<span class="na">—</span>';
    if (synthId) {
      mix.synth[synthId] = slider(0, (v) => player.setSynthVolume(synthId, v));
      syn.append(mix.synth[synthId]);
    } else syn.innerHTML = '<span class="na">—</span>';
    if (track && track.notes.length && track.id !== "drums") {
      const box = document.createElement("input");
      box.type = "checkbox";
      box.checked = true;
      box.setAttribute("aria-label", `Show ${track.name} in the piano roll`);
      box.onchange = () => {
        current.tracks.find((t) => t.id === track.id).visible = box.checked;
        roll.draw(player.time);
      };
      show.append(box);
    }
    tr.append(name, rec, syn, show);
    rows.append(tr);
  };
  addRow("Full recording", null, "original", null, null);
  for (const t of result.tracks.filter((t) => !t.silent)) {
    addRow(t.name, COLORS[t.id], t.stem ? t.id : null, t.notes.length ? t.id : null, t);
  }

  const band = result.tracks.some((t) => t.stem);
  $("playalong-wrap").hidden = !band;
  const pa = $("playalong");
  pa.innerHTML = "";
  pa.add(new Option("—", ""));
  for (const t of result.tracks.filter((t) => t.stem && !t.silent)) pa.add(new Option(t.name.split(" (")[0], t.id));
  pa.onchange = () => (pa.value ? playAlong() : applyPreset("original"));
  $("backing").onchange = () => pa.value && playAlong();
}

function setLevels(media, synth) {
  for (const [id, input] of Object.entries(mix.media)) {
    const v = media(id);
    input.value = Math.round(v * 100);
    player.setMediaVolume(id, v);
  }
  for (const [id, input] of Object.entries(mix.synth)) {
    const v = synth(id);
    input.value = Math.round(v * 100);
    player.setSynthVolume(id, v);
  }
}

function applyPreset(name) {
  if (name !== "playalong") $("playalong").value = "";
  for (const b of document.querySelectorAll(".presets button")) b.classList.toggle("active", b.dataset.preset === name);
  if (name === "original") setLevels((id) => (id === "original" ? 1 : 0), () => 0);
  if (name === "transcription") setLevels(() => 0, () => 0.9);
  if (name === "both") setLevels((id) => (id === "original" ? 0.7 : 0), () => 0.8);
}
for (const b of document.querySelectorAll(".presets button")) b.onclick = () => applyPreset(b.dataset.preset);

// Mute your part, keep the rest: from the separated recording or from the transcription.
function playAlong() {
  const me = $("playalong").value;
  const real = $("backing").value === "recording";
  applyPreset("playalong");
  setLevels(
    (id) => (real && id !== "original" && id !== me ? 1 : 0),
    (id) => (!real && id !== me ? 0.85 : 0),
  );
}

player.onLoading = (pending) => {
  $("sound-status").textContent = pending ? "Loading instrument sounds…" : "";
};

// ---------- Transport ----------

$("play").onclick = () => player.toggle();
player.onEnd = () => document.body.classList.remove("playing");

$("seek").addEventListener("input", () => {
  dragging = true;
  $("time").textContent = fmt(($("seek").value / 1000) * player.duration);
});
$("seek").addEventListener("change", () => {
  dragging = false;
  player.seek(($("seek").value / 1000) * player.duration);
});

$("speed").addEventListener("input", () => {
  const v = Number($("speed").value);
  $("speed-out").textContent = `${v}%`;
  player.setRate(v / 100);
});

$("loop-a").onclick = () => { player.loop.a = player.time; fixLoop(); };
$("loop-b").onclick = () => { player.loop.b = player.time; fixLoop(); };
$("loop-toggle").onclick = () => {
  const l = player.loop;
  if (l.a === null || l.b === null) {
    alert("Set A and B first: press A at the start of the passage and B at its end.");
    return;
  }
  l.on = !l.on;
  if (l.on && (player.time < l.a || player.time > l.b)) player.seek(l.a);
  updateLoopButtons();
};

function fixLoop() {
  const l = player.loop;
  if (l.a !== null && l.b !== null && l.b < l.a) [l.a, l.b] = [l.b, l.a];
  if (l.a !== null && l.b !== null && l.b - l.a < 0.5) l.b = l.a + 0.5;
  updateLoopButtons();
}

function updateLoopButtons() {
  const l = player.loop;
  $("loop-a").classList.toggle("set", l.a !== null);
  $("loop-b").classList.toggle("set", l.b !== null);
  $("loop-a").title = l.a === null ? "Loop start at the current position" : `Loop start: ${fmt(l.a)}`;
  $("loop-b").title = l.b === null ? "Loop end at the current position" : `Loop end: ${fmt(l.b)}`;
  $("loop-toggle").classList.toggle("active", l.on);
}

// Wheel over the piano roll scrubs through the song.
$("roll").addEventListener("wheel", (e) => {
  if (!current?.result) return;
  e.preventDefault();
  player.seek(player.time - roll.pxToSeconds(e.deltaY));
}, { passive: false });

document.addEventListener("keydown", (e) => {
  if (!current?.result || e.target.matches("input[type=url], input[type=text], select")) return;
  if (e.code === "Space") { e.preventDefault(); player.toggle(); }
  if (e.code === "ArrowLeft") player.seek(player.time - 5);
  if (e.code === "ArrowRight") player.seek(player.time + 5);
});

$("zoom-in").onclick = () => sheet.setZoom(sheet.zoom + 0.1);
$("zoom-out").onclick = () => sheet.setZoom(sheet.zoom - 0.1);

function frame() {
  requestAnimationFrame(frame);
  if (current?.result) {
    const t = player.time;
    document.body.classList.toggle("playing", player.playing);
    if (!dragging) {
      $("time").textContent = fmt(t);
      $("seek").value = player.duration ? Math.round((t / player.duration) * 1000) : 0;
    }
    if ($("stage").dataset.view !== "sheet") roll.draw(t);
    if ($("stage").dataset.view !== "roll") sheet.update(t);
  }
}

refreshJobs().then(() => {
  if (!location.hash.slice(1)) showWelcome();
});
requestAnimationFrame(frame);
