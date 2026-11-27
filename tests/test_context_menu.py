"""Schnellmenü (Rechtsklick/Long-press): Kopieren landet in der Zwischenablage, sonst Fallback-Dialog (#67)."""

from urllib.parse import urlsplit

import pytest

from test_integrations import _chrome_path, make_client
from test_koprotokoll import wav_bytes


def _page(pw, chrome, client, width, height=900, permissions=(), init_script=None):
    browser = pw.chromium.launch(executable_path=chrome, headless=True)
    ctx = browser.new_context(viewport={"width": width, "height": height}, permissions=list(permissions))
    page = ctx.new_page()
    if init_script:
        page.add_init_script(init_script)
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)

    def serve(route):
        request = route.request
        url = urlsplit(request.url)
        response = client.request(request.method, url.path + ("?" + url.query if url.query else ""),
                                  content=request.post_data_buffer)
        route.fulfill(status=response.status_code, headers=dict(response.headers), body=response.content)

    page.route("**/*", serve)
    page.goto("http://127.0.0.1/")
    return browser, page, errors


@pytest.mark.parametrize("width", [1280, 390])
def test_context_menu_copy_writes_clipboard(tmp_path, width):
    """Mit Clipboard-Permission (https/localhost): Erfolgs-Toast + Text in der Zwischenablage."""
    pytest.importorskip("playwright")
    from playwright.sync_api import expect, sync_playwright

    chrome = _chrome_path()
    if chrome is None:
        pytest.skip("Chromium für Playwright nicht installiert")
    with make_client(tmp_path) as client, sync_playwright() as pw:
        client.post("/api/meetings", files={
            "file": ("menue.wav", wav_bytes(tmp_path, seconds=1), "audio/wav"),
        }, data={"title": "Menü-Test"})
        client.app.state.pipeline.join()
        browser, page, errors = _page(pw, chrome, client, width,
                                      permissions=["clipboard-read", "clipboard-write"])
        try:
            expect(page.locator(".m-item")).to_have_count(1)
            page.locator(".m-item").click(button="right")
            menu = page.locator(".ctx-menu")
            expect(menu).to_be_visible()
            menu.get_by_role("menuitem", name="Transkript kopieren").click()
            expect(page.locator("#toast")).to_contain_text("in die Zwischenablage kopiert")
            clip = page.evaluate("navigator.clipboard.readText()")
            assert "Guten Morgen" in clip
            assert errors == []
        finally:
            page.close()
            browser.close()


def test_context_menu_copy_fallback_shows_marked_dialog(tmp_path):
    """Ohne Clipboard-API (z. B. http über LAN-IP): Dialog mit markiertem Text statt leerer Ablage."""
    pytest.importorskip("playwright")
    from playwright.sync_api import expect, sync_playwright

    chrome = _chrome_path()
    if chrome is None:
        pytest.skip("Chromium für Playwright nicht installiert")
    with make_client(tmp_path) as client, sync_playwright() as pw:
        client.post("/api/meetings", files={
            "file": ("menue.wav", wav_bytes(tmp_path, seconds=1), "audio/wav"),
        }, data={"title": "Fallback-Test"})
        client.app.state.pipeline.join()
        # Unsicheren Kontext simulieren: keine Clipboard-API, execCommand schlägt fehl.
        init = ("Object.defineProperty(navigator, 'clipboard', {value: undefined, configurable: true});"
                "document.execCommand = () => false;")
        browser, page, errors = _page(pw, chrome, client, 1280, init_script=init)
        try:
            expect(page.locator(".m-item")).to_have_count(1)
            page.locator(".m-item").click(button="right")
            page.locator(".ctx-menu").get_by_role("menuitem", name="Transkript kopieren").click()
            dlg = page.locator("#dlg-copy")
            expect(dlg).to_be_visible()
            text = page.locator("#dlg-copy-text").input_value()
            assert "Guten Morgen" in text
            assert page.evaluate("document.activeElement.id") == "dlg-copy-text"
            assert page.evaluate("(() => { const t = document.querySelector('#dlg-copy-text');"
                                 " return t.selectionStart === 0 && t.selectionEnd === t.value.length; })()")
            expect(page.locator("#toast")).to_contain_text("Kopieren")
            assert errors == []
        finally:
            page.close()
            browser.close()
