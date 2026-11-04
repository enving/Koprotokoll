"""Optionale Microsoft-Anbindung über OAuth und Graph; Einrichtung in docs/einrichtung.md."""

from __future__ import annotations

import base64
import html
import json
import re
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from .config import Settings
from .db import Database

# OAuth-Endpunkte der Microsoft Identity Platform (Tenant „common" erlaubt
# persönliche Microsoft-Konten und Azure-AD-Konten gleichermaßen).
AUTH_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/authorize"
TOKEN_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
DEVICE_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/devicecode"

# Microsoft Graph – Kalenderereignisse des angemeldeten Benutzers.
GRAPH_BASE = "https://graph.microsoft.com/v1.0"

SCOPES = [
    "openid",
    "email",
    "offline_access",          # nötig für das Refresh-Token
    "Calendars.Read",          # Outlook-/Microsoft-365-Kalender lesen
    "Mail.Send",               # Recap-Mails senden (analog zu Gmail)
    "User.Read",
]


class MicrosoftError(RuntimeError):
    def __init__(self, message: str, code: str = ""):
        super().__init__(message)
        self.code = code


def _http(method: str, url: str, *, form: dict | None = None, body: dict | None = None,
          token: str | None = None) -> dict:
    headers = {}
    data = None
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    elif body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        try:
            error = json.loads(e.read()).get("error", "")
        except (ValueError, AttributeError):
            error = ""
        code = error if error in ("authorization_pending", "slow_down", "expired_token", "authorization_declined") else ""
        raise MicrosoftError(f"Microsoft-Anfrage fehlgeschlagen (HTTP {e.code}). Anmeldung und Berechtigungen prüfen.", code) from None


def _jwt_payload(token: str) -> dict:
    try:
        part = token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
    except (IndexError, ValueError):
        return {}


