"""Mac-Tastenkürzel und lokales App-Paket ohne echte Aufnahme prüfen."""

import json
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from koprotokoll.config import Settings
from koprotokoll.desktop import build_app, link_app


@pytest.mark.skipif(sys.platform != "darwin" or shutil.which("swiftc") is None,
                    reason="Die native Swift-Prüfung benötigt macOS und Apple-Entwicklerwerkzeuge.")
def test_alt_double_tap_rejects_combinations_and_holds(tmp_path):
    source = Path(__file__).resolve().parents[1]
    binary = tmp_path / "hotkey-test"
    subprocess.run(["swiftc", str(source / "koprotokoll/macos_hotkey.swift"),
                    str(source / "tests/fixtures/hotkey_macos_test.swift"), "-o", str(binary)], check=True)
    result = subprocess.run([str(binary)], capture_output=True, text=True, check=True)
    assert "14 Tastenkürzel- und Einstellungsfälle bestanden" in result.stdout


def test_app_bundle_points_to_local_backend_without_extra_python_packages(tmp_path, monkeypatch):
    import koprotokoll.desktop as desktop

    calls = []

    def run(command, **kwargs):
        calls.append(command)
        if "-o" in command:
            Path(command[command.index("-o") + 1]).write_bytes(b"Testprogramm")
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(desktop.sys, "platform", "darwin")
    monkeypatch.setattr(desktop.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(desktop.subprocess, "run", run)
    app = build_app(Settings(data_dir=tmp_path), port=8123)
    info = plistlib.loads((app / "Contents/Info.plist").read_bytes())
    config = json.loads((app / "Contents/Resources/launcher.json").read_text())
    assert info["LSUIElement"] is True
    assert info["CFBundleExecutable"] == "Koprotokoll"
    assert "NSAudioCaptureUsageDescription" in info
    assert "NSMicrophoneUsageDescription" in info
    assert config["port"] == 8123
    assert config["data"] == str(tmp_path)
    assert config["python"] == sys.executable
    assert any(command[0] == "codesign" for command in calls)
    assert (app / "Contents/MacOS/Koprotokoll").is_file()
    before = (app / "Contents/Resources/launcher.json").read_bytes()
    build_app(Settings(data_dir=tmp_path), configure_launcher=False)
    assert (app / "Contents/Resources/launcher.json").read_bytes() == before


def test_desktop_rejects_unsupported_platform_before_writing(tmp_path, monkeypatch):
    import koprotokoll.desktop as desktop

    monkeypatch.setattr(desktop.sys, "platform", "linux")
    with pytest.raises(RuntimeError, match="nur für macOS"):
        build_app(Settings(data_dir=tmp_path))
    assert not list(tmp_path.iterdir())


def test_finder_launcher_reopens_the_same_generated_app(tmp_path):
    app = tmp_path / "data/desktop/Koprotokoll.app"
    app.mkdir(parents=True)
    applications = tmp_path / "Applications"
    launcher = link_app(app, applications)
    assert launcher.is_symlink()
    assert launcher.resolve() == app
    assert link_app(app, applications) == launcher


def test_finder_launcher_preserves_an_existing_app(tmp_path):
    applications = tmp_path / "Applications"
    installed = applications / "Koprotokoll.app"
    installed.mkdir(parents=True)
    original = installed / "vorhanden.txt"
    original.write_text("Vorhandene Installation")
    assert link_app(tmp_path / "anderes/Koprotokoll.app", applications) is None
    assert original.read_text() == "Vorhandene Installation"
