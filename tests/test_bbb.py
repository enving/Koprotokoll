"""BBB-Link-Erkennung und Browserablauf gegen eine lokale Nachbildung."""

from pathlib import Path
import time

import pytest

from koprotokoll.bot.platforms import BigBlueButton, BotError, detect_platform, find_meeting_url
from test_integrations import _chrome_path, make_client


@pytest.mark.parametrize("url,hosts,expected", [
    ("https://demo.bigbluebutton.org/b/abc-def", (), "bbb"),
    ("https://bbb.example/rooms/room-id/join", ("bbb.example",), "bbb"),
    ("https://bbb.example/b/abc-def?foo=bar", ("bbb.example",), "bbb"),
    ("https://bbb.example.evil/b/abc-def", ("bbb.example",), None),
    ("https://unbekannt.example/b/abc-def", (), None),
    ("https://bbb.example/rooms/id/join-not", ("bbb.example",), None),
])
def test_bbb_links_are_limited_to_known_hosts(url, hosts, expected):
    assert detect_platform(url, bbb_hosts=hosts) == expected


def test_bbb_calendar_and_bot_api_use_configured_hosts(tmp_path):
    url = "https://bbb.example/rooms/id/join"
    assert find_meeting_url({"description": "Konferenz: " + url}, bbb_hosts=("bbb.example",)) == (url, "bbb")
    with make_client(tmp_path, bbb_hosts=("bbb.example",)) as client:
        response = client.post("/api/bots", json={"meeting_url": url, "join_at": time.time() + 3600})
        assert response.status_code == 201 and response.json()["platform"] == "bbb"


@pytest.mark.parametrize("mode", ["normal", "de-consent", "en-consent", "denied", "waiting", "no-listen"])
def test_bbb_join_listen_only_and_safe_leave(mode):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=_chrome_path(), headless=True)
        page = browser.new_page()
        page.route("**/*", lambda route: route.fulfill(path=str(Path(__file__).parent / "fixtures/bbb_stub.html")))
        adapter = BigBlueButton()
        statuses = []
        url = "https://bbb.example/rooms/test/join?mode=" + mode
        if mode in ("normal", "de-consent", "en-consent"):
            adapter.join(page, url, "Koprotokoll Test", 5, statuses.append)
            assert page.evaluate("window.botName") == "Koprotokoll Test"
            assert page.evaluate("window.usedListenOnly && !window.usedMicrophone && !window.endedForOthers")
            assert not adapter.ended(page)
            adapter.leave(page)
            assert adapter.ended(page)
            assert not page.evaluate("window.endedForOthers")
        else:
            with pytest.raises(BotError):
                adapter.join(page, url, "Koprotokoll Test", 2, statuses.append)
            assert not page.evaluate("window.usedMicrophone")
        browser.close()


@pytest.mark.parametrize("message", ["Session ended.", "Sitzung beendet.",
    "This session has ended, or the link used to join it is no longer valid."])
def test_bbb_official_end_messages(message):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=_chrome_path(), headless=True)
        page = browser.new_page()
        page.set_content("<main></main>")
        page.locator("main").evaluate("(el, text) => el.textContent = text", message)
        assert BigBlueButton().ended(page)
        browser.close()
