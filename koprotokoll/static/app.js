"use strict";

const $ = (sel) => document.querySelector(sel);
const STATUS = {
  queued: "In Warteschlange", converting: "Konvertiere Audio", transcribing: "Transkribiere (Parakeet)",
  diarizing: "Erkenne Sprecher", summarizing: "Erstelle Zusammenfassung", done: "Fertig", error: "Fehler",
};
const COLORS = ["--s1", "--s2", "--s3", "--s4", "--s5", "--s6"];

const state = { meetings: [], bots: [], current: null, segEls: [], pollTimer: null };
const ACTIVE_BOT = ["scheduled", "joining", "waiting_admit", "recording", "stopping"];
const BOT_STATUS = {
  scheduled: "geplant", joining: "tritt bei", waiting_admit: "wartet auf Einlass", recording: "nimmt auf",
  stopping: "beendet", completed: "fertig", stopped: "gestoppt", failed: "fehlgeschlagen", cancelled: "abgesagt",
};

// ---------------------------------------------------------------- Helfer
function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (v !== null && v !== undefined && v !== false) el.setAttribute(k, v);
  }
  for (const c of children.flat(Infinity)) if (c !== null && c !== undefined) el.append(c);
  return el;
}

function fmt(sec) {
  sec = Math.max(0, Math.floor(sec || 0));
  const hh = Math.floor(sec / 3600), mm = Math.floor((sec % 3600) / 60), ss = sec % 60;
  const p = (n) => String(n).padStart(2, "0");
  return hh ? `${hh}:${p(mm)}:${p(ss)}` : `${p(mm)}:${p(ss)}`;
}

function parseTime(str) {
  if (typeof str !== "string") return null;
  const parts = str.split(":").map(Number);
  if (parts.some(isNaN)) return null;
  return parts.reduce((acc, n) => acc * 60 + n, 0);
}

function color(speaker) {
  const n = parseInt(String(speaker).replace(/\D/g, ""), 10) || 1;
  return `var(${COLORS[(n - 1) % COLORS.length]})`;
}

function speakerName(spk) {
  return state.current?.speakers?.[spk] || spk;
}

// API-Schlüssel nur nötig, wenn die Oberfläche von einem anderen Rechner aus genutzt wird.
const KEY_STORE = "koprotokoll_api_key";
function apiKey() { try { return localStorage.getItem(KEY_STORE) || ""; } catch { return ""; } }
function withKey(url) { const k = apiKey(); return k ? `${url}${url.includes("?") ? "&" : "?"}key=${encodeURIComponent(k)}` : url; }

async function api(path, opts = {}, retry = true) {
  const headers = { ...(opts.headers || {}) };
  if (apiKey()) headers.Authorization = `Bearer ${apiKey()}`;
  const res = await fetch(path, { ...opts, headers });
  if (res.status === 401 && retry) {
    const k = prompt("Dieser Server verlangt einen API-Schlüssel (kp_…):");
    if (k) { try { localStorage.setItem(KEY_STORE, k.trim()); } catch { /* ignore */ } return api(path, opts, false); }
  }
  if (!res.ok) {
    let msg = res.statusText;
    try { msg = (await res.json()).detail || msg; } catch { /* ignore */ }
    throw new Error(msg);
  }
  return res.json();
}

const jsonOpts = (method, body) => ({
  method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
});

// Text mit [[Treffer]]-Markierungen sicher als DOM rendern.
function highlighted(text) {
  const out = [];
  text.split(/(\[\[.*?\]\])/).forEach((part) => {
    if (part.startsWith("[[") && part.endsWith("]]")) out.push(h("mark", {}, part.slice(2, -2)));
    else if (part) out.push(part);
  });
  return out;
}

function tsLink(sec, label) {
  return h("a", { class: "ts", href: "#", onclick: (e) => { e.preventDefault(); seek(sec); } }, label ?? fmt(sec));
}

// --------------------------------------------------------- Meeting-Liste
async function loadMeetings() {
  state.meetings = await api("/api/meetings");
  const list = $("#meeting-list");
  list.replaceChildren(...state.meetings.map((m) => {
    const sub = m.status === "done"
      ? `${new Date(m.created_at * 1000).toLocaleString("de-DE", { dateStyle: "short", timeStyle: "short" })} · ${fmt(m.duration)}`
      : `${STATUS[m.status] || m.status}${m.status !== "error" ? ` · ${Math.round(m.progress * 100)} %` : ""}`;
    return h("div", {
      class: "m-item" + (state.current?.id === m.id ? " active" : ""),
      onclick: () => openMeeting(m.id),
    }, h("div", { class: "t" }, m.title), h("div", { class: "s" }, sub));
  }));
  const busy = state.meetings.some((m) => !["done", "error"].includes(m.status));
  await loadBots();
  const botsBusy = state.bots.some((b) => ACTIVE_BOT.includes(b.status));
  clearTimeout(state.pollTimer);
  if (busy || botsBusy) state.pollTimer = setTimeout(refresh, botsBusy && !busy ? 3000 : 1500);
}

async function refresh() {
  await loadMeetings();
  if (state.current) {
    const m = state.meetings.find((x) => x.id === state.current.id);
    if (m && (m.status !== state.current.status || m.progress !== state.current.progress)) {
      await openMeeting(m.id, { keepTab: true });
    }
  }
}

// ------------------------------------------------------------ Meeting-Ansicht
async function openMeeting(id, { keepTab = false, seekTo = null } = {}) {
  const m = await api(`/api/meetings/${id}`);
  const changedMeeting = state.current?.id !== id;
  state.current = m;
  showView("meeting");
  const canonical = `#meeting=${id}` + (seekTo !== null ? `&t=${seekTo}` : "");
  if (location.hash !== canonical) history.replaceState(null, "", canonical);
  $("#m-title").textContent = m.title;
  const created = new Date(m.created_at * 1000).toLocaleString("de-DE", { dateStyle: "medium", timeStyle: "short" });
  const diarization = m.meta?.diarization;
  let diarizationText = null;
  if (diarization?.status === "completed") {
    diarizationText = `Sprechererkennung: ${diarization.speaker_count} Sprecher`;
    if (diarization.warning) diarizationText += ` (${diarization.warning})`;
  } else if (diarization?.status === "failed" || diarization?.status === "empty") {
    diarizationText = "Sprechererkennung fehlgeschlagen – Transkript ohne Sprecherzuordnung.";
  } else if (diarization?.status === "disabled") {
    diarizationText = "Sprechererkennung deaktiviert.";
  }
  $("#m-meta").textContent = [created, m.duration ? fmt(m.duration) : null,
    Object.keys(m.speakers).length ? `${Object.keys(m.speakers).length} Sprecher` : null,
    diarizationText,
    m.summary?.engine ? `Zusammenfassung: ${m.summary.engine}` : null, m.summary?.warning, m.summary?.external_service ? `Auswertung durch externen Dienst: ${m.summary.external_service}` : null].filter(Boolean).join(" · ");

  const done = m.status === "done";
  $("#m-progress").hidden = done;
  if (!done) {
    $("#m-progress-bar").style.transform = `scaleX(${Math.max(0, Math.min(1, m.progress))})`;
    $("#m-progress-label").textContent = m.status === "error"
      ? `Fehler: ${m.error}` : `${STATUS[m.status] || m.status} … ${Math.round(m.progress * 100)} %`;
  }
  const player = $("#player");
  if (changedMeeting) player.src = withKey(`/api/meetings/${id}/audio`);

  renderSummary();
  renderTranscript();
  renderBookmarks();
  $("#m-recap").disabled = !done;
  if (changedMeeting) await renderDraftTargets();
  if (changedMeeting) $("#ask-answer").replaceChildren();
  if (!keepTab) showTab(done ? "summary" : "transcript");
  document.querySelectorAll(".m-item").forEach((el, i) =>
    el.classList.toggle("active", state.meetings[i]?.id === id));
  if (seekTo !== null) { showTab("transcript"); seek(seekTo); }
}

