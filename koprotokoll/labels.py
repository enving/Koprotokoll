"""Label-Sets für die Meeting-Klassifikation – als JSON konfigurierbar.

Standardmäßig greift das eingebaute Set (default_label_set()). Eine eigene Datei kann
über die Einstellung `labels_file` (Env: KOPROTOKOLL_LABELS_FILE) oder als
`<Datenordner>/labels.json` geladen werden. Beispiel: `labels.example.json` im Paket.
Doku: docs/einrichtung.md, Abschnitt „Eigene Klassifikation“. Die Klassifikation selbst
landet mit dem lokalen Klassifizierer (Issue #61); das Backfill über bestehende
Meetings mit Issue #62.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


class LabelSetError(ValueError):
    """Ungültiges Label-Set – Meldung ist deutsch und für API/UI gedacht."""


@dataclass
class Label:
    name: str
    description: str = ""
    examples: list[str] = field(default_factory=list)


@dataclass
class LabelSet:
    labels: list[Label]
    threshold: float = 0.6
    source: str = "standard"


def default_label_set() -> LabelSet:
    """Eingebautes Standard-Set (Kategorien aus Issue #61)."""
    return LabelSet(labels=[
        Label("privat", "Privater Kram; verlässt das Gerät nicht"),
        Label("notiz", "Reine Notiz / Gedankenmitschrift"),
        Label("diktat", "Reines Diktat / Copy-Paste (Diktiergerät-Modus)"),
        Label("aufgabe", "Konkreter Auftrag an ein Repo oder einen Agenten"),
        Label("meeting", "Echtes Meeting mit mehreren Personen"),
    ], threshold=0.6, source="standard")


def _as_str_list(value, what: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise LabelSetError(f"{what} muss eine Liste von Texten sein.")
    return [v.strip() for v in value if v.strip()]


def parse_label_set(raw, source: str = "standard") -> LabelSet:
    """JSON-Objekt validieren und in ein LabelSet umwandeln."""
    if not isinstance(raw, dict):
        raise LabelSetError("Label-Set muss ein JSON-Objekt sein.")
    labels_raw = raw.get("labels")
    if not isinstance(labels_raw, list) or not labels_raw:
        raise LabelSetError('Label-Set braucht ein nicht-leeres Feld "labels" (Liste von Objekten).')
    labels: list[Label] = []
    seen: set[str] = set()
    for i, item in enumerate(labels_raw, 1):
        if not isinstance(item, dict):
            raise LabelSetError(f"Label Nr. {i} muss ein Objekt sein.")
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            raise LabelSetError(f'Label Nr. {i} braucht ein nicht-leeres Feld "name".')
        name = name.strip()
        if name in seen:
            raise LabelSetError(f"Doppeltes Label „{name}“.")
        seen.add(name)
        description = item.get("description", "")
        if not isinstance(description, str):
            raise LabelSetError(f'„description“ von „{name}“ muss Text sein.')
        labels.append(Label(name=name, description=description.strip(),
                            examples=_as_str_list(item.get("examples", []), f'„examples“ von „{name}“')))
    threshold = raw.get("threshold", 0.6)
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)) or not 0 < float(threshold) <= 1:
        raise LabelSetError('„threshold“ muss eine Zahl größer 0 und höchstens 1 sein.')
    return LabelSet(labels=labels, threshold=float(threshold), source=source)


def load_label_set(path: Path | str) -> LabelSet:
    """Label-Set aus einer JSON-Datei laden und validieren."""
    path = Path(path).expanduser()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise LabelSetError(f"Label-Datei nicht gefunden: {path}") from None
    except json.JSONDecodeError as e:
        raise LabelSetError(f"Label-Datei {path} ist kein gültiges JSON: {e}") from None
    return parse_label_set(raw, source=str(path))


def resolve_label_set(data_dir: Path | str, labels_file: str | None = None) -> LabelSet:
    """Aktives Label-Set: expliziter Pfad (Setting/Env), sonst <Datenordner>/labels.json, sonst Standard."""
    candidates = []
    if labels_file:
        candidates.append(Path(labels_file).expanduser())
    candidates.append(Path(data_dir) / "labels.json")
    for path in candidates:
        if path.exists():
            return load_label_set(path)
    return default_label_set()
