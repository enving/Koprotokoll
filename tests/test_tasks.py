"""VTODO und wiederholter Export gegen lokale oder nachgebildete Aufgabenserver."""

from contextlib import contextmanager
from datetime import date, datetime, timezone
from http.server import HTTPServer
from types import SimpleNamespace as Obj
import threading
import time

from icalendar import Calendar
import pytest

from koprotokoll import microsoft, outlook
from koprotokoll.tasks import resolve_due
from test_integrations import FakeCalDavHandler, make_client


@pytest.mark.parametrize("text,expected", [
    ("bis Freitag", date(2026, 11, 6)), ("morgen", date(2026, 11, 5)),
    ("nächste Woche", date(2026, 11, 9)), ("Ende des Monats", date(2026, 11, 30)),
    ("bis zum 3.11.", date(2026, 11, 3)), ("03.11.2027", date(2027, 11, 3)),
    ("31.02.", None), ("irgendwann", None), (None, None),
])
def test_due_dates_relative_to_meeting(text, expected):
    assert resolve_due(text, date(2026, 11, 4)) == expected


def meeting(client):
    db = client.app.state.db
    mid = db.create_meeting("Planung", "sample.wav")
    db.update_meeting(mid, status="done", created_at=datetime(2026, 11, 4, 12, tzinfo=timezone.utc).timestamp(),
                      summary={"action_items": [{"task": "Grüße, Unterlagen prüfen; " + "ä" * 90,
                                                   "owner": "Ben", "due": "bis Freitag", "time": "00:05"}]})
    db.replace_segments(mid, [{"speaker": "S1", "start": 4, "end": 8, "text": "Ben, bitte die Unterlagen prüfen."}])
    return mid


def test_ics_unicode_due_quote_and_stable_uid_across_restarts(tmp_path):
    with make_client(tmp_path) as client:
        mid = meeting(client)
        response = client.get(f"/api/meetings/{mid}/aufgaben.ics")
        assert response.status_code == 200
        todo = Calendar.from_ical(response.content).walk("VTODO")[0]
        assert str(todo["SUMMARY"]).startswith("Grüße, Unterlagen prüfen;")
        assert "Transkript [00:05]: Ben, bitte" in str(todo["DESCRIPTION"])
        assert str(todo["URL"]).endswith(f"#meeting={mid}&t=5")
        assert todo["DUE"].dt == date(2026, 11, 6)
        assert all(len(line) <= 75 for line in response.content.split(b"\r\n"))
        uid = str(todo["UID"])
        assert client.get(f"/api/meetings/{mid}/aufgaben.ics", headers={"X-Forwarded-For": "203.0.113.1"}).status_code == 401
    with make_client(tmp_path) as client:
        assert str(Calendar.from_ical(client.get(f"/api/meetings/{mid}/aufgaben.ics").content).walk("VTODO")[0]["UID"]) == uid


def test_caldav_write_is_conditional_and_repeat_is_not_duplicate(tmp_path):
    stored = {}

    class Handler(FakeCalDavHandler):
        def do_PUT(self):
            assert self._authed()
            assert self.headers["If-None-Match"] == "*"
            assert self.headers["Content-Type"].startswith("text/calendar")
            body = self.rfile.read(int(self.headers["Content-Length"]))
            status = 412 if self.path in stored else 201
            if status == 201:
                stored[self.path] = body
            self.send_response(status)
            self.end_headers()

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with make_client(tmp_path) as client:
            client.app.state.db.add_calendar_source("caldav", "Testkonto", {
                "server_url": f"http://127.0.0.1:{server.server_port}/dav/",
                "username": "ich@example.com", "password": "app-pass"})
            mid = meeting(client)
            targets = client.get("/api/tasks/targets").json()
            assert targets["errors"] == [] and len(targets["targets"]) == 1
            target = targets["targets"][0]
            assert "todo" in target["url"] or "tasks" in target["url"]
            path = f"/api/meetings/{mid}/tasks/export"
            assert client.post(path, json={"target": target["id"]}).json() == {"created": 1, "skipped": 0}
            assert client.post(path, json={"target": target["id"]}).json() == {"created": 0, "skipped": 1}
            assert len(stored) == 1
            assert len(Calendar.from_ical(next(iter(stored.values()))).walk("VTODO")) == 1
    finally:
        server.shutdown(); server.server_close(); thread.join()