function taskExportControls(meetingId) {
  const content = h("div", { class: "stack" });
  let loaded = false;
  const details = h("details", { class: "task-export", ontoggle: async () => {
    if (!details.open || loaded) return;
    loaded = true;
    content.replaceChildren(h("p", { role: "status" }, "Aufgabenlisten werden geladen …"));
    try {
      const result = await api("/api/tasks/targets");
      const status = h("p", { role: "status", class: "muted small" }, result.errors.join(" "));
      if (!result.targets.length) {
        content.replaceChildren(h("p", {}, "Keine verbundene Aufgabenliste verfügbar. Die ICS-Datei lässt sich lokal importieren."), status);
        return;
      }
      const select = h("select", { id: "task-target" }, ...result.targets.map((target) => h("option", { value: target.id }, target.label)));
      const button = h("button", { type: "button", onclick: async () => {
        button.disabled = true; status.textContent = "Aufgaben werden exportiert …";
        try {
          const report = await api(`/api/meetings/${meetingId}/tasks/export`, jsonOpts("POST", { target: select.value }));
          status.textContent = `${report.created} ${report.created === 1 ? "Aufgabe" : "Aufgaben"} angelegt, ${report.skipped} bereits vorhanden.`;
        } catch (err) { status.textContent = err.message; }
        finally { button.disabled = false; }
      } }, "In gewählte Aufgabenliste exportieren");
      content.replaceChildren(h("label", { for: "task-target" }, "Aufgabenliste"), select, button, status);
    } catch (err) {
      loaded = false;
      content.replaceChildren(h("p", { role: "alert" }, err.message),
        h("p", {}, "Zum erneuten Laden zuklappen und wieder öffnen."));
    }
  } }, h("summary", {}, "Aufgaben exportieren"),
    h("p", {}, h("a", { href: withKey(`/api/meetings/${meetingId}/aufgaben.ics`) }, "ICS-Datei mit Aufgaben herunterladen")), content);
  return details;
}

function seek(sec) {
  const p = $("#player");
  const go = () => { p.currentTime = sec; p.play().catch(() => {}); };
  if (p.readyState >= 1) go(); else p.addEventListener("loadedmetadata", go, { once: true });
}

async function renderDraftTargets() {
  const select = $("#m-recap");
  const labels = { eml: "EML-Datei herunterladen", thunderbird: "Entwurf in Thunderbird", outlook: "Entwurf in Outlook" };
  try {
    const result = await api("/api/integrations/email/drafts");
    select.replaceChildren(h("option", { value: "" }, "Als E-Mail öffnen …"),
      ...result.targets.map((target) => h("option", { value: target }, labels[target] || target)));
  } catch (err) {
    select.replaceChildren(h("option", { value: "" }, "Als E-Mail öffnen …"),
      h("option", { value: "eml" }, labels.eml));
  }
}

$("#m-recap").addEventListener("change", async (event) => {
  const target = event.target.value;
  if (!target || !state.current) return;
  const base = `/api/meetings/${state.current.id}`;
  try {
    if (target === "eml") window.location = withKey(`${base}/recap.eml`);
    else await api(`${base}/recap/draft?ziel=${encodeURIComponent(target)}`, { method: "POST" });
  } catch (err) { alert(err.message); }
  finally { event.target.value = ""; }
});

function showTab(name) {
  document.querySelectorAll(".tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  document.querySelectorAll(".tab").forEach((t) => { t.hidden = t.id !== `tab-${name}`; });
}

function renderSummary() {
  const m = state.current, s = m.summary;
  const box = $("#tab-summary");
  if (!s) {
    box.replaceChildren(h("p", { class: "muted" }, "Die Zusammenfassung erscheint, sobald die Verarbeitung abgeschlossen ist."));
    return;
  }
  const timeOf = (t) => parseTime(t);
  const left = [
    h("div", { class: "card" }, h("h3", {}, "Zusammenfassung"), h("p", {}, s.overview || "–"),
      s.keywords?.length ? h("div", { class: "chips" }, s.keywords.map((k) => h("span", { class: "chip" }, k))) : null),
  ];
  if (s.action_items?.length) {
    left.push(h("div", { class: "card" }, h("h3", {}, `Action Items (${s.action_items.length})`),
      h("ul", { class: "list" }, s.action_items.map((a) => h("li", {},
        h("input", { type: "checkbox", style: "width:auto" }),
        h("span", { class: "grow" }, a.owner ? h("b", {}, `${a.owner}: `) : null, a.task,
          a.due ? h("span", { class: "muted" }, ` (Frist: ${a.due})`) : null),
        timeOf(a.time) !== null ? tsLink(timeOf(a.time), a.time) : null))), taskExportControls(m.id)));
  }
  if (s.outline?.length) {
    left.push(h("div", { class: "card" }, h("h3", {}, "Gliederung"),
      h("ul", { class: "list" }, s.outline.map((o) => h("li", {},
        timeOf(o.time) !== null ? tsLink(timeOf(o.time), o.time) : null,
        h("div", { class: "grow" }, h("b", {}, o.topic),
          h("ul", {}, (o.notes || []).map((n) => h("li", { style: "display:list-item;border:none;padding:2px 0" }, n)))))))));
  }
  for (const [key, title] of [["decisions", "Entscheidungen"], ["questions", "Offene Fragen"]]) {
    if (s[key]?.length) {
      left.push(h("div", { class: "card" }, h("h3", {}, title),
        h("ul", { class: "list" }, s[key].map((x) => h("li", {}, x)))));
    }
  }

  const total = Object.values(m.talk_time).reduce((a, b) => a + b, 0) || 1;
  const speakers = h("div", { class: "card" }, h("h3", {}, "Sprecher & Redeanteil"),
    Object.keys(m.speakers).map((spk) => {
      const input = h("input", { value: speakerName(spk), "aria-label": `Name für ${spk}` });
      input.addEventListener("change", async () => {
        await api(`/api/meetings/${m.id}`, jsonOpts("PATCH", { speakers: { [spk]: input.value } }));
        await openMeeting(m.id, { keepTab: true });
      });
      const pct = Math.round((m.talk_time[spk] || 0) / total * 100);
      return h("div", {},
        h("div", { class: "speaker-row" }, h("span", { class: "swatch", style: `background:${color(spk)}` }),
          input, h("span", { class: "muted" }, `${pct} %`)),
        h("div", { class: "talk" }, h("div", { style: `width:${pct}%;background:${color(spk)}` })));
    }),
    h("p", { class: "muted", style: "font-size:12px" }, "Namen ändern: Feld bearbeiten und Enter drücken."));
  const tools = h("div", { class: "card" }, h("h3", {}, "Werkzeuge"),
    h("button", { onclick: async (e) => {
      e.target.disabled = true; e.target.textContent = "Erstelle …";
      try { await api(`/api/meetings/${m.id}/summarize`, { method: "POST" }); await openMeeting(m.id, { keepTab: true }); }
      catch (err) { alert(err.message); e.target.disabled = false; }
    } }, "Zusammenfassung neu erstellen"));

  box.replaceChildren(h("div", { class: "grid2" }, h("div", {}, left), h("div", {}, speakers, tools)));
}

function renderTranscript() {
  const m = state.current;
  const q = $("#t-filter").value.trim().toLowerCase();
  const box = $("#transcript");
  if (!m.segments.length) {
    box.replaceChildren(h("p", { class: "muted" }, m.status === "done" ? "Keine Sprache erkannt." : "Transkript wird erstellt …"));
    state.segEls = [];
    return;
  }
  state.segEls = m.segments.map((s) => {
    let text = [s.text];
    if (q) {
      const idx = s.text.toLowerCase().indexOf(q);
      if (idx < 0) return null;
      text = [s.text.slice(0, idx), h("mark", {}, s.text.slice(idx, idx + q.length)), s.text.slice(idx + q.length)];
    }
    const el = h("div", { class: "seg", "data-start": s.start, "data-end": s.end },
      h("div", { class: "who", style: `color:${color(s.speaker)}` }, speakerName(s.speaker), tsLink(s.start)),
      h("div", {}, text),
      h("button", { class: "mark", title: "Als Soundbite markieren", onclick: async () => {
        await api(`/api/meetings/${m.id}/bookmarks`, jsonOpts("POST",
          { start: s.start, end: s.end, kind: "soundbite", note: `${speakerName(s.speaker)}: ${s.text}` }));
        await openMeeting(m.id, { keepTab: true });
      } }, "★"));
    return el;
  }).filter(Boolean);
  box.replaceChildren(...state.segEls);
}

function renderBookmarks() {
  const m = state.current;
  const labels = { soundbite: "Soundbite", action: "Action Item", comment: "Kommentar" };
  $("#bookmarks").replaceChildren(...(m.bookmarks.length ? m.bookmarks.map((b) => h("li", {},
    tsLink(b.start), h("span", { class: "tag" }, labels[b.kind] || b.kind), h("span", { class: "grow" }, b.note),
    h("button", { title: "Entfernen", onclick: async () => {
      await api(`/api/meetings/${m.id}/bookmarks/${b.id}`, { method: "DELETE" });
      await openMeeting(m.id, { keepTab: true });
    } }, "✕"))) : [h("li", { class: "muted" }, "Noch keine Markierungen. Im Transkript auf ★ klicken oder oben eine Notiz zur Abspielposition anlegen.")]));
}

// Aktuelles Segment beim Abspielen hervorheben.
$("#player").addEventListener("timeupdate", () => {
  const t = $("#player").currentTime;
  for (const el of state.segEls) {
    const on = t >= +el.dataset.start && t < +el.dataset.end + 0.3;
    if (on && !el.classList.contains("current")) {
      el.classList.add("current");
      if (!$("#tab-transcript").hidden) el.scrollIntoView({ block: "nearest", behavior: "smooth" });
    } else if (!on) el.classList.remove("current");
  }
});

// ---------------------------------------------------------- Events
document.querySelectorAll(".tabs button").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));
$("#t-filter").addEventListener("input", renderTranscript);

