from pathlib import Path

import pytest

from koprotokoll.capture import backend, list_devices_command, parse_devices, record_command


@pytest.mark.parametrize(("platform", "expected"), [
    ("darwin", "avfoundation"), ("linux", "pulse"), ("win32", "dshow"),
])
def test_backend_for_supported_desktop_systems(platform, expected):
    assert backend(platform) == expected


def test_unknown_platform_is_rejected():
    with pytest.raises(ValueError, match="Nicht unterstütztes Betriebssystem"):
        backend("freebsd")


def test_list_devices_uses_os_backend():
    assert list_devices_command("darwin")[-3:] == ["true", "-i", ""]
    assert list_devices_command("linux")[-3:] == ["pulse", "-i", "dummy"]
    assert list_devices_command("win32")[-3:] == ["dshow", "-i", "dummy"]


def test_record_command_is_lossless_and_keeps_device_as_one_argument():
    command = record_command("USB Mikrofon; keine Shell", Path("original.wav"), 120, platform="darwin")
    assert command[command.index("-i") + 1] == ":USB Mikrofon; keine Shell"
    assert command[-4:] == ["-ar", "48000", "-c:a", "pcm_s16le", "original.wav"][-4:]
    assert "-n" in command


def test_record_command_without_duration_runs_until_explicit_stop():
    command = record_command("Mikrofon", Path("original.wav"), platform="darwin")
    assert "-t" not in command
    assert command[-1] == "original.wav"


def test_parse_macos_audio_devices_without_video_devices():
    details = """[AVFoundation] video devices:
[AVFoundation] [0] Bildschirm
[AVFoundation] audio devices:
[AVFoundation] [0] USB-Mikrofon
[AVFoundation] [1] Loopback
"""
    assert parse_devices(details, "darwin") == [
        {"id": "0", "name": "USB-Mikrofon"}, {"id": "1", "name": "Loopback"}
    ]


@pytest.mark.parametrize("kwargs", [
    {"device": "", "output": Path("a.wav"), "seconds": 1},
    {"device": "1", "output": Path("a.mp3"), "seconds": 1},
    {"device": "1", "output": Path("a.wav"), "seconds": 0},
    {"device": "1", "output": Path("kein-ordner/a.wav"), "seconds": 1},
])
def test_record_command_rejects_ambiguous_or_lossy_input(kwargs):
    with pytest.raises(ValueError):
        record_command(platform="darwin", **kwargs)


def test_validate_stem_durations_rejects_incomplete_microphone_track(monkeypatch, tmp_path):
    from types import SimpleNamespace
    import koprotokoll.capture as capture

    monkeypatch.setattr(capture.shutil, "which", lambda _: "/usr/bin/ffprobe")
    durations = iter(("1.14\n", "0.10\n"))
    monkeypatch.setattr(capture.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(
        stdout=next(durations), returncode=0,
    ))

    with pytest.raises(RuntimeError, match="unterschiedlich lang"):
        capture._validate_stem_durations(tmp_path / "system.wav", tmp_path / "mikrofon.wav")


def test_validate_stem_durations_accepts_aligned_tracks(monkeypatch, tmp_path):
    from types import SimpleNamespace
    import koprotokoll.capture as capture

    monkeypatch.setattr(capture.shutil, "which", lambda _: "/usr/bin/ffprobe")
    durations = iter(("30.00\n", "29.92\n"))
    monkeypatch.setattr(capture.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(
        stdout=next(durations), returncode=0,
    ))

    assert capture._validate_stem_durations(tmp_path / "system.wav", tmp_path / "mikrofon.wav") == (30.0, 29.92)
