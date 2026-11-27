"""Browsernavigation mit echter Oberfläche und lokaler Test-API."""

import re
from urllib.parse import urlsplit

import pytest

from test_integrations import _chrome_path, make_client
from test_koprotokoll import wav_bytes


@pytest.mark.parametrize("width", [1280, 390])
def test_integrations_navigation(tmp_path, width):
    pytest.importorskip("playwright")
    from playwright.sync_api import expect, sync_playwright

    chrome = _chrome_path()
    if chrome is None:
        pytest.skip("Chromium für Playwright nicht installiert")

    with make_client(tmp_path) as client, sync_playwright() as pw:
        mid = client.post("/api/meetings", files={
            "file": ("navigation.wav", wav_bytes(tmp_path, seconds=1), "audio/wav"),
        }).json()["id"]
        browser = pw.chromium.launch(executable_path=chrome, headless=True)
        page = browser.new_page(viewport={"width": width, "height": 900})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)

        def serve(route):
            request = route.request
            url = urlsplit(request.url)
            response = client.request(request.method, url.path + ("?" + url.query if url.query else ""),
                                      content=request.post_data_buffer)
            route.fulfill(status=response.status_code, headers=dict(response.headers), body=response.content)

        page.route("**/*", serve)
        root = "http://127.0.0.1/"
        integrations = page.locator("#integrations")
        empty = page.locator("#empty")
        nav = page.locator("#btn-integrations")
        close = page.get_by_role("button", name="Integrationen & API schließen", exact=True)
        # Startansicht: breit die Willkommensseite in #main, auf dem Handy die Liste in der Sidebar.
        home = empty if width == 1280 else page.locator("#sidebar")

        page.goto(root + "#integrations")
        expect(integrations).to_be_visible()
        page.reload()
        expect(integrations).to_be_visible()
        close.click()
        expect(home).to_be_visible()
        nav.click()
        expect(integrations).to_be_visible()
        # Umschalten per Nav-Knopf nur breit; auf dem Handy schließt die ✕-Schaltfläche.
        (nav if width == 1280 else close).click()
        expect(home).to_be_visible()

        nav.focus()
        page.keyboard.press("Enter")
        expect(integrations).to_be_visible()
        page.go_back()
        expect(home).to_be_visible()
        page.go_forward()
        expect(integrations).to_be_visible()

        page.goto(root + f"#meeting={mid}")
        expect(page.locator("#meeting")).to_be_visible()
        # Master-Detail auf dem Handy: Liste ausgeblendet, Zurück-Button führt zur Liste.
        if width == 390:
            expect(page.locator("#m-back")).to_be_visible()
            expect(page.locator("#sidebar")).to_be_hidden()
            page.locator("#m-back").click()
            expect(home).to_be_visible()
            expect(page.locator("#meeting")).to_be_hidden()
            page.goto(root + f"#meeting={mid}")
            expect(page.locator("#meeting")).to_be_visible()
        else:
            expect(page.locator("#m-back")).to_be_hidden()
        # Auf dem Handy erreicht man Integrationen über „Zurück" → Liste → Nav-Knopf.
        for button in ((close, nav) if width == 1280 else (close,)):
            if width == 390:
                page.locator("#m-back").click()
                expect(page.locator("#sidebar")).to_be_visible()
            nav.click()
            expect(integrations).to_be_visible()
            button.click()
            expect(page.locator("#meeting")).to_be_visible()
            expect(page).to_have_url(root + f"#meeting={mid}")

        dialogs = []

        def dismiss(dialog):
            dialogs.append(dialog.message)
            dialog.accept()

        page.on("dialog", dismiss)
        page.goto(root + "#integrations?google_error=test")
        expect(integrations).to_be_visible()
        expect(page).to_have_url(root + "#integrations?google_error=test")
        assert dialogs == ["Anmeldung abgebrochen: test"]
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert errors == []
        browser.close()


