"""Transkript- und Sprecherzeiten gegen eine manuell geprüfte Referenz messen."""

from __future__ import annotations

import itertools
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
        "wer": distance / len(ref) if ref else (0.0 if not hyp else 1.0),
    }


def _active(turns: Iterable[dict], point: float) -> set[str]:
    return {str(turn["speaker"]) for turn in turns if float(turn["start"]) <= point < float(turn["end"])}


def _boundaries(turns: Iterable[dict]) -> set[float]:
    return {float(turn[key]) for turn in turns for key in ("start", "end")}


def _mapped_labels(active: set[str], mapping: dict[str, str | None]) -> set[str]:
    # Nicht zuordenbare Hypothesen bleiben sichtbar und zählen als falscher Alarm.
    return {mapping[label] if mapping.get(label) is not None else f"__unmapped__:{label}" for label in active}


def _der_counts(reference: list[dict], hypothesis: list[dict], mapping: dict[str, str | None],
                resolution: float, collar: float) -> tuple[int, int, int, int]:
    end = max([float(turn["end"]) for turn in reference + hypothesis], default=0.0)
    boundaries = _boundaries(reference)
    total = missed = false_alarm = confusion = 0
    steps = int(end / resolution) + 1
    for index in range(steps):
        point = (index + 0.5) * resolution
        if any(abs(point - boundary) < collar for boundary in boundaries):
            continue
        expected = _active(reference, point)
        if not expected:
            continue
        observed = _mapped_labels(_active(hypothesis, point), mapping)
        correct = len(expected & observed)
        total += len(expected)
        missed += max(0, len(expected) - len(observed))
        false_alarm += max(0, len(observed) - len(expected))
        confusion += min(len(expected), len(observed)) - correct
    return total, missed, false_alarm, confusion


def diarization_error_rate(reference: Iterable[dict], hypothesis: Iterable[dict],
                            resolution: float = 0.1, collar: float = 0.25) -> dict:
    """Frame-basierte DER mit globaler Zuordnung beliebiger Sprecher-IDs.

    Überlappende Referenzturns zählen pro aktivem Sprecher. Ein Kollarkorridor
    verwirft Frames an Referenzgrenzen, damit geringe Versätze nicht doppelt
    bestraft werden. Das Verfahren ist bewusst dependency-frei und daher keine
    Behauptung, exakt dieselben Werte wie pyannote.metrics zu liefern.
    """
    if resolution <= 0 or collar < 0:
        raise ValueError("resolution muss positiv und collar nicht negativ sein")
    ref, hyp = list(reference), list(hypothesis)
    ref_labels = sorted({str(turn["speaker"]) for turn in ref})
    hyp_labels = sorted({str(turn["speaker"]) for turn in hyp})
    if not ref_labels:
        return {"reference_speaker_seconds": 0.0, "missed_seconds": 0.0,
                "false_alarm_seconds": 0.0, "confusion_seconds": 0.0, "der": 0.0,
                "mapping": {}}

    candidates = []
    for values in itertools.product([*ref_labels, None], repeat=len(hyp_labels)):
        assigned = [value for value in values if value is not None]
        if len(set(assigned)) == len(assigned):
            candidates.append(dict(zip(hyp_labels, values)))
    best_mapping = min(candidates or [{}], key=lambda mapping: sum(_der_counts(
        ref, hyp, mapping, resolution, collar)[1:]))
    total, missed, false_alarm, confusion = _der_counts(ref, hyp, best_mapping, resolution, collar)
    return {
        "reference_speaker_seconds": round(total * resolution, 3),
        "missed_seconds": round(missed * resolution, 3),
        "false_alarm_seconds": round(false_alarm * resolution, 3),
        "confusion_seconds": round(confusion * resolution, 3),
        "der": round((missed + false_alarm + confusion) / total, 6) if total else 0.0,
        "mapping": best_mapping,
    }
