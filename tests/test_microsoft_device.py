"""Microsoft-Gerätecode und Graph-Mail mit nachgebildeten Endpunkten."""

import base64
import io
import json
from types import SimpleNamespace
from urllib.error import HTTPError

import pytest

import koprotokoll.microsoft as ms
from test_integrations import make_client


class FakeGraph:
    def __init__(self):
        self.responses = ["authorization_pending", "slow_down", "ready"]
        self.calls, self.sent, self.refresh_tokens = [], [], []

    def __call__(self, method, url, *, form=None, body=None, token=None):
        self.calls.append((method, url, form))
        if url == ms.DEVICE_URL:
            assert "User.Read" in form["scope"]
            assert "client_secret" not in form
            return {"device_code": "privater-code", "user_code": "TEST-CODE", "expires_in": 900,
                    "verification_uri": "https://microsoft.com/devicelogin", "interval": 5}
        if url == ms.TOKEN_URL:
            assert "client_secret" not in form
            if form["grant_type"] == "refresh_token":
                self.refresh_tokens.append(form["refresh_token"])
                return {"access_token": "neu", "refresh_token": "rt-neu", "expires_in": 3600}
            state = self.responses.pop(0)
            if state != "ready":
                raise ms.MicrosoftError("Erwarteter Testzustand", state)
            claim = base64.urlsafe_b64encode(json.dumps({"email": "ich@example.com"}).encode()).decode().rstrip("=")
            return {"access_token": "access-geheim", "refresh_token": "rt-alt", "expires_in": 3600,
                    "id_token": "x." + claim + ".y", "scope": "User.Read Calendars.Read Mail.Send offline_access"}
        if url == ms.GRAPH_BASE + "/me/sendMail":
            self.sent.append(body)
            return {}
        if url.startswith(ms.GRAPH_BASE + "/me?$select="):
            return {"mail": "ich@example.com"}
        if url.startswith(ms.GRAPH_BASE + "/me/calendarview"):
            return {"value": []}
        raise AssertionError((method, url))


def test_device_pending_slowdown_refresh_and_graph_recap(tmp_path, monkeypatch):
    fake = FakeGraph()
    clock = [1000.0]
    monkeypatch.setattr(ms, "_http", fake)
    monkeypatch.setattr(ms, "time", SimpleNamespace(time=lambda: clock[0]))
    with make_client(tmp_path, microsoft_client_id="public-client") as client:
        prefix = "/api/integrations/microsoft"
        assert client.get(prefix).json()["configured"]
        response = client.post(prefix + "/device")
        assert response.status_code == 200 and response.json()["user_code"] == "TEST-CODE"
        assert "privater-code" not in response.text
        assert client.post(prefix + "/device/poll").json()["status"] == "pending"
        assert len(fake.calls) == 1
        for interval in (5, 5):
            clock[0] += interval
            assert client.post(prefix + "/device/poll").json()["status"] == "pending"
        clock[0] += 10
        response = client.post(prefix + "/device/poll")
        assert response.json()["status"] == "connected"
        assert "access-geheim" not in response.text and "rt-alt" not in response.text
        assert client.app.state.db.get_kv("microsoft_device") is None
        assert client.put("/api/integrations/email", json={"provider": "microsoft", "send_recap": True}).status_code == 200
        assert client.post("/api/integrations/email/test").status_code == 200
        assert fake.sent[-1]["message"]["toRecipients"] == [{"emailAddress": {"address": "ich@example.com"}}]
        assert fake.sent[-1]["message"]["body"]["contentType"] == "HTML"
        client.app.state.events.emit("meeting.completed", {"id": 1, "title": "Test", "summary": {}, "meta": {"source": "bot"}})
        client.app.state.events.join()
        assert len(fake.sent) == 2
        tokens = client.app.state.db.get_kv("microsoft_tokens")
        client.app.state.db.set_kv("microsoft_tokens", {**tokens, "expires_at": 0})
        assert client.post("/api/integrations/email/test").status_code == 200
        assert fake.refresh_tokens == ["rt-alt"]
        assert client.app.state.db.get_kv("microsoft_tokens")["refresh_token"] == "rt-neu"


@pytest.mark.parametrize("terminal", ["expired_token", "authorization_declined", "local-expiry"])
def test_device_expired_or_declined_is_clear(tmp_path, monkeypatch, terminal):
    fake = FakeGraph()
    fake.responses = [terminal]
    monkeypatch.setattr(ms, "_http", fake)
    clock = [1000.0]
    monkeypatch.setattr(ms, "time", SimpleNamespace(time=lambda: clock[0]))
    with make_client(tmp_path, microsoft_client_id="public-client") as client:
        client.post("/api/integrations/microsoft/device")
        clock[0] += 1000 if terminal == "local-expiry" else 5
        response = client.post("/api/integrations/microsoft/device/poll")
        assert response.status_code == 400 and "abgelaufen" in response.text
        assert client.app.state.db.get_kv("microsoft_device") is None


