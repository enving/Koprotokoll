"""Hintergrund-Verarbeitung: Konvertieren → Transkribieren → Sprecher → Zusammenfassen."""

from __future__ import annotations

import logging
import queue
import threading
from pathlib import Path
from typing import Callable

from .audio import SAMPLE_RATE, load_wav, to_wav16k
from .db import Database
from .events import EventBus
from .speech import Diarizer, Transcriber, build_utterances, language_mode, transcription_info
from .speaker_names import names_from_activity
from .summarize import LLM, summarize
from .correction import correct

log = logging.getLogger(__name__)

# Fortschrittsanteile der einzelnen Schritte (Summe = 1).
STEPS = {"converting": (0.0, 0.05), "transcribing": (0.05, 0.7), "diarizing": (0.7, 0.9), "summarizing": (0.9, 1.0)}


class Pipeline:
    def __init__(self, db: Database, media_dir: Path, transcriber: Transcriber,
                 diarizer: Diarizer | None, llm: LLM | None, events: EventBus | None = None,
                 select_transcriber: Callable[[str], Transcriber] | None = None,
                 default_language: str = "auto"):
        self.db, self.media_dir, self.events = db, media_dir, events
        self.transcriber, self.diarizer, self.llm = transcriber, diarizer, llm
        self.select_transcriber, self.default_language = select_transcriber, default_language
        self._queue: queue.Queue[int] = queue.Queue()
        self._thread = threading.Thread(target=self._run, name="pipeline", daemon=True)
        self._thread.start()

    def submit(self, meeting_id: int) -> None:
        self._queue.put(meeting_id)

    def join(self) -> None:
        self._queue.join()

    def _run(self) -> None:
        while True:
            meeting_id = self._queue.get()
            try:
                self.process(meeting_id)
            except Exception as e:  # noqa: BLE001
                log.exception("Verarbeitung von Meeting %s fehlgeschlagen", meeting_id)
                self.db.update_meeting(meeting_id, status="error", error=str(e))
                self._emit("meeting.failed", meeting_id)
            finally:
                self._queue.task_done()

    def _step(self, meeting_id: int, step: str):
        lo, hi = STEPS[step]
        self.db.update_meeting(meeting_id, status=step, progress=lo)

        def progress(frac: float) -> None:
            self.db.update_meeting(meeting_id, progress=round(lo + (hi - lo) * frac, 3))

        return progress

    def process(self, meeting_id: int) -> None:
        meeting = self.db.get_meeting(meeting_id)
        if meeting is None:
            return
        src = Path(meeting["audio_path"])
        # Originale WAV-Dateien können 44,1/48 kHz haben. Ein eigener Name
        # verhindert, dass sie fälschlich als schon normalisierte 16-kHz-Datei
        # behandelt werden.
        wav = self.media_dir / f"{meeting_id}.normalized.wav"

        self._step(meeting_id, "converting")
        if not wav.exists():
            to_wav16k(src, wav)
        audio = load_wav(wav)
        self.db.update_meeting(meeting_id, duration=round(len(audio) / SAMPLE_RATE, 2))

        originals = self.db.get_segments(meeting_id, original=True)
        if not originals and not meeting["meta"].get("original_transcript_saved"):
            mode = language_mode(meeting["meta"].get("asr_language"), self.default_language)
            transcriber = self.select_transcriber(mode) if self.select_transcriber else self.transcriber
            current = self.db.get_meeting(meeting_id)
            self.db.update_meeting(meeting_id, meta={**current["meta"], "asr_language": mode,
                                                   "asr": transcription_info(transcriber, mode)})
            words = transcriber.transcribe(audio, self._step(meeting_id, "transcribing"))

            turns = []
            diarization: dict = {"status": "disabled" if self.diarizer is None else "skipped"}
            if self.diarizer is not None and not words:
                diarization["reason"] = "Keine Wörter erkannt; Sprechererkennung nicht ausgeführt."
            if self.diarizer is not None and words:
                progress = self._step(meeting_id, "diarizing")
                try:
                    turns = self.diarizer.diarize(audio, meeting.get("num_speakers"), progress)
                    detected = len({turn.speaker for turn in turns})
                    diarization = {
                        "status": "completed" if turns else "empty",
                        "turn_count": len(turns),
                        "speaker_count": detected,
                        # Rohzeiten erlauben eine getrennte, reproduzierbare DER-Messung.
                        "turns": [{"start": float(turn.start), "end": float(turn.end), "speaker": int(turn.speaker)}
                                  for turn in turns],
                    }
                    requested = meeting.get("num_speakers")
                    if requested is not None:
                        diarization["requested_speakers"] = requested
                        if detected != requested:
                            diarization["warning"] = (
                                f"{detected} statt der angeforderten {requested} Sprecher erkannt."
                            )
                    if not turns:
                        diarization["reason"] = "Keine Sprecherzeiten erkannt; Transkript enthält nur ein Ersatzlabel."
                except Exception as e:  # noqa: BLE001 – ohne Sprecher ist das Transkript trotzdem nützlich
                    log.warning("Sprechererkennung fehlgeschlagen: %s", e)
                    diarization = {"status": "failed", "error": str(e)[:500]}

            labels: dict[int, str] = {}
            segments = build_utterances(words, turns, speaker_labels=labels)
            self.db.replace_segments(meeting_id, segments)
            current = self.db.get_meeting(meeting_id)
            # Äußerungen verlieren überlappende Turns und können Pausen einschließen.
            # Die Zuordnung braucht deshalb die echten Sprecherzeiten, mit identischen Labels.
            speaker_turns = [{"start": turn.start, "end": turn.end,
                              "speaker": labels.get(turn.speaker, f"unmapped:{turn.speaker}")} for turn in turns]
            automatic = names_from_activity(speaker_turns, current["meta"].get("speaker_activity", [])) if turns else {}
            names = {segment["speaker"]: automatic.get(segment["speaker"], f"Sprecher {segment['speaker'][1:]}")
                     for segment in segments}
            speakers = self.db.merge_speaker_names(meeting_id, names, diarization=diarization)

            current = self.db.get_meeting(meeting_id)
            self.db.update_meeting(meeting_id, meta={**current["meta"], "original_transcript_saved": True})
        else:
            speakers = meeting["speakers"]
        preferences = self.db.correction_settings()
        if preferences["enabled"] and isinstance(self.llm, LLM):
            texts, report = correct(self.db.get_segments(meeting_id, original=True), self.llm,
                                    preferences["model"], preferences["vocabulary"])
            self.db.save_correction(meeting_id, texts, report)

        self._step(meeting_id, "summarizing")
        summary = summarize(self.db.get_segments(meeting_id), speakers, self.llm)
        fields: dict = {"summary": summary, "status": "done", "progress": 1.0, "error": None}
        current = self.db.get_meeting(meeting_id)
        if summary.get("title") and current["title"] == current.get("source_name"):
            fields["title"] = summary["title"]
        self.db.update_meeting(meeting_id, **fields)
        self._emit("meeting.completed", meeting_id)

    def _emit(self, event: str, meeting_id: int) -> None:
        if self.events is None:
            return
        m = self.db.get_meeting(meeting_id)
        if m is not None:
            self.events.emit(event, meeting_payload(m))

    def resummarize(self, meeting_id: int) -> dict:
        meeting = self.db.get_meeting(meeting_id)
        summary = summarize(self.db.get_segments(meeting_id), meeting["speakers"], self.llm)
        self.db.update_meeting(meeting_id, summary=summary)
        return summary


def meeting_payload(m: dict) -> dict:
    """Öffentliche Darstellung eines Meetings für Webhooks und E-Mails."""
    payload = {k: m.get(k) for k in ("id", "title", "created_at", "duration", "status", "error",
                                    "speakers", "summary", "meta")}
    # Die rohe personenbezogene Beobachtungsreihe gehört nicht in externe Webhooks.
    payload["meta"] = {k: v for k, v in (m.get("meta") or {}).items() if k not in ("speaker_activity", "manual_speakers", "automatic_speakers")}
    return payload
