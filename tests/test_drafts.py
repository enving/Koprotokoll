"""Mailentwürfe mit nachgebildetem Prozess und COM; kein echter Mailversand."""

from contextlib import contextmanager
from email import policy
from email.parser import BytesParser
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import unquote

import koprotokoll.drafts as drafts
from test_integrations import make_client


def meeting(client):
    db = client.app.state.db
    mid = db.create_meeting("Grüße, Bob's Plan", "test.wav", meta={"attendees": ["ben@example.com"]})
    db.update_meeting(mid, status="done", summary={"overview": "A < B"})
    return mid


def test_eml_download_headers_utf8_html_and_auth(tmp_path):
    with make_client(tmp_path) as client:
        mid = meeting(client)
        path = f"/api/meetings/{mid}/recap.eml"
        response = client.get(path)
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("message/rfc822")
        msg = BytesParser(policy=policy.default).parsebytes(response.content)
        assert msg["To"] == "ben@example.com"
        assert msg["Subject"] == "Meeting-Zusammenfassung: Grüße, Bob's Plan"
        assert msg["X-Unsent"] == "1"
        assert "A &lt; B" in msg.get_body(preferencelist=("html",)).get_content()
        assert "A < B" in msg.get_body(preferencelist=("plain",)).get_content()
        assert client.get(path, headers={"X-Forwarded-For": "203.0.113.1"}).status_code == 401
        assert client.get("/api/meetings/999/recap.eml").status_code == 404
        client.app.state.db.update_meeting(mid, status="queued")
        assert client.get(path).status_code == 409


def test_thunderbird_arguments_cannot_inject_compose_fields(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(drafts, "thunderbird_executable", lambda: "/fake/thunderbird")
    monkeypatch.setattr(drafts.subprocess, "Popen", lambda args, **kw: calls.append((args, kw)))
    title = 'Bob\',bcc=\'evil@example.com",attachment="/private/test'
    drafts.open_draft("thunderbird", {"id": 1, "title": title, "summary": {},
                                     "meta": {"attendees": ["ben@example.com"]}}, "http://localhost", tmp_path)
    args, kw = calls[0]
    assert args[:2] == ["/fake/thunderbird", "-compose"] and kw == {"shell": False}
    fields = dict(item.split("=", 1) for item in args[2].split(","))
    decoded = {key: unquote(value.strip('"')) for key, value in fields.items()}
    assert set(decoded) == {"to", "subject", "message", "format"}
    assert decoded["subject"] == "Meeting-Zusammenfassung: " + title
    assert Path(decoded["message"]).is_relative_to(tmp_path / "drafts")
    assert "&quot;" in Path(decoded["message"]).read_text()


def test_outlook_draft_displays_without_send(tmp_path, monkeypatch):
    shown = []
    item = SimpleNamespace(Display=lambda: shown.append(True))

    @contextmanager
    def session():
        yield SimpleNamespace(CreateItem=lambda kind: item if kind == 0 else None)

    monkeypatch.setattr(drafts, "outlook_available", lambda: True)
    monkeypatch.setattr(drafts, "outlook_session", session)
    with make_client(tmp_path) as client:
        mid = meeting(client)
        response = client.post(f"/api/meetings/{mid}/recap/draft?ziel=outlook")
        assert response.status_code == 200 and shown == [True]
        assert item.To == "ben@example.com" and "Grüße" in item.Subject and "A &lt; B" in item.HTMLBody


def test_native_targets_are_local_only(tmp_path, monkeypatch):
    monkeypatch.setattr(drafts, "thunderbird_executable", lambda: "/fake/thunderbird")
    monkeypatch.setattr(drafts, "outlook_available", lambda: True)
    with make_client(tmp_path) as client:
        assert client.get("/api/integrations/email/drafts").json()["targets"] == ["eml", "thunderbird", "outlook"]
        key = client.post("/api/keys", json={"name": "test"}).json()["key"]
        headers = {"Authorization": "Bearer " + key, "X-Forwarded-For": "203.0.113.1"}
        assert client.get("/api/integrations/email/drafts", headers=headers).json()["targets"] == ["eml"]
        mid = meeting(client)
        assert client.post(f"/api/meetings/{mid}/recap/draft?ziel=outlook", headers=headers).status_code == 403
        monkeypatch.setattr(drafts, "thunderbird_executable", lambda: None)
        monkeypatch.setattr(drafts, "outlook_available", lambda: False)
        assert client.get("/api/integrations/email/drafts").json()["targets"] == ["eml"]
        assert client.post(f"/api/meetings/{mid}/recap/draft?ziel=thunderbird").status_code == 400


def test_draft_dropdown_and_download_in_browser(tmp_path, monkeypatch):
    from urllib.parse import urlsplit
    from playwright.sync_api import expect, sync_playwright
    from test_integrations import _chrome_path
    from test_koprotokoll import wav_bytes, wait_done

    calls = []
    monkeypatch.setattr(drafts, "thunderbird_executable", lambda: "/fake/thunderbird")
    monkeypatch.setattr(drafts, "outlook_available", lambda: False)
    monkeypatch.setattr("koprotokoll.app.open_draft", lambda *args: calls.append(args))
    with make_client(tmp_path) as client, sync_playwright() as pw:
        mid = client.post("/api/meetings", files={"file": ("test.wav", wav_bytes(tmp_path, 1), "audio/wav")}).json()["id"]
        wait_done(client, mid)
        browser = pw.chromium.launch(executable_path=_chrome_path(), headless=True)
        for width in (1280, 390):
            page = browser.new_page(viewport={"width": width, "height": 900})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))

            def serve(route):
                req = route.request
                response = client.request(req.method, urlsplit(req.url).path +
                                          ("?" + urlsplit(req.url).query if urlsplit(req.url).query else ""),
                                          content=req.post_data_buffer)
                route.fulfill(status=response.status_code, headers=dict(response.headers), body=response.content)

            page.route("**/*", serve)
            page.goto(f"http://127.0.0.1/#meeting={mid}")
            select = page.get_by_role("combobox", name="Als E-Mail öffnen", exact=True)
            expect(select).to_be_enabled()
            select.select_option("thunderbird")
            expect(select).to_have_value("")
            assert len(calls) == (1 if width == 1280 else 2)
            with page.expect_download() as download:
                select.select_option("eml")
            assert download.value.suggested_filename == f"meeting-{mid}.eml"
            assert BytesParser(policy=policy.default).parsebytes(Path(download.value.path()).read_bytes())["X-Unsent"] == "1"
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            assert errors == []
            page.close()
        browser.close()
