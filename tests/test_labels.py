"""Label-Sets als JSON: laden, validieren, auflösen und per API ausliefern (Issue #63)."""

import json
from pathlib import Path

import pytest

import koprotokoll
from koprotokoll.config import Settings
from koprotokoll.labels import (LabelSetError, default_label_set, load_label_set,
                                parse_label_set, resolve_label_set)
from test_integrations import make_client


def test_default_set_has_expected_labels():
    ls = default_label_set()
    assert [l.name for l in ls.labels] == ["privat", "notiz", "diktat", "aufgabe", "meeting"]
    assert ls.source == "standard"
    assert 0 < ls.threshold <= 1


def test_shipped_example_file_is_valid():
    path = Path(koprotokoll.__file__).parent / "labels.example.json"
    ls = load_label_set(path)
    assert ls.source == str(path)
    assert len(ls.labels) >= 3
    assert all(l.name and isinstance(l.examples, list) for l in ls.labels)


@pytest.mark.parametrize("raw,match", [
    ("kein objekt", "JSON-Objekt"),
    ({}, '"labels"'),
    ({"labels": []}, '"labels"'),
    ({"labels": ["privat"]}, "Objekt"),
    ({"labels": [{"description": "x"}]}, '"name"'),
    ({"labels": [{"name": "a"}, {"name": "a"}]}, "Doppeltes Label"),
    ({"labels": [{"name": "a", "examples": "kein text"}]}, "Liste von Texten"),
    ({"labels": [{"name": "a"}], "threshold": 0}, "threshold"),
    ({"labels": [{"name": "a"}], "threshold": 1.5}, "threshold"),
    ({"labels": [{"name": "a"}], "threshold": True}, "threshold"),
])
def test_invalid_sets_raise_german_errors(raw, match):
    with pytest.raises(LabelSetError, match=match):
        parse_label_set(raw)


def test_load_custom_file_and_resolve_order(tmp_path):
    custom = tmp_path / "custom.json"
    custom.write_text(json.dumps({
        "threshold": 0.75,
        "labels": [{"name": "buero", "description": "Alles fürs Büro", "examples": ["Rechnung bezahlen"]}],
    }), encoding="utf-8")
    ls = load_label_set(custom)
    assert ls.threshold == 0.75 and ls.labels[0].name == "buero"
    assert ls.labels[0].examples == ["Rechnung bezahlen"]
    # Expliziter Pfad gewinnt vor <data_dir>/labels.json, das vor dem Standard.
    data_labels = tmp_path / "data" / "labels.json"
    data_labels.parent.mkdir()
    data_labels.write_text(json.dumps({"labels": [{"name": "datenordner"}]}), encoding="utf-8")
    assert resolve_label_set(tmp_path / "data", labels_file=str(custom)).labels[0].name == "buero"
    assert resolve_label_set(tmp_path / "data").labels[0].name == "datenordner"
    assert resolve_label_set(tmp_path / "leer").source == "standard"


def test_load_missing_file_raises_german_error(tmp_path):
    with pytest.raises(LabelSetError, match="nicht gefunden"):
        load_label_set(tmp_path / "fehlt.json")


def test_labels_file_setting_and_env(tmp_path, monkeypatch):
    custom = tmp_path / "env.json"
    custom.write_text(json.dumps({"labels": [{"name": "env"}]}), encoding="utf-8")
    assert Settings(data_dir=tmp_path, labels_file=str(custom)).labels_file == str(custom)
    monkeypatch.setenv("KOPROTOKOLL_LABELS_FILE", str(custom))
    assert Settings(data_dir=tmp_path).labels_file == str(custom)


def test_api_labels_returns_active_set(tmp_path):
    with make_client(tmp_path) as client:
        result = client.get("/api/labels")
        assert result.status_code == 200
        body = result.json()
        assert body["source"] == "standard"
        assert [l["name"] for l in body["labels"]] == ["privat", "notiz", "diktat", "aufgabe", "meeting"]
        assert body["threshold"] == 0.6


def test_api_labels_custom_file_and_invalid_file(tmp_path):
    custom = tmp_path / "labels.json"
    custom.write_text(json.dumps({"threshold": 0.9, "labels": [{"name": "eins"}, {"name": "zwei"}]}),
                      encoding="utf-8")
    with make_client(tmp_path, labels_file=str(custom)) as client:
        body = client.get("/api/labels").json()
        assert body["source"] == str(custom)
        assert body["threshold"] == 0.9 and [l["name"] for l in body["labels"]] == ["eins", "zwei"]
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"labels": [{"name": "a"}, {"name": "a"}]}), encoding="utf-8")
    with make_client(tmp_path, labels_file=str(bad)) as client:
        result = client.get("/api/labels")
        assert result.status_code == 400
        assert "Doppeltes Label" in result.json()["detail"]
