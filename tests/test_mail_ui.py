"""SMTP-Einrichtung mit echter Oberfläche, lokaler API und ohne externen Versand."""

from urllib.parse import urlsplit

import pytest

from test_integrations import _chrome_path, make_client


@pytest.mark.parametrize("width", [1280, 390])
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_smtp_settings_ui(tmp_path, width, theme, monkeypatch):
    from playwright.sync_api import expect, sync_playwright

    sends = []
    monkeypatch.setattr("koprotokoll.mail.SmtpMailSender.send", lambda self, *args: sends.append(args))
    with make_client(tmp_path) as client, sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=_chrome_path(), headless=True)
        page = browser.new_page(viewport={"width": width, "height": 900}, color_scheme=theme)
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)

        def serve(route):
            req = route.request
            url = urlsplit(req.url)
            response = client.request(req.method, url.path, content=req.post_data_buffer,
                                      headers={"content-type": req.headers.get("content-type", "")})
            route.fulfill(status=response.status_code, headers=dict(response.headers), body=response.content)

        page.route("**/*", serve)
        page.goto("http://127.0.0.1/#integrations")
        page.wait_for_load_state("networkidle")
        page.get_by_label("Versandweg", exact=True).select_option("smtp")
        page.get_by_role("button", name="E-Mail-Einstellungen speichern", exact=True).click()
        assert page.get_by_label("SMTP-Server", exact=True).evaluate("el => el.validity.valueMissing")
        page.get_by_label("SMTP-Server", exact=True).fill("mail.example.com")
        page.get_by_label("Eigene Absenderadresse", exact=True).fill("ich@example.com")
        page.get_by_label("App-Passwort", exact=True).fill("test-passwort")
        page.get_by_role("button", name="E-Mail-Einstellungen speichern", exact=True).click()
        expect(page.get_by_role("status")).to_have_text("E-Mail-Einstellungen gespeichert.")
        expect(page.get_by_label("App-Passwort", exact=True)).to_have_value("")
        page.get_by_role("button", name="Test-Mail an mich", exact=True).click()
        expect(page.get_by_role("status")).to_have_text("Test-Mail an die gespeicherte eigene Adresse gesendet.")
        assert sends[0][0] == ["ich@example.com"]
        page.reload()
        expect(page.get_by_label("Versandweg", exact=True)).to_have_value("smtp")
        expect(page.get_by_label("SMTP-Server", exact=True)).to_have_value("mail.example.com")
        expect(page.get_by_label("App-Passwort", exact=True)).to_have_value("")
        assert client.get("/api/integrations/email").json()["smtp"]["password_set"]
        page.get_by_label("Gespeichertes Passwort löschen", exact=True).check()
        page.get_by_role("button", name="E-Mail-Einstellungen speichern", exact=True).click()
        expect(page.get_by_role("status")).to_have_text("E-Mail-Einstellungen gespeichert.")
        assert not client.get("/api/integrations/email").json()["smtp"]["password_set"]
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert errors == []
        browser.close()