$("#m-title").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); e.target.blur(); } });
$("#m-title").addEventListener("blur", async () => {
  const title = $("#m-title").textContent.trim();
  if (state.current && title && title !== state.current.title) {
    await api(`/api/meetings/${state.current.id}`, jsonOpts("PATCH", { title }));
    state.current.title = title;
    await loadMeetings();
  }
});

$("#m-export").addEventListener("change", (e) => {
  if (e.target.value && state.current) window.location = withKey(`/api/meetings/${state.current.id}/export?format=${e.target.value}`);
  e.target.value = "";
});

$("#m-delete").addEventListener("click", async () => {
  if (!state.current || !confirm(`„${state.current.title}“ wirklich löschen?`)) return;
  await api(`/api/meetings/${state.current.id}`, { method: "DELETE" });
  state.current = null;
  $("#player").removeAttribute("src");
  showView("empty");
  history.replaceState(null, "", "#");
  await loadMeetings();
});

$("#bm-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const t = $("#player").currentTime || 0;
  await api(`/api/meetings/${state.current.id}/bookmarks`, jsonOpts("POST",
    { start: t, end: t, kind: $("#bm-kind").value, note: $("#bm-note").value.trim() }));
  $("#bm-note").value = "";
  await openMeeting(state.current.id, { keepTab: true });
});

$("#ask-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const out = $("#ask-answer");
  out.replaceChildren(h("p", { class: "muted" }, "Denke nach …"));
  try {
    const r = await api(`/api/meetings/${state.current.id}/ask`, jsonOpts("POST", { question: $("#ask-q").value }));
    out.replaceChildren(h("div", { class: "card" }, h("div", {}, r.answer),
      r.hits.length ? h("p", { class: "muted", style: "font-size:13px" }, "Fundstellen: ",
        r.hits.map((x) => [tsLink(x.start), " "])) : null,
      h("p", { class: "muted", style: "font-size:12px" }, `Engine: ${r.engine}`)));
  } catch (err) { out.replaceChildren(h("p", {}, `Fehler: ${err.message}`)); }
});

// Globale Suche
let searchTimer;
$("#global-search").addEventListener("input", (e) => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(async () => {
    const q = e.target.value.trim();
    const box = $("#search-results");
    if (!q) { box.hidden = true; return; }
    const hits = await api(`/api/search?q=${encodeURIComponent(q)}`);
    box.hidden = false;
    box.replaceChildren(...(hits.length ? hits.map((x) => h("div", {
      class: "hit", onclick: () => openMeeting(x.meeting_id, { seekTo: x.start }),
    }, h("b", {}, `${x.title} · ${x.speaker_name} · ${fmt(x.start)}`), highlighted(x.snippet)))
      : [h("div", { class: "hit muted" }, "Keine Treffer")]));
  }, 200);
});

// ---------------------------------------------------------- Upload
async function uploadBlob(blob, filename, title, numSpeakers) {
  const fd = new FormData();
  fd.append("file", blob, filename);
  fd.append("title", title || "");
  if (numSpeakers) fd.append("num_speakers", numSpeakers);
  const m = await api("/api/meetings", { method: "POST", body: fd });
  await loadMeetings();
  await openMeeting(m.id);
}

$("#btn-upload").addEventListener("click", () => $("#dlg-upload").showModal());
$("#upload-form").addEventListener("submit", async (e) => {
  if (e.submitter?.value !== "ok") return;
  const file = $("#up-file").files[0];
  if (!file) return;
  try { await uploadBlob(file, file.name, $("#up-title").value, $("#up-speakers").value); }
  catch (err) { alert(`Upload fehlgeschlagen: ${err.message}`); }
  e.target.reset();
});

// ---------------------------------------------------------- Aufnahme
const rec = { recorder: null, streams: [], chunks: [], start: 0, timer: null, ctx: null, raf: null, hardware: false };

function drawCaptureLevel(levelDb) {
  const c = $("#rec-level"), g = c.getContext("2d");
  const level = Math.max(0, Math.min(1, (Number(levelDb) + 60) / 60));
  g.clearRect(0, 0, c.width, c.height);
  g.fillStyle = getComputedStyle(document.documentElement).getPropertyValue("--accent");
  g.fillRect(0, 6, c.width * level, 12);
}

async function pollCaptureLevel() {
  if (!rec.hardware) return;
  try {
    const status = await api("/api/capture");
    drawCaptureLevel(status.level_db ?? -120);
  } catch { /* Aufnahme läuft unabhängig von der Pegelanzeige weiter. */ }
  if (rec.hardware) rec.raf = setTimeout(pollCaptureLevel, 250);
}

function selectRecordingMethod() {
  const hardware = $("#rec-method").value === "hardware";
  $("#rec-device-wrap").hidden = !hardware;
  $("#rec-device-help").hidden = !hardware;
  $("#rec-source-wrap").hidden = hardware;
}

