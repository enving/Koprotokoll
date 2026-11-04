"""Klassisches Outlook mit plattformunabhängigen COM-Nachbildungen."""

from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace as Obj

import pytest

import koprotokoll.outlook as outlook
from koprotokoll.bot.platforms import find_meeting_url
from test_integrations import make_client


def install_calendar(monkeypatch, appointments):
    calls = []

    class Items:
        def Sort(self, key):
            calls.append(("sort", key))

        def __setattr__(self, key, value):
            calls.append((key, value))

        def Restrict(self, query):
            calls.append(("restrict", query))
            assert calls[:2] == [("sort", "[Start]"), ("IncludeRecurrences", True)]
            return appointments

    @contextmanager
    def session():
        yield Obj(GetNamespace=lambda name: Obj(GetDefaultFolder=lambda number: Obj(Items=Items()) if number == 9 else None))

    monkeypatch.setattr(outlook, "outlook_session", session)
    monkeypatch.setattr(outlook, "outlook_date", lambda timestamp: datetime.fromtimestamp(timestamp).strftime("%d.%m.%Y %H:%M"))
    monkeypatch.setattr(outlook, "outlook_available", lambda: True)
    monkeypatch.setattr("koprotokoll.app.outlook_available", lambda: True)
    return calls


def appointment(start, entry="serie", **changes):
    values = dict(EntryID=entry, Subject="Besprechung", Start=datetime.fromtimestamp(start, timezone.utc),
                  End=datetime.fromtimestamp(start + 1800, timezone.utc), AllDayEvent=False,
                  Location="", Body="https://teams.live.com/meet/123456789", ResponseStatus=3,
                  Recipients=[Obj(Address="ben@example.com", Type=1), Obj(Address="raum@example.com", Type=3)])
    values.update(changes)
    return Obj(**values)


def test_outlook_calendar_series_responses_and_links(monkeypatch):
    start = 1_800_000_000
    calls = install_calendar(monkeypatch, [appointment(start), appointment(start + 86400, ResponseStatus=4),
                                           appointment(start + 3 * 86400)])
    source = outlook.OutlookComCalendarSource("outlook-1")
    events = source.events(start, start + 2 * 86400)
    assert len(events) == 2 and len({e.id for e in events}) == 2
    assert events[0].attendees == ["ben@example.com"]
    assert events[0].self_response == "accepted" and events[1].self_response == "declined"
    assert find_meeting_url(vars(events[0])) == ("https://teams.live.com/meet/123456789", "teams")
    assert "[Start]" in calls[-1][1]


def test_exchange_smtp_address():
    recipient = Obj(Address="/O=EXCHANGE/OU=...", AddressEntry=Obj(
        Type="EX", GetExchangeUser=lambda: Obj(PrimarySmtpAddress="ben@example.com")))
    assert outlook.recipient_address(recipient) == "ben@example.com"


def test_windows_date_format_uses_user_locale_without_seconds(monkeypatch):
    import sys
    calls = []
    monkeypatch.setitem(sys.modules, "win32con", Obj(LOCALE_USER_DEFAULT=0x400, DATE_SHORTDATE=1, TIME_NOSECONDS=2))
    monkeypatch.setitem(sys.modules, "win32api", Obj(
        GetDateFormat=lambda *args: calls.append(args) or "04.11.2026",
        GetTimeFormat=lambda *args: calls.append(args) or "14:35"))
    assert outlook.outlook_date(1_800_000_000) == "04.11.2026 14:35"
    assert calls[0][0:2] == (0x400, 1) and calls[1][0:2] == (0x400, 2)


def test_outlook_source_api_and_single_default_calendar(tmp_path, monkeypatch):
    install_calendar(monkeypatch, [])
    with make_client(tmp_path) as client:
        assert client.get("/api/calendars/capabilities").json()["outlook"]
        response = client.post("/api/calendars", json={"kind": "outlook"})
        assert response.status_code == 201
        source = response.json()
        assert source["kind"] == "outlook" and source["enabled"]
        assert client.get(f"/api/calendars/{source['id']}/test").json() == {"events": []}
        assert client.post("/api/calendars", json={"kind": "outlook"}).status_code == 409
        assert not client.patch(f"/api/calendars/{source['id']}", json={"enabled": False}).json()["enabled"]
        client.delete(f"/api/calendars/{source['id']}")
        assert client.get("/api/calendars").json() == []


def test_outlook_unavailable_is_visible(tmp_path, monkeypatch):
    monkeypatch.setattr(outlook, "outlook_available", lambda: False)
    monkeypatch.setattr("koprotokoll.app.outlook_available", lambda: False)
    with make_client(tmp_path) as client:
        assert not client.get("/api/calendars/capabilities").json()["outlook"]
        assert client.post("/api/calendars", json={"kind": "outlook"}).status_code == 400
    with pytest.raises(outlook.OutlookError, match="Windows"):
        outlook.OutlookComCalendarSource("outlook").events(0, 1)
