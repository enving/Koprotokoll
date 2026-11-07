"""Konfiguration über Umgebungsvariablen (alle Werte haben lokale Defaults)."""

from __future__ import annotations

import json
import os
import ipaddress
from urllib.parse import urlsplit
from dataclasses import dataclass, field
from pathlib import Path


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


# CalDAV-Anbieter-Presets für die Quellen-Einrichtung. ``server`` ist die
# Basis-URL für die Server-Erkennung nach RFC 6764 (/.well-known/caldav →
# Principal → Kalender-Verzeichnis). Weicht ein Anbieter davon ab, tragen
# Nutzerinnen die Server- und Kalender-URL in den erweiterten Einstellungen
# ein – die Erkennung scheitert dann abgesichert mit einer deutschen Meldung.
# Google und Outlook.com fehlen absichtlich: deren Kalender laufen über die
# OAuth-Punkte (Google/Microsoft), nicht über klassisches CalDAV.
CALDAV_PRESETS: dict[str, dict[str, str]] = {
    "icloud": {"label": "iCloud", "server": "https://caldav.icloud.com/",
               "hint": "Apple-ID, z. B. name@icloud.com"},
    "posteo": {"label": "Posteo", "server": "https://posteo.de:8443/",
               "hint": "Posteo-E-Mail-Adresse"},
    "mailbox": {"label": "mailbox.org", "server": "https://caldav.mailbox.org/",
                "hint": "mailbox.org-Adresse"},
    "fastmail": {"label": "Fastmail", "server": "https://caldav.fastmail.com/",
                 "hint": "Fastmail-Adresse"},
    "nextcloud": {"label": "Nextcloud / eigener Server", "server": "",
                  "hint": "Server-Adresse, z. B. https://cloud.example.com"},
    "other": {"label": "Anderer Anbieter", "server": "",
              "hint": "Server- und Kalender-URL unter „Erweiterte Einstellungen“"},
}


def external_llm(url: str) -> bool:
    host = urlsplit(url).hostname or ""
    if host == "localhost":
        return False
    try:
        address = ipaddress.ip_address(host)
        return not (address.is_loopback or address in ipaddress.ip_network("10.0.0.0/8") or address in ipaddress.ip_network("172.16.0.0/12") or address in ipaddress.ip_network("192.168.0.0/16") or address in ipaddress.ip_network("fc00::/7"))
    except ValueError:
        return True


