"use strict";

const $ = (sel) => document.querySelector(sel);
const STATUS = {
  queued: "In Warteschlange", converting: "Konvertiere Audio", transcribing: "Transkribiere (Parakeet)",
  diarizing: "Erkenne Sprecher", summarizing: "Erstelle Zusammenfassung", done: "Fertig", error: "Fehler",
};
const COLORS = ["--s1", "--s2", "--s3", "--s4", "--s5", "--s6"];

const state = { meetings: [], bots: [], current: null, segEls: [], pollTimer: null, selected: new Set() };
// Erklaerung, die am Meeting-Ergebnis und in den Einstellungen steht, wenn die Heuristik lief.
const LLM_HINT = "Diese Zusammenfassung wurde mit Regeln erstellt (ohne Sprachmodell). Ein lokales Sprachmodell schreibt Übersicht, Entscheidungen und Aufgaben – es läuft auf diesem Rechner, ca. 5 GB.";
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

function fmtBytes(bytes) {
  if (!bytes) return "";
  const units = ["Byte", "kB", "MB", "GB"];
  let value = bytes, i = 0;
  while (value >= 1024 && i < units.length - 1) { value /= 1024; i += 1; }
  return `${value.toLocaleString("de-DE", { maximumFractionDigits: i ? 1 : 0 })} ${units[i]}`;
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
  renderList();
  await loadBots();
  schedulePoll();
}

// Sortierung/Filter der Liste – als Drop-down oben in der Sidebar, damit es nicht
// dauerhaft Platz wegnimmt; die Wahl liegt lokal im Browser (localStorage).
function listPrefs() {
  try {
    return { sort: localStorage.getItem("koprotokoll-sort") || "new",
             filter: localStorage.getItem("koprotokoll-filter") || "all" };
  } catch { return { sort: "new", filter: "all" }; }
}

try {
  $("#sort-order").value = localStorage.getItem("koprotokoll-sort") || "new";
  $("#filter-status").value = localStorage.getItem("koprotokoll-filter") || "all";
} catch { /* ohne Browserspeicher bleiben die Vorgabewerte stehen */ }
$("#sort-order").addEventListener("change", (e) => {
  try { localStorage.setItem("koprotokoll-sort", e.target.value); } catch { /* optional */ }
  renderList();
});
$("#filter-status").addEventListener("change", (e) => {
  try { localStorage.setItem("koprotokoll-filter", e.target.value); } catch { /* optional */ }
  renderList();
});

function normText(s) {
  return (s || "").toLowerCase().replace(/[^\p{L}\p{N}]+/gu, " ").trim();
}

// Vorschau nur anzeigen, wenn sie den Titel nicht nur wiederholt (Titel = erste Wörter).
function previewOf(m) {
  const p = normText(m.preview), t = normText(m.title);
  if (!p || !t || t.includes(p) || p.includes(t)) return "";
  return m.preview;
}

function visibleMeetings() {
  const { sort, filter } = listPrefs();
  const view = state.meetings.filter((m) =>
    filter === "all" ? true
      : filter === "processing" ? !["done", "error"].includes(m.status)
      : m.status === filter);
  const by = {
    new: (a, b) => b.created_at - a.created_at,
    old: (a, b) => a.created_at - b.created_at,
    title: (a, b) => a.title.localeCompare(b.title, "de"),
    duration: (a, b) => (b.duration || 0) - (a.duration || 0),
  }[sort] || ((a, b) => b.created_at - a.created_at);
  return [...view].sort(by);
}

function renderList() {
  const list = $("#meeting-list");
  const items = visibleMeetings().map((m) => {
    const when = new Date(m.created_at * 1000);
    const dateFull = when.toLocaleString("de-DE", { dateStyle: "short", timeStyle: "short" });
    const dateShort = when.toLocaleDateString("de-DE", { day: "2-digit", month: "2-digit" });
    const info = m.status === "done"
      ? fmt(m.duration)
      : `${STATUS[m.status] || m.status}${m.status !== "error" ? ` · ${Math.round(m.progress * 100)} %` : ""}`;
    const preview = previewOf(m);
    return h("div", {
      class: "m-item" + (state.current?.id === m.id ? " active" : "") + (state.selected.has(m.id) ? " selected" : ""),
      "data-id": String(m.id),
      onclick: (e) => {
        // Auswahlmodus (ab der ersten Markierung): Tippen irgendwo auf die Zeile
        // schaltet die Markierung um, statt das Meeting zu öffnen.
        if (state.selected.size > 0) toggleSelect(m.id, e.currentTarget);
        else openMeeting(m.id);
      },
    },
      h("input", {
        type: "checkbox", class: "m-check", title: "Zum Sammellöschen markieren",
        checked: state.selected.has(m.id) || null,
        onclick: (e) => e.stopPropagation(),
        onchange: (e) => toggleSelect(m.id, e.target),
      }),
      h("div", { class: "m-body" },
        h("div", { class: "t" }, m.title),
        preview ? h("div", { class: "p", title: m.preview }, `„${preview}“`) : null,
        h("div", { class: "s" },
          h("span", { class: "s-date" }, `${dateFull} · `),
          h("span", { class: "s-info" }, info))),
      h("span", { class: "m-date", title: dateFull }, dateShort));
  });
  if (!items.length) items.push(h("div", { class: "muted small", style: "padding:8px 10px" },
    state.meetings.length
      ? "Keine Meetings passen zum Filter."
      : "Noch keine Meetings – oben „Aufnehmen“, „Hochladen“ oder einen Meet-Link für den Bot nutzen."));
  list.replaceChildren(...items);
  renderBulkBar();
}

