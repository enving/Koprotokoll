"""FastAPI-Server: REST-API + Weboberfläche."""

from __future__ import annotations

import io
import logging
import os
import re
import secrets
import shutil
import time
import threading
import urllib.parse
from hashlib import sha256
from dataclasses import asdict
from pathlib import Path

import numpy as np

from fastapi import APIRouter, Depends, FastAPI, File, Form, HTTPException, UploadFile, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import request_validation_exception_handler
from pydantic import BaseModel

from . import auth
from . import cli_llm
from .activity import SILENCE_SECONDS, SilenceTimer, SpeechDetector
from .bot.platforms import SUPPORTED, detect_platform, find_meeting_url, link_hint
from .caldav_source import CalDavCalendarSource, CalDavError, normalize_server_url
from .calendar_source import CalendarEvent
from .capture import HardwareRecorder
from .config import CALDAV_PRESETS, Settings
from .db import Database
from .drafts import draft_targets, open_draft, recap_eml
from .events import EVENTS, EventBus
from .export import export
from .google import GoogleCalendarSource, GoogleClient, GoogleError
from .ics_source import IcsCalendarSource, IcsError
from .microsoft import MicrosoftCalendarSource, MicrosoftClient, MicrosoftError
from .mail import MailError, MailService
from .outlook import OutlookComCalendarSource, OutlookError, outlook_available
from typing import Literal
from .pipeline import Pipeline, meeting_payload
from .recap import RecapMailer
from .scheduler import CalendarScheduler
from .speech import (Diarizer, ModelUnavailable, SherpaDiarizer, Transcriber, build_utterances, create_transcriber,
                     language_mode, transcription_info)
from .summarize import LLM, ask, validate_model_name
from .transcriptions import FORMATS, render, transcribe_upload, title_from_transcript
from .tasks import TaskError, TaskExporter, meeting_tasks, tasks_ics

log = logging.getLogger(__name__)
STATIC = Path(__file__).parent / "static"


class FreshStaticFiles(StaticFiles):
    """Oberflächendateien beim erneuten Laden stets beim Server abgleichen."""

    async def get_response(self, path: str, scope: dict) -> Response:
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response
ALLOWED_EXT = {".mp3", ".mp4", ".wav", ".m4a", ".webm", ".ogg", ".oga", ".opus", ".flac", ".aac", ".mov", ".mkv"}
# Zusätzliche Endungen, die OpenAI bei /v1/audio/transcriptions annimmt.
TRANSCRIBE_EXT = ALLOWED_EXT | {".mpeg", ".mpga"}


class CorrectionIn(BaseModel):
    enabled: bool = False
    model: str = ""
    vocabulary: list[str] = []


class TranscriptVersionIn(BaseModel):
    version: Literal["original", "corrected"]


class MeetingPatch(BaseModel):
    title: str | None = None
    speakers: dict[str, str] | None = None


class BulkDeleteIn(BaseModel):
    ids: list[int]


class BookmarkIn(BaseModel):
    start: float
    end: float
    kind: str = "soundbite"
    note: str = ""


class CaptureStart(BaseModel):
    """Explizit gestartete, lokale Aufnahme eines Betriebssystem-Audioeingangs."""

    device: str
    title: str = ""
    num_speakers: int | None = None
    channels: int = 2
    sample_rate: int = 48_000
    language: str | None = None


class RetranscribeIn(BaseModel):
    language: str


class Question(BaseModel):
    question: str


class BotIn(BaseModel):
    meeting_url: str
    title: str | None = None
    join_at: float | None = None  # Unix-Zeit; leer = sofort
    platform: str | None = None   # automatisch erkannt; "web" = beliebige Webseite


class KeyIn(BaseModel):
    name: str


class WebhookIn(BaseModel):
    url: str
    events: list[str] = ["*"]
    secret: str | None = None


class CalendarSourceIn(BaseModel):
    """Neue gespeicherte Kalenderquelle: ICS (Expertenoption) oder CalDAV."""

    kind: str                     # "ics" oder "caldav"
    label: str = ""
    url: str = ""                 # ICS-Adresse/-Datei oder CalDAV-Kalender-Kollektion
    server_url: str = ""          # CalDAV-Server für die automatische Erkennung
    provider: str = ""            # Preset-Schlüssel aus config.CALDAV_PRESETS
    username: str = ""
    password: str = ""
    own_email: str = ""


class CalendarSourcePatch(BaseModel):
    label: str | None = None
    enabled: bool | None = None
    url: str | None = None
    server_url: str | None = None
    username: str | None = None
    password: str | None = None
    own_email: str | None = None


class GoogleClientIn(BaseModel):
    client_id: str
    client_secret: str


class GoogleSettingsIn(BaseModel):
    auto_join: bool | None = None
    send_recap: bool | None = None
    recap_to_attendees: bool | None = None


class MicrosoftClientIn(BaseModel):
    client_id: str
    client_secret: str = ""


class MicrosoftSettingsIn(BaseModel):
    auto_join: bool | None = None
    send_recap: bool | None = None
    recap_to_attendees: bool | None = None


class EmailSettingsIn(BaseModel):
    provider: str | None = None
    host: str | None = None
    port: int | None = None
    user: str | None = None
    password: str | None = None
    sender: str | None = None
    tls: str | None = None
    send_recap: bool | None = None
    recap_to_attendees: bool | None = None


class TaskExportIn(BaseModel):
    target: str


class ProviderIn(BaseModel):
    provider: str
    url: str
    api_key: str | None = None
    model: str
    timeout: float = 300
    external_confirmed: bool = False


class LocalModelIn(BaseModel):
    model: str
    max_tokens: int | None = None
    reasoning_effort: str | None = None
    compatible_parameters: bool = False


class PullIn(BaseModel):
    model: str


class CliImportIn(BaseModel):
    source: str
    external_confirmed: bool = False
    api_key: str | None = None


# Deutsche Anleitung für den Fall, dass kein lokaler LLM-Server läuft (Standard: Ollama).
OLLAMA_SETUP_STEPS = [
    {"title": "Ollama installieren",
     "text": "Gratis-Download für Mac, Windows und Linux unter",
     "link": "https://ollama.com/download", "link_label": "ollama.com/download"},
    {"title": "Ollama starten",
     "text": "Die Ollama-App öffnen oder „ollama serve“ im Terminal ausführen – "
             "der Server läuft dann lokal unter http://localhost:11434."},
    {"title": "Danach hier „Erneut prüfen“",
     "text": "Sobald der Server erreichbar ist, kann die App das Modell direkt laden "
             "(ca. 5 GB Download, bleibt auf diesem Rechner)."},
]