@dataclass
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(_env("KOPROTOKOLL_DATA", "./data")).resolve())
    # Öffentliche Basis-URL (für OAuth-Redirect, Links in E-Mails).
    public_url: str = field(default_factory=lambda: _env("KOPROTOKOLL_PUBLIC_URL", "http://localhost:8000").rstrip("/"))

    # auto/en: mehrsprachiges v3, de: Primeline; ausschließlich lokale CPU-Erkennung.
    asr_model: str = field(default_factory=lambda: _env("KOPROTOKOLL_ASR_MODEL", "auto"))
    asr_language: str = field(default_factory=lambda: _env("KOPROTOKOLL_ASR_LANGUAGE", "auto"))
    asr_quantization: str | None = field(default_factory=lambda: _env("KOPROTOKOLL_ASR_QUANT", "int8") or None)
    asr_model_path: str | None = field(default_factory=lambda: os.environ.get("KOPROTOKOLL_ASR_PATH"))
    asr_v3_path: str | None = field(default_factory=lambda: os.environ.get("KOPROTOKOLL_ASR_V3_PATH"))
    asr_primeline_path: str | None = field(default_factory=lambda: os.environ.get("KOPROTOKOLL_ASR_PRIMELINE_PATH"))
    asr_vad_path: str | None = field(default_factory=lambda: os.environ.get("KOPROTOKOLL_ASR_VAD_PATH"))

    # Sprechererkennung (sherpa-onnx: pyannote-Segmentierung + Sprecher-Embeddings).
    diarization: bool = field(default_factory=lambda: _env("KOPROTOKOLL_DIARIZATION", "1") != "0")
    diarization_threshold: float = field(default_factory=lambda: float(_env("KOPROTOKOLL_DIARIZATION_THRESHOLD", "0.8")))

    # Lokales LLM über eine OpenAI-kompatible API (Ollama, llama.cpp-Server, LM Studio …).
    llm_base_url: str = field(default_factory=lambda: _env("KOPROTOKOLL_LLM_URL", "http://localhost:11434/v1"))
    llm_model: str = field(default_factory=lambda: _env("KOPROTOKOLL_LLM_MODEL", "qwen2.5:7b-instruct"))
    llm_api_key: str = field(default_factory=lambda: _env("KOPROTOKOLL_LLM_API_KEY", "local"))
    llm_enabled: bool = field(default_factory=lambda: _env("KOPROTOKOLL_LLM", "1") != "0")
    llm_timeout: float = field(default_factory=lambda: float(_env("KOPROTOKOLL_LLM_TIMEOUT", "300")))

    # Google-Integration (Kalender + Gmail) – OAuth-Client aus der Google Cloud Console.
    google_client_id: str = field(default_factory=lambda: _env("KOPROTOKOLL_GOOGLE_CLIENT_ID", ""))
    google_client_secret: str = field(default_factory=lambda: _env("KOPROTOKOLL_GOOGLE_CLIENT_SECRET", ""))
    calendar_poll_seconds: float = field(default_factory=lambda: float(_env("KOPROTOKOLL_CALENDAR_POLL", "60")))

    # Microsoft-Integration (Kalender + Mail) – OAuth-Client aus der Azure-App-Registrierung.
    microsoft_client_id: str = field(default_factory=lambda: _env("KOPROTOKOLL_MICROSOFT_CLIENT_ID", ""))
    microsoft_client_secret: str = field(default_factory=lambda: _env("KOPROTOKOLL_MICROSOFT_CLIENT_SECRET", ""))

    smtp_host: str | None = field(default_factory=lambda: os.environ.get("KOPROTOKOLL_SMTP_HOST"))
    smtp_port: int | None = field(default_factory=lambda: int(os.environ["KOPROTOKOLL_SMTP_PORT"]) if "KOPROTOKOLL_SMTP_PORT" in os.environ else None)
    smtp_user: str | None = field(default_factory=lambda: os.environ.get("KOPROTOKOLL_SMTP_USER"))
    smtp_password: str | None = field(default_factory=lambda: os.environ.get("KOPROTOKOLL_SMTP_PASSWORD"))
    smtp_sender: str | None = field(default_factory=lambda: os.environ.get("KOPROTOKOLL_SMTP_FROM"))
    smtp_tls: str | None = field(default_factory=lambda: os.environ.get("KOPROTOKOLL_SMTP_TLS"))

    # Meeting-Bot (tritt Meetings als Teilnehmer bei).
    bot_name: str = field(default_factory=lambda: _env("KOPROTOKOLL_BOT_NAME", "Koprotokoll"))
    bot_headless: bool = field(default_factory=lambda: _env("KOPROTOKOLL_BOT_HEADLESS", "1") != "0")
    bot_chrome_path: str | None = field(default_factory=lambda: os.environ.get("KOPROTOKOLL_BOT_CHROME"))
    bot_silence_minutes: float = field(default_factory=lambda: float(_env("KOPROTOKOLL_BOT_SILENCE_MINUTES", "1")))
    bot_max_minutes: float = field(default_factory=lambda: float(_env("KOPROTOKOLL_BOT_MAX_MINUTES", "180")))
    bot_alone_grace_seconds: float = field(default_factory=lambda: float(_env("KOPROTOKOLL_BOT_ALONE_SECONDS", "15")))
    bot_admit_minutes: float = field(default_factory=lambda: float(_env("KOPROTOKOLL_BOT_ADMIT_MINUTES", "10")))

    # Eigene Jitsi-Instanzen (kommagetrennte Hostnames) zusätzlich zu meet.jit.si.
    jitsi_hosts: tuple[str, ...] = field(
        default_factory=lambda: tuple(
            h.strip().lower() for h in _env("KOPROTOKOLL_JITSI_HOSTS", "").split(",") if h.strip()
        )
    )
    bbb_hosts: tuple[str, ...] = field(default_factory=lambda: tuple(
        host.strip().lower() for host in _env("KOPROTOKOLL_BBB_HOSTS", "").split(",") if host.strip()))

    llm_provider: str = "ollama"
    llm_external_confirmed: bool = False
    llm_max_tokens: int | None = None
    llm_reasoning_effort: str | None = None

    def __post_init__(self) -> None:
        path = self.data_dir / "llm-settings.json"
        if path.exists():
            saved = json.loads(path.read_text())
            for key, attr, env in self.llm_fields():
                if env not in os.environ and key in saved:
                    setattr(self, attr, saved[key])
            self.llm_provider = saved.get("provider", "ollama")
            self.llm_external_confirmed = saved.get("confirmed_url") == self.llm_base_url
            if "KOPROTOKOLL_LLM_MODEL" not in os.environ:
                self.llm_model = saved.get("model", self.llm_model)
            if saved.get("model") == self.llm_model and saved.get("url") == self.llm_base_url:
                self.llm_max_tokens = saved.get("max_tokens", self.llm_max_tokens)
                self.llm_reasoning_effort = saved.get("reasoning_effort", self.llm_reasoning_effort)

    @staticmethod
    def llm_fields():
        return [("url", "llm_base_url", "KOPROTOKOLL_LLM_URL"),
                ("api_key", "llm_api_key", "KOPROTOKOLL_LLM_API_KEY"),
                ("model", "llm_model", "KOPROTOKOLL_LLM_MODEL"),
                ("timeout", "llm_timeout", "KOPROTOKOLL_LLM_TIMEOUT")]

    def save_provider(self, values: dict) -> None:
        self.ensure_dirs()
        path = self.data_dir / "llm-settings.json"
        saved = json.loads(path.read_text()) if path.exists() else {}
        connection_fields = [f for f in self.llm_fields() if f[0] in ("url", "model")]
        previous_saved = tuple(saved.get(key, getattr(self, attr)) for key, attr, _ in connection_fields)
        following_saved = tuple(values.get(key, old) for (key, _, _), old in zip(connection_fields, previous_saved))
        previous_effective = tuple(getattr(self, attr) for _, attr, _ in connection_fields)
        following_effective = tuple(os.environ.get(env, values.get(key, getattr(self, attr)))
                                    for key, attr, env in connection_fields)
        parameter_fields = ("max_tokens", "reasoning_effort")
        unbound_parameters = "url" not in saved and any(saved.get(key) is not None for key in parameter_fields)
        reset_parameters = (previous_saved != following_saved or previous_effective != following_effective
                            or unbound_parameters)
        if reset_parameters:
            for key in parameter_fields:
                saved.pop(key, None)
        saved.update(values)
        temporary = path.with_suffix(".tmp")
        temporary.touch(mode=0o600, exist_ok=True)
        temporary.chmod(0o600)
        temporary.write_text(json.dumps(saved))
        temporary.replace(path)
        for key, attr, env in self.llm_fields():
            if env not in os.environ and key in values:
                setattr(self, attr, values[key])
        self.llm_provider = saved.get("provider", "ollama")
        self.llm_external_confirmed = saved.get("confirmed_url") == self.llm_base_url
        if reset_parameters:
            self.llm_max_tokens = self.llm_reasoning_effort = None

    def save_llm(self, model: str, max_tokens: int | None, reasoning_effort: str | None) -> None:
        self.save_provider(dict(url=self.llm_base_url, model=model,
                                max_tokens=max_tokens, reasoning_effort=reasoning_effort))
        if "KOPROTOKOLL_LLM_MODEL" not in os.environ:
            self.llm_model = model
        self.llm_max_tokens = max_tokens if model == self.llm_model else None
        self.llm_reasoning_effort = reasoning_effort if model == self.llm_model else None

    @property
    def db_path(self) -> Path:
        return self.data_dir / "koprotokoll.sqlite3"

    @property
    def media_dir(self) -> Path:
        return self.data_dir / "media"

    @property
    def models_dir(self) -> Path:
        return self.data_dir / "models"

    @property
    def bot_profile_dir(self) -> Path:
        return self.data_dir / "bot-profile"

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.media_dir, self.models_dir):
            d.mkdir(parents=True, exist_ok=True)
