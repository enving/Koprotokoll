"""Optionaler Zugriff auf das klassische Outlook; COM bleibt im aufrufenden Thread."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import importlib.util
import sys

from .calendar_source import CalendarEvent


class OutlookError(RuntimeError):
    pass


def outlook_available() -> bool:
    if sys.platform != "win32":
        return False
    try:
        if importlib.util.find_spec("win32com.client") is None:
            return False
        import winreg
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, r"Outlook.Application\CLSID"):
            return True
    except (ImportError, ValueError, OSError):
        return False


@contextmanager
def outlook_session():
    import pythoncom
    import win32com.client

    pythoncom.CoInitialize()
    try:
        yield win32com.client.Dispatch("Outlook.Application")
    finally:
        pythoncom.CoUninitialize()


def recipient_address(recipient) -> str:
    try:
        entry = recipient.AddressEntry
        exchange_user = entry.GetExchangeUser() if entry.Type == "EX" else None
        address = exchange_user.PrimarySmtpAddress if exchange_user else recipient.Address
    except Exception:
        try:
            address = recipient.Address
        except Exception:
            return ""
    return str(address) if "@" in str(address) else ""


def outlook_date(timestamp: float) -> str:
    """Jet-Filter verwenden das Windows-Datumsformat des Nutzers ohne Sekunden."""
    import win32api
    import win32con

    # win32api rechnet nach UTC um; für diese Formatierung müssen lokale Uhrfelder erhalten bleiben.
    value = datetime.fromtimestamp(timestamp).replace(tzinfo=timezone.utc)
    date = win32api.GetDateFormat(win32con.LOCALE_USER_DEFAULT, win32con.DATE_SHORTDATE, value, None)
    time = win32api.GetTimeFormat(win32con.LOCALE_USER_DEFAULT, win32con.TIME_NOSECONDS, value, None)
    return f"{date} {time}"


class OutlookComCalendarSource:
    """Standardkalender des lokalen klassischen Outlook, einschließlich Serien."""

    def __init__(self, name: str, label: str = "Outlook (lokal)", *, enabled: bool = True):
        self.name, self.label, self._enabled = name, label, enabled

    def enabled(self) -> bool:
        return self._enabled and outlook_available()

    def events(self, time_min: float, time_max: float) -> list[CalendarEvent]:
        if not outlook_available():
            raise OutlookError("Der lokale Outlook-Kalender benötigt Windows, klassisches Outlook und pywin32.")
        if time_max <= time_min:
            return []
        events = []
        try:
            # Ganze lokale Tage vermeiden Minutenverluste und mehrdeutige DST-Uhrzeiten im Jet-Filter.
            first_day = datetime.fromtimestamp(time_min).replace(hour=0, minute=0, second=0, microsecond=0)
            last_day = datetime.fromtimestamp(time_max).replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
            start_filter, end_filter = outlook_date(first_day.timestamp()), outlook_date(last_day.timestamp())
            with outlook_session() as app:
                items = app.GetNamespace("MAPI").GetDefaultFolder(9).Items
                # Outlook verlangt Sortieren vor IncludeRecurrences und anschließend Restrict.
                items.Sort("[Start]")
                items.IncludeRecurrences = True
                restricted = items.Restrict(f"[Start] >= '{start_filter}' AND [Start] < '{end_filter}'")
                for item in restricted:
                    start = item.StartUTC.replace(tzinfo=timezone.utc).timestamp()
                    if not time_min <= start < time_max:
                        continue
                    attendees = [recipient_address(r) for r in item.Recipients if getattr(r, "Type", 1) != 3]
                    events.append(CalendarEvent(
                        id=f"{item.EntryID}:{start:.0f}", title=item.Subject or "Meeting",
                        start=start, end=item.EndUTC.replace(tzinfo=timezone.utc).timestamp(), all_day=bool(item.AllDayEvent),
                        location=item.Location or "", description=item.Body or "",
                        attendees=list(dict.fromkeys(a for a in attendees if a)),
                        self_response={1: "accepted", 2: "tentative", 3: "accepted", 4: "declined"}.get(item.ResponseStatus),
                        source=self.name,
                    ))
        except Exception:
            raise OutlookError("Outlook-Kalender konnte nicht gelesen werden. Klassisches Outlook, Profil und Zugriff prüfen.") from None
        return events
