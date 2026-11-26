"""Weboberfläche: eine außerhalb des Browsers gestartete Aufnahme (z. B. per Doppeltipp
im Infobereich) wird über /api/capture auch ohne Klick in der Seite sichtbar."""

import time
from urllib.parse import urlsplit

import pytest

from test_integrations import _chrome_path, make_client
from test_koprotokoll import wav_bytes


def _playwright_page(pw, chrome, client, width, capture_log):
    browser = pw.chromium.launch(executable_path=chrome, headless=True)
    page = browser.new_page(viewport={"width": width, "height": 900})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)

    def serve(route):
        request = route.request
        url = urlsplit(request.url)
        if url.path == "/api/capture":
            capture_log.append(time.monotonic())
        response = client.request(request.method, url.path + ("?" + url.query if url.query else ""),
                                  content=request.post_data_buffer)
        route.fulfill(status=response.status_code, headers=dict(response.headers), body=response.content)

    page.route("**/*", serve)
    page.goto("http://127.0.0.1/")
    return browser, page, errors


@pytest.mark.parametrize("width", [1280, 390])
def test_fremd_gestartete_aufnahme_wird_sichtbar(tmp_path, monkeypatch, width):
    """Doppeltipp im Tray: Punkt leuchtet, Uhr läuft ab echtem Start, Pegel zeichnet,
    Start ist gesperrt mit sichtbarem Grund; Doppeltipp zum Stopp räumt auf wie ein Klick."""
    pytest.importorskip("playwright")
    from playwright.sync_api import expect, sync_playwright

    chrome = _chrome_path()
    if chrome is None:
        pytest.skip("Chromium für Playwright nicht installiert")
    with make_client(tmp_path) as client, sync_playwright() as pw:
        mid = client.post("/api/meetings", files={
            "file": ("fremd-start.wav", wav_bytes(tmp_path, 1), "audio/wav"),
        }).json()["id"]
        # Aufnahme läuft, wurde aber nicht von dieser Seite gestartet.
        started_at = time.time() - 5
        monkeypatch.setattr(client.app.state.capture, "status",
                            lambda: {"active": True, "device": "Mikrofon", "started_at": started_at,
                                     "level_db": -25, "error": None})
        capture_log = []
        browser, page, errors = _playwright_page(pw, chrome, client, width, capture_log)
        try:
            page.click("#btn-record")
            dot = page.locator("#rec-dot")
            expect(dot).to_have_class("dot on")
            expect(page.locator("#rec-stop")).to_be_visible()
            expect(page.locator("#rec-stop")).to_have_text("Stopp & auswerten")
            expect(page.locator("#rec-start")).to_be_hidden()
            expect(page.locator("#rec-start")).to_be_disabled()
            expect(page.locator("#rec-busy")).to_be_visible()
            expect(page.locator("#rec-busy")).to_have_text("Es läuft bereits eine Aufnahme.")
            # Uhr läuft ab dem echten Startzeitpunkt des Servers (started_at = jetzt - 5 s).
            expect(page.locator("#rec-time")).not_to_have_text("00:00")
            elapsed = sum(int(p) * 60 ** i for i, p in enumerate(reversed(page.locator("#rec-time").inner_text().split(":"))))
            assert 5 <= elapsed <= 20
            # Pegel zeichnet auf dem Canvas.
            assert page.evaluate(
                "(() => { const c = document.querySelector('#rec-level');"
                " return c.getContext('2d').getImageData(0, 0, c.width, c.height).data.some(v => v > 0); })()")

            # Doppeltipp stoppt: Server meldet inaktiv und das gespeicherte Meeting.
            monkeypatch.setattr(client.app.state.capture, "status", lambda: {"active": False})
            client.app.state.last_capture = {"meeting_id": mid, "stop_reason": None}
            expect(dot).to_have_class("dot")
            expect(page.locator("#rec-time")).to_have_text("00:00")
            expect(page.locator("#dlg-record")).to_be_hidden()
            expect(page.locator("#rec-start")).to_be_enabled()
            expect(page.locator("#rec-busy")).to_be_hidden()
            # Meeting-Liste aktualisiert und neues Meeting geöffnet wie nach einem Klick.
            expect(page.locator("#meeting-list .m-item", has_text="fremd-start")).to_be_visible()
            expect(page.locator("#m-title")).to_have_text("fremd-start")
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            assert errors == []
        finally:
            page.close()
            browser.close()


def test_hintergrund_tab_fragt_capture_nicht_ab(tmp_path, monkeypatch):
    """Im Hintergrund-Tab (document.hidden) geht keine Abfrage an /api/capture raus."""
    pytest.importorskip("playwright")
    from playwright.sync_api import sync_playwright

    chrome = _chrome_path()
    if chrome is None:
        pytest.skip("Chromium für Playwright nicht installiert")
    with make_client(tmp_path) as client, sync_playwright() as pw:
        monkeypatch.setattr(client.app.state.capture, "status", lambda: {"active": False})
        capture_log = []
        browser, page, errors = _playwright_page(pw, chrome, client, 1280, capture_log)
        try:
            page.wait_for_timeout(2500)
            assert len(capture_log) >= 2  # Start-Abfrage plus Sekundentakt
            page.evaluate("Object.defineProperty(document, 'hidden', {value: true, configurable: true})")
            page.wait_for_timeout(2500)
            hidden_count = len(capture_log)
            page.wait_for_timeout(1500)
            assert len(capture_log) == hidden_count  # keine Abfrage im Hintergrund-Tab
            page.evaluate("delete document.hidden")
            page.wait_for_timeout(2500)
            assert len(capture_log) > hidden_count  # sichtbar wieder: Takt läuft weiter
            assert errors == []
        finally:
            page.close()
            browser.close()


def test_capture_fehler_als_deutsche_meldung_sichtbar(tmp_path, monkeypatch):
    """Ein error aus /api/capture erscheint als deutsche Meldung statt eines stummen Punkts."""
    pytest.importorskip("playwright")
    from playwright.sync_api import expect, sync_playwright

    chrome = _chrome_path()
    if chrome is None:
        pytest.skip("Chromium für Playwright nicht installiert")
    with make_client(tmp_path) as client, sync_playwright() as pw:
        monkeypatch.setattr(client.app.state.capture, "status", lambda: {"active": False})
        client.app.state.last_capture = {"error": "Der Audioeingang lieferte beim Start keine Samples."}
        capture_log = []
        browser, page, errors = _playwright_page(pw, chrome, client, 1280, capture_log)
        try:
            page.click("#btn-record")
            expect(page.locator("#rec-silence-status")).to_contain_text(
                "Der Audioeingang lieferte beim Start keine Samples.")
            expect(page.locator("#rec-dot")).to_have_class("dot")
            expect(page.locator("#rec-start")).to_be_enabled()
            assert errors == []
        finally:
            page.close()
            browser.close()