// Liste aktualisieren sich von selbst: im Hintergrund-Tab pausiert, beim
// Sichtbarwerden sofort; im Leerlauf alle 5 s, während Verarbeitung/Bot aktiv 1,5–3 s.
function schedulePoll() {
  clearTimeout(state.pollTimer);
  if (document.hidden) return;
  const busy = state.meetings.some((m) => !["done", "error"].includes(m.status));
  const botsBusy = state.bots.some((b) => ACTIVE_BOT.includes(b.status));
  state.pollTimer = setTimeout(refresh, busy ? 1500 : botsBusy ? 3000 : 5000);
}

document.addEventListener("visibilitychange", () => {
  if (!document.hidden) refresh().catch(() => { /* Server evtl. nicht erreichbar */ });
});

function renderBulkBar() {
  const n = state.selected.size;
  $("#m-bulk").hidden = n === 0;
  $("#m-bulk-count").textContent = n ? `${n} ausgewählt` : "";
}

// Markierung eines Listeneintrags umschalten – egal ob über das Kästchen oder,
// im Auswahlmodus, über einen Tipp irgendwo auf die Zeile.
function toggleSelect(id, el) {
  if (state.selected.has(id)) state.selected.delete(id); else state.selected.add(id);
  const item = el?.closest?.(".m-item");
  if (item) {
    item.classList.toggle("selected", state.selected.has(id));
    const check = item.querySelector(".m-check");
    if (check) check.checked = state.selected.has(id);
  }
  renderBulkBar();
}

$("#m-bulk-done").addEventListener("click", async () => {
  state.selected.clear();
  renderBulkBar();
  await loadMeetings();
});

$("#m-bulk-delete").addEventListener("click", async () => {
  const ids = [...state.selected];
  if (!ids.length) return;
  if (!confirm(`${ids.length} Aufnahme${ids.length > 1 ? "n" : ""} wirklich löschen?`)) return;
  await api("/api/meetings/bulk-delete", jsonOpts("POST", { ids }));
  if (state.current && ids.includes(state.current.id)) {
    state.current = null;
    $("#player").removeAttribute("src");
    showView("empty");
    history.replaceState(null, "", "#");
  }
  state.selected.clear();
  await loadMeetings();
});

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
  } else if (diarization?.status === "fixed") {
    diarizationText = diarization.source === "manual" ? "Sprecherzuordnung: auf eine Person korrigiert" : "Sprecherzahl: 1 vorgegeben";
  } else if (diarization?.status === "failed" || diarization?.status === "empty") {
    diarizationText = "Sprechererkennung fehlgeschlagen – Transkript ohne Sprecherzuordnung.";
  } else if (diarization?.status === "disabled") {
    diarizationText = "Sprechererkennung deaktiviert.";
  }
  const own = diarization?.own_voice;
  const ownText = own?.status === "collapsed"
    ? `Eigene Stimme erkannt (${Math.round((own.share || 0) * 100)} % Redeanteil) – als „Ich“ zusammengefasst`
    : own?.status === "single" ? "Nur eine Stimme erkannt – als „Ich“ übernommen" : null;
  $("#m-meta").textContent = [created, m.duration ? fmt(m.duration) : null,
    Object.keys(m.speakers).length ? `${Object.keys(m.speakers).length} Sprecher` : null,
    m.meta?.asr_language ? `Transkriptionsmodus: ${LANGUAGE_LABELS[m.meta.asr_language] || m.meta.asr_language}` : null,
    m.meta?.stop_reason === "silence" ? "Automatisch nach 5 Minuten ohne Sprache gestoppt" : null,
    diarizationText, ownText,
    m.summary?.engine ? `Zusammenfassung: ${m.summary.engine}` : null, m.summary?.warning, m.summary?.external_service ? `Auswertung durch externen Dienst: ${m.summary.external_service}` : null].filter(Boolean).join(" · ");

  const done = m.status === "done";
  if (changedMeeting) {
    $("#t-language").value = m.meta?.asr_language || preferredLanguage();
    $("#t-language-error").hidden = true;
  }
  $("#t-retranscribe").disabled = !["done", "error"].includes(m.status);
  $("#t-language").disabled = !["done", "error"].includes(m.status);
  $("#m-progress").hidden = done;
  if (!done) {
    $("#m-progress-bar").style.transform = `scaleX(${Math.max(0, Math.min(1, m.progress))})`;
    $("#m-progress-label").textContent = m.status === "error"
      ? `Fehler: ${m.error}` : `${STATUS[m.status] || m.status} … ${Math.round(m.progress * 100)} %`;
  }
  const player = $("#player");
  const audioLink = $("#m-audio");
  const audio = m.audio || null;
  player.hidden = !audio;
  audioLink.hidden = !audio;
  if (audio) {
    audioLink.href = withKey(`/api/meetings/${id}/audio?download=1`);
    const format = audio.suffix.replace(".", "").toUpperCase();
    audioLink.textContent = `Audio herunterladen (${format}, ${fmtBytes(audio.bytes)})`;
    audioLink.title = `Originalaufnahme als ${format}-Datei speichern`;
  }
  if (changedMeeting && audio) player.src = withKey(`/api/meetings/${id}/audio`);

  renderSummary();
  renderTranscript();
  renderBookmarks();
  $("#m-recap").disabled = !done;
  if (changedMeeting) await renderDraftTargets();
  if (changedMeeting) $("#ask-answer").replaceChildren();
  if (!keepTab) showTab(done ? "summary" : "transcript");
  document.querySelectorAll(".m-item").forEach((el) =>
    el.classList.toggle("active", el.dataset.id === String(id)));
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
  if (m.meta.summary_speakers_changed) {
    left.unshift(h("p", { role: "status", class: "muted" }, "Die Sprecherzuordnung wurde geändert. Die vorhandene Zusammenfassung bei Bedarf über „Zusammenfassung neu erstellen“ aktualisieren."));
  }
  if (s.engine === "heuristik") {
    // Ergebnis ohne Sprachmodell sichtbar als solches kennzeichnen (#57).
    left.unshift(h("div", { class: "card llm-hint" },
      h("p", {}, LLM_HINT),
      h("button", { type: "button", class: "primary", onclick: openLlmSetup }, "Sprachmodell einrichten")));
  }
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
  const speakerError = h("p", { role: "alert" });
  if (m.meta.speaker_correction || Object.keys(m.speakers).length > 1) {
    const restore = Boolean(m.meta.speaker_correction);
    speakers.append(h("button", { id: "speaker-single", disabled: !["done", "error"].includes(m.status), onclick: async (event) => {
      const button = event.currentTarget;
      button.disabled = true;
      speakerError.textContent = "";
      try {
        await api(`/api/meetings/${m.id}/single-speaker`, { method: restore ? "DELETE" : "POST" });
        await openMeeting(m.id, { keepTab: true });
      } catch (err) {
        speakerError.textContent = err.message;
        button.disabled = false;
      }
    } }, restore ? "Frühere Sprecheraufteilung wiederherstellen" : "Alle als eine Person zusammenführen"),
    h("p", { class: "small muted" }, restore ? "Die frühere Aufteilung ist gespeichert." : "Wenn überall dieselbe Person spricht. Die Änderung lässt sich rückgängig machen."), speakerError);
  }
  const tools = h("div", { class: "card" }, h("h3", {}, "Werkzeuge"),
    h("button", { onclick: async (e) => {
      e.target.disabled = true; e.target.textContent = "Erstelle …";
      try { await api(`/api/meetings/${m.id}/summarize`, { method: "POST" }); await openMeeting(m.id, { keepTab: true }); }
      catch (err) { alert(err.message); e.target.disabled = false; }
    } }, "Zusammenfassung neu erstellen"));

  box.replaceChildren(h("div", { class: "grid2" }, h("div", {}, left), h("div", {}, speakers, tools)));
}

