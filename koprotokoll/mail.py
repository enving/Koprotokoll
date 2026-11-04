"""Optionale Mailwege mit einheitlicher Recap-Vorlage und lokalen Einstellungen."""

from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage
from email.utils import parseaddr
from typing import Protocol

from .config import Settings
from .db import Database


class MailError(RuntimeError):
    pass


class MailSender(Protocol):
    def send(self, to: list[str], subject: str, text: str, html: str) -> None: ...


def message(to: list[str], subject: str, text: str, html: str, sender: str = "") -> EmailMessage:
    msg = EmailMessage()
    if sender:
        msg["From"] = sender
    if to:
        msg["To"] = ", ".join(to)
    msg["Subject"] = subject
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    return msg


class GmailMailSender:
    def __init__(self, client):
        self.client = client

    def send(self, to: list[str], subject: str, text: str, html: str) -> None:
        self.client.send_mail(to, subject, text, html)


class SmtpMailSender:
    def __init__(self, config: dict):
        self.config = config

    def send(self, to: list[str], subject: str, text: str, html: str) -> None:
        c = self.config
        validate_smtp(c)
        try:
            msg = message(to, subject, text, html, c["sender"])
            context = ssl.create_default_context()
            if c["tls"] == "ssl":
                connection = smtplib.SMTP_SSL(c["host"], c["port"], timeout=30, context=context)
            else:
                connection = smtplib.SMTP(c["host"], c["port"], timeout=30)
            with connection as smtp:
                if c["tls"] == "starttls":
                    smtp.starttls(context=context)
                if c["user"]:
                    smtp.login(c["user"], c["password"])
                refused = smtp.send_message(msg, from_addr=parseaddr(c["sender"])[1], to_addrs=to)
                if refused:
                    raise MailError("SMTP hat mindestens einen Empfänger abgelehnt.")
        except (smtplib.SMTPException, OSError, ValueError):
            # Serverantworten können Anmeldedaten enthalten und bleiben deshalb intern.
            raise MailError("SMTP-Versand fehlgeschlagen. Server, Port, Verschlüsselung und Anmeldung prüfen.") from None


def validate_smtp(config: dict) -> None:
    if not config["host"] or any(c.isspace() for c in config["host"]):
        raise MailError("SMTP-Server fehlt oder enthält Leerzeichen.")
    if config["tls"] not in ("ssl", "starttls", "none"):
        raise MailError("SMTP-Verschlüsselung muss ssl, starttls oder none sein.")
    if config["tls"] == "none" and config["host"] not in ("127.0.0.1", "localhost", "::1"):
        raise MailError("Unverschlüsseltes SMTP ist nur für einen lokalen Mailserver erlaubt.")
    if not 1 <= config["port"] <= 65535:
        raise MailError("SMTP-Port muss zwischen 1 und 65535 liegen.")
    if any(c in config["sender"] for c in "\r\n") or "@" not in parseaddr(config["sender"])[1]:
        raise MailError("Eine gültige eigene Absenderadresse fehlt.")


class MailService:
    def __init__(self, settings: Settings, db: Database, google):
        self.s, self.db, self.google = settings, db, google

    def smtp_config(self) -> dict:
        config = {"host": "", "port": 587, "user": "", "password": "", "sender": "", "tls": "starttls",
                  **self.db.get_kv("smtp_config", {})}
        for key in config:
            value = getattr(self.s, "smtp_" + key)
            if value is not None:
                config[key] = value
        return config

    def preferences(self) -> dict:
        defaults = {"provider": "smtp" if self.s.smtp_host else "gmail",
                    "send_recap": False, "recap_to_attendees": False}
        stored = self.db.get_kv("mail_settings", {})
        provider = stored.get("provider", defaults["provider"])
        if provider == "gmail":
            return {"provider": provider, **self.google.settings()}
        return {**defaults, **stored}

    def own_email(self) -> str:
        if self.preferences()["provider"] == "gmail":
            return self.google.status().get("email") or ""
        return parseaddr(self.smtp_config()["sender"])[1]

    def connected(self) -> bool:
        provider = self.preferences()["provider"]
        return self.google.connected() if provider == "gmail" else provider == "smtp" and bool(self.smtp_config()["host"])

    def status(self) -> dict:
        config = self.smtp_config()
        password_set = bool(config.pop("password"))
        return {**self.preferences(), "smtp": {**config, "password_set": password_set},
                "connected": self.connected(), "own_email": self.own_email()}

    def update(self, values: dict) -> dict:
        provider = values.get("provider", self.preferences()["provider"])
        if provider not in ("gmail", "smtp", "none"):
            raise MailError("Mailweg muss gmail, smtp oder none sein.")
        smtp = self.smtp_config()
        smtp.update({key: values[key] for key in smtp if key in values})
        if provider == "smtp":
            validate_smtp(smtp)
        stored_smtp = self.db.get_kv("smtp_config", {})
        stored_smtp.update({key: values[key] for key in smtp if key in values})
        self.db.set_kv("smtp_config", stored_smtp)
        preferences = {**self.db.get_kv("mail_settings", {}), "provider": provider}
        preferences.update({key: values[key] for key in ("send_recap", "recap_to_attendees") if key in values})
        self.db.set_kv("mail_settings", preferences)
        if provider == "gmail":
            self.google.update_settings(**{k: values[k] for k in ("send_recap", "recap_to_attendees") if k in values})
        return self.status()

    def send(self, to: list[str], subject: str, text: str, html: str) -> None:
        provider = self.preferences()["provider"]
        if not self.connected():
            raise MailError("Kein Mailweg verbunden. Bitte E-Mail in den Integrationen einrichten.")
        sender = GmailMailSender(self.google) if provider == "gmail" else SmtpMailSender(self.smtp_config())
        sender.send(to, subject, text, html)