def create_app(settings: Settings | None = None, transcriber: Transcriber | None = None,
               diarizer: Diarizer | None = None, llm: LLM | None = None,
               google: GoogleClient | None = None, background: bool = True) -> FastAPI:
    s = settings or Settings()
    s.ensure_dirs()
    db = Database(s.db_path)
    custom_transcriber = transcriber is not None
    default_mode = language_mode(None, s.asr_language)
    if transcriber is None:
        transcriber = create_transcriber(s)
    engines = {}
    engines_lock = threading.Lock()

    def select_transcriber(language: str | None = None) -> Transcriber:
        mode = language_mode(language, default_mode)
        if custom_transcriber:
            return pipeline.transcriber
        candidate = create_transcriber(s, mode)
        info = candidate.asr_info
        key = (info["model"], info["model_path"], info["quantization"])
        with engines_lock:
            return engines.setdefault(key, candidate)

    if not custom_transcriber:
        info = transcriber.asr_info
        engines[(info["model"], info["model_path"], info["quantization"])] = transcriber
    if diarizer is None and s.diarization:
        diarizer = SherpaDiarizer(s.models_dir, s.diarization_threshold)
    if llm is None:
        llm = LLM(s)
    events = EventBus(db)
    pipeline = Pipeline(db, s.media_dir, transcriber, diarizer, llm, events,
                        select_transcriber=None if custom_transcriber else select_transcriber,
                        default_language=default_mode)
    capture = HardwareRecorder(s.data_dir / "captures")
    capture_lock = threading.RLock()
    speech_detector = SpeechDetector(Path(s.asr_vad_path).expanduser() if s.asr_vad_path
                                     else s.models_dir / "silero-vad-onnx")
    google = google or GoogleClient(s, db)
    microsoft = MicrosoftClient(s, db)
    mail = MailService(s, db, google, microsoft)
    task_exporter = TaskExporter(db, microsoft)
    events.subscribe(RecapMailer(mail, s.public_url))
    from .bot.runner import BotManager

    bots = BotManager(s, db, pipeline, events, tick_seconds=5.0 if background else 3600)

    def stored_source(row: dict):
        """Gespeicherte Quelle aus der Datenbank als CalendarSource-Objekt."""
        cfg = row["config"]
        if row["kind"] == "ics":
            return IcsCalendarSource(f"ics-{row['id']}", row["label"], cfg.get("url", ""),
                                     own_email=cfg.get("own_email", ""), enabled=row["enabled"],
                                     base_dir=s.data_dir)
        if row["kind"] == "outlook":
            return OutlookComCalendarSource(f"outlook-{row['id']}", row["label"], enabled=row["enabled"])
        return CalDavCalendarSource(f"caldav-{row['id']}", row["label"],
                                    server_url=cfg.get("server_url", ""),
                                    calendar_url=cfg.get("url", ""),
                                    username=cfg.get("username", ""),
                                    password=cfg.get("password", ""),
                                    own_email=cfg.get("own_email", ""),
                                    enabled=row["enabled"])

    def build_sources() -> list:
        sources: list = [GoogleCalendarSource(google), MicrosoftCalendarSource(microsoft)]
        for row in db.list_calendar_sources():
            sources.append(stored_source(row))
        return sources

    def refresh_sources() -> None:
        """Quellenliste des Schedulers nach Änderungen neu aufbauen."""
        calendar.sources = build_sources()

    calendar = CalendarScheduler(db, build_sources(), s.calendar_poll_seconds, s.jitsi_hosts, s.bbb_hosts)
    if background:
        calendar.start()

    # Nach einem Neustart unterbrochene Jobs erneut einreihen.
    for m in db.list_meetings():
        if m["status"] not in ("done", "error"):
            pipeline.submit(m["id"])

    app = FastAPI(
        title="Koprotokoll API",
        description="Lokaler Meeting-Assistent: Bots, Transkripte (Parakeet), Zusammenfassungen, Suche, "
                    "Webhooks und Google-Integration. Authentifizierung: `Authorization: Bearer kp_…` "
                    "(lokale Anfragen ohne Proxy brauchen keinen Schlüssel).",
        version="2.0",
    )
    app.state.db, app.state.pipeline, app.state.settings, app.state.capture = db, pipeline, s, capture
    app.state.speech_detector = speech_detector
    app.state.capture_request = {}
    app.state.last_capture = {}
    app.state.capture_clock = time.monotonic
    app.state.capture_stop_event = threading.Event()

    def check_capture_silence(request: dict) -> bool:
        with capture_lock:
            if app.state.capture_request is not request:
                return False
            speech = None
            try:
                tracks = capture.speech_audio()
                if tracks and all(track is not None for track in tracks):
                    speech = any(speech_detector.has_speech(track) for track in tracks)
                    app.state.capture_warning = None
            except Exception as exc:
                app.state.capture_warning = f"Automatischer Stopp nicht verfügbar: {exc}"
            if app.state.capture_timer.check(speech, app.state.capture_clock()):
                try:
                    save_hardware_capture("silence")
                except HTTPException as exc:
                    app.state.last_capture = {"error": exc.detail}
                return False
            return True

    app.state.check_capture_silence = check_capture_silence
    app.state.events, app.state.bots, app.state.google, app.state.calendar = events, bots, google, calendar
    api = APIRouter(dependencies=[Depends(auth.require_auth)])
    public = APIRouter()

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError):
        if request.url.path in ("/api/llm/provider", "/api/integrations/email", "/api/integrations/microsoft/client", "/api/integrations/google/client"):
            details = [{key: value for key, value in item.items() if key not in ("input", "ctx")}
                       for item in error.errors()]
            return JSONResponse(status_code=422, content={"detail": details})
        return await request_validation_exception_handler(request, error)

    @api.get("/api/integrations/email", tags=["email"])
    def email_status():
        return mail.status()

    @api.put("/api/integrations/email", tags=["email"])
    def email_settings(body: EmailSettingsIn):
        try:
            return mail.update(body.model_dump(exclude_none=True))
        except MailError as e:
            raise HTTPException(400, str(e)) from e

    @api.post("/api/integrations/email/test", tags=["email"])
    def email_test():
        try:
            own = mail.own_email()
            if not own:
                raise MailError("Eigene E-Mail-Adresse fehlt. Bitte den Mailweg einrichten.")
            mail.send([own], "Koprotokoll: Test-Mail", "Der Mailversand funktioniert.",
                      "<p>Der Mailversand funktioniert.</p>")
        except (MailError, GoogleError, MicrosoftError, OSError) as e:
            raise HTTPException(400, str(e)) from e
        return {"ok": True}

    def meeting_or_404(meeting_id: int) -> dict:
        m = db.get_meeting(meeting_id)
        if m is None:
            raise HTTPException(404, "Meeting nicht gefunden")
        return m

    def _delete_meeting_files(m: dict) -> None:
        """Datenbankzeile und Audiodateien eines Meetings entfernen (nur media_dir)."""
        db.delete_meeting(m["id"])
        files = list(s.media_dir.glob(f"{m['id']}.*"))
        if m["audio_path"]:
            files.append(Path(m["audio_path"]))
        for p in files:
            if p.resolve().parent == s.media_dir.resolve():
                p.unlink(missing_ok=True)

    def safe_download_name(title: str, suffix: str) -> str:
        """Dateiname für Downloads: Umlaute aufschreiben, Sonderzeichen ersetzen."""
        name = title or "aufnahme"
        for umlaut, ascii_ersatz in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"),
                                     ("Ä", "Ae"), ("Ö", "Oe"), ("Ü", "Ue"), ("ß", "ss")):
            name = name.replace(umlaut, ascii_ersatz)
        name = re.sub(r"[^\w.-]+", "_", name).strip("._")[:80] or "aufnahme"
        return f"{name}{suffix}"

    def audio_file(meeting_id: int, m: dict) -> Path | None:
        """Audiodatei eines Meetings: bevorzugt die WAV-Fassung, sonst die Originaldatei."""
        wav = s.media_dir / f"{meeting_id}.wav"
        if wav.is_file():
            return wav
        path = Path(m.get("audio_path") or "")
        return path if path.is_file() else None

    def finished_meeting(meeting_id: int) -> dict:
        meeting = meeting_or_404(meeting_id)
        if meeting["status"] != "done":
            raise HTTPException(409, "Das Meeting muss zuerst fertig ausgewertet sein.")
        return meeting

    def tasks_for(meeting_id: int) -> list[dict]:
        meeting = finished_meeting(meeting_id)
        try:
            return meeting_tasks(meeting, db.get_segments(meeting_id), s.public_url, db.task_namespace())
        except TaskError as e:
            raise HTTPException(400, str(e)) from e

    @api.get("/api/tasks/targets", tags=["tasks"])
    def task_targets():
        return task_exporter.targets()

    @api.get("/api/meetings/{meeting_id}/aufgaben.ics", tags=["tasks"])
    def download_tasks(meeting_id: int):
        return Response(tasks_ics(tasks_for(meeting_id)), media_type="text/calendar",
                        headers={"Content-Disposition": f'attachment; filename="meeting-{meeting_id}-aufgaben.ics"'})

    @api.post("/api/meetings/{meeting_id}/tasks/export", tags=["tasks"])
    def write_tasks(meeting_id: int, body: TaskExportIn):
        tasks = tasks_for(meeting_id)
        try:
            return task_exporter.export(body.target, tasks)
        except (TaskError, CalDavError, MicrosoftError) as e:
            raise HTTPException(400, str(e)) from e
        except Exception:
            raise HTTPException(400, "Aufgabenexport fehlgeschlagen. Bereits angelegte Aufgaben bleiben erhalten; Verbindung prüfen und erneut exportieren.") from None

    @api.get("/api/integrations/email/drafts", tags=["email"])
    def email_draft_targets(request: Request):
        return {"targets": draft_targets(auth.is_local(request))}

    @api.get("/api/meetings/{meeting_id}/recap.eml", tags=["email"])
    def download_recap_eml(meeting_id: int):
        try:
            content = recap_eml(finished_meeting(meeting_id), s.public_url, mail.own_email())
        except MailError as e:
            raise HTTPException(400, str(e)) from e
        return Response(content, media_type="message/rfc822",
                        headers={"Content-Disposition": f'attachment; filename="meeting-{meeting_id}.eml"'})

    @api.post("/api/meetings/{meeting_id}/recap/draft", tags=["email"])
    def create_recap_draft(meeting_id: int, request: Request, ziel: str):
        if not auth.is_local(request):
            raise HTTPException(403, "Native Mailprogramme können nur direkt am Server geöffnet werden. EML-Datei herunterladen.")
        try:
            open_draft(ziel, finished_meeting(meeting_id), s.public_url, s.data_dir)
        except MailError as e:
            raise HTTPException(400, str(e)) from e
        return {"ok": True}

    @api.get("/api/transcript-correction")
    def correction_settings():
        return db.correction_settings()

    @api.put("/api/transcript-correction")
    def save_correction_settings(body: CorrectionIn):
        if (body.enabled and not body.model.strip()) or len(body.model) > 200:
            raise HTTPException(400, "Bitte ein Korrekturmodell angeben.")
        if len(body.vocabulary) > 200 or any(not word.strip() or len(word) > 100 for word in body.vocabulary):
            raise HTTPException(400, "Wortliste: höchstens 200 Begriffe mit je 100 Zeichen.")
        db.set_kv("transcript_correction", {**body.model_dump(), "model": body.model.strip()})
        return db.correction_settings()

    @api.put("/api/meetings/{meeting_id}/transcript-version")
    def select_transcript(meeting_id: int, body: TranscriptVersionIn):
        if db.get_meeting(meeting_id) is None:
            raise HTTPException(404, "Meeting nicht gefunden.")
        db.select_transcript(meeting_id, body.version)
        return {"ok": True}

    @api.put("/api/llm/provider")
    def save_provider(body: ProviderIn):
        from urllib.parse import urlsplit
        from .config import external_llm
        import math
        url = body.url.strip().rstrip("/")
        try:
            parsed = urlsplit(url)
            parsed.port
        except ValueError:
            raise HTTPException(400, "Ungültige Basis-URL.")
        if (parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username
                or parsed.password or parsed.query or parsed.fragment):
            raise HTTPException(400, "Bitte eine HTTP(S)-Basis-URL ohne Zugangsdaten eingeben.")
        if body.provider not in ("ollama", "openai", "mistral", "other") or not body.model.strip():
            raise HTTPException(400, "Anbieter und Modellname prüfen.")
        if not math.isfinite(body.timeout) or not 0.01 <= body.timeout <= 3600:
            raise HTTPException(400, "Timeout muss zwischen 0,01 und 3600 Sekunden liegen.")
        effective = os.environ.get("KOPROTOKOLL_LLM_URL", url)
        if external_llm(effective) and not body.external_confirmed:
            raise HTTPException(400, "Transkripttext wird an diesen Dienst gesendet. Bitte ausdrücklich bestätigen.")
        values = dict(provider=body.provider, url=url, model=body.model.strip(), timeout=body.timeout,
                      confirmed_url=effective if body.external_confirmed else None)
        if body.api_key is not None:
            if any(ord(c) < 32 or ord(c) > 126 for c in body.api_key):
                raise HTTPException(400, "API-Schlüssel enthält ungültige Zeichen.")
            values["api_key"] = body.api_key
        s.save_provider(values)
        return {"ok": True}

    @api.post("/api/llm/test")
    def test_provider():
        state = local_model()
        if state.get("model_present"):
            try:
                llm.chat("Antworte kurz.", "Antworte mit OK.")
                state["inference"] = "erfolgreich"
            except Exception:
                state["inference"] = "fehlgeschlagen (Antwort, Zugang oder Timeout prüfen)"
        return state

    @api.get("/api/llm")
    def local_model():
        state = llm.status() if isinstance(llm, LLM) else dict(models=[], model=s.llm_model, url=s.llm_base_url, available=llm.available(), message="Modellstatus nicht verfügbar.")
        from .config import external_llm
        return {**state, "provider": s.llm_provider, "timeout": s.llm_timeout,
                "api_key_set": bool(s.llm_api_key), "external": external_llm(s.llm_base_url),
                "external_confirmed": s.llm_external_confirmed,
                "env_overrides": [key for key, attr, env in s.llm_fields() if env in os.environ],
                "model_env_override": "KOPROTOKOLL_LLM_MODEL" in os.environ,
                "max_tokens": s.llm_max_tokens, "reasoning_effort": s.llm_reasoning_effort}

    @api.put("/api/llm")
    def select_local_model(body: LocalModelIn):
        if body.model not in llm.status()["models"]:
            raise HTTPException(400, "Bitte ein vorhandenes Modell auswählen.")
        if body.max_tokens is not None and not 1 <= body.max_tokens <= 32768:
            raise HTTPException(400, "Ausgabelimit muss zwischen 1 und 32768 liegen.")
        if body.reasoning_effort not in (None, "none", "low", "medium", "high"):
            raise HTTPException(400, "Ungültige Thinking-Einstellung.")
        if (body.max_tokens is not None or body.reasoning_effort is not None) and not body.compatible_parameters:
            raise HTTPException(400, "Unterstützung der optionalen Parameter für Server und Modell bestätigen.")
        s.save_llm(body.model, body.max_tokens, body.reasoning_effort)
        return local_model()

    # ---------------------------------------------- Sprachmodell einrichten
    pull_lock = threading.Lock()
    pull_state: dict = {"running": False, "model": None, "status": "", "total": 0, "completed": 0,
                        "done": False, "error": None, "cancel": None}

    def pull_snapshot() -> dict:
        with pull_lock:
            return {k: v for k, v in pull_state.items() if k != "cancel"}

    def _pull_progress(model: str, status_text: str, total: int, completed: int) -> None:
        with pull_lock:
            pull_state.update(running=True, model=model, status=status_text, total=total,
                              completed=completed, done=False, error=None)

    def _run_pull(model: str) -> None:
        cancel = threading.Event()
        with pull_lock:
            pull_state.update(running=True, model=model, status="gestartet", total=0, completed=0,
                              done=False, error=None, cancel=cancel)
        try:
            llm.pull_model(model, progress=lambda st, t, c: _pull_progress(model, st, t, c), cancel=cancel)
            with pull_lock:
                pull_state.update(running=False, status="success", done=True)
        except Exception as e:  # noqa: BLE001 – ein abgebrochener/fehlerhafter Download darf den Server nicht stoppen
            with pull_lock:
                pull_state.update(running=False, done=True,
                                  status="abgebrochen" if cancel.is_set() else "fehler",
                                  error=None if cancel.is_set() else str(e)[:300])
        finally:
            with pull_lock:
                pull_state["cancel"] = None

    @api.get("/api/llm/setup")
    def llm_setup():
        """Was fehlt, damit ein lokales Sprachmodell läuft – und welcher Schritt als Nächstes ansteht."""
        if isinstance(llm, LLM):
            state = llm.status()
        else:
            state = dict(models=[], model=s.llm_model, url=s.llm_base_url, available=False, enabled=True,
                         reason="unavailable", server_reachable=False, model_present=False,
                         message="Modellstatus nicht verfügbar.")
        supported = isinstance(llm, LLM) and llm.pull_supported()
        reason = state.get("reason")
        from .config import external_llm
        if reason in ("disabled", "external_blocked", "unavailable"):
            step = "disabled"
        elif not state.get("server_reachable"):
            # Ohne lokalen Server: Ollama-Anleitung; externer Endpunkt: Verbindung prüfen.
            step = "install_server" if not external_llm(s.llm_base_url) else "check_endpoint"
        elif not state.get("model_present"):
            step = "pull_model" if supported else "select_model"
        else:
            step = "ready"
        return {**state, "provider": s.llm_provider, "pull_supported": supported, "setup_step": step,
                "suggested_model": s.llm_model, "size_hint": "ca. 5 GB",
                "instructions": OLLAMA_SETUP_STEPS if step == "install_server" else []}

    @api.post("/api/llm/pull", status_code=201)
    def start_pull(body: PullIn):
        """Lädt das Modell über die lokale Ollama-API; Fortschritt siehe GET /api/llm/pull."""
        if not isinstance(llm, LLM) or not llm.pull_supported():
            raise HTTPException(400, "Modell-Download wird nur für einen lokalen Ollama-Server unterstützt "
                                     "(Anbieter „ollama“, lokale URL).")
        try:
            model = validate_model_name(body.model)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        with pull_lock:
            if pull_state["running"]:
                raise HTTPException(409, "Es läuft bereits ein Modell-Download.")
            pull_state.update(running=True, model=model, status="gestartet", total=0, completed=0,
                              done=False, error=None)
        threading.Thread(target=_run_pull, args=(model,), name="llm-pull", daemon=True).start()
        return pull_snapshot()

    @api.get("/api/llm/pull")
    def pull_progress():
        return {**pull_snapshot(), "supported": isinstance(llm, LLM) and llm.pull_supported()}

    @api.post("/api/llm/pull/cancel")
    def cancel_pull():
        with pull_lock:
            running = pull_state["running"]
            cancel = pull_state.get("cancel")
        if not running or cancel is None:
            raise HTTPException(409, "Es läuft gerade kein Download.")
        cancel.set()
        if isinstance(llm, LLM):
            llm.cancel_pull()
        return {"cancelled": True}

    @api.get("/api/llm/cli-sources")
    def llm_cli_sources():
        """Gefundene Coding-CLI-Quellen (z. B. Vibe) – ohne Schlüsselmaterial."""
        return {"sources": cli_llm.cli_sources()}

    @api.post("/api/llm/cli-import")
    def llm_cli_import(body: CliImportIn):
        """Übernimmt Endpunkt und Modell aus der Coding CLI; Schlüssel bleibt lokal (0600)."""
        from .config import external_llm
        try:
            imported = cli_llm.import_cli_source(body.source)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        model = imported["model"].strip()
        if not model or len(model) > 200:
            raise HTTPException(400, "Modellname aus der CLI-Konfiguration ist ungültig.")
        key = (body.api_key or "").strip() or imported["api_key"] or ""
        if not key:
            raise HTTPException(400, "In der CLI wurde kein API-Schlüssel gefunden (Umgebung "
                                     "MISTRAL_API_KEY oder macOS-Schlüsselbund). Bitte einmal direkt eintragen.")
        if not cli_llm.valid_api_key(key):
            raise HTTPException(400, "API-Schlüssel enthält ungültige Zeichen.")
        effective = os.environ.get("KOPROTOKOLL_LLM_URL", imported["url"])
        if external_llm(effective) and not body.external_confirmed:
            raise HTTPException(400, "Transkripttext wird an diesen Dienst gesendet. Bitte ausdrücklich bestätigen.")
        s.save_provider(dict(provider=imported["provider"], url=imported["url"], model=model,
                             timeout=s.llm_timeout, api_key=key,
                             confirmed_url=effective if body.external_confirmed else None))
        return local_model()

    @api.get("/api/status")
    def status():
        return {
            "asr_model": transcription_info(transcriber, default_mode)["model"],
            "asr": transcription_info(transcriber, default_mode),
            "diarization": diarizer is not None,
            "llm": llm.status() if isinstance(llm, LLM) else {"available": llm.available(), "model": s.llm_model, "url": s.llm_base_url},
        }

    @api.get("/api/meetings")
    def list_meetings():
        return [{k: m[k] for k in ("id", "title", "created_at", "duration", "status", "progress", "error")}
                for m in db.list_meetings()]

    @api.get("/api/capture/devices", tags=["recording"])
    def capture_devices():
        """Lokale Hardware-/Loopback-Eingänge; der Server ändert kein Audiogerät."""
        try:
            return capture.devices()
        except RuntimeError as exc:
            raise HTTPException(503, str(exc)) from exc

    @api.get("/api/capture", tags=["recording"])
    def capture_status():
        with capture_lock:
            result = capture.status()
            if not result["active"]:
                result.update(app.state.last_capture)
            if getattr(app.state, "capture_warning", None):
                result["warning"] = app.state.capture_warning
            return result

    @api.post("/api/capture/activity", tags=["recording"])
    def speech_activity(file: UploadFile = File(...)):
        """Kurzen PCM-Ausschnitt lokal auf Sprache prüfen, ohne ihn zu speichern."""
        data = file.file.read(64002)
        if not 16000 <= len(data) <= 64000 or len(data) % 2:
            raise HTTPException(400, "Erwartet werden 0,5–2 Sekunden PCM16-Mono mit 16 kHz.")
        try:
            speech = speech_detector.has_speech(np.frombuffer(data, dtype='<i2').astype(np.float32) / 32768)
        except Exception as exc:
            raise HTTPException(503, f"Sprachaktivität konnte nicht geprüft werden: {exc}") from exc
        return {"speech": speech, "silence_seconds": SILENCE_SECONDS}

    @api.post("/api/capture/start", tags=["recording"])
    def start_hardware_capture(body: CaptureStart):
        if body.num_speakers is not None and body.num_speakers < 1:
            raise HTTPException(400, "Die Sprecherzahl muss positiv sein")
        try:
            mode = language_mode(body.language, default_mode)
            select_transcriber(mode)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        with capture_lock:
            try:
                state = capture.start(body.device, body.channels, body.sample_rate)
            except (RuntimeError, ValueError) as exc:
                raise HTTPException(409, str(exc)) from exc
            state["title"] = body.title.strip()
            state["num_speakers"] = body.num_speakers
            state["asr_language"] = mode
            app.state.capture_request = state
            app.state.last_capture = {}
            app.state.capture_warning = None
            app.state.capture_timer = SilenceTimer(app.state.capture_clock())
            stopped = threading.Event()
            app.state.capture_stop_event = stopped
            if background:
                def watch():
                    while not stopped.wait(1):
                        if not check_capture_silence(state):
                            break
                threading.Thread(target=watch, name="capture-silence", daemon=True).start()
        return state

    @api.post("/api/capture/stop", status_code=201, tags=["recording"])
    def stop_hardware_capture():
        with capture_lock:
            return save_hardware_capture()

    def save_hardware_capture(reason: str | None = None):
        app.state.capture_stop_event.set()
        try:
            path, duration, device = capture.stop()
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc
        request = getattr(app.state, "capture_request", {})
        title = request.get("title") or f"Hardwareaufnahme {time.strftime('%Y-%m-%d %H:%M')}"
        speakers = request.get("num_speakers")
        meeting_id = db.create_meeting(title, path.stem, speakers,
                                       meta={"source": "hardware", "device": device,
                                             **({"stop_reason": reason} if reason else {}),
                                             "asr_language": request.get("asr_language", default_mode)})
        original = s.media_dir / f"{meeting_id}.original.wav"
        shutil.move(str(path), original)
        db.update_meeting(meeting_id, audio_path=str(original), duration=round(duration, 2))
        pipeline.submit(meeting_id)
        app.state.capture_request = {}
        app.state.last_capture = {"meeting_id": meeting_id, "stop_reason": reason}
        return db.get_meeting(meeting_id)

    @api.post("/api/meetings", status_code=201)
    def upload(file: UploadFile = File(...), title: str = Form(""), num_speakers: int | None = Form(None),
               language: str | None = Form(None), stop_reason: str | None = Form(None)):
        name = Path(file.filename or "aufnahme.webm").name
        ext = Path(name).suffix.lower()
        if ext not in ALLOWED_EXT:
            raise HTTPException(400, f"Dateityp {ext or '?'} wird nicht unterstützt")
        if stop_reason not in (None, "silence"):
            raise HTTPException(400, "Unbekannter Grund für das Aufnahmeende.")
        try:
            mode = language_mode(language, default_mode)
            select_transcriber(mode)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        meeting_id = db.create_meeting(title.strip() or Path(name).stem, Path(name).stem,
                                       num_speakers if num_speakers and num_speakers > 0 else None,
                                       meta={"asr_language": mode, **({"stop_reason": stop_reason} if stop_reason else {})})
        dst = s.media_dir / f"{meeting_id}{ext}"
        with dst.open("wb") as out:
            shutil.copyfileobj(file.file, out)
        db.update_meeting(meeting_id, audio_path=str(dst))
        pipeline.submit(meeting_id)
        return db.get_meeting(meeting_id)

    @api.get("/api/meetings/{meeting_id}")
    def get_meeting(meeting_id: int):
        m = meeting_or_404(meeting_id)
        audio = audio_file(meeting_id, m)
        m["audio"] = ({"suffix": audio.suffix.lower() or ".wav", "bytes": audio.stat().st_size}
                      if audio else None)
        m.pop("audio_path", None)
        m["segments"] = db.get_segments(meeting_id)
        m["bookmarks"] = db.list_bookmarks(meeting_id)
        m["talk_time"] = talk_time(m["segments"])
        return m

    @api.post("/api/meetings/{meeting_id}/retranscribe", status_code=201)
    def retranscribe(meeting_id: int, body: RetranscribeIn):
        """Audio erneut erkennen; die bisherige Fassung bleibt als eigenes Meeting erhalten."""
        m = meeting_or_404(meeting_id)
        if m["status"] not in ("done", "error"):
            raise HTTPException(409, "Die Aufnahme wird noch verarbeitet. Bitte warten.")
        try:
            mode = language_mode(body.language, default_mode)
            select_transcriber(mode)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        source = Path(m["audio_path"] or "")
        if not source.is_file():
            raise HTTPException(404, "Audio nicht vorhanden; bitte die Aufnahme erneut hochladen.")
        labels = {"de": "Deutsch", "en": "Englisch", "auto": "Automatisch"}
        new_id = db.create_meeting(f"{m['title']} ({labels[mode]})", m["source_name"], m["num_speakers"],
                                   meta={"asr_language": mode, "retranscribed_from": meeting_id})
        destination = s.media_dir / f"{new_id}.original{source.suffix}"
        try:
            shutil.copyfile(source, destination)
        except OSError as exc:
            destination.unlink(missing_ok=True)
            db.delete_meeting(new_id)
            raise HTTPException(500, "Die Aufnahme konnte nicht kopiert werden. Bitte erneut versuchen.") from exc
        db.update_meeting(new_id, audio_path=str(destination), duration=m["duration"])
        pipeline.submit(new_id)
        return db.get_meeting(new_id)

    @api.patch("/api/meetings/{meeting_id}")
    def patch_meeting(meeting_id: int, patch: MeetingPatch):
        m = meeting_or_404(meeting_id)
        fields: dict = {}
        if patch.title is not None and patch.title.strip():
            fields["title"] = patch.title.strip()
        if patch.speakers is not None:
            db.merge_speaker_names(meeting_id, {k: v.strip() for k, v in patch.speakers.items() if v.strip()},
                                   manual=True)
        db.update_meeting(meeting_id, **fields)
        return {"ok": True}

    @api.delete("/api/meetings/{meeting_id}")
    def delete_meeting(meeting_id: int):
        m = meeting_or_404(meeting_id)
        _delete_meeting_files(m)
        return {"ok": True, "id": m["id"]}

    @api.post("/api/meetings/bulk-delete")
    def bulk_delete_meetings(body: BulkDeleteIn):
        """Mehrere Meetings auf einmal löschen; unbekannte IDs werden übersprungen."""
        deleted = []
        for mid in dict.fromkeys(body.ids):
            m = db.get_meeting(mid)
            if m is None:
                continue
            _delete_meeting_files(m)
            deleted.append(mid)
        return {"ok": True, "deleted": deleted}

    @api.post("/api/meetings/{meeting_id}/single-speaker")
    def single_speaker(meeting_id: int):
        meeting_or_404(meeting_id)
        try:
            db.set_single_speaker(meeting_id)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"ok": True}

    @api.delete("/api/meetings/{meeting_id}/single-speaker")
    def restore_speakers(meeting_id: int):
        meeting_or_404(meeting_id)
        try:
            db.set_single_speaker(meeting_id, restore=True)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"ok": True}

    @api.get("/api/meetings/{meeting_id}/audio")
    def audio(meeting_id: int, download: bool = False):
        m = meeting_or_404(meeting_id)
        path = audio_file(meeting_id, m)
        if path is None:
            raise HTTPException(404, "Audio nicht vorhanden")
        if download:
            suffix = path.suffix.lower() or ".wav"
            return FileResponse(path, filename=safe_download_name(m["title"], suffix))
        return FileResponse(path)

    @api.post("/api/meetings/{meeting_id}/summarize")
    def resummarize(meeting_id: int):
        meeting_or_404(meeting_id)
        return pipeline.resummarize(meeting_id)

    @api.post("/api/meetings/{meeting_id}/ask")
    def ask_meeting(meeting_id: int, q: Question):
        m = meeting_or_404(meeting_id)
        hits = db.search(q.question, meeting_id, limit=10, any_word=True)
        return ask(q.question, db.get_segments(meeting_id), m["speakers"], llm, hits)

    @api.post("/api/meetings/{meeting_id}/bookmarks", status_code=201)
    def add_bookmark(meeting_id: int, b: BookmarkIn):
        meeting_or_404(meeting_id)
        bid = db.add_bookmark(meeting_id, b.start, max(b.end, b.start), b.kind, b.note)
        return {"id": bid}

    @api.delete("/api/meetings/{meeting_id}/bookmarks/{bookmark_id}")
    def delete_bookmark(meeting_id: int, bookmark_id: int):
        if not db.delete_bookmark(meeting_id, bookmark_id):
            raise HTTPException(404, "Markierung nicht gefunden")
        return {"ok": True}

    @api.get("/api/meetings/{meeting_id}/export")
    def export_meeting(meeting_id: int, format: str = "md"):
        m = meeting_or_404(meeting_id)
        m.pop("audio_path", None)
        try:
            body, mime = export(m, db.get_segments(meeting_id), db.list_bookmarks(meeting_id), format)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        filename = safe_download_name(m["title"], f".{format}")
        return Response(body, media_type=mime,
                        headers={"Content-Disposition": f'attachment; filename="{filename}"'})

    @api.get("/api/search")
    def search(q: str, meeting_id: int | None = None):
        return db.search(q, meeting_id)

    # -- OpenAI-kompatible Transkription (z. B. für Kodiktat) ------------------------------
    @api.post("/v1/audio/transcriptions", tags=["openai"])
    def openai_transcribe(file: UploadFile = File(...), model: str = Form(""), language: str | None = Form(None),
                          prompt: str = Form(""), response_format: str = Form("json"),
                          temperature: float | None = Form(None), save_meeting: bool = Form(False),
                          num_speakers: int | None = Form(None)):
        # model, prompt und temperature bleiben Kompatibilitätsfelder; language wählt den Modus.
        if response_format not in FORMATS:
            raise HTTPException(400, f"Unbekanntes response_format: {response_format} "
                                     f"(erlaubt: {', '.join(FORMATS)})")
        ext = Path(file.filename or "aufnahme.wav").suffix.lower() or ".wav"
        if ext not in TRANSCRIBE_EXT:
            raise HTTPException(400, f"Dateityp {ext} wird nicht unterstützt")
        if save_meeting and num_speakers is not None and num_speakers < 1:
            raise HTTPException(400, "Die Sprecherzahl muss positiv sein")
        try:
            mode = language_mode(language, default_mode)
            selected = select_transcriber(mode)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        raw = file.file.read()
        try:
            words, duration = transcribe_upload(io.BytesIO(raw), ext, selected, s.data_dir / "tmp")
        except ModelUnavailable as e:
            raise HTTPException(503, str(e)) from e
        except RuntimeError as e:
            raise HTTPException(400, str(e)) from e
        except Exception as e:
            log.exception("Lokale Transkription fehlgeschlagen (%s)", selected)
            raise HTTPException(503, f"Lokale Transkription fehlgeschlagen: {type(e).__name__}: {e}") from e
        info = transcription_info(selected, mode)
        meeting_id = None
        if save_meeting:
            # Schlank ablegen: Audio nach data/media, Segmente direkt aus der
            # ohnehin berechneten Transkription übernehmen – die Pipeline läuft
            # nicht erneut (kein zweites Transkribieren, keine Sprechererkennung,
            # keine Zusammenfassung).
            text = " ".join(w.text for w in words).strip()
            meeting_id = db.create_meeting(title_from_transcript(text), Path(file.filename or "aufnahme").stem,
                                           num_speakers,
                                           meta={"asr_language": mode, "source": "api-transcription", "asr": info})
            dst = s.media_dir / f"{meeting_id}{ext}"
            dst.write_bytes(raw)
            segments = build_utterances(words, [])
            db.replace_segments(meeting_id, segments)
            diarization = ({"status": "fixed", "speaker_count": 1, "requested_speakers": 1, "source": "setting"}
                           if num_speakers == 1 else {"status": "disabled"})
            db.merge_speaker_names(meeting_id, {s["speaker"]: f"Sprecher {s['speaker'][1:]}" for s in segments},
                                   diarization=diarization)
            db.update_meeting(meeting_id, audio_path=str(dst), duration=duration,
                              status="done", progress=1.0, error=None)
            events.emit("meeting.completed", meeting_payload(db.get_meeting(meeting_id)))
        body, mime = render(words, duration, response_format, None)
        headers = {"X-Koprotokoll-ASR-Model": info["model"], "X-Koprotokoll-ASR-Mode": mode,
                   "X-Koprotokoll-ASR-Backend": info["backend"]}
        if meeting_id is not None:
            headers["X-Koprotokoll-Meeting"] = str(meeting_id)
        if isinstance(body, dict):
            body["asr"] = info
            return JSONResponse(body, headers=headers)
        return Response(body, media_type=mime, headers=headers)

    # -- Bots (Teilnehmer in Online-Meetings) -------------------------------------------
    @api.post("/api/bots", status_code=201, tags=["bots"])
    def create_bot(b: BotIn):
        url = b.meeting_url.strip()
        platform = b.platform or detect_platform(url, s.jitsi_hosts, s.bbb_hosts)
        if not url.startswith(("http://", "https://")):
            raise HTTPException(400, "meeting_url muss mit http:// oder https:// beginnen")
        if platform is None:
            raise HTTPException(400, link_hint(url) or "Unbekannter Meeting-Link – für beliebige Webseiten platform='web' setzen")
        if platform not in SUPPORTED:
            raise HTTPException(400, f"Plattform {platform} wird vom Bot noch nicht unterstützt "
                                     f"(unterstützt: {', '.join(sorted(SUPPORTED))})")
        bot_id = db.create_bot(url, platform, (b.title or "").strip() or "Meeting", b.join_at or time.time())
        bots.dispatch_due()
        return db.get_bot(bot_id)

    @api.get("/api/bots", tags=["bots"])
    def list_bots():
        return db.list_bots()

    @api.get("/api/bots/{bot_id}", tags=["bots"])
    def get_bot(bot_id: int):
        bot = db.get_bot(bot_id)
        if bot is None:
            raise HTTPException(404, "Bot nicht gefunden")
        return bot

    @api.delete("/api/bots/{bot_id}", tags=["bots"])
    def stop_bot(bot_id: int):
        if not bots.stop(bot_id):
            raise HTTPException(404, "Kein aktiver oder geplanter Bot mit dieser ID")
        return {"ok": True}

    # -- API-Schlüssel ---------------------------------------------------------------------
    @api.get("/api/keys", tags=["admin"])
    def list_keys():
        return db.list_api_keys()

    @api.get("/api/tokens", tags=["admin"])
    def check_token():
        """Schlüsselprüfung für Kodiktat: 200 mit gültigem Schlüssel, sonst 401."""
        return {"ok": True}

    @api.post("/api/keys", status_code=201, tags=["admin"])
    def create_key(k: KeyIn):
        return auth.create_key(db, k.name.strip() or "API-Schlüssel")

    @api.delete("/api/keys/{key_id}", tags=["admin"])
    def delete_key(key_id: int):
        if not db.delete_api_key(key_id):
            raise HTTPException(404, "Schlüssel nicht gefunden")
        return {"ok": True}

    # -- Webhooks -------------------------------------------------------------------------------
    @api.get("/api/webhooks", tags=["webhooks"])
    def list_webhooks():
        return [{**h, "secret": h["secret"][:6] + "…"} for h in db.list_webhooks()]

    @api.post("/api/webhooks", status_code=201, tags=["webhooks"])
    def add_webhook(w: WebhookIn):
        if not w.url.startswith(("http://", "https://")):
            raise HTTPException(400, "Webhook-URL muss mit http:// oder https:// beginnen")
        unknown = [e for e in w.events if e != "*" and e not in EVENTS]
        if unknown:
            raise HTTPException(400, f"Unbekannte Ereignisse: {unknown}; erlaubt: {list(EVENTS)}")
        secret = w.secret or secrets.token_urlsafe(24)
        return {"id": db.add_webhook(w.url, secret, w.events or ["*"]), "secret": secret}

    @api.delete("/api/webhooks/{webhook_id}", tags=["webhooks"])
    def delete_webhook(webhook_id: int):
        if not db.delete_webhook(webhook_id):
            raise HTTPException(404, "Webhook nicht gefunden")
        return {"ok": True}

    @api.post("/api/webhooks/{webhook_id}/test", tags=["webhooks"])
    def test_webhook(webhook_id: int):
        hook = next((h for h in db.list_webhooks() if h["id"] == webhook_id), None)
        if hook is None:
            raise HTTPException(404, "Webhook nicht gefunden")
        return {"delivered": events.deliver(hook, "test", {"message": "Hallo vom Koprotokoll"})}

    # -- Google (Kalender + Gmail) ----------------------------------------------------------------
    @api.get("/api/integrations/google", tags=["google"])
    def google_status():
        return {**google.status(), "last_sync": calendar.last_sync}

    @api.put("/api/integrations/google/client", tags=["google"])
    def google_client(c: GoogleClientIn):
        google.set_client(c.client_id, c.client_secret)
        return google.status()

    @api.get("/api/integrations/google/connect", tags=["google"])
    def google_connect():
        try:
            return RedirectResponse(google.auth_url())
        except GoogleError as e:
            raise HTTPException(400, str(e)) from e

    @public.get("/api/integrations/google/callback", include_in_schema=False)
    def google_callback(code: str = "", state: str = "", error: str = ""):
        if error:
            return RedirectResponse("/#integrations?" + urllib.parse.urlencode({"google_error": error[:100]}))
        try:
            google.exchange(code, state)
        except (GoogleError, OSError) as e:
            raise HTTPException(400, str(e)) from e
        return RedirectResponse("/")

    @api.patch("/api/integrations/google/settings", tags=["google"])
    def google_settings(g: GoogleSettingsIn):
        return google.update_settings(**g.model_dump(exclude_none=True))

    @api.post("/api/integrations/google/sync", tags=["google"])
    def google_sync():
        try:
            created = calendar.sync()
        except GoogleError as e:
            raise HTTPException(400, str(e)) from e
        bots.dispatch_due()
        return {"created": [db.get_bot(b) for b in created]}

    @api.delete("/api/integrations/google", tags=["google"])
    def google_disconnect():
        google.disconnect()
        return {"ok": True}

    # -- Microsoft (Kalender + Mail) ------------------------------------------------------
    @api.get("/api/integrations/microsoft", tags=["microsoft"])
    def microsoft_status():
        return {**microsoft.status(), "last_sync": calendar.last_sync}

    @api.put("/api/integrations/microsoft/client", tags=["microsoft"])
    def microsoft_client(c: MicrosoftClientIn):
        microsoft.set_client(c.client_id, c.client_secret)
        return microsoft.status()

    @api.get("/api/integrations/microsoft/connect", tags=["microsoft"])
    def microsoft_connect():
        try:
            return RedirectResponse(microsoft.auth_url())
        except MicrosoftError as e:
            raise HTTPException(400, str(e)) from e

    @api.post("/api/integrations/microsoft/device", tags=["microsoft"])
    def microsoft_device_start(tasks: bool = False):
        try:
            return microsoft.start_device(include_tasks=tasks)
        except (MicrosoftError, OSError) as e:
            raise HTTPException(400, str(e)) from e

    @api.post("/api/integrations/microsoft/device/poll", tags=["microsoft"])
    def microsoft_device_poll():
        try:
            return microsoft.poll_device()
        except (MicrosoftError, OSError) as e:
            raise HTTPException(400, str(e)) from e

    @public.get("/api/integrations/microsoft/callback", include_in_schema=False)
    def microsoft_callback(code: str = "", state: str = "", error: str = ""):
        if error:
            return RedirectResponse("/#integrations?" + urllib.parse.urlencode({"microsoft_error": error[:100]}))
        try:
            microsoft.exchange(code, state)
        except (MicrosoftError, OSError) as e:
            raise HTTPException(400, str(e)) from e
        return RedirectResponse("/")

    @api.patch("/api/integrations/microsoft/settings", tags=["microsoft"])
    def microsoft_settings(m: MicrosoftSettingsIn):
        return microsoft.update_settings(**m.model_dump(exclude_none=True))

    @api.delete("/api/integrations/microsoft", tags=["microsoft"])
    def microsoft_disconnect():
        microsoft.disconnect()
        return {"ok": True}

    # -- Gespeicherte Kalender-Quellen (ICS-Expertenoption + CalDAV) --------------------------
    def source_out(row: dict) -> dict:
        cfg = dict(row["config"])
        has_password = bool(cfg.pop("password", ""))
        return {"id": row["id"], "kind": row["kind"], "label": row["label"],
                "enabled": row["enabled"], **cfg, "password": "", "password_set": has_password}

    def source_or_404(source_id: int) -> dict:
        row = db.get_calendar_source(source_id)
        if row is None:
            raise HTTPException(404, "Kalenderquelle nicht gefunden")
        return row

    def check_ics_url(url: str) -> str:
        if "://" in url and not url.startswith(("http://", "https://")):
            raise HTTPException(400, "ICS-Adresse muss mit http:// oder https:// beginnen "
                                     "(oder ein Pfad zu einer .ics-Datei sein)")
        return url

    @api.get("/api/calendars/presets", tags=["calendars"])
    def calendar_presets():
        return [{"id": key, **value} for key, value in CALDAV_PRESETS.items()]

    @api.get("/api/calendars/capabilities", tags=["calendars"])
    def calendar_capabilities():
        return {"outlook": outlook_available()}

    @api.get("/api/calendars", tags=["calendars"])
    def calendar_sources():
        return [source_out(row) for row in db.list_calendar_sources()]

    @api.post("/api/calendars", status_code=201, tags=["calendars"])
    def add_calendar_source(c: CalendarSourceIn):
        kind = (c.kind or "").strip().lower()
        if kind not in ("ics", "caldav", "outlook"):
            raise HTTPException(400, f"Unbekannte Quellen-Art „{c.kind}“; erlaubt: ics, caldav, outlook")
        label = (c.label or "").strip()
        if kind == "outlook":
            if not outlook_available():
                raise HTTPException(400, "Outlook lokal benötigt Windows, klassisches Outlook und pywin32.")
            if any(row["kind"] == "outlook" for row in db.list_calendar_sources()):
                raise HTTPException(409, "Der lokale Outlook-Standardkalender ist bereits verbunden.")
            source_id = db.add_calendar_source(kind, label or "Outlook (lokal)", {})
            refresh_sources()
            return source_out(db.get_calendar_source(source_id))
        if kind == "ics":
            url = check_ics_url((c.url or "").strip())
            if not url:
                raise HTTPException(400, "ICS-Adresse oder Pfad zur .ics-Datei fehlt")
            config = {"url": url, "own_email": (c.own_email or "").strip().lower()}
            label = label or "ICS-Kalender"
        else:
            provider = (c.provider or "").strip().lower()
            preset_server = CALDAV_PRESETS.get(provider, {}).get("server", "")
            server_url = normalize_server_url(provider, (c.server_url or "").strip() or preset_server)
            calendar_url = (c.url or "").strip()
            if not server_url and not calendar_url:
                raise HTTPException(400, "Kein Server bekannt – Anbieter wählen oder die "
                                         "Server-URL unter „Erweiterte Einstellungen“ eintragen")
            if not (c.password or "").strip():
                raise HTTPException(400, "Passwort fehlt – bei den meisten Anbietern ist ein "
                                         "App-Passwort nötig")
            if calendar_url and not calendar_url.startswith(("http://", "https://")):
                raise HTTPException(400, "Kalender-URL muss mit http:// oder https:// beginnen")
            config = {"provider": provider, "server_url": server_url, "url": calendar_url,
                      "username": (c.username or "").strip(), "password": c.password,
                      "own_email": (c.own_email or "").strip().lower()}
            label = label or f"CalDAV ({config['username'] or provider or 'Kalender'})"
            # Verbindungstest: Fehlgeschlagene Erkennung soll sofort eine Meldung
            # liefern, damit die erweiterten Einstellungen die Rettung bieten.
            probe = stored_source({"id": 0, "kind": kind, "label": label,
                                   "config": config, "enabled": True})
            try:
                probe.events(time.time() - 86400, time.time() + 86400)
            except (CalDavError, IcsError) as e:
                raise HTTPException(400, f"{e} – bei Bedarf Server- und Kalender-URL unter "
                                         "„Erweiterte Einstellungen“ eintragen.") from e
        source_id = db.add_calendar_source(kind, label, config)
        refresh_sources()
        return source_out(db.get_calendar_source(source_id))

    @api.post("/api/calendars/sync", tags=["calendars"])
    def sync_calendar_sources():
        refresh_sources()
        created = calendar.sync()
        return {"created": [db.get_bot(bot_id) for bot_id in created],
                "last_sync": calendar.last_sync}

    @api.get("/api/calendars/{source_id}/test", tags=["calendars"])
    @api.post("/api/calendars/{source_id}/test", tags=["calendars"])
    def test_calendar_source(source_id: int):
        row = source_or_404(source_id)
        source = stored_source(row)
        try:
            found = source.events(time.time() - 86400, time.time() + 30 * 86400)
        except (CalDavError, IcsError, OutlookError) as e:
            raise HTTPException(400, str(e)) from e
        return {"events": [asdict(e) for e in found]}

    @api.patch("/api/calendars/{source_id}", tags=["calendars"])
    def patch_calendar_source(source_id: int, p: CalendarSourcePatch):
        row = source_or_404(source_id)
        fields: dict = {}
        config = dict(row["config"])
        for key, value in p.model_dump(exclude_none=True).items():
            if key in ("label", "enabled"):
                fields[key] = value
            elif key == "password":
                config[key] = value
            else:
                config[key] = value.strip()
        if "url" in p.model_fields_set and row["kind"] == "ics":
            check_ics_url(config.get("url", ""))
        fields["config"] = config
        db.update_calendar_source(source_id, **fields)
        refresh_sources()
        return source_out(db.get_calendar_source(source_id))

    @api.delete("/api/calendars/{source_id}", tags=["calendars"])
    def delete_calendar_source(source_id: int):
        source_or_404(source_id)
        db.delete_calendar_source(source_id)
        refresh_sources()
        return {"ok": True}

    # -- Kommende Termine (Anzeige) --------------------------------------------------------
    @api.get("/api/upcoming", tags=["calendars"])
    def upcoming(days: int = 7):
        """Termine der nächsten Tage aus allen Kalenderquellen, inkl. Link-Erkennung.

        Anzeigefenster: 7 Tage (Entscheidung 2026-10-17). Die Bot-Einplanung bleibt
        wie gehabt auf ~15 Minuten vor Terminbeginn beschränkt.
        """
        days = max(1, min(days, 30))
        now = time.time()
        out: list[dict] = []
        for source in build_sources():
            try:
                source_events = source.events(now, now + days * 86400)
            except Exception:  # noqa: BLE001 – nicht verbundene/defekte Quellen nicht anzeigen
                continue
            for e in source_events:
                found = find_meeting_url(
                    {"location": e.location, "description": e.description,
                     "meeting_url": e.meeting_url or ""}, s.jitsi_hosts, s.bbb_hosts)
                url, platform = found or ("", "")
                will_join = bool(found and platform in SUPPORTED and source.enabled()
                                 and not e.all_day and e.self_response != "declined")
                if will_join:
                    reason = ""
                elif e.self_response == "declined":
                    reason = "abgelehnt – kein Bot"
                elif e.all_day:
                    reason = "ganztägig – kein Bot"
                elif not found:
                    reason = "kein Meeting-Link erkannt"
                elif platform not in SUPPORTED:
                    reason = f"„{platform}“ wird noch nicht unterstützt"
                else:
                    reason = "Auto-Join ist deaktiviert"
                out.append({
                    "id": e.id, "title": e.title, "start": e.start, "end": e.end,
                    "all_day": e.all_day, "source": source.name, "label": source.label,
                    "meeting_url": url, "platform": platform,
                    "self_response": e.self_response,
                    "will_join": will_join, "reason": reason,
                })
        out.sort(key=lambda x: x["start"])
        return out

    @public.get("/", include_in_schema=False)
    @public.get("/index.html", include_in_schema=False)
    def index_html():
        """Inhaltsabhängige Asset-Adressen verhindern eine veraltete Browseransicht."""
        content = (STATIC / "index.html").read_text(encoding="utf-8")
        for filename in ("app.js", "style.css"):
            version = sha256((STATIC / filename).read_bytes()).hexdigest()[:12]
            content = content.replace(f'"{filename}"', f'"{filename}?v={version}"')
        return HTMLResponse(content, headers={"Cache-Control": "no-cache"})

    app.include_router(public)
    app.include_router(api)
    app.mount("/", FreshStaticFiles(directory=STATIC, html=True), name="static")
    return app


def talk_time(segments: list[dict]) -> dict[str, float]:
    out: dict[str, float] = {}
    for seg in segments:
        out[seg["speaker"]] = round(out.get(seg["speaker"], 0.0) + seg["end"] - seg["start"], 2)
    return out
