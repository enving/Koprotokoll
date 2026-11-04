# Primeline-Vergleich für Issue #2

Am 04.11.2026 auf macOS, Python 3.12, Projekt-venv; Ausgangscommit `603a840`.
Geprüft wurde der zusammen mit diesem Nachweis committete Engine-Stand.
Die feste Modellrevision steht in `koprotokoll/speech.py`.

Beide echten Engines erhielten dieselbe normalisierte 16-kHz-Mono-Datei des
[synthetischen Testgesprächs](issue-31-lokale-audioaufnahme.md#synthetisches-testgespräch-auf-nutzerauftrag-03112026).
SHA-256 der normalisierten Datei:
`667b29f81ebf8a22cc5c5a518c189711accb71ca8de0edf847836c9af2307522`;
Originalaudio und Ergebnisse liegen außerhalb von Git. Referenz: 312 Wörter
aus dem vorab geschriebenen Dialog. Sie wurde nicht aus einem Modellergebnis
abgeleitet und ist keine menschlich geprüfte Hörreferenz.

| Engine (int8) | Wortfehler | WER | Laufzeit einschließlich Laden |
|---|---:|---:|---:|
| Parakeet v3, onnx-asr | 1 Einfügung, 9 Löschungen, 21 Ersetzungen | 9,94 % | 42,83 s |
| Primeline, sherpa-onnx | 0 Einfügungen, 14 Löschungen, 3 Ersetzungen | 5,45 % | 11,02 s |

Das ist ein einzelner künstlicher Dialog. Die geringere WER belegt keine
allgemeine Verbesserung bei echten Meetings. Die Laufzeiten sind kein
kontrollierter CPU-Benchmark: onnx-asr verwendete seine vorhandene
Provider-Auswahl einschließlich CoreML, Primeline die CPU mit vier Threads.
Es wurden keine neuen Modelle heruntergeladen; `HF_HUB_OFFLINE=1` war gesetzt.
Eine Netzwerksperre wurde bei diesem Vergleich nicht eingerichtet.

Der Vergleich lässt sich mit vorbereiteten Modellen so wiederholen:

```python
import time
from koprotokoll.audio import load_wav
from koprotokoll.evaluation import word_error_rate
from koprotokoll.speech import create_transcriber
from koprotokoll.config import Settings

# Für beide Läufe dieselben lokalen Dateien verwenden.
import json
from pathlib import Path
reference = json.loads(Path("sample/referenz.json").read_text())["turns"]
audio = load_wav(Path("sample/normalisiert.wav"))
for model in ("nemo-parakeet-tdt-0.6b-v3", "parakeet-primeline"):
    engine = create_transcriber(Settings(asr_model=model))
    start = time.perf_counter()
    result = engine.transcribe(audio, lambda _: None)
    seconds = time.perf_counter() - start
    words = [{"start": w.start, "text": w.text} for w in result]
    print(model, seconds, word_error_rate(reference, words))
```

`tests/test_primeline.py` prüft mit synthetischen Signalen und nachgebildetem
Recognizer: verlustfreie Aufteilung an Pausen, feste Downloadrevision,
Engine-Auswahl, begrenzte Verstärkung, Wiederholung leerer Abschnitte und
globale Wortzeiten. Diese Tests laden keine Modelle und sind kein Qualitätsmaß.

Abschlussprüfung: `.venv/bin/python -m pytest -q -ra`, **158 bestanden in
53,43 s**, keine übersprungenen Tests. `git diff --check` ohne Befund.

Das unabhängige Review fand anschließend ein zu langes Wortende am Ende eines
Primeline-Abschnitts. Die Korrektur nutzt `result.durations`. Mit den ersten acht
Sekunden desselben synthetischen Samples plus zwölf Sekunden Stille endet
„Nachbarschaft.“ nun bei 8,56 s statt 20 s. Ein Regressionstest prüft auch,
dass der spätere Sprecher dadurch nicht dem frühen Wort zugeordnet wird.
