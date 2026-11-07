"""UI-Nachweis mit erfundenem Meeting, ohne Modellaufrufe."""
import argparse
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import time
import socket

import uvicorn
from playwright.sync_api import sync_playwright
from koprotokoll.app import create_app
from koprotokoll.config import Settings
from koprotokoll.summarize import heuristic_summary


class NoLLM:
    def available(self):
        return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory() as directory:
        app = create_app(Settings(data_dir=Path(directory), diarization=False), llm=NoLLM())
        db = app.state.db
        mid = db.create_meeting("Erfundenes Gartenfest", "Demo")
        segments = [dict(start=0, end=4, speaker="S1", text="Wir kaufen Getrenke für das Fest.")]
        db.replace_segments(mid, segments)
        db.save_correction(mid, ["Wir kaufen Getränke für das Fest."], {"external": False, "service": "http://localhost:11434/v1", "model": "Demoantwort", "status": "completed", "rejected": []})
        db.update_meeting(mid, status="done", summary=heuristic_summary(segments, {}))
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
        worker = threading.Thread(target=server.run)
        worker.start()
        try:
            while not server.started:
                time.sleep(.02)
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                for width in (1280, 390):
                    page.set_viewport_size({"width": width, "height": 900})
                    page.goto(f"http://127.0.0.1:{port}/#integrations")
                    page.wait_for_load_state("networkidle")
                    page.get_by_label("Optional aktivieren").check()
                    page.get_by_label("Eigenes Korrekturmodell").fill("klein")
                    page.get_by_label("Wortliste (ein Begriff je Zeile)").fill("Getränke")
                    page.get_by_role("button", name="Nachbesserung speichern", exact=True).click()
                    page.get_by_text("Gespeichert. Gilt bei der nächsten Verarbeitung.", exact=True).wait_for()
                    page.get_by_role("button", name="Nachbesserung speichern", exact=True).scroll_into_view_if_needed()
                    page.screenshot(path=str(args.output / f"einstellungen-{width}.png"))
                    page.goto(f"http://127.0.0.1:{port}/#meeting={mid}")
                    page.wait_for_load_state("networkidle")
                    page.get_by_role("tab", name="Transkript", exact=True).click()
                    page.get_by_label("Transkriptfassung").select_option("original")
                    page.locator("#transcript").get_by_text("Wir kaufen Getrenke für das Fest.", exact=True).wait_for()
                    page.get_by_label("Transkriptfassung").select_option("corrected")
                    page.locator("#transcript mark").wait_for()
                    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
                    page.screenshot(path=str(args.output / f"transkript-{width}.png"))
                assert not errors, errors
                browser.close()
                print("Desktop 1280 und Mobil 390: Einstellungen gespeichert, Fassungen umgeschaltet, Änderungen markiert; keine Seitenfehler.")
        finally:
            server.should_exit = True
            worker.join()


if __name__ == "__main__":
    main()