def test_http_auth_error_keeps_code_without_echoing_server_body(monkeypatch):
    def fail(*args, **kwargs):
        raise HTTPError(ms.TOKEN_URL, 400, "Fehler", {}, io.BytesIO(
            b'{"error":"authorization_pending","error_description":"dummy-passwort"}'))
    monkeypatch.setattr(ms.urllib.request, "urlopen", fail)
    with pytest.raises(ms.MicrosoftError) as error:
        ms._http("POST", ms.TOKEN_URL, form={})
    assert error.value.code == "authorization_pending" and "dummy-passwort" not in str(error.value)


def test_graph_pagination_and_foreign_next_link(tmp_path, monkeypatch):
    from koprotokoll.config import Settings
    from koprotokoll.db import Database

    client = ms.MicrosoftClient(Settings(data_dir=tmp_path), Database(tmp_path / "db.sqlite3"))
    client.db.set_kv("microsoft_tokens", {"access_token": "test-token", "refresh_token": "rt", "expires_at": float("inf")})
    calls = []
    next_link = [ms.GRAPH_BASE + "/page2"]

    def http(method, url, **kwargs):
        calls.append(url)
        if url.endswith("page2"):
            return {"value": [{"id": "zweiter"}]}
        return {"value": [{"id": "erster"}], "@odata.nextLink": next_link[0]}

    monkeypatch.setattr(ms, "_http", http)
    assert [e["id"] for e in client.events(0, 10)] == ["erster", "zweiter"]
    next_link[0] = "https://example.com/token-falle"
    with pytest.raises(ms.MicrosoftError, match="Folgeseite"):
        client.events(0, 10)
    assert not any("example.com" in url for url in calls)


def test_device_login_in_real_browser_with_fake_provider(tmp_path, monkeypatch):
    from urllib.parse import urlsplit
    from playwright.sync_api import expect, sync_playwright
    from test_integrations import _chrome_path

    fake = FakeGraph()
    fake.responses = ["ready"]
    clock = [1000.0]
    monkeypatch.setattr(ms, "_http", fake)
    monkeypatch.setattr(ms, "time", SimpleNamespace(time=lambda: clock[0]))
    with make_client(tmp_path, microsoft_client_id="public-client") as client, sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=_chrome_path(), headless=True)
        page = browser.new_page(viewport={"width": 390, "height": 900})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))

        def serve(route):
            req = route.request
            url = urlsplit(req.url)
            response = client.request(req.method, url.path, content=req.post_data_buffer)
            route.fulfill(status=response.status_code, headers=dict(response.headers), body=response.content)

        page.route("**/*", serve)
        page.goto("http://127.0.0.1/#integrations")
        page.get_by_role("button", name="Mit Microsoft per Code anmelden", exact=True).click()
        expect(page.get_by_text("TEST-CODE", exact=True)).to_be_visible()
        expect(page.get_by_role("link", name="Microsoft-Anmeldeseite öffnen", exact=True)).to_have_attribute("href", "https://microsoft.com/devicelogin")
        clock[0] += 5
        page.get_by_role("button", name="Anmeldung prüfen", exact=True).click()
        expect(page.locator("#microsoft-box")).to_contain_text("Verbunden als ich@example.com")
        assert "access-geheim" not in page.content() and "privater-code" not in page.content()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert errors == []
        browser.close()


def test_task_permissions_are_requested_only_explicitly(tmp_path, monkeypatch):
    fake = FakeGraph()
    monkeypatch.setattr(ms, "_http", fake)
    with make_client(tmp_path, microsoft_client_id="public-client") as client:
        client.post("/api/integrations/microsoft/device")
        assert "Tasks.ReadWrite" not in fake.calls[-1][2]["scope"]
        client.post("/api/integrations/microsoft/device?tasks=1")
        assert "Tasks.ReadWrite" in fake.calls[-1][2]["scope"]


def test_invalid_client_secret_is_not_echoed(tmp_path):
    with make_client(tmp_path) as client:
        response = client.put("/api/integrations/microsoft/client", json={
            "client_id": "test", "client_secret": {"sensitive": "dummy-review-secret"}})
        assert response.status_code == 422 and "dummy-review-secret" not in response.text


