"""Transkript- und Sprecherzeiten gegen eine manuell geprüfte Referenz messen."""

from __future__ import annotations

import itertools
import math
import re
from collections.abc import Iterable


def words(turns: Iterable[dict]) -> list[str]:
    """Kleinschreibung, Satzzeichen und Mehrfachleerzeichen einheitlich behandeln."""
    text = " ".join(str(turn.get("text", "")) for turn in sorted(turns, key=lambda t: t["start"]))
    return re.findall(r"[^\W_]+(?:['’-][^\W_]+)?|\d+", text.casefold(), flags=re.UNICODE)


def word_error_rate(reference: Iterable[dict], hypothesis: Iterable[dict]) -> dict:
    """WER mit Wort-Levenshtein-Distanz; Einfügen/Löschen/Ersetzen getrennt."""
    ref, hyp = words(reference), words(hypothesis)
    rows = [[(i, 0, 0, 0) for _ in range(len(hyp) + 1)] for i in range(len(ref) + 1)]
    for i in range(1, len(ref) + 1):
        rows[i][0] = (i, 0, i, 0)
    for j in range(1, len(hyp) + 1):
        rows[0][j] = (j, j, 0, 0)
    for i, reference_word in enumerate(ref, 1):
        for j, hypothesis_word in enumerate(hyp, 1):
            if reference_word == hypothesis_word:
                rows[i][j] = rows[i - 1][j - 1]
                continue
            substitute = rows[i - 1][j - 1]
            insert = rows[i][j - 1]
            delete = rows[i - 1][j]
            options = [
                (substitute[0] + 1, substitute[1], substitute[2], substitute[3] + 1),
                (insert[0] + 1, insert[1] + 1, insert[2], insert[3]),
                (delete[0] + 1, delete[1], delete[2] + 1, delete[3]),
            ]
            rows[i][j] = min(options, key=lambda value: value[0])
    distance, insertions, deletions, substitutions = rows[-1][-1]
    return {
        "reference_words": len(ref), "hypothesis_words": len(hyp),
        "insertions": insertions, "deletions": deletions, "substitutions": substitutions,
        "wer": distance / len(ref) if ref else None,
    }


def _active(turns: Iterable[dict], point: float) -> set[str]:
    return {str(turn["speaker"]) for turn in turns if float(turn["start"]) <= point < float(turn["end"])}


def _boundaries(turns: Iterable[dict]) -> set[float]:
    return {float(turn[key]) for turn in turns for key in ("start", "end")}


def _validate_turns(turns: list[dict]) -> None:
    for turn in turns:
        try:
            start, end = float(turn["start"]), float(turn["end"])
            speaker = turn["speaker"]
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Sprecherzeiten brauchen speaker, start und end.") from exc
        if (speaker is None or not math.isfinite(start) or not math.isfinite(end)
                or start < 0 or end <= start):
            raise ValueError("Sprecherzeiten brauchen ein Label und end > start >= 0 mit endlichen Zeiten.")


def _frame_counts(expected: set[str], observed: set[str], mapping: dict[str, str | None]) -> tuple:
    # Nur zugeordnete Labels vergleichen; unbekannte Labels können so niemals
    # versehentlich mit einer frei benannten Referenzstimme übereinstimmen.
    correct = sum(mapping.get(label) in expected for label in observed)
    return (len(expected), max(0, len(expected) - len(observed)),
            max(0, len(observed) - len(expected)), min(len(expected), len(observed)) - correct)


def _score(frames: list[tuple], mapping: dict[str, str | None]) -> dict:
    totals = [0.0] * 4
    errors = []
    scored_seconds = 0.0
    for start, end, expected, observed in frames:
        counts = _frame_counts(expected, observed, mapping)
        duration = end - start
        scored_seconds += duration
        totals = [total + count * duration for total, count in zip(totals, counts)]
        if sum(counts[1:]):
            errors.append({"start": round(start, 6), "end": round(end, 6),
                           "missed": counts[1], "false_alarm": counts[2], "confusion": counts[3]})
    total, missed, false_alarm, confusion = totals
    return {
        "scored_seconds": round(scored_seconds, 6),
        "reference_speaker_seconds": round(total, 6),
        "missed_seconds": round(missed, 6),
        "false_alarm_seconds": round(false_alarm, 6),
        "confusion_seconds": round(confusion, 6),
        "der": round((missed + false_alarm + confusion) / total, 6) if total else None,
        "error_frames": errors,
    }


def diarization_error_rate(reference: Iterable[dict], hypothesis: Iterable[dict],
                            resolution: float = 0.1, collar: float = 0.25) -> dict:
    """Frame-basierte DER mit globaler Zuordnung beliebiger Sprecher-IDs.

    Überlappende Referenzturns zählen pro aktivem Sprecher. Ein Kollarkorridor
    verwirft Frames an Referenzgrenzen, damit geringe Versätze nicht doppelt
    bestraft werden. Das Verfahren ist bewusst dependency-frei und daher keine
    Behauptung, exakt dieselben Werte wie pyannote.metrics zu liefern.
    """
    if not math.isfinite(resolution) or not math.isfinite(collar) or resolution <= 0 or collar < 0:
        raise ValueError("resolution muss endlich und positiv, collar endlich und nicht negativ sein")
    ref, hyp = list(reference), list(hypothesis)
    _validate_turns(ref)
    _validate_turns(hyp)
    ref_labels = sorted({str(turn["speaker"]) for turn in ref})
    hyp_labels = sorted({str(turn["speaker"]) for turn in hyp})
    end = max((float(turn["end"]) for turn in ref + hyp), default=0.0)
    boundaries = _boundaries(ref)
    frames = []
    for index in range(math.ceil(end / resolution)):
        start, stop = index * resolution, min((index + 1) * resolution, end)
        point = (start + stop) / 2
        if any(abs(point - boundary) < collar for boundary in boundaries):
            continue
        frames.append((start, stop, _active(ref, point), _active(hyp, point)))

    # Die Zuordnung maximiert die korrekt erkannten Sprechersekunden. Pausen
    # bleiben im Bewertungsfenster und zählen bei erkannter Sprache als Fehlalarm.
    weights = {(h, r): sum(stop - start for start, stop, expected, observed in frames
                           if h in observed and r in expected)
               for h in hyp_labels for r in ref_labels}
    best_mapping, best_weight = {}, -1.0
    # Nur vollständige Zuordnungen der kleineren Labelmenge sind nötig:
    # eine zusätzliche Paarung kann bei nichtnegativen Gewichten nie schaden.
    small, large = (hyp_labels, ref_labels) if len(hyp_labels) <= len(ref_labels) else (ref_labels, hyp_labels)
    for assigned in itertools.permutations(large, len(small)):
        pairs = zip(small, assigned) if len(hyp_labels) <= len(ref_labels) else zip(assigned, small)
        mapping = dict.fromkeys(hyp_labels)
        mapping.update(pairs)
        weight = sum(weights[(h, r)] for h, r in mapping.items() if r is not None)
        if weight > best_weight:
            best_mapping, best_weight = mapping, weight
    report = _score(frames, best_mapping)
    report.update({"mapping": best_mapping, "resolution_seconds": resolution,
                   "collar_seconds": collar, "evaluation_end_seconds": end,
                   "overlap": _score([frame for frame in frames if len(frame[2]) > 1], best_mapping)})
    return report
