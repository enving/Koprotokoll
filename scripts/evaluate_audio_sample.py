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

    reference = json.loads(args.reference.read_text())
    result = json.loads(args.result.read_text())
    ref_turns = reference.get("turns", reference)
    segments = result.get("segments", result if isinstance(result, list) else [])
    diarization = result.get("meta", {}).get("diarization", {})
    hyp_turns = diarization.get("turns", [])
    if not hyp_turns:
        parser.error("Das Ergebnis enthält keine Rohzeiten der Sprechererkennung (meta.diarization.turns).")
    report = {
        "wer": word_error_rate(ref_turns, segments),
        "der": diarization_error_rate(ref_turns, hyp_turns, args.resolution, args.collar),
        "rules": {
            "normalization": "Kleinschreibung; Satzzeichen und Mehrfachleerzeichen ignoriert.",
            "der": "100-ms-Raster, globale Sprecher-ID-Zuordnung, Referenzgrenzen ±250 ms ausgeschlossen.",
            "overlap": "Jede gleichzeitig aktive Referenzstimme zählt separat.",
        },
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