@pytest.mark.parametrize("response_state", ["ready", "authorization_pending"])
def test_disconnect_cancels_inflight_device_response(tmp_path, monkeypatch, response_state):
    import threading
    from koprotokoll.config import Settings
    from koprotokoll.db import Database

    entered, release = threading.Event(), threading.Event()
    fake = FakeGraph()
    fake.responses = [response_state]

    def http(method, url, **kwargs):
        if url == ms.TOKEN_URL:
            entered.set()
            assert release.wait(5)
        return fake(method, url, **kwargs)

    monkeypatch.setattr(ms, "_http", http)
    client = ms.MicrosoftClient(Settings(data_dir=tmp_path, microsoft_client_id="public-client"), Database(tmp_path / "db.sqlite3"))
    client.start_device()
    pending = client.db.get_kv("microsoft_device")
    client.db.set_kv("microsoft_device", {**pending, "next_poll": 0})
    errors = []

    def poll():
        try:
            client.poll_device()
        except ms.MicrosoftError as error:
            errors.append(str(error))

    thread = threading.Thread(target=poll)
    thread.start()
    assert entered.wait(5)
    client.disconnect()
    release.set()
    thread.join(5)
    assert not thread.is_alive() and not client.connected()
    assert client.db.get_kv("microsoft_device") is None
    assert errors and "abgebrochen" in errors[0]


def test_mail_address_comes_from_graph_mailbox(tmp_path, monkeypatch):
    from koprotokoll.config import Settings
    from koprotokoll.db import Database

    claim = base64.urlsafe_b64encode(json.dumps({"preferred_username": "login@tenant.onmicrosoft.com"}).encode()).decode().rstrip("=")
    monkeypatch.setattr(ms, "_http", lambda *args, **kwargs: {"mail": "ben@example.com"})
    client = ms.MicrosoftClient(Settings(data_dir=tmp_path), Database(tmp_path / "db.sqlite3"))
    client._store_tokens({"access_token": "test", "refresh_token": "rt", "id_token": "x." + claim + ".y"}, "device")
    assert client.status()["email"] == "ben@example.com"


def test_parallel_device_checks_reserve_provider_interval(tmp_path, monkeypatch):
    import threading
    from koprotokoll.config import Settings
    from koprotokoll.db import Database
    from koprotokoll.microsoft import MicrosoftClient, MicrosoftError, TOKEN_URL
    import time
    import koprotokoll.microsoft as module

    db = Database(tmp_path / 'db.sqlite')
    microsoft = MicrosoftClient(Settings(data_dir=tmp_path, microsoft_client_id='client'), db)
    db.set_kv('microsoft_device', {'device_code': 'code', 'expires': time.time() + 600,
                                 'interval': 5, 'next_poll': 0, 'scope': 'offline_access'})
    started, release, calls, errors = threading.Event(), threading.Event(), [], []

    def token_request(method, url, **kwargs):
        assert url == TOKEN_URL
        calls.append(url)
        started.set()
        assert release.wait(3)
        raise MicrosoftError('Noch nicht bestätigt', 'authorization_pending')

    monkeypatch.setattr(module, '_http', token_request)
    def first():
        try:
            microsoft.poll_device()
        except Exception as error:
            errors.append(error)
    worker = threading.Thread(target=first)
    worker.start()
    assert started.wait(2)
    try:
        assert microsoft.poll_device()['status'] == 'pending'
        assert len(calls) == 1
    finally:
        release.set()
        worker.join(3)
    assert not worker.is_alive() and not errors


def test_slow_device_check_cannot_be_overtaken_after_interval(tmp_path, monkeypatch):
    import threading
    from koprotokoll.config import Settings
    from koprotokoll.db import Database

    clock = [100.0]
    monkeypatch.setattr(ms.time, 'time', lambda: clock[0])
    db = Database(tmp_path / 'db.sqlite')
    client = ms.MicrosoftClient(Settings(data_dir=tmp_path, microsoft_client_id='client'), db)
    db.set_kv('microsoft_device', {'device_code': 'code', 'expires': 1000,
                                 'interval': 5, 'next_poll': 0, 'scope': 'offline_access'})
    entered, release, calls, results = threading.Event(), threading.Event(), [], []

    def http(*args, **kwargs):
        calls.append(clock[0])
        if len(calls) == 1:
            entered.set()
            assert release.wait(3)
            raise ms.MicrosoftError('Langsamer abfragen', 'slow_down')
        raise ms.MicrosoftError('Noch nicht bestätigt', 'authorization_pending')

    monkeypatch.setattr(ms, '_http', http)
    worker = threading.Thread(target=lambda: results.append(client.poll_device()))
    worker.start()
    assert entered.wait(2)
    clock[0] = 106
    try:
        assert client.poll_device() == {'status': 'pending', 'interval': 5}
        assert len(calls) == 1
    finally:
        release.set()
        worker.join(3)
    assert results == [{'status': 'pending', 'interval': 10}]
    assert db.get_kv('microsoft_device')['interval'] == 10
    clock[0] = 117
    assert client.poll_device() == {'status': 'pending', 'interval': 10}
    assert db.get_kv('microsoft_device')['next_poll'] == 127