async function loadCaptureDevices() {
  const select = $("#rec-device");
  try {
    const result = await api("/api/capture/devices");
    select.replaceChildren(...result.devices.map((device) =>
      h("option", { value: device.id }, device.name)));
    if (!result.devices.length) {
      select.append(h("option", { value: "" }, "Kein Audioeingang verfügbar"));
      $("#rec-device-help").textContent = "Das Betriebssystem meldet keinen Audioeingang. Nutze eine lokale Audiodatei über Hochladen.";
    } else {
      updateCaptureHelp();
    }
  } catch (err) {
    select.replaceChildren(h("option", { value: "" }, "Audioeingänge nicht verfügbar"));
    $("#rec-device-help").textContent = `Audioeingänge konnten nicht geladen werden: ${err.message}`;
  }
}

function updateCaptureHelp() {
  const device = $("#rec-device").selectedOptions[0]?.textContent || "";
  $("#rec-device-help").textContent = device.startsWith("Systemton + Standardmikrofon")
    ? "Nimmt den hörbaren Systemton und das Standardmikrofon des Rechners als getrennte lokale Spuren auf."
    : "Für Gesprächspartner aus einem Online-Meeting müssen sie über diesen Eingang hörbar sein.";
}

$("#rec-method").addEventListener("change", selectRecordingMethod);
$("#rec-device").addEventListener("change", updateCaptureHelp);

async function startRecording() {
  if ($("#rec-method").value === "hardware") {
    const device = $("#rec-device").value;
    if (!device) throw new Error("Bitte einen Audioeingang auswählen.");
    await api("/api/capture/start", jsonOpts("POST", {
      device,
      title: $("#rec-title").value,
      num_speakers: Number($("#rec-speakers").value) || null,
      channels: 2,
      sample_rate: 48000,
    }));
    rec.hardware = true;
    rec.start = Date.now();
    rec.timer = setInterval(() => { $("#rec-time").textContent = fmt((Date.now() - rec.start) / 1000); }, 500);
    $("#rec-dot").classList.add("on");
    $("#rec-start").hidden = true;
    $("#rec-stop").hidden = false;
    $("#rec-cancel").hidden = true;
    $("#rec-method").disabled = true;
    $("#rec-device").disabled = true;
    $("#rec-stop").textContent = "Stopp & auswerten";
    pollCaptureLevel();
    return;
  }
  const mic = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
  rec.streams = [mic];
  rec.ctx = new AudioContext();
  const dest = rec.ctx.createMediaStreamDestination();
  const analyser = rec.ctx.createAnalyser();
  rec.ctx.createMediaStreamSource(mic).connect(dest);
  if ($("#rec-source").value === "mix") {
    // Online-Meeting: Ton des geteilten Tabs/Bildschirms mit aufnehmen.
    const display = await navigator.mediaDevices.getDisplayMedia({ video: true, audio: true });
    rec.streams.push(display);
    if (display.getAudioTracks().length) rec.ctx.createMediaStreamSource(display).connect(dest);
    else alert("Der geteilte Inhalt liefert keinen Ton – bitte beim Teilen „Tab-Audio teilen“ aktivieren.");
  }
  rec.ctx.createMediaStreamSource(dest.stream).connect(analyser);
  rec.chunks = [];
  rec.recorder = new MediaRecorder(dest.stream, { mimeType: MediaRecorder.isTypeSupported("audio/webm;codecs=opus") ? "audio/webm;codecs=opus" : "" });
  rec.recorder.ondataavailable = (ev) => ev.data.size && rec.chunks.push(ev.data);
  rec.recorder.start(1000);
  rec.start = Date.now();
  rec.timer = setInterval(() => { $("#rec-time").textContent = fmt((Date.now() - rec.start) / 1000); }, 500);
  $("#rec-dot").classList.add("on");
  $("#rec-start").hidden = true;
  $("#rec-stop").hidden = false;
  $("#rec-source").disabled = true;
  $("#rec-cancel").hidden = true;
  $("#rec-stop").textContent = "Stopp & auswerten";
  drawLevel(analyser);
}

function drawLevel(analyser) {
  const c = $("#rec-level"), g = c.getContext("2d"), buf = new Uint8Array(analyser.fftSize);
  const loop = () => {
    analyser.getByteTimeDomainData(buf);
    const peak = buf.reduce((m, v) => Math.max(m, Math.abs(v - 128)), 0) / 128;
    g.clearRect(0, 0, c.width, c.height);
    g.fillStyle = getComputedStyle(document.documentElement).getPropertyValue("--accent");
    g.fillRect(0, 6, c.width * Math.min(1, peak * 1.5), 12);
    rec.raf = requestAnimationFrame(loop);
  };
  loop();
}

function cleanupRecording() {
  clearInterval(rec.timer);
  clearTimeout(rec.raf);
  cancelAnimationFrame(rec.raf);
  rec.streams.forEach((s) => s.getTracks().forEach((t) => t.stop()));
  rec.ctx?.close();
  rec.recorder = null;
  rec.hardware = false;
  $("#rec-dot").classList.remove("on");
  $("#rec-start").hidden = false;
  $("#rec-stop").hidden = true;
  $("#rec-cancel").hidden = false;
  $("#rec-method").disabled = false;
  $("#rec-device").disabled = false;
  $("#rec-source").disabled = false;
  $("#rec-time").textContent = "00:00";
}

$("#btn-record").addEventListener("click", async () => {
  selectRecordingMethod();
  $("#dlg-record").showModal();
  await loadCaptureDevices();
});
$("#rec-start").addEventListener("click", () => startRecording().catch((err) => {
  cleanupRecording();
  alert(`Aufnahme nicht möglich: ${err.message}`);
}));
$("#rec-stop").addEventListener("click", () => {
  if (rec.hardware) {
    $("#rec-stop").disabled = true;
    clearInterval(rec.timer);
    rec.timer = null;
    api("/api/capture/stop", { method: "POST" }).then(async (meeting) => {
      cleanupRecording();
      $("#dlg-record").close();
      await loadMeetings();
      await openMeeting(meeting.id);
    }).catch((err) => {
      cleanupRecording();
      alert(`Aufnahme konnte nicht ausgewertet werden: ${err.message}`);
    }).finally(() => { $("#rec-stop").disabled = false; });
    return;
  }
  const r = rec.recorder;
  if (!r) return;
  r.onstop = async () => {
    const blob = new Blob(rec.chunks, { type: "audio/webm" });
    const title = $("#rec-title").value || `Aufnahme ${new Date().toLocaleString("de-DE")}`;
    cleanupRecording();
    $("#dlg-record").close();
    try { await uploadBlob(blob, "aufnahme.webm", title); } catch (err) { alert(`Upload fehlgeschlagen: ${err.message}`); }
  };
  r.stop();
});
$("#dlg-record").addEventListener("close", () => { if (rec.recorder) { rec.recorder.onstop = null; rec.recorder.stop(); cleanupRecording(); } });
$("#dlg-record").addEventListener("cancel", (e) => { if (rec.hardware || rec.recorder) e.preventDefault(); });

// ---------------------------------------------------------- Ansichten
function showView(name) {
  for (const v of ["empty", "meeting", "integrations"]) $(`#${v}`).hidden = v !== name;
  $("#btn-integrations").classList.toggle("active", name === "integrations");
  if (name !== "meeting") document.querySelectorAll(".m-item").forEach((el) => el.classList.remove("active"));
}

// ---------------------------------------------------------- Bots
async function loadBots() {
  try { state.bots = await api("/api/bots"); } catch { state.bots = []; }
  const live = state.bots.filter((b) => ACTIVE_BOT.includes(b.status));
  $("#bot-live").replaceChildren(...live.map((b) => h("div", { class: "live" },
    h("span", { class: `badge ${b.status}` }, BOT_STATUS[b.status] || b.status),
    h("span", { class: "t", title: b.meeting_url }, b.title),
    h("button", { title: "Bot stoppen", onclick: () => stopBot(b.id) }, "■"))));
  if (!$("#integrations").hidden) renderBots();
}