@pytest.mark.parametrize("width", [1280, 390])
def test_listenauswahl_ueber_ganze_zeile(tmp_path, width):
    """Kästchen als große Touch-Fläche; ab der ersten Markierung schaltet ein Tipp
    irgendwo auf die Zeile die Markierung um, statt das Meeting zu öffnen."""
    pytest.importorskip("playwright")
    from playwright.sync_api import expect, sync_playwright

    chrome = _chrome_path()
    if chrome is None:
        pytest.skip("Chromium für Playwright nicht installiert")

    with make_client(tmp_path) as client, sync_playwright() as pw:
        for name in ("eins.wav", "zwei.wav"):
            client.post("/api/meetings", files={
                "file": (name, wav_bytes(tmp_path, seconds=1), "audio/wav"),
            })
        browser = pw.chromium.launch(executable_path=chrome, headless=True)
        page = browser.new_page(viewport={"width": width, "height": 900})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)

        def serve(route):
            request = route.request
            url = urlsplit(request.url)
            response = client.request(request.method, url.path + ("?" + url.query if url.query else ""),
                                      content=request.post_data_buffer)
            route.fulfill(status=response.status_code, headers=dict(response.headers), body=response.content)

        page.route("**/*", serve)
        root = "http://127.0.0.1/"
        page.goto(root)
        first = page.locator(".m-item").first
        second = page.locator(".m-item").nth(1)
        expect(page.locator(".m-item")).to_have_count(2)

        # Ohne Markierung öffnet ein Tipp auf die Zeile das Meeting.
        first.locator(".m-body").click()
        expect(page.locator("#meeting")).to_be_visible()
        page.goto(root)
        expect(page.locator(".m-item")).to_have_count(2)

        # Kästchen markiert; Zeile bekommt Auswahl-Layout, Sammelleiste erscheint.
        first.locator(".m-check").check()
        expect(first).to_have_class(re.compile(r"\bselected\b"))
        expect(page.locator("#m-bulk")).to_be_visible()
        expect(page.locator("#m-bulk-count")).to_have_text("1 ausgewählt")

        # Auswahlmodus: Tipp auf den Text der zweiten Zeile markiert sie, ohne zu öffnen.
        second.locator(".m-body").click()
        expect(second).to_have_class(re.compile(r"\bselected\b"))
        expect(page.locator("#m-bulk-count")).to_have_text("2 ausgewählt")
        expect(page.locator("#meeting")).to_be_hidden()
        # Erneuter Tipp hebt die Markierung wieder auf.
        second.locator(".m-body").click()
        expect(second).not_to_have_class(re.compile(r"\bselected\b"))
        expect(page.locator("#m-bulk-count")).to_have_text("1 ausgewählt")

        # „Fertig" beendet die Auswahl.
        page.locator("#m-bulk-done").click()
        expect(page.locator("#m-bulk")).to_be_hidden()
        expect(first).not_to_have_class(re.compile(r"\bselected\b"))

        # Ohne Auswahlmodus öffnet der Zeilen-Tipp wieder das Meeting.
        second.locator(".m-body").click()
        expect(page.locator("#meeting")).to_be_visible()
        assert errors == []
        browser.close()


@pytest.mark.parametrize("width", [1280, 390])
def test_list_preview_dedup_date_column_filter_sort(tmp_path, width):
    """Vorschau entfällt, wenn der Titel sie nur wiederholt; Datumsspalte nur mobil (TT.MM.);
    Sortieren/Filter per Drop-down oben in der Liste."""
    pytest.importorskip("playwright")
    from playwright.sync_api import expect, sync_playwright

    chrome = _chrome_path()
    if chrome is None:
        pytest.skip("Chromium für Playwright nicht installiert")

    with make_client(tmp_path) as client, sync_playwright() as pw:
        ids = []
        for name in ("Zeta", "Alpha", "MitFehler"):
            ids.append(client.post("/api/meetings", files={
                "file": (f"{name}.wav", wav_bytes(tmp_path, seconds=1), "audio/wav"),
            }, data={"title": name}).json()["id"])
        client.app.state.pipeline.join()
        client.app.state.db.update_meeting(ids[2], status="error", progress=0)
        browser = pw.chromium.launch(executable_path=chrome, headless=True)
        page = browser.new_page(viewport={"width": width, "height": 900})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)

        def serve(route):
            request = route.request
            url = urlsplit(request.url)
            response = client.request(request.method, url.path + ("?" + url.query if url.query else ""),
                                      content=request.post_data_buffer)
            route.fulfill(status=response.status_code, headers=dict(response.headers), body=response.content)

        page.route("**/*", serve)
        page.goto("http://127.0.0.1/")
        items = page.locator(".m-item")
        expect(items).to_have_count(3)

        # Vorschau steht unter dem Titel, solange der Titel sie nicht wiederholt.
        expect(items.first.locator(".p")).to_have_count(1)
        target = next(m for m in client.get("/api/meetings").json() if m["id"] == ids[0])
        client.patch(f"/api/meetings/{target['id']}", json={"title": target["preview"]})
        page.reload()
        expect(page.locator(".m-item")).to_have_count(3)
        # Titel == Vorschau → keine Wiederholung mehr darunter; andere Einträge keep sie.
        expect(page.locator(f'.m-item[data-id="{ids[0]}"] .p')).to_have_count(0)
        expect(page.locator(f'.m-item[data-id="{ids[1]}"] .p')).to_have_count(1)

        # Datumsspalte rechts: nur auf dem Handy, kompakt als TT.MM.
        date = items.first.locator(".m-date")
        if width == 390:
            expect(date).to_be_visible()
            expect(date).to_have_text(re.compile(r"^\d{2}\.\d{2}\.$"))
        else:
            expect(date).to_be_hidden()

        # Filter und Sortierung über das Drop-down oben.
        page.locator("#list-options summary").click()
        page.locator("#filter-status").select_option("error")
        expect(items).to_have_count(1)
        expect(items.first).to_contain_text("MitFehler")
        page.locator("#filter-status").select_option("all")
        expect(items).to_have_count(3)
        page.locator("#sort-order").select_option("title")
        expect(items.first).to_contain_text("Alpha")
        page.locator("#sort-order").select_option("new")
        expect(items.first).to_contain_text("MitFehler")
        assert errors == []
        browser.close()
