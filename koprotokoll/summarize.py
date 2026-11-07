"""Zusammenfassung, Stichwörter, Action Items und „Frag das Meeting“.

Primär über ein lokales LLM (OpenAI-kompatible API, z. B. Ollama). Ist keines
erreichbar, greift eine einfache extraktive Heuristik, damit die App vollständig
offline nutzbar bleibt.
"""

from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.request
from collections import Counter

from .config import Settings, external_llm

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# LLM-Client
# --------------------------------------------------------------------------
class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Keine Schlüssel oder Transkripttexte an umgeleitete Ziele senden."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class LLM:
    def __init__(self, settings: Settings):
        self.s = settings

    def status(self) -> dict:
        state = dict(server_reachable=False, model_present=False, available=False,
                     inference="nicht geprüft", model=self.s.llm_model, url=self.s.llm_base_url,
                     models=[], enabled=self.s.llm_enabled)
        if not self.s.llm_enabled or (external_llm(self.s.llm_base_url) and not self.s.llm_external_confirmed):
            state["message"] = "LLM deaktiviert; Heuristik wird verwendet."
            return state
        try:
            req = urllib.request.Request(self.s.llm_base_url.rstrip("/") + "/models", headers=self._headers())
            with urllib.request.build_opener(NoRedirect).open(req, timeout=2) as response:
                data = json.load(response)
            state["models"] = sorted({m["id"] for m in data["data"] if isinstance(m.get("id"), str)})
            state["server_reachable"] = True
            state["model_present"] = self.s.llm_model in state["models"]
            state["available"] = state["model_present"]
            state["message"] = ("Modell vorhanden; Inferenz noch nicht geprüft." if state["model_present"]
                                else "Ausgewähltes Modell fehlt; bitte ein vorhandenes Modell wählen.")
        except (urllib.error.URLError, OSError, ValueError, KeyError, TypeError):
            state["message"] = "Modellliste nicht abrufbar; Server oder Antwort prüfen."
        return state

    def available(self) -> bool:
        return self.status()["available"]

    def _headers(self) -> dict:
        return {"Content-Type": "application/json", "Authorization": f"Bearer {self.s.llm_api_key}"}

    def chat(self, system: str, user: str, json_mode: bool = False) -> str:
        if external_llm(self.s.llm_base_url) and not self.s.llm_external_confirmed:
            raise ValueError("Externe Übertragung nicht bestätigt.")
        body: dict = {
            "model": self.s.llm_model,
            "temperature": 0.2,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        if self.s.llm_max_tokens is not None:
            body["max_tokens"] = self.s.llm_max_tokens
        if self.s.llm_reasoning_effort is not None:
            body["reasoning_effort"] = self.s.llm_reasoning_effort
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        req = urllib.request.Request(
            self.s.llm_base_url.rstrip("/") + "/chat/completions",
            data=json.dumps(body).encode(), headers=self._headers(), method="POST",
        )
        with urllib.request.build_opener(NoRedirect).open(req, timeout=self.s.llm_timeout) as r:
            data = json.load(r)
        return data["choices"][0]["message"]["content"]


def parse_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < 0:
        raise ValueError("Keine JSON-Antwort")
    return json.loads(text[start:end + 1])


# --------------------------------------------------------------------------
# Transkript-Formatierung
# --------------------------------------------------------------------------
def fmt_time(sec: float) -> str:
    sec = int(sec)
    h, m, s = sec // 3600, sec % 3600 // 60, sec % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def transcript_text(segments: list[dict], names: dict[str, str]) -> str:
    return "\n".join(
        f"[{fmt_time(s['start'])}] {names.get(s['speaker'], s['speaker'])}: {s['text']}" for s in segments
    )


def _chunks(text: str, size: int) -> list[str]:
    lines, chunks, cur = text.splitlines(), [], ""
    for line in lines:
        if len(cur) + len(line) > size and cur:
            chunks.append(cur)
            cur = ""
        cur += line + "\n"
    if cur:
        chunks.append(cur)
    return chunks


# --------------------------------------------------------------------------
# Zusammenfassung
# --------------------------------------------------------------------------
SUMMARY_SYSTEM = """Du bist ein präziser Meeting-Assistent. Antworte ausschließlich mit JSON in der
Sprache des Transkripts. Erfinde nichts, was nicht im Transkript steht. Schema:
{
  "title": "kurzer, aussagekräftiger Meeting-Titel",
  "overview": "2-4 Sätze Zusammenfassung",
  "keywords": ["5-10 Stichwörter"],
  "outline": [{"time": "mm:ss", "topic": "Thema", "notes": ["Stichpunkt", "..."]}],
  "action_items": [{"owner": "Name oder null", "task": "Aufgabe", "due": "Frist oder null", "time": "mm:ss"}],
  "decisions": ["getroffene Entscheidungen"],
  "questions": ["offene Fragen"]
}"""

CHUNK_SYSTEM = """Fasse diesen Abschnitt eines Meeting-Transkripts stichpunktartig zusammen.
Behalte Zeitmarken [mm:ss], Namen, Zahlen, Entscheidungen, Aufgaben und Fristen bei."""

MAX_CONTEXT_CHARS = 24_000


def summarize(segments: list[dict], names: dict[str, str], llm: LLM | None) -> dict:
    if not segments:
        return {"engine": "none", "overview": "Kein gesprochener Inhalt erkannt.", "keywords": [],
                "outline": [], "action_items": [], "decisions": [], "questions": []}
    failure = None
    if llm is not None and llm.available():
        try:
            return _llm_summary(segments, names, llm)
        except Exception as e:  # noqa: BLE001 – LLM-Fehler dürfen die Verarbeitung nicht abbrechen
            failure = "Modellaufruf fehlgeschlagen (Fehler oder Timeout); Heuristik verwendet."
            log.warning("LLM-Zusammenfassung fehlgeschlagen, nutze Heuristik.")
    result = heuristic_summary(segments, names)
    if failure and external_llm(llm.s.llm_base_url):
        result["external_service"] = llm.s.llm_base_url
    result["warning"] = failure or "Heuristik verwendet: LLM deaktiviert, Modell fehlt oder Server nicht erreichbar."
    return result


def _llm_summary(segments: list[dict], names: dict[str, str], llm: LLM) -> dict:
    text = transcript_text(segments, names)
    if len(text) > MAX_CONTEXT_CHARS:
        # Map-Reduce: Abschnitte einzeln verdichten, dann gemeinsam zusammenfassen.
        partial = [llm.chat(CHUNK_SYSTEM, c) for c in _chunks(text, MAX_CONTEXT_CHARS)]
        text = "\n\n".join(partial)
    data = parse_json(llm.chat(SUMMARY_SYSTEM, text, json_mode=True))
    result = {k: data.get(k) or [] for k in ("keywords", "outline", "action_items", "decisions", "questions")}
    result["overview"] = data.get("overview") or ""
    result["title"] = data.get("title") or None
    result["external_service"] = llm.s.llm_base_url if external_llm(llm.s.llm_base_url) else None
    result["engine"] = f"llm:{llm.s.llm_model}"
    return result


# --------------------------------------------------------------------------
# Offline-Heuristik
# --------------------------------------------------------------------------
STOPWORDS = set("""
aber alle allem allen aller alles als also am an ander andere anderen auch auf aus bei beim bin bis bist da dabei
damit dann das dass daß dein deine dem den denn der des dich die dies diese diesem diesen dieser dieses dir doch dort
du durch ein eine einem einen einer eines einfach er es etwas euch euer für gegen genau gibt gut habe haben hat hatte
hier hin hinter ich ihm ihn ihnen ihr ihre im in ins ist ja jetzt kann kannst kein keine können könnte machen man
mal mehr mein meine mich mir mit muss müssen nach nein nicht nichts noch nun nur ob oder ohne okay schon sehr sein
seine sich sie sind so solche soll sollen sollte sondern sowie über um und uns unser unter viel vom von vor wann
war waren was weil weiter welche wenn wer werde werden wie wieder will wir wird wirklich wo würde zu zum zur zwar
a about after again all also am an and any are as at be because been but by can could did do does doing for from
get go going got had has have he her here him his how i if in into is it its just know like me more my no not now
of on one or our out really right so some that the their them then there these they thing think this to up us
very was we well were what when where which who will with would yeah yes you your
""".split())

_ACTION_VERBS = (r"kümmere|kümmerst|kümmert|kümmern|übernehme|übernehmen|übernimmt|"
                 r"schick(?:e|st|t|en)?|send(?:e|est|et|en)?|meld(?:e|est|et|en)?|"
                 r"liefer(?:e|st|t|n)?|erstell(?:e|st|t|en)?|prüf(?:e|st|t|en)?|"
                 r"aktualisier(?:e|st|t|en)?|überarbeit(?:e|est|et|en)?|"
                 r"ergänz(?:e|st|t|en)?|abschließen|fertigstellen|nachfassen|dokumentier(?:e|st|t|en)?")
ACTION_PATTERNS = [
    rf"\b(?:ich|wir)\s+(?:(?:werde|werden)\b.*\b(?:{_ACTION_VERBS})|(?:{_ACTION_VERBS}))\b",
    rf"\b(?:muss|müssen|soll|sollen)\b.*\b(?:{_ACTION_VERBS})\b",
    rf"\b(?:kannst|könnt|könnten)\s+(?:du|ihr|Sie)\b.*\b(?:{_ACTION_VERBS})\b",
    rf"\bbitte\b.*\b(?:{_ACTION_VERBS})\b",
    rf"^[A-ZÄÖÜ][\wäöüß-]+,\s+.*\b(?:{_ACTION_VERBS})\b",
    r"\b(?:to-?do|aufgabe|action item)\s*:",
    rf"^[A-ZÄÖÜ][\wäöüß-]+\s+(?:wird\b.*\b(?:{_ACTION_VERBS})|(?:{_ACTION_VERBS}))\b",
    r"\b(i|we)('ll| will| need to| have to| should)\b",
    r"\b(let's|lets|make sure|deadline\s*:|by (monday|tuesday|wednesday|thursday|friday|tomorrow|next))\b",
]
_ACTION_RE = [re.compile(p, re.I) for p in ACTION_PATTERNS]
_DUE_RE = re.compile(
    r"\bbis\s+(?:zum\s+)?(?:\d{1,2}\.\d{1,2}\.(?:\d{4})?|"
    r"(?:nächsten?\s+)?(?:Montag|Dienstag|Mittwoch|Donnerstag|Freitag|Samstag|Sonntag)|"
    r"übermorgen|morgen|heute|Ende\s+(?:der\s+Woche|des\s+Monats))|"
    r"\bnächste\s+Woche\b|\bEnde\s+des\s+Monats\b|\b(?:übermorgen|morgen|heute)\b", re.I)
_WORD_RE = re.compile(r"[A-Za-zÄÖÜäöüß][\wÄÖÜäöüß-]{2,}")


def _action_item(sentence: dict, names: dict[str, str]) -> dict | None:
    text = sentence["text"]
    if len(text.split()) < 3 or re.search(
        r"\b(?:nicht|nichts|kein\w*|vielleicht|eventuell)\b|"
        r"\b(?:ist|sind|hat|haben|wurde|wurden)\b.*\b(?:erledigt|abgeschlossen|geschickt)\b", text, re.I
    ):
        return None
    due = _DUE_RE.search(text)
    if not any(r.search(text) for r in _ACTION_RE):
        if not (due and re.search(r"\bsollten?\b", text, re.I)
                and re.search(rf"\b(?:{_ACTION_VERBS})\b", text, re.I)):
            return None
    named = re.match(rf"(?:Bitte\s+)?([A-ZÄÖÜ][\wäöüß-]+)(?:,|\s+(?:wird|muss|soll|{_ACTION_VERBS})\b)", text)
    pronouns = {"ich", "wir", "du", "ihr", "sie", "bitte", "heute", "morgen", "danach", "vorher", "dann", "dafür", "das", "dies", "dieses", "was", "wer", "es"}
    owner = named[1] if named and named[1].lower() not in pronouns else None
    main_clause = re.split(r"[,;]", text, maxsplit=1)[0]
    if owner is None and re.search(rf"\bich\s+(?:werde|muss|soll|{_ACTION_VERBS})\b|\b(?:werde|muss|soll|{_ACTION_VERBS})\s+ich\b",
                                   main_clause, re.I):
        owner = names.get(sentence["speaker"], sentence["speaker"])
    return {"owner": owner, "task": text, "due": due[0] if due else None,
            "time": fmt_time(sentence["start"])}


def _sentences(segments: list[dict]) -> list[dict]:
    out = []
    for s in segments:
        # Nach Datumszahlen nur vor einem groß geschriebenen Satzanfang trennen.
        boundary = r"(?<=[!?])\s+|(?<=\.)\s+(?=[A-ZÄÖÜ])|(?<!\d\.)(?<=\.)\s+"
        for part in re.split(boundary, s["text"]):
            if part.strip():
                out.append({"text": part.strip(), "start": s["start"], "speaker": s["speaker"]})
    return out


def heuristic_summary(segments: list[dict], names: dict[str, str]) -> dict:
    sents = _sentences(segments)
    freq = Counter(
        w.lower() for s in sents for w in _WORD_RE.findall(s["text"]) if w.lower() not in STOPWORDS
    )
    keywords = [w for w, n in freq.most_common(10) if n > 1] or [w for w, _ in freq.most_common(5)]

    def score(s: dict) -> float:
        ws = [w.lower() for w in _WORD_RE.findall(s["text"])]
        return sum(freq[w] for w in ws if w not in STOPWORDS) / (len(ws) ** 0.5 or 1)

    ranked = sorted(sents, key=score, reverse=True)[: max(3, min(8, len(sents) // 8))]
    top = sorted(ranked, key=lambda s: s["start"])

    actions = []
    for s in sents:
        action = _action_item(s, names)
        if action:
            actions.append(action)

    # Gliederung: Transkript in bis zu 6 gleich lange Zeitabschnitte teilen.
    n_parts = min(6, max(1, len(segments) // 6))
    size = -(-len(segments) // n_parts)
    outline = []
    for i in range(0, len(segments), size):
        part = segments[i:i + size]
        part_sents = [s for s in sents if part[0]["start"] <= s["start"] <= part[-1]["start"]]
        best = sorted(part_sents, key=score, reverse=True)[:2]
        part_freq = Counter(w.lower() for s in part_sents for w in _WORD_RE.findall(s["text"])
                            if w.lower() not in STOPWORDS)
        topic = ", ".join(w.capitalize() for w, _ in part_freq.most_common(3)) or "Abschnitt"
        outline.append({"time": fmt_time(part[0]["start"]), "topic": topic,
                        "notes": [b["text"] for b in sorted(best, key=lambda s: s["start"])]})

    return {
        "engine": "heuristik",
        "title": None,
        "overview": " ".join(s["text"] for s in top[:3]),
        "keywords": keywords,
        "outline": outline,
        "action_items": actions[:20],
        "decisions": [],
        "questions": [s["text"] for s in sents if s["text"].endswith("?") and len(s["text"].split()) >= 3][:10],
    }


# --------------------------------------------------------------------------
# „Frag das Meeting“
# --------------------------------------------------------------------------
ASK_SYSTEM = """Beantworte die Frage ausschließlich anhand des Meeting-Transkripts.
Nenne relevante Zeitmarken im Format [mm:ss]. Wenn die Antwort nicht im Transkript steht, sag das."""


def ask(question: str, segments: list[dict], names: dict[str, str], llm: LLM | None, search_hits: list[dict]) -> dict:
    if llm is not None and llm.available():
        text = transcript_text(segments, names)
        if len(text) > MAX_CONTEXT_CHARS:
            # Nur Umgebung der Suchtreffer mitgeben.
            keep = {h["segment_id"] for h in search_hits}
            ids = [i for i, s in enumerate(segments) if s.get("id") in keep]
            window = sorted({j for i in ids for j in range(max(0, i - 3), min(len(segments), i + 4))})
            text = transcript_text([segments[j] for j in window], names)[:MAX_CONTEXT_CHARS]
        answer = llm.chat(ASK_SYSTEM, f"Transkript:\n{text}\n\nFrage: {question}")
        return {"engine": f"llm:{llm.s.llm_model}", "answer": answer, "hits": search_hits[:5]}
    if search_hits:
        lines = [f"[{fmt_time(h['start'])}] {h['speaker_name']}: {h['snippet'].replace('[[', '').replace(']]', '')}"
                 for h in search_hits[:5]]
        answer = "Kein lokales LLM erreichbar – passende Stellen im Transkript:\n" + "\n".join(lines)
    else:
        answer = "Kein lokales LLM erreichbar und keine passenden Stellen im Transkript gefunden."
    return {"engine": "suche", "answer": answer, "hits": search_hits[:5]}
