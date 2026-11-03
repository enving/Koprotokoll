"""Aufnahme eines vorhandenen Hardware- oder Loopback-Eingangs als WAV-Datei.

Die Anwendung, die Ton erzeugt, ist egal. Das Betriebssystem muss nur einen
Eingang bereitstellen, etwa ein Mikrofon, USB-Audiointerface oder Loopback.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from koprotokoll.capture import list_devices_command, record_command  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Lokale Audioaufnahme für Koprotokoll")
    parser.add_argument("--list", action="store_true", help="vom Betriebssystem sichtbare Quellen auflisten")
    parser.add_argument("--device", help="Gerätenummer (macOS) oder Gerätename (Linux/Windows)")
    parser.add_argument("--output", type=Path, help="neue WAV-Datei für die Originalaufnahme")
    parser.add_argument("--seconds", type=float, help="Aufnahmedauer in Sekunden")
    parser.add_argument("--channels", type=int, default=2, choices=(1, 2))
    parser.add_argument("--sample-rate", type=int, default=48_000, choices=(16_000, 24_000, 44_100, 48_000))
    args = parser.parse_args(argv)

    if args.list:
        try:
            proc = subprocess.run(list_devices_command(), capture_output=True, text=True, check=False)
        except FileNotFoundError:
            print("ffmpeg wurde nicht gefunden. Bitte ffmpeg installieren.", file=sys.stderr)
            return 127
        # Einige ffmpeg-Eingabemodule geben nach der Auflistung absichtlich Exit 1 zurück.
        print(proc.stderr or proc.stdout, end="")
        return 0 if proc.stdout or proc.stderr else proc.returncode

    if args.device is None or args.output is None or args.seconds is None:
        parser.error("ohne --list sind --device, --output und --seconds erforderlich")
    try:
        command = record_command(args.device, args.output, args.seconds, args.channels, args.sample_rate)
    except ValueError as exc:
        parser.error(str(exc))
    print("Aufnahme läuft nur lokal; Abbruch mit Strg-C. Ziel:", args.output)
    try:
        return subprocess.run(command, check=False).returncode
    except FileNotFoundError:
        print("ffmpeg wurde nicht gefunden. Bitte ffmpeg installieren.", file=sys.stderr)
        return 127


if __name__ == "__main__":
    raise SystemExit(main())
