"""Export von Transkript und Zusammenfassung (TXT, Markdown, SRT, JSON)."""

from __future__ import annotations

import json
import re

from .summarize import fmt_time


def _srt_time(sec: float) -> str:
    ms = int(round(sec * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def transcript_text(segments: list[dict], names: dict[str, str]) -> str:
    """Fortlaufende Absätze; Namen nur bei tatsächlichem Sprecherwechsel ausgeben."""
    multiple = len({segment['speaker'] for segment in segments}) > 1
    paragraphs, parts = [], []
    speaker, length = None, 0
    for segment in segments:
        changed = segment['speaker'] != speaker
        if parts and (changed or (length >= 650 and re.search(r'[.!?…]["“”»]?$', parts[-1]))):
            paragraphs.append(' '.join(parts))
            parts, length = [], 0
        if multiple and changed:
            paragraphs.append(names.get(segment['speaker'], segment['speaker']) + ':')
        speaker = segment['speaker']
        parts.append(segment['text'])
        length += len(segment['text']) + 1
    if parts:
        paragraphs.append(' '.join(parts))
    return '\n\n'.join(paragraphs)


def export(meeting: dict, segments: list[dict], bookmarks: list[dict], fmt: str) -> tuple[str, str]:
    names = meeting.get("speakers") or {}
    name = lambda spk: names.get(spk, spk)  # noqa: E731
    if fmt == "txt":
        body = transcript_text(segments, names)
        return body + "\n", "text/plain; charset=utf-8"
    if fmt == "srt":
        blocks = [
            f"{i}\n{_srt_time(s['start'])} --> {_srt_time(s['end'])}\n{name(s['speaker'])}: {s['text']}\n"
            for i, s in enumerate(segments, 1)
        ]
        return "\n".join(blocks), "application/x-subrip; charset=utf-8"
    if fmt == "json":
        data = {"meeting": meeting, "segments": segments, "bookmarks": bookmarks}
        return json.dumps(data, ensure_ascii=False, indent=2), "application/json"
    if fmt == "md":
        return _markdown(meeting, segments, bookmarks), "text/markdown; charset=utf-8"
    raise ValueError(f"Unbekanntes Format: {fmt}")


def _markdown(meeting: dict, segments: list[dict], bookmarks: list[dict]) -> str:
    out = [f"# {meeting['title']}", ""]
    if meeting.get("duration"):
        out += [f"Dauer: {fmt_time(meeting['duration'])}", ""]
    summary = meeting.get("summary") or {}
    if summary.get("overview"):
        out += ["## Zusammenfassung", "", summary["overview"], ""]
    if summary.get("keywords"):
        out += ["**Stichwörter:** " + ", ".join(summary["keywords"]), ""]
    if summary.get("outline"):
        out += ["## Gliederung", ""]
        for o in summary["outline"]:
            out.append(f"- **{o.get('time', '')} {o.get('topic', '')}**")
            out += [f"  - {n}" for n in o.get("notes", [])]
        out.append("")
    if summary.get("action_items"):
        out += ["## Action Items", ""]
        for a in summary["action_items"]:
            who = f"**{a['owner']}**: " if a.get("owner") else ""
            due = f" (Frist: {a['due']})" if a.get("due") else ""
            out.append(f"- [ ] {who}{a.get('task', '')}{due}")
        out.append("")
    for key, title in (("decisions", "Entscheidungen"), ("questions", "Offene Fragen")):
        if summary.get(key):
            out += [f"## {title}", ""] + [f"- {x}" for x in summary[key]] + [""]
    if bookmarks:
        out += ["## Markierungen", ""]
        out += [f"- [{fmt_time(b['start'])}] ({b['kind']}) {b['note']}" for b in bookmarks]
        out.append("")
    out += ["## Transkript", ""]
    out.append(transcript_text(segments, meeting.get('speakers') or {}))
    return "\n".join(out) + "\n"