function renderBots() {
  $("#bot-list").replaceChildren(...state.bots.slice(0, 15).map((b) => h("li", {},
    h("span", { class: `badge ${b.status}` }, BOT_STATUS[b.status] || b.status),
    h("span", { class: "grow" }, h("b", {}, b.title), " ",
      h("span", { class: "muted small" }, new Date(b.join_at * 1000).toLocaleString("de-DE", { dateStyle: "short", timeStyle: "short" })),
      b.error ? h("div", { class: "muted small" }, b.error) : null),
    b.meeting_id ? h("a", { class: "ts", href: `#meeting=${b.meeting_id}` }, "Öffnen") : null,
    ACTIVE_BOT.includes(b.status) ? h("button", { onclick: () => stopBot(b.id) }, b.status === "scheduled" ? "Absagen" : "Stoppen") : null)));
}

async function sendBot(url, title, joinAt) {
  const body = { meeting_url: url.trim(), title: title || null };
  if (joinAt) body.join_at = joinAt;
  await api("/api/bots", jsonOpts("POST", body));
  await loadMeetings();
}

async function stopBot(id) {
  await api(`/api/bots/${id}`, { method: "DELETE" });
  await loadMeetings();
}

$("#bot-quick").addEventListener("submit", async (e) => {
  e.preventDefault();
  const url = $("#bot-quick-url").value.trim();
  if (!url) return;
  try { await sendBot(url); $("#bot-quick-url").value = ""; } catch (err) { alert(err.message); }
});

$("#bot-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const at = $("#bot-at").value ? new Date($("#bot-at").value).getTime() / 1000 : null;
  try { await sendBot($("#bot-url").value, $("#bot-title").value, at); e.target.reset(); } catch (err) { alert(err.message); }
});

// ---------------------------------------------------------- Integrationen
async function openIntegrations() {
  showView("integrations");
  // pushState statt replaceState: Die Zurück-Taste des Browsers führt zurück (route() bei hashchange).
  if (location.hash.split("?")[0] !== "#integrations") history.pushState(null, "", "#integrations");
  const params = new URLSearchParams(location.hash.split("?")[1] || "");
  const err = params.get("google_error") || params.get("microsoft_error");
  if (err) alert(`Anmeldung abgebrochen: ${err}`);
  renderBots();
  await Promise.all([renderGoogle(), renderMicrosoft(), renderEmail(), renderSources(), renderKeys(), renderHooks()]);
}

async function renderEmail() {
  const box = $("#email-box");
  box.replaceChildren(h("p", { class: "muted" }, "E-Mail-Einstellungen werden geladen …"));
  try {
    const config = await api("/api/integrations/email");
    const smtp = config.smtp;
    const provider = h("select", { id: "mail-provider" },
      ...[["gmail", "Gmail"], ["microsoft", "Microsoft 365 / Outlook.com"], ["smtp", "SMTP"], ["none", "Kein Versand"]].map(([value, label]) =>
        h("option", { value, selected: config.provider === value }, label)));
    const field = (id, value, type = "text") => h("input", { id, type, value });
    const host = field("mail-host", smtp.host);
    const port = field("mail-port", smtp.port, "number");
    port.min = "1"; port.max = "65535";
    const user = field("mail-user", smtp.user);
    const sender = field("mail-sender", smtp.sender, "email");
    const password = field("mail-password", "", "password");
    password.autocomplete = "new-password";
    password.placeholder = smtp.password_set ? "Gespeichert; leer lassen zum Beibehalten" : "App-Passwort";
    const clear = h("input", { type: "checkbox" });
    const tls = h("select", { id: "mail-tls" },
      ...[["starttls", "STARTTLS"], ["ssl", "SSL/TLS"], ["none", "Ohne TLS (nur lokaler Server)"]].map(([value, label]) =>
        h("option", { value, selected: smtp.tls === value }, label)));
    const send = h("input", { type: "checkbox" }); send.checked = config.send_recap;
    const attendees = h("input", { type: "checkbox" }); attendees.checked = config.recap_to_attendees;
    const status = h("p", { role: "status", class: "muted small" });
    const fields = h("div", { class: "stack" },
      h("label", {}, "SMTP-Server", host), h("label", {}, "Port", port),
      h("label", { for: "mail-tls" }, "Verschlüsselung"), tls, h("label", {}, "Benutzername", user),
      h("label", {}, "App-Passwort", password),
      h("label", { class: "row small" }, clear, "Gespeichertes Passwort löschen"),
      h("label", {}, "Eigene Absenderadresse", sender));
    const updateFields = () => {
      fields.hidden = provider.value !== "smtp";
      host.required = !fields.hidden; port.required = !fields.hidden; sender.required = !fields.hidden;
      const preferences = config.provider_settings[provider.value];
      send.checked = preferences.send_recap; attendees.checked = preferences.recap_to_attendees;
      send.disabled = provider.value === "none"; attendees.disabled = provider.value === "none";
    };
    updateFields();
    provider.addEventListener("change", updateFields);
    const save = h("button", { class: "primary", type: "submit" }, "E-Mail-Einstellungen speichern");
    const test = h("button", { type: "button", onclick: async () => {
      test.disabled = true; status.textContent = "Test-Mail wird gesendet …";
      try { await api("/api/integrations/email/test", { method: "POST" }); status.textContent = "Test-Mail an die gespeicherte eigene Adresse gesendet."; }
      catch (err) { status.textContent = err.message; }
      finally { test.disabled = false; }
    } }, "Test-Mail an mich");
    const form = h("form", { id: "mail-form", class: "stack", onsubmit: async (event) => {
      event.preventDefault(); save.disabled = true; status.textContent = "Einstellungen werden gespeichert …";
      const values = { provider: provider.value, send_recap: send.checked, recap_to_attendees: attendees.checked };
      if (provider.value === "smtp") Object.assign(values, {
        host: host.value.trim(), port: Number(port.value), user: user.value.trim(),
        sender: sender.value.trim(), tls: tls.value,
        ...(password.value || clear.checked ? { password: clear.checked ? "" : password.value } : {}),
      });
      try {
        Object.assign(config, await api("/api/integrations/email", jsonOpts("PUT", values)));
        password.value = ""; clear.checked = false;
        password.placeholder = config.smtp.password_set ? "Gespeichert; leer lassen zum Beibehalten" : "App-Passwort";
        status.textContent = "E-Mail-Einstellungen gespeichert.";
      }
      catch (err) { status.textContent = err.message; }
      finally { save.disabled = false; }
    } }, h("label", { for: "mail-provider" }, "Versandweg"), provider, fields,
      h("label", { class: "row small" }, send, "Zusammenfassung nach Bot-Meetings an mich senden"),
      h("label", { class: "row small" }, attendees, "Zusätzlich an alle Teilnehmenden senden"),
      h("p", { class: "muted small" }, "Passwörter liegen lokal in der Datenbank. Umgebungsvariablen haben Vorrang. Vor einer Test-Mail die Einstellungen speichern."),
      h("div", { class: "row wrap" }, save, test), status);
    box.replaceChildren(form);
  } catch (err) {
    box.replaceChildren(h("p", { role: "alert" }, err.message),
      h("button", { onclick: renderEmail }, "Erneut laden"));
  }
}