class MicrosoftClient:
    """OAuth-Client mit Gerätecode oder bestehender Anmeldung über Weiterleitung."""

    def __init__(self, settings: Settings, db: Database):
        self.s, self.db = settings, db
        self._token_lock = threading.RLock()

    # -- Konfiguration ---------------------------------------------------------
    def _client(self) -> tuple[str, str]:
        stored = self.db.get_kv("microsoft_client", {})
        return (self.s.microsoft_client_id or stored.get("client_id", ""),
                self.s.microsoft_client_secret or stored.get("client_secret", ""))

    def set_client(self, client_id: str, client_secret: str) -> None:
        self.db.set_kv("microsoft_client", {"client_id": client_id.strip(),
                                            "client_secret": client_secret.strip()})

    @property
    def redirect_uri(self) -> str:
        return f"{self.s.public_url}/api/integrations/microsoft/callback"

    def configured(self) -> bool:
        return bool(self._client()[0])

    def connected(self) -> bool:
        return bool(self.db.get_kv("microsoft_tokens", {}).get("refresh_token"))

    def status(self) -> dict:
        tokens = self.db.get_kv("microsoft_tokens", {})
        return {"configured": self.configured(), "connected": self.connected(),
                "redirect_available": bool(self._client()[1]),
                "email": tokens.get("email"), "redirect_uri": self.redirect_uri,
                "settings": self.settings()}

    def settings(self) -> dict:
        return {"auto_join": True, "send_recap": False, "recap_to_attendees": False,
                **self.db.get_kv("microsoft_settings", {})}

    def update_settings(self, **values: bool) -> dict:
        self.db.set_kv("microsoft_settings", {**self.settings(), **values})
        return self.settings()

    # -- OAuth --------------------------------------------------------------------
    def auth_url(self) -> str:
        client_id, _ = self._client()
        if not client_id:
            raise MicrosoftError("Microsoft-OAuth-Client ist nicht konfiguriert")
        if not self._client()[1]:
            raise MicrosoftError("Für die Weiterleitung fehlt ein Client-Secret. Bitte stattdessen mit Gerätecode anmelden.")
        state = secrets.token_urlsafe(24)
        self.db.set_kv("microsoft_oauth_state", {"state": state, "expires": time.time() + 600})
        return AUTH_URL + "?" + urllib.parse.urlencode({
            "client_id": client_id, "redirect_uri": self.redirect_uri, "response_type": "code",
            "scope": " ".join(SCOPES), "response_mode": "query", "state": state,
        })

    def start_device(self) -> dict:
        client_id, _ = self._client()
        if not client_id:
            raise MicrosoftError("Microsoft-Client-ID fehlt. Zuerst die App-Registrierung eintragen.")
        data = _http("POST", DEVICE_URL, form={"client_id": client_id, "scope": " ".join(SCOPES)})
        uri = urllib.parse.urlsplit(data["verification_uri"])
        if uri.scheme != "https" or not (uri.hostname in ("microsoft.com", "microsoftonline.com") or
                                         (uri.hostname or "").endswith((".microsoft.com", ".microsoftonline.com"))):
            raise MicrosoftError("Microsoft lieferte keine gültige Anmeldeseite.")
        interval = max(5, int(data.get("interval", 5)))
        self.db.set_kv("microsoft_device", {"device_code": data["device_code"],
                       "expires": time.time() + int(data["expires_in"]), "interval": interval,
                       "next_poll": time.time() + interval})
        return {"user_code": data["user_code"], "verification_uri": data["verification_uri"],
                "expires_in": int(data["expires_in"]), "interval": interval}

    def poll_device(self) -> dict:
        pending = self.db.get_kv("microsoft_device", {})
        if not pending or pending["expires"] <= time.time():
            self.db.set_kv("microsoft_device", None)
            raise MicrosoftError("Der Anmeldecode ist abgelaufen. Bitte neu starten.")
        interval = pending["interval"]
        if time.time() < pending["next_poll"]:
            return {"status": "pending", "interval": interval}
        client_id, _ = self._client()
        try:
            token = _http("POST", TOKEN_URL, form={
                "client_id": client_id, "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "device_code": pending["device_code"],
            })
        except MicrosoftError as e:
            if e.code in ("authorization_pending", "slow_down"):
                if e.code == "slow_down":
                    interval += 5
                self.db.set_kv("microsoft_device", {**pending, "interval": interval,
                                                    "next_poll": time.time() + interval})
                return {"status": "pending", "interval": interval}
            self.db.set_kv("microsoft_device", None)
            if e.code in ("expired_token", "authorization_declined"):
                raise MicrosoftError("Anmeldung abgelehnt oder abgelaufen. Bitte bei Bedarf neu starten.") from None
            raise
        self._store_tokens(token, "device")
        self.db.set_kv("microsoft_device", None)
        return {"status": "connected", **self.status()}

    def _store_tokens(self, token: dict, flow: str) -> dict:
        if not token.get("refresh_token") or not token.get("access_token"):
            raise MicrosoftError("Microsoft hat keine vollständige Anmeldung geliefert. Offline-Zugriff erlauben und erneut verbinden.")
        claims = _jwt_payload(token.get("id_token", ""))
        email = claims.get("email") or claims.get("preferred_username")
        if not email:
            profile = _http("GET", GRAPH_BASE + "/me?$select=mail,userPrincipalName", token=token["access_token"])
            email = profile.get("mail") or profile.get("userPrincipalName")
        tokens = {"refresh_token": token["refresh_token"], "access_token": token["access_token"],
                  "expires_at": time.time() + int(token.get("expires_in", 3600)) - 60,
                  "email": email, "scope": token.get("scope"), "auth_flow": flow}
        self.db.set_kv("microsoft_tokens", tokens)
        return tokens

    def exchange(self, code: str, state: str) -> dict:
        expected = self.db.get_kv("microsoft_oauth_state", {})
        if not expected or not secrets.compare_digest(expected.get("state", ""), state or "") \
                or expected.get("expires", 0) < time.time():
            raise MicrosoftError("Ungültiger oder abgelaufener OAuth-Status – bitte erneut verbinden")
        self.db.set_kv("microsoft_oauth_state", None)
        client_id, client_secret = self._client()
        tok = _http("POST", TOKEN_URL, form={
            "code": code, "client_id": client_id, "client_secret": client_secret,
            "redirect_uri": self.redirect_uri, "grant_type": "authorization_code",
        })
        return self._store_tokens(tok, "redirect")

    def disconnect(self) -> None:
        with self._token_lock:
            self.db.set_kv("microsoft_tokens", None)
            self.db.set_kv("microsoft_device", None)

    def _access_token(self) -> str:
        with self._token_lock:
            return self._current_access_token()

    def _current_access_token(self) -> str:
        tokens = self.db.get_kv("microsoft_tokens", {})
        if not tokens.get("refresh_token"):
            raise MicrosoftError("Microsoft ist nicht verbunden")
        if tokens.get("expires_at", 0) > time.time():
            return tokens["access_token"]
        client_id, client_secret = self._client()
        form = {
            "refresh_token": tokens["refresh_token"], "client_id": client_id,
            "grant_type": "refresh_token", "scope": tokens.get("scope") or " ".join(SCOPES),
        }
        if tokens.get("auth_flow") != "device" and client_secret:
            form["client_secret"] = client_secret
        tok = _http("POST", TOKEN_URL, form=form)
        tokens.update(access_token=tok["access_token"],
                      refresh_token=tok.get("refresh_token") or tokens["refresh_token"],
                      expires_at=time.time() + int(tok.get("expires_in", 3600)) - 60)
        self.db.set_kv("microsoft_tokens", tokens)
        return tokens["access_token"]

    def send_mail(self, to: list[str], subject: str, text: str, html: str | None = None) -> dict:
        return _http("POST", GRAPH_BASE + "/me/sendMail", token=self._access_token(), body={
            "message": {"subject": subject, "body": {"contentType": "HTML" if html else "Text", "content": html or text},
                        "toRecipients": [{"emailAddress": {"address": address}} for address in to]},
            "saveToSentItems": True,
        })

    def list_values(self, path: str) -> list[dict]:
        url = GRAPH_BASE + path
        seen = set()
        items = []
        while url:
            if not isinstance(url, str) or not url.startswith(GRAPH_BASE + "/") or url in seen or len(seen) >= 1000:
                raise MicrosoftError("Microsoft lieferte eine ungültige oder endlose Folgeseite.")
            seen.add(url)
            data = _http("GET", url, token=self._access_token())
            if not isinstance(data.get("value", []), list):
                raise MicrosoftError("Microsoft lieferte keine gültige Ergebnisliste.")
            items.extend(data.get("value", []))
            url = data.get("@odata.nextLink")
        return items

    # -- Kalender (Microsoft Graph) ---------------------------------------------------
    def events(self, time_min: float, time_max: float) -> list[dict]:
        def iso(t: float) -> str:
            return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))

        params = urllib.parse.urlencode({
            "startDateTime": iso(time_min), "endDateTime": iso(time_max),
            "$orderby": "start/dateTime", "$top": "50",
        })
        return self.list_values(f"/me/calendarview?{params}")


