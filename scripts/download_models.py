"""Alle Modelle vorab herunterladen, damit der Koprotokoll danach komplett offline läuft.

    python scripts/download_models.py
"""

from __future__ import annotations

import sys
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from koprotokoll.config import Settings  # noqa: E402
from koprotokoll.speech import create_transcriber, ensure_diarization_models, install_transcriber  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--language", choices=("auto", "de", "en"), help="Modus gezielt vorbereiten")
    parser.add_argument("--all-asr", action="store_true", help="auto/en und de vorbereiten")
    parser.add_argument("--asr-only", action="store_true", help="Sprechererkennung nicht herunterladen")
    args = parser.parse_args()
    s = Settings()
    s.ensure_dirs()
    installed = set()
    for mode in (("auto", "de") if args.all_asr else (args.language or s.asr_language,)):
        transcriber = create_transcriber(s, mode)
        key = (transcriber.asr_info["model"], transcriber.asr_info["model_path"])
        if key in installed:
            continue
        print(f"Parakeet: {transcriber.asr_info}", flush=True)
        install_transcriber(transcriber)
        installed.add(key)
    if not args.asr_only:
        print("Sprechererkennung …")
        seg, emb = ensure_diarization_models(s.models_dir)
        print(f"  {seg}\n  {emb}")
    print("Fertig.")


if __name__ == "__main__":
    main()