// Integrationen schließen: zurück zum zuletzt geöffneten Meeting, sonst zur Übersicht.
async function closeIntegrations() {
  if (state.current) {
    await openMeeting(state.current.id, { keepTab: true }).catch(() => showView("meeting"));
    return;
  }
  showView("empty");
  history.replaceState(null, "", "#");
  await renderUpcoming();
}

async function renderGoogle() {
  const g = await api("/api/integrations/google");
  const box = $("#google-box");
  if (!g.configured) {
    const id = h("input", { placeholder: "Client-ID" }), secret = h("input", { placeholder: "Client-Secret", type: "password" });
    box.replaceChildren(
      h("p", { class: "muted small" }, "1. In der Google Cloud Console Calendar- und Gmail-API aktivieren. 2. OAuth-Client vom Typ „Webanwendung“ anlegen mit Weiterleitungs-URI:"),
      h("pre", {}, g.redirect_uri),
      h("div", { class: "stack" }, id, secret, h("button", { class: "primary", onclick: async () => {
        await api("/api/integrations/google/client", jsonOpts("PUT", { client_id: id.value, client_secret: secret.value }));
        renderGoogle();
      } }, "Speichern")));
    return;
  }
  if (!g.connected) {
    box.replaceChildren(
      h("p", { class: "muted small" }, "Verbinde dein Google-Konto für Kalender-Meetings und Gmail. Den Versand von Zusammenfassungen stellst du im Bereich E-Mail ein."),
      h("button", { class: "primary", onclick: () => { window.location = withKey("/api/integrations/google/connect"); } }, "Mit Google verbinden"));
    return;
  }
  const toggle = (key, label) => {
    const cb = h("input", { type: "checkbox" });
    cb.checked = !!g.settings[key];
    cb.addEventListener("change", () => api("/api/integrations/google/settings", jsonOpts("PATCH", { [key]: cb.checked })));
    return h("label", { class: "switch" }, cb, label);
  };
  const sync = g.last_sync?.at ? `Letzter Abgleich: ${new Date(g.last_sync.at * 1000).toLocaleTimeString("de-DE")}${g.last_sync.error ? ` – Fehler: ${g.last_sync.error}` : ""}` : "";
  box.replaceChildren(
    h("p", {}, "Verbunden als ", h("b", {}, g.email || "?")),
    toggle("auto_join", "Bot tritt Kalender-Meetings mit Meet-Link automatisch bei"),
    h("p", { class: "muted small" }, "Zusammenfassungen und Empfänger im Bereich E-Mail einstellen."),
    sync ? h("p", { class: "muted small" }, sync) : null,
    h("div", { class: "row" },
      h("button", { onclick: async (e) => {
        e.target.disabled = true;
        try { const r = await api("/api/integrations/google/sync", { method: "POST" }); alert(`${r.created.length} neue(r) Bot(s) eingeplant.`); await loadMeetings(); }
        catch (err) { alert(err.message); }
        e.target.disabled = false; renderGoogle();
      } }, "Kalender jetzt prüfen"),
      h("button", { class: "danger", onclick: async () => {
        if (!confirm("Google-Verbindung trennen?")) return;
        await api("/api/integrations/google", { method: "DELETE" }); renderGoogle();
      } }, "Trennen")));
}

function msAdvanced(g, open) {
  const id = h("input", { id: "ms-client-id", placeholder: "Client-ID" }),
    secret = h("input", { id: "ms-client-secret", placeholder: "Client-Secret (optional für Weiterleitung)", type: "password" });
  return h("details", open ? { open: true } : {},
    h("summary", { class: "muted small" }, "Erweiterte Einstellungen (Client-ID / Secret)"),
    h("p", { class: "muted small" }, "Für die Anmeldung mit Code reicht die Client-ID. In Entra unter „Authentifizierung → Erweiterte Einstellungen“ öffentliche Clientflows erlauben; eine Weiterleitungs-URI und ein Secret sind dafür nicht nötig. Die Anleitung darunter beschreibt den weiterhin verfügbaren Weg mit Weiterleitung."),
    h("p", { class: "muted small" }, "Einmalige Einrichtung im Azure-Portal (portal.azure.com) – die Anmeldung dort geht auch mit einem privaten Microsoft- oder GitHub-Konto, ein Abonnement ist nicht nötig:"),
    h("ol", { class: "muted small" },
      h("li", {}, "In der Suche nach „App-Registrierungen“ suchen → „Neue Registrierung“, Name z. B. „Koprotokoll“."),
      h("li", {}, h("b", {}, "Unterstützte Kontotypen: „Any Entra ID Tenant + Personal Microsoft accounts“ (der unterste Eintrag)"),
        " – nur damit sind auch Privatkonten (outlook.com, live.com) erlaubt. Falsch gewählt erscheint später der Fehler „Mandant … nicht vorhanden“."),
      h("li", {}, "Umleitungs-URI: Plattform „Web“ auswählen und diese Adresse eintragen:"),
      h("pre", {}, g.redirect_uri),
      h("li", {}, "„Registrieren“ → danach die ", h("b", {}, "Client-ID („Application (client) ID“)"),
        " aus der Übersicht kopieren und unten eintragen."),
      h("li", {}, "Client-Secret anlegen: „Certificates & secrets“ → „New client secret“ → nach dem Anlegen den ",
        h("b", {}, "Wert aus der Spalte „Value“ sofort kopieren"),
        " – in der Tabelle stehen „Secret ID“ und „Value“ nebeneinander, gebraucht wird der Wert. Er wird nur einmal angezeigt; bei Verlust hilft nur ein neues Secret."),
      h("li", {}, "„API permissions“ → „Add a permission“ → „Microsoft Graph“ → „Delegated permissions“ → ",
        h("code", {}, "Calendars.Read"), " und ", h("code", {}, "Mail.Send"), " hinzufügen."),
      h("li", {}, "Hier unten Client-ID und Secret eintragen und speichern, dann „Mit Microsoft verbinden“.")),
    h("div", { class: "stack" }, h("label", { for: "ms-client-id" }, "Microsoft Client-ID"), id,
      h("label", { for: "ms-client-secret" }, "Client-Secret für Weiterleitung (optional)"), secret, h("button", { onclick: async () => {
      await api("/api/integrations/microsoft/client", jsonOpts("PUT", { client_id: id.value, client_secret: secret.value }));
      renderMicrosoft();
    } }, "Speichern")));
}

async function startMicrosoftDevice(tasks = false) {
  const box = $("#microsoft-box");
  box.replaceChildren(h("p", { role: "status" }, "Anmeldecode wird angefordert …"));
  try {
    const device = await api("/api/integrations/microsoft/device" + (tasks === true ? "?tasks=1" : ""), { method: "POST" });
    const status = h("p", { role: "status" }, "Nach der Bestätigung bei Microsoft die Anmeldung hier prüfen.");
    const button = h("button", { onclick: async () => {
      button.disabled = true;
      try {
        const result = await api("/api/integrations/microsoft/device/poll", { method: "POST" });
        if (result.status === "connected") { await renderMicrosoft(); await renderEmail(); }
        else status.textContent = `Noch nicht bestätigt. Frühestens in ${result.interval} Sekunden erneut prüfen.`;
      } catch (err) { status.textContent = err.message; }
      finally { button.disabled = false; }
    } }, "Anmeldung prüfen");
    box.replaceChildren(h("p", {}, "Diesen Code bei Microsoft eingeben: ", h("strong", {}, device.user_code)),
      h("a", { href: device.verification_uri, target: "_blank", rel: "noopener noreferrer" }, "Microsoft-Anmeldeseite öffnen"),
      h("p", { class: "muted small" }, `Der Code ist ${Math.ceil(device.expires_in / 60)} Minuten gültig.`), status,
      h("div", { class: "row" }, button, h("button", { onclick: renderMicrosoft }, "Zurück")));
  } catch (err) {
    box.replaceChildren(h("p", { role: "alert" }, err.message), h("button", { onclick: renderMicrosoft }, "Zurück"));
  }
}

