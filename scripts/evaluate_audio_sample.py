"""WER und DER eines Koprotokoll-Ergebnisses gegen eine freigegebene Referenz messen."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from koprotokoll.evaluation import diarization_error_rate, word_error_rate  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="WER und DER einer lokalen Koprotokoll-Auswertung messen")
    parser.add_argument("reference", type=Path, help="JSON mit geprüften turns (speaker, start, end, text)")
    parser.add_argument("result", type=Path, help="JSON aus GET /api/meetings/{id}")
    parser.add_argument("--resolution", type=float, default=0.1, help="DER-Raster in Sekunden")
    parser.add_argument("--collar", type=float, default=0.25, help="Grenztoleranz in Sekunden")
    args = parser.parse_args(argv)

    try:
        reference = json.loads(args.reference.read_text(encoding="utf-8"))
        result = json.loads(args.result.read_text(encoding="utf-8"))
        ref_turns = reference if isinstance(reference, list) else reference["turns"]
        segments = result["segments"]
        diarization = result["meta"]["diarization"]
        hyp_turns = diarization["turns"]
        if diarization.get("status") != "completed" or not hyp_turns:
            raise ValueError("Keine erfolgreich abgeschlossene Sprechererkennung mit Rohzeiten vorhanden.")
        if not isinstance(ref_turns, list) or not isinstance(segments, list) or not isinstance(hyp_turns, list):
            raise ValueError("Referenz, Segmente und Sprecherzeiten müssen Listen sein.")
        der = diarization_error_rate(ref_turns, hyp_turns, args.resolution, args.collar)
        if any(not isinstance(turn["text"], str) for turn in ref_turns + segments):
            raise ValueError("Referenz und Segmente brauchen Textfelder.")
        wer = word_error_rate(ref_turns, segments)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.error(f"Auswertung nicht möglich: {exc}")
    report = {
        "wer": wer,
        "der": der,
        "rules": {
            "normalization": "Unicode-casefold; Wortfolgen nach Segmentbeginn; Satzzeichen getrennt, innere Apostrophe/Bindestriche erhalten.",
            "der": f"{args.resolution:g}-s-Raster, globale Sprecher-ID-Zuordnung, Referenzgrenzen ±{args.collar:g} s ausgeschlossen.",
            "window": "Ab 0 bis zum letzten Referenz- oder Hypothesenende, einschließlich Pausen; letzter Rasterabschnitt gekürzt.",
            "overlap": "Jede aktive Referenzstimme zählt separat; overlap nutzt dieselbe globale Zuordnung und Grenztoleranz.",
            "undefined": "WER ohne Referenzwörter und DER ohne bewertbare Referenzsprecherzeit sind null, nicht 0.",
            "error_frames": "Fehlerzeitstellen sind Rasterabschnitte mit Anzahlen verpasster, zusätzlicher und verwechselter Stimmen.",
        },
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
