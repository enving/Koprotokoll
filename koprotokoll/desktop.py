"""Native Mac-Menüleiste für Alt-Doppeltipp; verwendet die vorhandene Aufnahme-API."""

from __future__ import annotations

import json
import os
import platform
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

from .config import Settings


def build_app(settings: Settings, port: int = 8000, *, configure_launcher: bool = True) -> Path:
    """Die lokale App aus den mitgelieferten Swift-Quellen bauen, ohne Zusatzpakete."""
    if sys.platform != "darwin":
        raise RuntimeError("Die Menüleistensteuerung ist derzeit nur für macOS verfügbar.")
    compiler = shutil.which("swiftc")
    if not compiler:
        raise RuntimeError("Die Apple-Entwicklerwerkzeuge fehlen. Einmalig `xcode-select --install` ausführen.")
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg wurde nicht gefunden; siehe Installationsanleitung.")
    sources = [Path(__file__).with_name(name) for name in
               ("macos_hotkey.swift", "capture_macos.swift", "desktop_macos.swift")]
    app = settings.data_dir / "desktop" / "Koprotokoll.app"
    contents = app / "Contents"
    binary = contents / "MacOS" / "Koprotokoll"
    resources = contents / "Resources"
    binary.parent.mkdir(parents=True, exist_ok=True)
    resources.mkdir(parents=True, exist_ok=True)
    changed = not binary.exists() or any(p.stat().st_mtime > binary.stat().st_mtime for p in sources)
    if changed:
        temporary = binary.with_suffix(".neu")
        result = subprocess.run(
            [compiler, *map(str, sources), "-target", f"{platform.machine()}-apple-macosx12.0",
             "-o", str(temporary)], capture_output=True, text=True,
        )
        if result.returncode:
            raise RuntimeError("Die Mac-Steuerung ließ sich nicht bauen:\n" + result.stderr[-4000:])
        if binary.exists():
            shutil.copy2(binary, app.parent / "Koprotokoll.vorher")
        os.replace(temporary, binary)
    info = {
        "CFBundleIdentifier": "de.digitalalchemisten.koprotokoll.desktop",
        "CFBundleName": "Koprotokoll", "CFBundleExecutable": "Koprotokoll",
        "CFBundlePackageType": "APPL", "CFBundleVersion": "1",
        "LSUIElement": True, "LSMinimumSystemVersion": "12.0",
        "NSMicrophoneUsageDescription": "Koprotokoll nimmt nach deinem Start die gewählte Audioquelle auf.",
        "NSAudioCaptureUsageDescription": "Koprotokoll nimmt für dein Protokoll den Systemton auf. Es werden keine Bildschirminhalte aufgenommen.",
    }
    info_data = plistlib.dumps(info)
    config_data = json.dumps({
        "project": str(Path(__file__).resolve().parents[1]),
        "python": sys.executable,
        "data": str(settings.data_dir.resolve()),
        "port": port,
        "path": os.environ.get("PATH", "/usr/bin:/bin:/usr/sbin:/sbin"),
    }, ensure_ascii=False).encode()
    targets = [(contents / "Info.plist", info_data)]
    if configure_launcher:
        targets.append((resources / "launcher.json", config_data))
    for target, content in targets:
        if not target.exists() or target.read_bytes() != content:
            target.write_bytes(content)
            changed = True
    if changed:
        subprocess.run(["codesign", "--force", "--sign", "-", str(app)], check=True, capture_output=True)
    return app


def link_app(app: Path, applications_dir: Path | None = None) -> Path | None:
    """Einen Finder-Einstieg anlegen; vorhandene fremde Apps niemals ersetzen."""
    folder = applications_dir or Path.home() / "Applications"
    folder.mkdir(parents=True, exist_ok=True)
    launcher = folder / "Koprotokoll.app"
    if launcher.is_symlink() and launcher.resolve() == app.resolve():
        return launcher
    if launcher.exists() or launcher.is_symlink():
        return None
    launcher.symlink_to(app.resolve(), target_is_directory=True)
    return launcher


def main(port: int = 8000) -> None:
    try:
        app = build_app(Settings(), port)
        launcher = link_app(app)
        subprocess.run(["open", str(app)], check=True)
    except (RuntimeError, subprocess.CalledProcessError) as exc:
        raise SystemExit(str(exc)) from exc
    print("Koprotokoll geöffnet. Audioquelle und Tastenkürzel im Einstellungsfenster ändern.")
    print(f"App zum erneuten Öffnen: {launcher or app}")