async function renderMicrosoft() {
  const g = await api("/api/integrations/microsoft");
  const box = $("#microsoft-box");
  if (!g.configured) {
    box.replaceChildren(
      h("p", { class: "muted small" }, "Für die Anbindung wird eine einmalige Azure-App-Registrierung benötigt – die Zugangsdaten kommen unten unter „Erweiterte Einstellungen“."),
      msAdvanced(g, true));
    return;
  }
  if (!g.connected) {
    box.replaceChildren(
      h("p", { class: "muted small" }, "Verbinde dein Microsoft-/Outlook-Konto für Kalender-Meetings. Den Mailversand wählst du anschließend unter „E-Mail“ und aktivierst ihn dort separat."),
      h("button", { class: "primary", onclick: startMicrosoftDevice }, "Mit Microsoft per Code anmelden"),
      g.redirect_available ? h("button", { onclick: () => { window.location = withKey("/api/integrations/microsoft/connect"); } }, "Anmeldung mit Weiterleitung") : null,
      msAdvanced(g, false));
    return;
  }
  const toggle = (key, label) => {
    const cb = h("input", { type: "checkbox" });
    cb.checked = !!g.settings[key];
    cb.addEventListener("change", () => api("/api/integrations/microsoft/settings", jsonOpts("PATCH", { [key]: cb.checked })));
    return h("label", { class: "switch" }, cb, label);
  };
  const sync = g.last_sync?.at ? `Letzter Abgleich: ${new Date(g.last_sync.at * 1000).toLocaleTimeString("de-DE")}${g.last_sync.error ? ` – Fehler: ${g.last_sync.error}` : ""}` : "";
  box.replaceChildren(...[
    h("p", {}, "Verbunden als ", h("b", {}, g.email || "?")),
    !g.tasks_enabled ? h("button", { onclick: () => startMicrosoftDevice(true) }, "Microsoft-Aufgabenrechte freigeben") : null,
    toggle("auto_join", "Bot tritt unterstützten Kalender-Meetings automatisch bei"),
    sync ? h("p", { class: "muted small" }, sync) : null,
    h("div", { class: "row" },
      h("button", { class: "danger", onclick: async () => {
        if (!confirm("Microsoft-Verbindung trennen?")) return;
        await api("/api/integrations/microsoft", { method: "DELETE" }); renderMicrosoft();
      } }, "Trennen")),
    msAdvanced(g, false),
  ].filter(Boolean));
}

// ---------------------------------------------------------- Kalender-Quellen
const KIND_LABEL = { ics: "ICS", caldav: "CalDAV", outlook: "Outlook (lokal)" };

async function renderSources() {
  const [sources, presets, capabilities] = await Promise.all([
    api("/api/calendars").catch(() => []),
    api("/api/calendars/presets").catch(() => []),
    api("/api/calendars/capabilities").catch(() => ({ outlook: false })),
  ]);
  $("#source-outlook").hidden = !capabilities.outlook || sources.some((s) => s.kind === "outlook");
  const select = $("#src-provider");
  if (select && !select.options.length) {
    select.replaceChildren(...presets.map((p) => h("option", { value: p.id }, p.label)));
    select.addEventListener("change", () => {
      const preset = presets.find((p) => p.id === select.value);
      $("#src-user").placeholder = (preset && preset.hint) || "Benutzername / E-Mail";
    });
    select.dispatchEvent(new Event("change"));
  }
  $("#source-list").replaceChildren(...sources.map((s) => h("li", {},
    h("span", { class: "grow" }, h("b", {}, s.label), " ",
      h("span", { class: "muted small" },
        `${KIND_LABEL[s.kind] || s.kind}${s.password_set ? " · Passwort gesetzt" : ""}`)),
    h("button", { title: s.enabled ? "Deaktivieren" : "Aktivieren",
      onclick: async () => {
        await api(`/api/calendars/${s.id}`, jsonOpts("PATCH", { enabled: !s.enabled }));
        renderSources();
      } }, s.enabled ? "An" : "Aus"),
    h("button", { title: "Quelle entfernen",
      onclick: async () => {
        if (confirm(`Quelle „${s.label}“ entfernen?`)) {
          await api(`/api/calendars/${s.id}`, { method: "DELETE" });
          renderSources();
        }
      } }, "✕"))));
}

$("#source-outlook").addEventListener("click", async (event) => {
  event.target.disabled = true;
  try { await api("/api/calendars", jsonOpts("POST", { kind: "outlook" })); await renderSources(); }
  catch (err) { $("#src-msg").textContent = err.message; }
  finally { event.target.disabled = false; }
});

$("#caldav-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const msg = $("#src-msg");
  msg.textContent = "";
  try {
    await api("/api/calendars", jsonOpts("POST", {
      kind: "caldav", provider: $("#src-provider").value,
      username: $("#src-user").value.trim(), password: $("#src-pass").value,
      label: $("#src-label").value.trim(),
      server_url: $("#src-server").value.trim(), url: $("#src-calurl").value.trim(),
      own_email: $("#src-own-email").value.trim(),
    }));
    e.target.reset();
    $("#src-advanced").open = false;
    renderSources();
  } catch (err) {
    // Stufenmodell: Fehlermeldung zeigen, manuelle Daten in den erweiterten Einstellungen anbieten
    msg.textContent = err.message;
    $("#src-advanced").open = true;
  }
});

$("#ics-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const msg = $("#src-msg");
  msg.textContent = "";
  try {
    await api("/api/calendars", jsonOpts("POST", {
      kind: "ics", label: $("#ics-label").value.trim(), url: $("#ics-url").value.trim(),
    }));
    e.target.reset();
    renderSources();
  } catch (err) { msg.textContent = err.message; }
});

async function renderUpcoming() {
  const events = await api("/api/upcoming");
  const list = $("#upcoming-list");
  if (!events.length) {
    list.replaceChildren(h("li", { class: "muted small" },
      "Keine Termine in den nächsten 7 Tagen. Verbundene Kalender werden automatisch angezeigt."));
    return;
  }
  list.replaceChildren(...events.map((e) => {
    const when = new Date(e.start * 1000).toLocaleString("de-DE",
      { weekday: "short", day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
    return h("li", {},
      h("span", { class: "grow" },
        h("b", {}, e.title),
        h("div", { class: "muted small" }, `${when} · ${e.label}`),
        h("div", { class: "muted small" },
          e.will_join ? `Bot wird eingeplant (${e.platform})` : e.reason,
          e.meeting_url ? " · " : "",
          e.meeting_url ? h("a", { href: e.meeting_url, target: "_blank", rel: "noreferrer" }, "Link") : null)));
  }));
}

async function renderKeys() {
  const keys = await api("/api/keys");
  $("#key-list").replaceChildren(...keys.map((k) => h("li", {},
    h("span", { class: "grow" }, h("b", {}, k.name), " ", h("code", {}, `${k.prefix}…`),
      h("div", { class: "muted small" }, k.last_used ? `zuletzt genutzt ${new Date(k.last_used * 1000).toLocaleString("de-DE")}` : "noch nie genutzt")),
    h("button", { onclick: async () => { if (confirm(`Schlüssel „${k.name}“ widerrufen?`)) { await api(`/api/keys/${k.id}`, { method: "DELETE" }); renderKeys(); } } }, "Widerrufen"))));
}

$("#key-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const k = await api("/api/keys", jsonOpts("POST", { name: $("#key-name").value }));
  $("#key-new").replaceChildren(h("div", { class: "secret" }, "Neuer Schlüssel (wird nur jetzt angezeigt): ", h("code", {}, k.key)));
  e.target.reset();
  renderKeys();
});

async function renderHooks() {
  const hooks = await api("/api/webhooks");
  $("#hook-list").replaceChildren(...hooks.map((w) => h("li", {},
    h("span", { class: "grow" }, h("code", {}, w.url), h("div", { class: "muted small" }, w.events.join(", "))),
    h("button", { onclick: async () => { const r = await api(`/api/webhooks/${w.id}/test`, { method: "POST" }); alert(r.delivered ? "Test zugestellt." : "Zustellung fehlgeschlagen."); } }, "Testen"),
    h("button", { onclick: async () => { await api(`/api/webhooks/${w.id}`, { method: "DELETE" }); renderHooks(); } }, "✕"))));
}

$("#hook-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    const w = await api("/api/webhooks", jsonOpts("POST", { url: $("#hook-url").value, events: [$("#hook-events").value] }));
    $("#hook-new").replaceChildren(h("div", { class: "secret" }, "Signatur-Secret (wird nur jetzt angezeigt): ", h("code", {}, w.secret)));
    e.target.reset();
    renderHooks();
  } catch (err) { alert(err.message); }
});