function renderReadingTranscript(meeting, query, box) {
  const paragraphs = [];
  let paragraph = null;
  for (const segment of meeting.segments) {
    const previous = paragraph?.segments.at(-1);
    if (!paragraph || paragraph.speaker !== segment.speaker ||
        (paragraph.length >= 650 && /[.!?…]["“”»]?$/u.test(previous.text))) {
      paragraph = { speaker: segment.speaker, segments: [], length: 0 };
      paragraphs.push(paragraph);
    }
    paragraph.segments.push(segment);
    paragraph.length += segment.text.length + 1;
  }
  const multiple = new Set(meeting.segments.map((segment) => segment.speaker)).size > 1;
  const nodes = [];
  state.segEls = [];
  let previousSpeaker = null;
  for (const paragraph of paragraphs) {
    const plain = paragraph.segments.map((segment) => segment.text).join(" ");
    const match = query ? plain.toLowerCase().indexOf(query) : -1;
    if (query && match < 0) continue;
    if (multiple && (query || previousSpeaker !== paragraph.speaker)) {
      nodes.push(h("p", { class: "reading-speaker", style: `color:${color(paragraph.speaker)}` }, speakerName(paragraph.speaker)));
    }
    previousSpeaker = paragraph.speaker;
    const node = h("p", { class: "reading-paragraph" });
    let offset = 0;
    for (const segment of paragraph.segments) {
      let text = segment.text !== segment.original_text
        ? [h("del", {}, segment.original_text), " ", h("mark", {}, segment.text)] : [segment.text];
      if (query) {
        const begin = Math.max(0, match - offset), end = Math.min(segment.text.length, match + query.length - offset);
        text = begin < end
          ? [segment.text.slice(0, begin), h("mark", {}, segment.text.slice(begin, end)), segment.text.slice(end)] : [segment.text];
      }
      const span = h("span", { class: "reading-segment", "data-start": segment.start, "data-end": segment.end }, text);
      if (node.childNodes.length) node.append(" ");
      node.append(span);
      state.segEls.push(span);
      offset += segment.text.length + 1;
    }
    nodes.push(node);
  }
  box.replaceChildren(...(nodes.length ? nodes : [h("p", { class: "muted" }, "Keine passenden Textstellen.")]));
}

function renderTranscript() {
  const m = state.current;
  let controls = $("#transcript-version");
  if (!controls) { controls = h("div", { id: "transcript-version" }); $("#transcript").before(controls); }
  const version = h("select", { "aria-label": "Transkriptfassung" },
    h("option", { value: "original" }, "Original"), h("option", { value: "corrected" }, "Nachgebessert"));
  version.value = m.meta.transcript_version || "original";
  version.onchange = async () => {
    try { await api(`/api/meetings/${m.id}/transcript-version`, jsonOpts("PUT", { version: version.value })); await openMeeting(m.id, { keepTab: true }); }
    catch (err) { controls.append(h("p", { role: "alert" }, err.message)); }
  };
  const report = m.meta.correction;
  controls.replaceChildren(h("label", {}, "Fassung für Anzeige, Suche und Export ", version),
    h("p", { class: "muted" }, report ? `${report.external ? "Externer Dienst" : "Lokaler Dienst"}: ${report.service} · ${report.model} · ${report.status === "completed" ? "abgeschlossen" : "Original als Ersatz verwendet"} · ${report.rejected.length} Äußerungen unverändert belassen. Änderungen bitte prüfen.` : "Keine Nachbesserung vorhanden."));
  const q = $("#t-filter").value.trim().toLowerCase();
  const box = $("#transcript");
  box.classList.add("reading");
  if (!m.segments.length) {
    box.replaceChildren(h("p", { class: "muted" }, m.status === "done" ? "Keine Sprache erkannt." : "Transkript wird erstellt …"));
    state.segEls = [];
    return;
  }
  renderReadingTranscript(m, q, box);
}

function renderBookmarks() {
  const m = state.current;
  const labels = { soundbite: "Soundbite", action: "Action Item", comment: "Kommentar" };
  $("#bookmarks").replaceChildren(...(m.bookmarks.length ? m.bookmarks.map((b) => h("li", {},
    tsLink(b.start), h("span", { class: "tag" }, labels[b.kind] || b.kind), h("span", { class: "grow" }, b.note),
    h("button", { title: "Entfernen", onclick: async () => {
      await api(`/api/meetings/${m.id}/bookmarks/${b.id}`, { method: "DELETE" });
      await openMeeting(m.id, { keepTab: true });
    } }, "✕"))) : [h("li", { class: "muted" }, "Noch keine Markierungen. Oben eine Notiz oder einen Soundbite zur aktuellen Abspielposition anlegen.")]));
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
  const id = state.current.id;
  await api(`/api/meetings/${id}`, { method: "DELETE" });
  state.selected.delete(id);
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
const LANGUAGE_LABELS = { de: "Deutsch", auto: "Automatisch (mehrsprachig)", en: "Englisch" };

function preferredSpeakers() {
  try {
    const saved = localStorage.getItem("koprotokoll-speakers");
    if (saved === "" || /^(?:[1-9]|1[0-9]|20)$/.test(saved || "")) return saved;
  } catch { /* Ohne Browserspeicher bleibt die Auswahl im Dialog nutzbar. */ }
  return "1";
}

for (const id of ["up-speakers", "rec-speakers"]) {
  const select = $(`#${id}`);
  for (let count = 2; count <= 20; count++) {
    select.append(h("option", { value: count }, `${count} Personen`));
  }
  select.addEventListener("change", (event) => {
    try { localStorage.setItem("koprotokoll-speakers", event.target.value); } catch { /* optional */ }
  });
}

function preferredLanguage() {
  const saved = localStorage.getItem("koprotokoll-language");
  return Object.hasOwn(LANGUAGE_LABELS, saved) ? saved : "de";
}

for (const id of ["up-language", "rec-language"]) {
  $(`#${id}`).addEventListener("change", (event) => {
    localStorage.setItem("koprotokoll-language", event.target.value);
  });
}

$("#t-language-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = $("#t-retranscribe"), error = $("#t-language-error");
  if (button.disabled) return;
  button.disabled = true;
  button.textContent = "Transkription wird gestartet …";
  error.hidden = true;
  try {
    const m = await api(`/api/meetings/${state.current.id}/retranscribe`, jsonOpts("POST", { language: $("#t-language").value }));
    await loadMeetings();
    await openMeeting(m.id);
    showTab("transcript");
  } catch (err) {
    error.textContent = err.message;
    error.hidden = false;
    button.disabled = false;
  } finally {
    button.textContent = "Erneut transkribieren";
  }
});

async function uploadBlob(blob, filename, title, numSpeakers, language, stopReason) {
  const fd = new FormData();
  fd.append("file", blob, filename);
  fd.append("title", title || "");
  if (numSpeakers) fd.append("num_speakers", numSpeakers);
  if (language) fd.append("language", language);
  if (stopReason) fd.append("stop_reason", stopReason);
  const m = await api("/api/meetings", { method: "POST", body: fd });
  await loadMeetings();
  await openMeeting(m.id);
}

$("#btn-upload").addEventListener("click", () => {
  $("#up-language").value = preferredLanguage();
  $("#up-speakers").value = preferredSpeakers();
  $("#dlg-upload").showModal();
});
$("#upload-form").addEventListener("submit", async (e) => {
  if (e.submitter?.value !== "ok") return;
  const file = $("#up-file").files[0];
  if (!file) return;
  try { await uploadBlob(file, file.name, $("#up-title").value, $("#up-speakers").value, $("#up-language").value); }
  catch (err) { alert(`Upload fehlgeschlagen: ${err.message}`); }
  e.target.reset();
});

// ---------------------------------------------------------- Aufnahme
const rec = { recorder: null, streams: [], chunks: [], start: 0, timer: null, ctx: null, raf: null, hardware: false,
  speechTimer: null, speechSession: 0, autoStopped: false, external: false, poll: null, lastStatus: null, lastKey: "" };

function watchBrowserSpeech(analyser) {
  const session = ++rec.speechSession;
  let lastSpeech = performance.now(), lastCheck = lastSpeech;
  async function check() {
    if (session !== rec.speechSession || !rec.recorder) return;
    const now = performance.now();
    // Ein angehaltener Browser hat die Zwischenzeit nicht beobachtet.
    if (now - lastCheck > 2000) lastSpeech = now;
    lastCheck = now;
    const samples = new Float32Array(analyser.fftSize);
    analyser.getFloatTimeDomainData(samples);
    const ratio = rec.ctx.sampleRate / 16000;
    const pcm = new Int16Array(Math.floor(samples.length / ratio));
    for (let i = 0; i < pcm.length; i++) {
      const begin = Math.floor(i * ratio), end = Math.floor((i + 1) * ratio);
      let sum = 0;
      for (let j = begin; j < end; j++) sum += samples[j];
      pcm[i] = Math.max(-32768, Math.min(32767, sum / (end - begin) * 32768));
    }
    const data = new FormData();
    data.append("file", new Blob([pcm.buffer]), "activity.pcm");
    try {
      const result = await api("/api/capture/activity", { method: "POST", body: data });
      if (session !== rec.speechSession) return;
      $("#rec-silence-status").textContent = "Stoppt nach 5 Minuten ohne erkannte Sprache und wertet die Aufnahme aus.";
      if (result.speech) lastSpeech = now;
      if (!result.speech && now - lastSpeech >= result.silence_seconds * 1000) {
        rec.autoStopped = true;
        $("#rec-stop").click();
        return;
      }
    } catch (err) {
      if (session !== rec.speechSession) return;
      lastSpeech = performance.now();
      $("#rec-silence-status").textContent = `Automatischer Stopp nicht verfügbar: ${err.message}`;
    }
    if (session === rec.speechSession) rec.speechTimer = setTimeout(check, 500);
  }
  rec.speechTimer = setTimeout(check, 500);
}

function drawCaptureLevel(levelDb) {
  const c = $("#rec-level"), g = c.getContext("2d");
  const level = Math.max(0, Math.min(1, (Number(levelDb) + 60) / 60));
  g.clearRect(0, 0, c.width, c.height);
  g.fillStyle = getComputedStyle(document.documentElement).getPropertyValue("--accent");
  g.fillRect(0, 6, c.width * level, 12);
}

// Ein Abfragetakt pro Sekunde genügt; im Hintergrund-Tab wird nicht abgefragt.
// Der Zustand wird auch übernommen, wenn die Aufnahme außerhalb dieser Seite
// (z. B. per Doppeltipp im Infobereich) gestartet wurde.
async function pollCaptureState() {
  clearTimeout(rec.poll);
  if (!document.hidden && !rec.recorder) {
    try { applyCaptureStatus(await api("/api/capture")); }
    catch { /* Server nicht erreichbar – die Anzeige bleibt, wie sie ist. */ }
  }
  rec.poll = setTimeout(pollCaptureState, 1000);
}

document.addEventListener("visibilitychange", () => {
  if (!document.hidden) { clearTimeout(rec.poll); pollCaptureState(); }
});

function captureKey(status) {
  return [status.active, status.error || "", status.meeting_id || "", status.warning || ""].join("|");
}

// Nur Zustandswechsel neu zeichnen, damit nichts flackert.
function applyCaptureStatus(status) {
  rec.lastStatus = status;
  const key = captureKey(status);
  if (status.active) {
    if (!rec.hardware) startCaptureView(status, true);
    if (status.error) $("#rec-silence-status").textContent = status.error;
    else if (status.warning) $("#rec-silence-status").textContent = status.warning;
    drawCaptureLevel(status.level_db ?? -120);
  } else if (rec.hardware && key !== rec.lastKey) {
    endCaptureView(status).catch(() => { /* Anzeige bleibt sonst unverändert. */ });
  } else if (key !== rec.lastKey && status.error && !status.meeting_id) {
    $("#rec-silence-status").textContent = status.error;
  }
  rec.lastKey = key;
}

// Anzeige für eine laufende Hardwareaufnahme – egal, wer sie gestartet hat.
function startCaptureView(status, external) {
  rec.hardware = true;
  rec.external = external;
  rec.start = (Number(status.started_at) || Date.now() / 1000) * 1000;
  clearInterval(rec.timer);
  rec.timer = setInterval(() => { $("#rec-time").textContent = fmt((Date.now() - rec.start) / 1000); }, 500);
  $("#rec-time").textContent = fmt(Math.max(0, Date.now() - rec.start) / 1000);
  $("#rec-dot").classList.add("on");
  $("#rec-start").hidden = true;
  $("#rec-start").disabled = true;
  $("#rec-busy").hidden = !external;
  $("#rec-stop").hidden = false;
  $("#rec-stop").textContent = "Stopp & auswerten";
  $("#rec-cancel").hidden = true;
  $("#rec-method").disabled = true;
  $("#rec-device").disabled = true;
  $("#rec-source").disabled = true;
  $("#rec-language").disabled = true;
  drawCaptureLevel(status.level_db ?? -120);
}

// Aufnahme endete (z. B. per Doppeltipp): genauso aufräumen wie nach einem Klick.
async function endCaptureView(status) {
  const meetingId = status.meeting_id || null;
  cleanupRecording();
  await loadMeetings();
  if (meetingId) {
    // Erst das Meeting öffnen (setzt den Hash), dann den Dialog schließen –
    // so kann ein reload dazwischen nicht auf der Listenansicht landen.
    await openMeeting(meetingId);
    $("#dlg-record").close();
  } else if (status.error) {
    $("#rec-silence-status").textContent = status.error;
  }
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
  rec.autoStopped = false;
  $("#rec-silence-status").textContent = "Stoppt nach 5 Minuten ohne erkannte Sprache und wertet die Aufnahme aus.";
  if ($("#rec-method").value === "hardware") {
    const device = $("#rec-device").value;
    if (!device) throw new Error("Bitte einen Audioeingang auswählen.");
    const state = await api("/api/capture/start", jsonOpts("POST", {
      device,
      title: $("#rec-title").value,
      num_speakers: Number($("#rec-speakers").value) || null,
      language: $("#rec-language").value,
      channels: 2,
      sample_rate: 48000,
    }));
    startCaptureView(state, false);
    return;
  }
  const mic = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
  rec.streams = [mic];
  rec.ctx = new AudioContext({ sampleRate: 48000 });
  const dest = rec.ctx.createMediaStreamDestination();
  const analyser = rec.ctx.createAnalyser();
  analyser.fftSize = 32768;
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
  watchBrowserSpeech(analyser);
  $("#rec-language").disabled = true;
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
  clearTimeout(rec.speechTimer);
  rec.speechSession++;
  clearInterval(rec.timer);
  clearTimeout(rec.raf);
  cancelAnimationFrame(rec.raf);
  rec.streams.forEach((s) => s.getTracks().forEach((t) => t.stop()));
  rec.ctx?.close();
  rec.recorder = null;
  rec.hardware = false;
  rec.external = false;
  $("#rec-dot").classList.remove("on");
  $("#rec-start").hidden = false;
  $("#rec-start").disabled = false;
  $("#rec-busy").hidden = true;
  $("#rec-stop").hidden = true;
  $("#rec-cancel").hidden = false;
  $("#rec-method").disabled = false;
  $("#rec-device").disabled = false;
  $("#rec-source").disabled = false;
  $("#rec-language").disabled = false;
  $("#rec-time").textContent = "00:00";
}

$("#btn-record").addEventListener("click", async () => {
  $("#rec-language").value = preferredLanguage();
  $("#rec-speakers").value = preferredSpeakers();
  selectRecordingMethod();
  $("#dlg-record").showModal();
  if (rec.lastStatus) applyCaptureStatus(rec.lastStatus);
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
    const language = $("#rec-language").value;
    const speakers = $("#rec-speakers").value;
    const stopReason = rec.autoStopped ? "silence" : null;
    cleanupRecording();
    $("#dlg-record").close();
    try { await uploadBlob(blob, "aufnahme.webm", title, speakers, language, stopReason); } catch (err) { alert(`Upload fehlgeschlagen: ${err.message}`); }
  };
  r.stop();
});
$("#dlg-record").addEventListener("close", () => { if (rec.recorder) { rec.recorder.onstop = null; rec.recorder.stop(); cleanupRecording(); } });
$("#dlg-record").addEventListener("cancel", (e) => { if (rec.hardware || rec.recorder) e.preventDefault(); });

// ---------------------------------------------------------- Ansichten
function showView(name) {
  for (const v of ["empty", "meeting", "integrations"]) $(`#${v}`).hidden = v !== name;
  $("#btn-integrations").classList.toggle("active", name === "integrations");
  // Auf dem Handy ist die Liste die Startansicht; Meeting/Integrationen sind eigene Vollbild-Ansichten.
  document.body.classList.toggle("detail", name !== "empty");
  if (name !== "meeting") document.querySelectorAll(".m-item").forEach((el) => el.classList.remove("active"));
}

// Zurück zur Liste (nur auf dem Handy sichtbar): Hash zurücksetzen, route() zeigt die Listenansicht.
$("#m-back").addEventListener("click", () => {
  if (location.hash && location.hash !== "#") location.hash = "#";
  else showView("empty");
});

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
// Aktives Klassifikations-Label-Set (JSON) – die Klassifikation selbst läuft lokal (#61).
async function renderLabels() {
  const box = $("#labels-box");
  try {
    const ls = await api("/api/labels");
    box.replaceChildren(
      h("p", { class: "muted small" },
        `Aktives Label-Set: ${ls.source === "standard" ? "Standard (eingebaut)" : ls.source} · Schwelle ${Math.round(ls.threshold * 100)} %`),
      h("div", { class: "chips" }, ls.labels.map((l) => h("span", {
        class: "chip",
        title: [l.description, l.examples?.length ? `Beispiele: ${l.examples.join("; ")}` : null].filter(Boolean).join(" – "),
      }, l.name))),
      h("p", { class: "muted small" },
        "Eigenes Set: labels.json im Datenordner oder Pfad in KOPROTOKOLL_LABELS_FILE. Schema und Anleitung: docs/einrichtung.md („Eigene Klassifikation“); Beispiel: labels.example.json im Repo."));
  } catch (err) {
    box.replaceChildren(h("p", { role: "alert" }, err.message),
      h("button", { onclick: renderLabels }, "Erneut laden"));
  }
}

async function openIntegrations() {
  showView("integrations");
  // pushState statt replaceState: Die Zurück-Taste des Browsers führt zurück (route() bei hashchange).
  if (location.hash.split("?")[0] !== "#integrations") history.pushState(null, "", "#integrations");
  const params = new URLSearchParams(location.hash.split("?")[1] || "");
  const err = params.get("google_error") || params.get("microsoft_error");
  if (err) alert(`Anmeldung abgebrochen: ${err}`);
  renderBots();
  await Promise.all([renderGoogle(), renderMicrosoft(), renderEmail(), renderLabels(), renderSources(), renderKeys(), renderHooks()]);
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
  const lists = [$("#upcoming-list"), $("#upcoming-list-mobile")].filter(Boolean);
  if (!events.length) {
    for (const list of lists) list.replaceChildren(h("li", { class: "muted small" },
      "Keine Termine in den nächsten 7 Tagen. Verbundene Kalender werden automatisch angezeigt."));
    return;
  }
  for (const list of lists) list.replaceChildren(...events.map((e) => {
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
  // Zurück in der Listenansicht (z. B. „Zurück" vom Handy): Liste frischen Stand laden.
  else { showView("empty"); await loadMeetings(); await renderUpcoming(); }
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
  pollCaptureState();
})();

async function renderLocalModel() {
  const box = $("#local-model");
  try {
    const config = await api("/api/llm");
    const correction = await api("/api/transcript-correction");
    const enabled = h("input", { type: "checkbox" }); enabled.checked = correction.enabled;
    const correctionModel = h("input", { value: correction.model, list: "correction-models" });
    const vocabulary = h("textarea", { rows: "3" }); vocabulary.value = correction.vocabulary.join("\n");
    const correctionMessage = h("p", { "aria-live": "polite" });
    const correctionSave = h("button", { type: "button", onclick: async () => {
      try { await api("/api/transcript-correction", jsonOpts("PUT", { enabled: enabled.checked, model: correctionModel.value, vocabulary: vocabulary.value.split("\n").map(s => s.trim()).filter(Boolean) })); correctionMessage.textContent = "Gespeichert. Gilt bei der nächsten Verarbeitung."; }
      catch (err) { correctionMessage.textContent = err.message; }
    } }, "Nachbesserung speichern");
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
    const provider = h("select", { "aria-label": "Anbieter" }, ...[["ollama", "Ollama lokal"], ["mistral", "Mistral API"], ["openai", "OpenAI"], ["other", "Anderer OpenAI-kompatibler Server"]].map(([value, label]) => h("option", { value }, label)));
    provider.value = config.provider;
    const url = h("input", { "aria-label": "Basis-URL", value: config.url });
    const name = h("input", { "aria-label": "Modellname", value: config.model });
    const key = h("input", { type: "password", "aria-label": "API-Schlüssel", autocomplete: "new-password", placeholder: config.api_key_set ? "Gesetzt – leer lassen zum Beibehalten" : "Nicht gesetzt" });
    const remove = h("input", { type: "checkbox" });
    const timeout = h("input", { type: "number", "aria-label": "Timeout in Sekunden", value: config.timeout, min: "0.01", max: "3600", step: "any" });
    const confirm = h("input", { type: "checkbox" });
    provider.onchange = () => { if (provider.value === "openai") url.value = "https://api.openai.com/v1"; if (provider.value === "mistral") url.value = "https://api.mistral.ai/v1"; if (provider.value === "ollama") url.value = "http://localhost:11434/v1"; confirm.checked = false; };
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
      h("p", {}, "Ohne Sprachmodell erstellt die App die Zusammenfassung mit Regeln. Ein lokales Sprachmodell schreibt Übersicht, Entscheidungen und Aufgaben selbst – es läuft auf diesem Rechner (Download ca. 5 GB, bleibt lokal). Ohne Modell bleibt alles nutzbar."),
      h("button", { type: "button", onclick: openLlmSetup }, "Sprachmodell einrichten"),
      h("p", {}, config.external ? `Externer Dienst: ${config.url} (${config.external_confirmed ? "bestätigt" : "Übertragung gesperrt"})` : "Lokaler Dienst"),
      h("p", {}, config.env_overrides.length ? `Vom Server vorgegeben: ${config.env_overrides.join(", ")}` : "Eigene Einstellungen"),
      h("label", {}, "Anbieter ", provider), h("label", {}, "Basis-URL ", url),
      h("label", {}, "Modellname ", name), h("label", {}, "API-Schlüssel ", key),
      h("label", {}, remove, " Gespeicherten Schlüssel löschen"), h("label", {}, "Timeout in Sekunden ", timeout),
      h("label", {}, confirm, " Transkripttext wird an diesen Dienst gesendet. Ich bestätige die externe Übertragung."), providerSave, test, h("p", {}, config.url), message,
      h("p", {}, config.model_env_override ? `KOPROTOKOLL_LLM_MODEL hat Vorrang: ${config.model}` : `Aktuelle Auswahl: ${config.model}`),
      model, h("label", {}, "Maximale Ausgabetokens ", limit), h("label", {}, "Thinking ", thinking),
      h("label", {}, compatible, " Server und Modell unterstützen diese optionalen Parameter (laut Dokumentation)."), save,
      h("button", { type: "button", onclick: renderLocalModel }, "Modellliste aktualisieren"),
      h("h3", {}, "Transkript nachbessern"), h("label", {}, enabled, " Optional aktivieren"),
      h("p", {}, "Verwendet denselben Anbieter und dessen externe Freigabe. Original bleibt erhalten. Kleine Textänderungen können trotzdem falsch sein."),
      h("label", {}, "Eigenes Korrekturmodell ", correctionModel),
      h("datalist", { id: "correction-models" }, ...config.models.map(id => h("option", { value: id }))),
      h("label", {}, "Wortliste (ein Begriff je Zeile) ", vocabulary), correctionSave, correctionMessage);
  } catch (err) { box.textContent = err.message; }
}
renderLocalModel();

// ---------------------------------------------- Sprachmodell einrichten (#57)
const PULL_STATUS_DE = {
  "pulling manifest": "Manifest wird geladen",
  "downloading": "Download läuft",
  "verifying sha256 digest": "Prüfsumme wird kontrolliert",
  "writing manifest": "Manifest wird geschrieben",
  "removing any unused layers": "Unbenutzte Ebenen werden entfernt",
  "success": "Fertig",
};
let llmSetupTimer = null;

function stopLlmSetupPoll() {
  if (llmSetupTimer) { clearInterval(llmSetupTimer); llmSetupTimer = null; }
}

async function openLlmSetup() {
  const dlg = $("#dlg-llm-setup");
  if (!dlg.open) dlg.showModal();
  await renderLlmSetup();
}

async function renderLlmSetup(flash = null) {
  const body = $("#llm-setup-body");
  stopLlmSetupPoll();
  body.replaceChildren(h("p", { role: "status" }, "Status wird geprüft …"));
  let setup;
  try { setup = await api("/api/llm/setup"); }
  catch (err) { body.replaceChildren(h("p", { role: "alert" }, err.message)); return; }
  const kids = [h("p", {},
    "Ein lokales Sprachmodell schreibt Übersicht, Entscheidungen und Aufgaben selbst – es läuft auf diesem Rechner (Download ca. 5 GB, bleibt lokal). Ohne Modell bleibt alles nutzbar; Koprotokoll arbeitet dann mit Regeln.")];
  if (flash) kids.unshift(h("p", { role: "status" }, flash));
  if (setup.setup_step === "install_server") {
    kids.push(
      h("p", {}, `Der Server unter ${setup.url} ist nicht erreichbar. Damit ein Sprachmodell lokal laufen kann, muss zuerst einer installiert sein:`),
      h("ol", {}, setup.instructions.map((step) => h("li", {},
        h("b", {}, `${step.title}. `), step.text, " ",
        step.link ? h("a", { href: step.link, target: "_blank", rel: "noopener" }, step.link_label || step.link) : null))),
      h("div", { class: "row" }, h("button", { type: "button", onclick: () => renderLlmSetup() }, "Erneut prüfen")));
  } else if (setup.setup_step === "check_endpoint") {
    kids.push(
      h("p", {}, `${setup.message} Endpunkt, Schlüssel und Erreichbarkeit lassen sich unten bzw. in den Einstellungen prüfen.`),
      h("div", { class: "row" }, h("button", { type: "button", onclick: () => renderLlmSetup() }, "Erneut prüfen")));
  } else if (setup.setup_step === "pull_model" || setup.setup_step === "select_model") {
    const modelInput = h("input", { value: setup.suggested_model, "aria-label": "Modellname" });
    const status = h("p", { role: "status", class: "muted small" }, setup.message);
    const barWrap = h("div", { class: "progress", hidden: true },
      h("div", { class: "bar" }, h("div")), h("span", {}, ""));
    const start = h("button", { type: "button", class: "primary" }, "Modell laden");
    const cancel = h("button", { type: "button" }, "Abbrechen");
    const reset = () => { start.disabled = setup.setup_step !== "pull_model"; cancel.disabled = false; };
    start.onclick = async () => {
      start.disabled = true; cancel.disabled = true;
      try {
        await api("/api/llm/pull", jsonOpts("POST", { model: modelInput.value.trim() }));
        barWrap.hidden = false;
        pollLlmPull(barWrap, status, reset);
      } catch (err) { status.textContent = err.message; reset(); }
    };
    cancel.onclick = async () => {
      try { await api("/api/llm/pull/cancel", { method: "POST" }); }
      catch (err) { status.textContent = err.message; }
    };
    kids.push(
      h("p", {}, setup.setup_step === "pull_model"
        ? `Server erreichbar (${setup.url}), aber das Modell „${setup.model}“ fehlt. Laden aus der App – ca. 5 GB, der genaue Fortschritt erscheint beim Laden; der Download bleibt auf diesem Rechner.`
        : `Server erreichbar (${setup.url}), aber das Modell „${setup.model}“ fehlt. Dieser Anbieter lädt die App nicht selbst – bitte manuell laden und danach „Erneut prüfen“.`),
      h("label", {}, "Modellname ", modelInput),
      h("div", { class: "row" }, start, cancel, h("button", { type: "button", onclick: () => renderLlmSetup() }, "Erneut prüfen")),
      barWrap, status);
    if (setup.setup_step !== "pull_model") start.disabled = true;
  } else if (setup.setup_step === "ready") {
    kids.push(
      h("p", {}, `Server erreichbar (${setup.url}) und Modell „${setup.model}“ ist vorhanden. Jetzt in der Modellliste auswählen und speichern – das nächste Meeting schreibt dann eine echte Zusammenfassung (Engine „llm:<Modell>“).`),
      h("button", { type: "button", onclick: () => { $("#dlg-llm-setup").close(); location.hash = "#integrations"; } }, "Zur Modellauswahl"));
  } else {
    kids.push(h("p", {}, `${setup.message} Ohne Sprachmodell arbeitet Koprotokoll mit Regeln – alles bleibt nutzbar.`));
  }

  // Eigenen Endpunkt / Coding CLI – in jedem Zustand sichtbar, auch ohne lokalen Server.
  kids.push(h("h3", {}, "Eigenen Endpunkt oder Coding CLI verwenden"));
  let sources = [];
  try { sources = (await api("/api/llm/cli-sources")).sources; } catch { sources = []; }
  if (sources.length) {
    for (const src of sources) {
      const confirm = h("input", { type: "checkbox", "aria-label": "Externe Übertragung bestätigen" });
      const importBtn = h("button", { type: "button" }, "Konfiguration übernehmen");
      const st = h("p", { role: "status", class: "muted small" });
      importBtn.onclick = async () => {
        importBtn.disabled = true;
        try {
          await api("/api/llm/cli-import", jsonOpts("POST", { source: src.source, external_confirmed: confirm.checked }));
          await renderLlmSetup(`Übernommen: ${src.label} · ${src.url} · Modell „${src.model}“. Jetzt in den Einstellungen den Anbieter testen und die Modellauswahl speichern.`);
        } catch (err) { st.textContent = err.message; importBtn.disabled = false; }
      };
      kids.push(h("div", { class: "cli-source" },
        h("p", {}, `${src.label}: Modell „${src.model}“, Endpunkt ${src.url} – ${src.key_available ? `Schlüssel gefunden (${src.key_source})` : "kein Schlüssel gefunden (Umgebung/Schlüsselbund)"}. Der Schlüssel wird nur lokal gespeichert und nie angezeigt.`),
        h("label", {}, confirm, ` Transkripttext wird an ${src.url} gesendet. Ich bestätige die externe Übertragung.`),
        h("div", { class: "row" }, importBtn), st));
    }
  } else {
    kids.push(h("p", { class: "muted small" }, "Keine Coding-CLI-Konfiguration gefunden (~/.vibe/config.toml)."));
  }
  kids.push(h("button", { type: "button", onclick: () => { $("#dlg-llm-setup").close(); location.hash = "#integrations"; } },
    "Eigenen Endpunkt in den Einstellungen eintragen"));
  body.replaceChildren(...kids);
}

function pollLlmPull(barWrap, status, done) {
  stopLlmSetupPoll();
  const bar = barWrap.querySelector(".bar div");
  const label = barWrap.querySelector("span");
  llmSetupTimer = setInterval(async () => {
    let st;
    try { st = await api("/api/llm/pull"); } catch { stopLlmSetupPoll(); return; }
    const frac = st.total ? st.completed / st.total : 0;
    bar.style.transform = `scaleX(${Math.max(0, Math.min(1, frac))})`;
    const parts = [PULL_STATUS_DE[st.status] || st.status || ""];
    if (st.total) parts.push(`${fmtBytes(st.completed)} / ${fmtBytes(st.total)}`);
    label.textContent = parts.filter(Boolean).join(" – ");
    if (st.done) {
      stopLlmSetupPoll();
      status.textContent = st.error ? st.error
        : st.status === "success" ? "Modell geladen. Jetzt in der Liste auswählen und speichern."
        : "Download beendet.";
      done();
    } else {
      status.textContent = "Lädt … abbrechbar über „Abbrechen“.";
    }
  }, 1000);
}

$("#dlg-llm-setup").addEventListener("close", stopLlmSetupPoll);