class MicrosoftCalendarSource:
    """Kalenderquelle, die Microsoft-/Outlook-Termine als ``CalendarEvent`` liefert."""

    name = "microsoft"
    label = "Microsoft Kalender"

    def __init__(self, client: MicrosoftClient):
        self.client = client

    def enabled(self) -> bool:
        return self.client.connected() and self.client.settings().get("auto_join", True)

    def events(self, time_min: float, time_max: float) -> list["CalendarEvent"]:
        from .calendar_source import CalendarEvent

        out: list[CalendarEvent] = []
        for item in self.client.events(time_min, time_max):
            if item.get("isCancelled"):
                continue
            start_ts = _graph_ts(item.get("start"))
            if start_ts is None:  # ganztägige Termine überspringen
                continue
            end_ts = _graph_ts(item.get("end"))
            attendees = [a.get("emailAddress", {}).get("address", "")
                         for a in (item.get("attendees") or [])]
            attendees = [e for e in attendees if e]
            # Eigene Antwort steht in responseStatus, nicht bei den anderen Teilnehmenden.
            response = {"accepted": "accepted", "organizer": "accepted", "declined": "declined",
                        "tentativelyAccepted": "tentative"}.get(
                (item.get("responseStatus") or {}).get("response"))
            meeting_url = ""
            online = item.get("onlineMeeting") or {}
            if online.get("joinUrl"):
                meeting_url = online["joinUrl"]
            out.append(CalendarEvent(
                id=item.get("id", ""),
                title=item.get("subject") or "Meeting",
                start=start_ts,
                end=end_ts,
                all_day=False,
                location=(item.get("location") or {}).get("displayName", ""),
                description=_event_text(item),
                attendees=attendees,
                self_response=response,
                meeting_url=meeting_url,
                source=self.name,
            ))
        return out


def _event_text(item: dict) -> str:
    """Beschreibungstext eines Graph-Termins: vollen Body nutzen, HTML entfernen.

    Wichtig: Auch ``href="..."``-Ziele bleiben erhalten – Meeting-Links stehen
    in Outlook-Terminen oft nur als klickbarer Link im HTML-Body.
    """
    body = (item.get("body") or {}).get("content", "") or item.get("bodyPreview") or ""
    if not body:
        return ""
    links = re.findall(r'href="([^"]+)"', body)
    text = html.unescape(re.sub(r"<[^>]+>", " ", body))
    return " ".join(text.split() + links)


def _graph_ts(value: dict | None) -> float | None:
    if not value or "dateTime" not in value:
        return None
    from datetime import datetime, timezone
    dt = datetime.fromisoformat(value["dateTime"].replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()