$("#btn-integrations").addEventListener("click", () => {
  if ($("#integrations").hidden) openIntegrations();
  else closeIntegrations();
});

$("#btn-close-integrations").addEventListener("click", () => closeIntegrations());

async function route() {
  const hash = location.hash;
  const m = hash.match(/^#meeting=(\d+)/);
  if (m) {
    const raw = new URLSearchParams(hash.slice(1)).get("t");
    const timestamp = raw === null ? null : Number(raw);
    await openMeeting(+m[1], { seekTo: timestamp !== null && Number.isFinite(timestamp) && timestamp >= 0 ? timestamp : null }).catch(() => showView("empty"));
  }
  else if (hash.startsWith("#integrations")) await openIntegrations();
  else { showView("empty"); await renderUpcoming(); }
}
window.addEventListener("hashchange", () => route());

// ---------------------------------------------------------- Start
(async () => {
  try {
    const st = await api("/api/status");
    $("#engine").textContent = `Parakeet · ${st.llm.available ? "LLM" : "ohne LLM"}`;
    $("#engine").title = `ASR: ${st.asr_model}\nSprechererkennung: ${st.diarization ? "an" : "aus"}\nLLM: ${st.llm.available ? st.llm.model : "nicht erreichbar (" + st.llm.url + ")"}`;
  } catch { /* Server-Status optional */ }
  await loadMeetings();
  await route();
})();

async function renderLocalModel() {
  const box = $("#local-model");
  try {
    const config = await api("/api/llm");
    const model = h("select", { "aria-label": "Lokales Textmodell" },
      h("option", { value: "" }, "Bitte Modell auswählen …"), ...config.models.map(id => h("option", { value: id }, id)));
    model.value = config.models.includes(config.model) ? config.model : "";
    const limit = h("input", { type: "number", min: "1", max: "32768", "aria-label": "Maximale Ausgabetokens", placeholder: "Serverstandard" });
    limit.value = config.max_tokens || "";
    const thinking = h("select", { "aria-label": "Thinking" }, ...[ ["", "Modellstandard"], ["none", "Aus"], ["low", "Niedrig"], ["medium", "Mittel"], ["high", "Hoch"] ].map(([value, label]) => h("option", { value }, label)));
    thinking.value = config.reasoning_effort || "";
    const compatible = h("input", { type: "checkbox" });
    const message = h("p", { "aria-live": "polite" }, config.message);
    const save = h("button", { type: "button" }, "Modellauswahl speichern");
    save.onclick = async () => {
      try {
        await api("/api/llm", jsonOpts("PUT", { model: model.value, max_tokens: limit.value ? Number(limit.value) : null, reasoning_effort: thinking.value || null, compatible_parameters: compatible.checked }));
        await renderLocalModel();
      } catch (err) { message.textContent = err.message; }
    };
    const provider = h("select", { "aria-label": "Anbieter" }, ...[["ollama", "Ollama lokal"], ["openai", "OpenAI"], ["other", "Anderer OpenAI-kompatibler Server"]].map(([value, label]) => h("option", { value }, label)));
    provider.value = config.provider;
    const url = h("input", { "aria-label": "Basis-URL", value: config.url });
    const name = h("input", { "aria-label": "Modellname", value: config.model });
    const key = h("input", { type: "password", "aria-label": "API-Schlüssel", autocomplete: "new-password", placeholder: config.api_key_set ? "Gesetzt – leer lassen zum Beibehalten" : "Nicht gesetzt" });
    const remove = h("input", { type: "checkbox" });
    const timeout = h("input", { type: "number", "aria-label": "Timeout in Sekunden", value: config.timeout, min: "0.01", max: "3600", step: "any" });
    const confirm = h("input", { type: "checkbox" });
    provider.onchange = () => { if (provider.value === "openai") url.value = "https://api.openai.com/v1"; if (provider.value === "ollama") url.value = "http://localhost:11434/v1"; confirm.checked = false; };
    url.oninput = () => { confirm.checked = false; };
    model.onchange = () => { name.value = model.value; };
    const providerSave = h("button", { type: "button" }, "Anbieter speichern");
    providerSave.onclick = async () => {
      try {
        await api("/api/llm/provider", jsonOpts("PUT", { provider: provider.value, url: url.value, model: name.value, timeout: Number(timeout.value), api_key: remove.checked ? "" : key.value || null, external_confirmed: confirm.checked }));
        key.value = "";
        await renderLocalModel();
      } catch (err) { key.value = ""; message.textContent = err.message; }
    };
    const test = h("button", { type: "button" }, "Gespeicherten Anbieter testen");
    test.onclick = async () => { try { const result = await api("/api/llm/test", { method: "POST" }); message.textContent = `Server: ${result.server_reachable ? "erreichbar" : "nicht erreichbar"} · Modell: ${result.model_present ? "vorhanden" : "fehlt"} · Probeaufruf: ${result.inference}`; } catch (err) { message.textContent = err.message; } };
    box.replaceChildren(h("h2", {}, "Sprachmodell"),
      h("p", {}, config.external ? `Externer Dienst: ${config.url} (${config.external_confirmed ? "bestätigt" : "Übertragung gesperrt"})` : "Lokaler Dienst"),
      h("p", {}, config.env_overrides.length ? `Vom Server vorgegeben: ${config.env_overrides.join(", ")}` : "Eigene Einstellungen"),
      h("label", {}, "Anbieter ", provider), h("label", {}, "Basis-URL ", url),
      h("label", {}, "Modellname ", name), h("label", {}, "API-Schlüssel ", key),
      h("label", {}, remove, " Gespeicherten Schlüssel löschen"), h("label", {}, "Timeout in Sekunden ", timeout),
      h("label", {}, confirm, " Transkripttext wird an diesen Dienst gesendet. Ich bestätige die externe Übertragung."), providerSave, test, h("p", {}, config.url), message,
      h("p", {}, config.model_env_override ? `KOPROTOKOLL_LLM_MODEL hat Vorrang: ${config.model}` : `Aktuelle Auswahl: ${config.model}`),
      model, h("label", {}, "Maximale Ausgabetokens ", limit), h("label", {}, "Thinking ", thinking),
      h("label", {}, compatible, " Server und Modell unterstützen diese optionalen Parameter (laut Dokumentation)."), save,
      h("button", { type: "button", onclick: renderLocalModel }, "Modellliste aktualisieren"));
  } catch (err) { box.textContent = err.message; }
}
renderLocalModel();