def test_graph_task_repeat_uses_linked_resource_id(tmp_path, monkeypatch):
    stored = []

    def http(method, url, *, body=None, **kwargs):
        if url.endswith("/me/todo/lists"):
            return {"value": [{"id": "liste", "displayName": "Aufgaben"}]}
        if method == "GET" and "/liste/tasks?" in url:
            return {"value": stored}
        if method == "POST" and url.endswith("/liste/tasks"):
            stored.append(body)
            return {"id": str(len(stored))}
        raise AssertionError((method, url))

    monkeypatch.setattr(microsoft, "_http", http)
    with make_client(tmp_path) as client:
        client.app.state.db.set_kv("microsoft_tokens", {"access_token": "test", "refresh_token": "rt",
            "expires_at": time.time() + 3600, "scope": "Tasks.ReadWrite"})
        mid = meeting(client)
        target = client.get("/api/tasks/targets").json()["targets"][0]
        for expected in ({"created": 1, "skipped": 0}, {"created": 0, "skipped": 1}):
            assert client.post(f"/api/meetings/{mid}/tasks/export", json={"target": target["id"]}).json() == expected
        assert len(stored) == 1 and stored[0]["dueDateTime"]["dateTime"] == "2026-11-06T00:00:00"


def test_outlook_task_repeat_uses_hidden_property(tmp_path, monkeypatch):
    stored = []

    class Item:
        def __init__(self):
            self.properties = {}
            self.UserProperties = Obj(Find=self.properties.get, Add=self.add)

        def add(self, name, kind, folder):
            self.properties[name] = Obj(Value=None)
            return self.properties[name]

        def Save(self):
            stored.append(self)

    @contextmanager
    def session():
        yield Obj(GetNamespace=lambda _: Obj(GetDefaultFolder=lambda _: Obj(Items=stored)),
                  CreateItem=lambda kind: Item() if kind == 3 else None)

    monkeypatch.setattr(outlook, "outlook_available", lambda: True)
    monkeypatch.setattr(outlook, "outlook_session", session)
    with make_client(tmp_path) as client:
        mid = meeting(client)
        path = f"/api/meetings/{mid}/tasks/export"
        assert client.post(path, json={"target": "outlook"}).json() == {"created": 1, "skipped": 0}
        assert client.post(path, json={"target": "outlook"}).json() == {"created": 0, "skipped": 1}
        assert len(stored) == 1 and stored[0].DueDate.date() == date(2026, 11, 6)


def test_task_export_and_time_link_in_browser(tmp_path, monkeypatch):
    from pathlib import Path
    from urllib.parse import urlsplit
    from playwright.sync_api import expect, sync_playwright
    from test_integrations import _chrome_path
    from test_koprotokoll import wav_bytes

    calls = []
    monkeypatch.setattr("koprotokoll.tasks.TaskExporter.targets", lambda self: {
        "targets": [{"id": "testliste", "label": "Lokale Testliste"}], "errors": []})

    def export(self, target, tasks):
        calls.append((target, tasks))
        return {"created": 1 if len(calls) == 1 else 0, "skipped": 0 if len(calls) == 1 else 1}

    monkeypatch.setattr("koprotokoll.tasks.TaskExporter.export", export)
    with make_client(tmp_path) as client, sync_playwright() as pw:
        mid = meeting(client)
        audio = tmp_path / "player.wav"
        audio.write_bytes(wav_bytes(tmp_path, 10))
        client.app.state.db.update_meeting(mid, audio_path=str(audio))
        browser = pw.chromium.launch(executable_path=_chrome_path(), headless=True)
        page = browser.new_page(viewport={"width": 390, "height": 900})
        page.add_init_script("HTMLMediaElement.prototype.play = function() { return Promise.resolve(); };")
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))

        def serve(route):
            req = route.request
            url = urlsplit(req.url)
            response = client.request(req.method, url.path, content=req.post_data_buffer,
                                      headers={"content-type": req.headers.get("content-type", "")})
            route.fulfill(status=response.status_code, headers=dict(response.headers), body=response.content)

        page.route("**/*", serve)
        page.goto(f"http://127.0.0.1/#meeting={mid}&t=5")
        expect(page).to_have_url(f"http://127.0.0.1/#meeting={mid}&t=5")
        page.wait_for_function("document.querySelector('#player').currentTime >= 4.9")
        page.get_by_role("tab", name="Übersicht", exact=True).click()
        page.get_by_text("Aufgaben exportieren", exact=True).click()
        expect(page.get_by_label("Aufgabenliste", exact=True)).to_have_value("testliste")
        button = page.get_by_role("button", name="In gewählte Aufgabenliste exportieren", exact=True)
        button.click()
        expect(page.locator(".task-export [role=status]")).to_have_text("1 Aufgabe angelegt, 0 bereits vorhanden.")
        button.click()
        expect(page.locator(".task-export [role=status]")).to_have_text("0 Aufgaben angelegt, 1 bereits vorhanden.")
        with page.expect_download() as download:
            page.get_by_role("link", name="ICS-Datei mit Aufgaben herunterladen", exact=True).click()
        assert len(Calendar.from_ical(Path(download.value.path()).read_bytes()).walk("VTODO")) == 1
        assert calls[0][0] == "testliste" and calls[0][1][0]["due"] == date(2026, 11, 6)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert errors == []
        browser.close()
