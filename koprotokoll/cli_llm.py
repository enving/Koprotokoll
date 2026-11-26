"""Sprachmodell aus der Coding CLI übernehmen (z. B. Mistral Vibe).

Liest ~/.vibe/config.toml (aktives Modell, Anbieter „mistral“ → https://api.mistral.ai/v1)
und holt den API-Schlüssel aus der Umgebung (MISTRAL_API_KEY) oder – nur macOS – aus dem
Login-Schlüsselbund der Vibe CLI. Der Schlüssel verlässt den Rechner nicht und wird weder
über die API zurückgegeben noch protokolliert.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tomllib
from pathlib import Path

MISTRAL_API_URL = "https://api.mistral.ai/v1"
VIBE_CONFIG_ENV = "KOPROTOKOLL_VIBE_CONFIG"
KEYCHAIN_SERVICE = "ai.mistral.vibe"
KEYCHAIN_ACCOUNT = "MISTRAL_API_KEY"


def vibe_config_path() -> Path:
    return Path(os.environ.get(VIBE_CONFIG_ENV) or (Path.home() / ".vibe" / "config.toml"))


def read_vibe_config() -> dict | None:
    """Aktives Modell und Modellliste der Vibe CLI – ohne Zugangsdaten."""
    path = vibe_config_path()
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return None
    models = []
    for m in data.get("models", []):
        if isinstance(m, dict) and m.get("name"):
            models.append({"name": str(m["name"]), "provider": str(m.get("provider") or ""),
                           "alias": m.get("alias"), "display_name": m.get("display_name")})
    active = data.get("active_model")
    return {"path": str(path), "active_model": str(active) if active else None, "models": models}


def _keychain_key() -> str | None:
    """macOS: Schlüssel der Vibe CLI aus dem Login-Schlüsselbund lesen (nie ausgegeben)."""
    if sys.platform != "darwin":
        return None
    try:
        proc = subprocess.run(
            ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", KEYCHAIN_ACCOUNT, "-w"],
            capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.stdout.strip() or None


def find_api_key() -> tuple[str | None, str]:
    """(key, quelle) – Umgebung zuerst, dann macOS-Schlüsselbund."""
    env_key = os.environ.get("MISTRAL_API_KEY", "").strip()
    if env_key:
        return env_key, "Umgebungsvariable MISTRAL_API_KEY"
    key = _keychain_key()
    if key:
        return key, "macOS-Schlüsselbund (ai.mistral.vibe)"
    return None, ""


def valid_api_key(key: str) -> bool:
    return bool(key) and all(32 <= ord(c) <= 126 for c in key)


def cli_sources() -> list[dict]:
    """Gefundene CLI-Quellen; enthält niemals Schlüsselmaterial."""
    vibe = read_vibe_config()
    if vibe is None:
        return []
    mistral_models = [m["name"] for m in vibe["models"] if m["provider"] == "mistral"]
    model = vibe["active_model"] or (mistral_models[0] if mistral_models else None)
    if not model:
        return []
    key, key_source = find_api_key()
    return [{
        "source": "vibe",
        "label": "Mistral Vibe CLI",
        "config": vibe["path"],
        "provider": "mistral",
        "url": MISTRAL_API_URL,
        "model": model,
        "models": mistral_models or [model],
        "key_available": key is not None,
        "key_source": key_source if key else "",
    }]


def import_cli_source(source: str) -> dict:
    """Konfiguration (provider, url, model, api_key) einer CLI-Quelle zum Speichern."""
    sources = {s["source"]: s for s in cli_sources()}
    if source not in sources:
        raise ValueError("Unbekannte CLI-Quelle.")
    src = sources[source]
    key, _ = find_api_key()
    return {"provider": src["provider"], "url": src["url"], "model": src["model"], "api_key": key}
