"""Plattform-Hinweise zeitlich mit erkannten Sprechersegmenten abgleichen."""

from __future__ import annotations

from collections import defaultdict
import math


def names_from_activity(segments: list[dict], activity: list[dict],
                        minimum_share: float = 0.6, minimum_seconds: float = 2.0) -> dict[str, str]:
    """Nur eindeutige Einzelstimmen zählen; veraltete Hinweise nach einer Sekunde verwerfen.

    Der Anteil bezieht sich auf die gesamte erkannte Redezeit einschließlich Lücken.
    Bei überlappenden Sprechersegmenten ist keine sichere Zuordnung möglich.
    """
    samples = []
    for item in activity:
        at, names = item.get("time"), item.get("names")
        if isinstance(at, (int, float)) and math.isfinite(at) and at >= 0 and isinstance(names, list):
            samples.append((float(at), sorted({n.strip() for n in names if isinstance(n, str) and n.strip()})))
    samples.sort(key=lambda sample: sample[0])
    # Zeitgrenzen einmal durchlaufen statt jede Probe mit jedem Segment zu vergleichen.
    events = defaultdict(list)
    for segment in segments:
        if segment["end"] > segment["start"]:
            events[segment["start"]].append((segment["speaker"], 1))
            events[segment["end"]].append((segment["speaker"], -1))
    totals, matches, active = defaultdict(float), defaultdict(lambda: defaultdict(float)), defaultdict(int)
    previous, sample_index = None, 0
    for boundary in sorted(events):
        if previous is not None:
            speakers = [speaker for speaker, count in active.items() if count > 0]
            for speaker in speakers:
                totals[speaker] += boundary - previous
            if len(speakers) == 1:
                while sample_index < len(samples) and samples[sample_index][0] + 1 <= previous:
                    sample_index += 1
                index = sample_index
                while index < len(samples) and samples[index][0] < boundary:
                    at, names = samples[index]
                    until = min(at + 1.0, samples[index + 1][0] if index + 1 < len(samples) else at + 0.5)
                    overlap = min(boundary, until) - max(previous, at)
                    if len(names) == 1 and overlap > 0:
                        matches[speakers[0]][names[0]] += overlap
                    index += 1
        for speaker, delta in events[boundary]:
            active[speaker] += delta
        previous = boundary
    result = {}
    for speaker, counts in matches.items():
        name, duration = max(counts.items(), key=lambda item: item[1])
        if duration >= minimum_seconds and duration / totals[speaker] >= minimum_share:
            result[speaker] = name
    return result
