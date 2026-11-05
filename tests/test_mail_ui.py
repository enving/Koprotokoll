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
        expect(page.get_by_label("Zusammenfassung nach Bot-Meetings an mich senden", exact=True)).not_to_be_checked()
        expect(page.get_by_label("Zusätzlich an alle Teilnehmenden senden", exact=True)).not_to_be_checked()
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
        assert page.locator("#mail-form").evaluate("""form => {
          const controls = [...form.querySelectorAll('input, select, button, p')];
          return controls.every(el => {
            const bounds = el.getBoundingClientRect();
            return bounds.left >= 0 && bounds.right <= innerWidth;
          });
        }""")
        assert page.locator("#main").evaluate("el => el.scrollWidth <= el.clientWidth")
        assert errors == []
        browser.close()


def test_gmail_recipient_preference_has_one_editor(tmp_path, monkeypatch):
    from playwright.sync_api import expect, sync_playwright

    monkeypatch.setattr('koprotokoll.google._http', lambda *args, **kwargs: {'items': []})
    with make_client(tmp_path) as client, sync_playwright() as pw:
        db = client.app.state.db
        db.set_kv('google_tokens', {'refresh_token': 'test', 'access_token': 'test',
                                    'expires_at': 99999999999, 'email': 'anna@example.com'})
        db.set_kv('google_settings', {'recap_to_attendees': True})
        browser = pw.chromium.launch(executable_path=_chrome_path(), headless=True)
        page = browser.new_page()

        def serve(route):
            req = route.request
            response = client.request(req.method, req.url, content=req.post_data_buffer, headers=req.headers)
            route.fulfill(status=response.status_code, headers=dict(response.headers), body=response.content)

        page.route('**/*', serve)
        page.goto('http://127.0.0.1/#integrations')
        page.wait_for_load_state('networkidle')
        expect(page.locator('#google-box input[type=checkbox]')).to_have_count(1)
        attendees = page.get_by_label('Zusätzlich an alle Teilnehmenden senden', exact=True)
        expect(attendees).to_be_checked()
        attendees.uncheck()
        page.get_by_role('button', name='E-Mail-Einstellungen speichern', exact=True).click()
        expect(page.get_by_role('status')).to_have_text('E-Mail-Einstellungen gespeichert.')
        assert not db.get_kv('google_settings')['recap_to_attendees']
        page.get_by_label('Versandweg', exact=True).select_option('smtp')
        page.get_by_label('Versandweg', exact=True).select_option('gmail')
        expect(attendees).not_to_be_checked()
        page.get_by_role('button', name='E-Mail-Einstellungen speichern', exact=True).click()
        expect(page.get_by_role('status')).to_have_text('E-Mail-Einstellungen gespeichert.')
        assert not db.get_kv('google_settings')['recap_to_attendees']
        page.reload()
        expect(page.get_by_label('Zusätzlich an alle Teilnehmenden senden', exact=True)).not_to_be_checked()
        page.get_by_role('button', name='E-Mail-Einstellungen speichern', exact=True).click()
        expect(page.get_by_role('status')).to_have_text('E-Mail-Einstellungen gespeichert.')
        assert not db.get_kv('google_settings')['recap_to_attendees']
        browser.close()
