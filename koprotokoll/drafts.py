"""Recaps als Datei oder Entwurf öffnen, ohne eine Nachricht zu versenden."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from email import policy
from pathlib import Path
from urllib.parse import quote

from .mail import MailError, message
from .outlook import outlook_available, outlook_session
from .recap import build_recap


def thunderbird_executable() -> str | None:
    found = shutil.which("thunderbird")
    if found:
        return found
    mac = Path("/Applications/Thunderbird.app/Contents/MacOS/thunderbird")
    return str(mac) if mac.is_file() else None


def draft_targets(local: bool) -> list[str]:
    targets = ["eml"]
    if local:
        if thunderbird_executable():
            targets.append("thunderbird")
        if outlook_available():
            targets.append("outlook")
    return targets


def recipients(meeting: dict) -> list[str]:
    return list(dict.fromkeys(address for address in meeting.get("meta", {}).get("attendees", [])
                              if isinstance(address, str) and "@" in address))


def recap_eml(meeting: dict, public_url: str, sender: str = "") -> bytes:
    subject, text, html = build_recap(meeting, public_url)
    try:
        msg = message(recipients(meeting), subject, text, html, sender)
        msg["X-Unsent"] = "1"
        return msg.as_bytes(policy=policy.SMTP)
    except ValueError:
        raise MailError("Der E-Mail-Entwurf enthält ungültige Kopfzeilen. Titel und Empfänger prüfen.") from None


def open_draft(target: str, meeting: dict, public_url: str, data_dir: Path) -> None:
    subject, text, html = build_recap(meeting, public_url)
    to = recipients(meeting)
    if target == "thunderbird":
        executable = thunderbird_executable()
        if not executable:
            raise MailError("Thunderbird wurde auf dem Server nicht gefunden. Stattdessen die EML-Datei öffnen.")
        folder = data_dir / "drafts"
        folder.mkdir(parents=True, exist_ok=True)
        # Thunderbird liest die Datei verzögert; sie bleibt deshalb lokal erhalten.
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", suffix=".html", dir=folder, delete=False) as file:
            file.write(html)
            path = file.name
        values = {"to": ",".join(to), "subject": subject, "message": path, "format": "html"}
        # Doppelt zitierte Werte dekodiert Thunderbird nach dem Zerlegen der Argumente.
        args = ",".join(f'{key}="{quote(value, safe="")}"' for key, value in values.items())
        try:
            subprocess.Popen([executable, "-compose", args], shell=False)
        except OSError:
            raise MailError("Thunderbird konnte nicht gestartet werden. Stattdessen die EML-Datei öffnen.") from None
        return
    if target == "outlook":
        if not outlook_available():
            raise MailError("Outlook-Entwürfe benötigen Windows, klassisches Outlook und pywin32.")
        try:
            with outlook_session() as app:
                item = app.CreateItem(0)
                item.To = ";".join(to)
                item.Subject = subject
                item.HTMLBody = html
                item.Display()
        except Exception:
            raise MailError("Der Outlook-Entwurf konnte nicht geöffnet werden. Klassisches Outlook prüfen.") from None
        return
    raise MailError("Unbekanntes Entwurfsziel. Erlaubt sind thunderbird und outlook.")
