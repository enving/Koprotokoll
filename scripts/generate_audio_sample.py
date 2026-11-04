"""Synthetisches Zwei-Stimmen-Testgespräch mit macOS-Stimmen lokal erzeugen.

Die Kompositionsreferenz entsteht vor der Erkennung. Sie ist keine menschlich
geprüfte Referenz eines realen Gesprächs. Audiodateien bleiben außerhalb von Git.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
from pathlib import Path

import numpy as np
import soundfile as sf

RATE = 48_000
VOICES = {"A": "Anna", "B": "Eddy (Deutsch (Deutschland))"}
# Abstand zum Ende des vorherigen Abschnitts; negativ bedeutet Überlappung.
DIALOGUE = [
    ("A", 1.0, "Willkommen zu unserem Testgespräch. Wir sind zwei künstliche Stimmen und planen ein fiktives Nachbarschaftsfest. Es kommen keine echten Personen oder vertraulichen Informationen vor. Hast du den Ablauf schon vorbereitet?"),
    ("B", 0.5, "Ja, ich habe einen ersten Vorschlag. Wir treffen uns am Samstag um zehn Uhr im Gemeinschaftsgarten. Zuerst stellen wir die Tische auf, danach kümmern wir uns um Getränke und die kleine Bühne."),
    ("A", 0.4, "Das klingt gut. Für den Aufbau brauchen wir allerdings genügend Zeit. Ich würde die Helfer schon eine halbe Stunde früher einladen. Außerdem fehlt noch eine Lösung, falls es regnet."),
    ("B", 0.3, "Stimmt."),
    ("A", 0.5, "Bei schlechtem Wetter könnten wir in den großen Raum neben dem Garten wechseln. Ich werde morgen nachfragen, ob er frei ist, und die Antwort bis Donnerstag in unsere Liste eintragen."),
    ("B", 1.8, "Eine andere Frage: Wie viele Gäste erwarten wir? Für zwanzig Personen reichen die vorhandenen Tassen. Wenn es deutlich mehr werden, sollten alle einen eigenen Becher mitbringen. Das spart auch Müll."),
    ("A", 0.4, "Im Moment rechnen wir mit dreißig Gästen. Auf der Einladung steht bereits, dass jeder einen Becher mitbringen soll. Für Kinder stellen wir Wasser und Apfelsaft bereit. Kaffee können wir vor Ort kochen."),
    ("B", 0.4, "Dann übernehme ich die Getränke. Ich kaufe Wasser und Saft ein und bringe zusätzlich eine Kanne Tee mit. Für die Musik brauchen wir noch eine kleine Box und ein langes Kabel."),
    ("A", -1.3, "Das Kabel habe ich schon!"),
    ("B", 0.5, "Prima, dann fehlt nur die Box. Ich frage im Haus nach. Die Musik soll leise bleiben, damit wir uns unterhalten können und niemand in der Nachbarschaft gestört wird."),
    ("A", 0.5, "Einverstanden."),
    ("B", 2.0, "Halten wir die Aufgaben fest. Du klärst den Ausweichraum und bringst das Kabel mit. Ich besorge die Getränke und frage nach der Box. Die Tische bauen wir am Samstag gemeinsam auf."),
    ("A", 0.5, "Genau. Wir treffen uns um halb zehn, die Gäste kommen um zehn. Am Donnerstag prüfen wir noch einmal die Wettervorhersage. Damit sind wir fertig. Danke für das Gespräch und bis Samstag."),
    ("B", 0.4, "Bis Samstag!"),
]


def compose(clips: list[np.ndarray], sample_rate: int = RATE) -> tuple[np.ndarray, dict, dict]:
    """Clips anhand des Dialogplans setzen; Stimmen getrennt und als Mono-Mix liefern."""
    if len(clips) != len(DIALOGUE):
        raise ValueError("Zu jedem Dialogabschnitt muss genau ein Clip vorliegen.")
    placements, turns = [], []
    end = 0
    for (speaker, gap, text), clip in zip(DIALOGUE, clips):
        start = end + round(gap * sample_rate)
        if start < 0 or not len(clip):
            raise ValueError("Leerer Clip oder negativer Startzeitpunkt.")
        end = start + len(clip)
        placements.append((speaker, start, end, clip))
        turns.append({"speaker": speaker, "start": start / sample_rate,
                      "end": end / sample_rate, "text": text})
    length = max(p[2] for p in placements) + sample_rate
    stems = {speaker: np.zeros(length, dtype=np.float32) for speaker in VOICES}
    for speaker, start, end, clip in placements:
        stems[speaker][start:end] += clip
    mixed = sum(stems.values())
    gain = 0.85 / max(float(np.max(np.abs(mixed))), 1e-9)
    stems = {speaker: stem * gain for speaker, stem in stems.items()}
    return mixed * gain, stems, {"kind": "synthetic_composition", "human_reviewed": False,
                                "turns": turns}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="Neuer Ausgabeordner außerhalb des Git-Verlaufs")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    clips = []
    for index, (speaker, _, text) in enumerate(DIALOGUE):
        aiff = args.output / f"{index:02d}-{speaker}.aiff"
        wav = aiff.with_suffix(".wav")
        subprocess.run(["say", "-v", VOICES[speaker], "-r", "150", "-o", str(aiff), text], check=True)
        subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-n", "-i", str(aiff),
                        "-ar", str(RATE), "-ac", "1", "-c:a", "pcm_s16le", str(wav)], check=True)
        clip, _ = sf.read(wav, dtype="float32")
        audible = np.flatnonzero(np.abs(clip) >= 10 ** (-55 / 20))
        if not len(audible):
            raise RuntimeError(f"Abschnitt {index} enthält kein messbares Signal.")
        # Nur äußere Stille entfernen. Innere Sprechpausen bleiben erhalten.
        clips.append(clip[audible[0]:audible[-1] + 1])
    mixed, stems, reference = compose(clips)
    audio_path = args.output / "testgespraech.wav"
    sf.write(audio_path, mixed, RATE, subtype="PCM_16")
    for speaker, stem in stems.items():
        sf.write(args.output / f"stimme-{speaker}.wav", stem, RATE, subtype="PCM_16")
    metadata = {"synthetic": True, "voices": VOICES, "words_per_minute_requested": 150,
                "platform": platform.platform(), "sample_rate": RATE, "channels": 1,
                "duration_seconds": len(mixed) / RATE,
                "peak_dbfs": float(20 * np.log10(np.max(np.abs(mixed)))),
                "rms_dbfs": float(20 * np.log10(np.sqrt(np.mean(mixed ** 2)))),
                "clipped_samples": int(np.count_nonzero(np.abs(mixed) >= 1)),
                "sha256": hashlib.sha256(audio_path.read_bytes()).hexdigest(),
                "reference_note": "Vor ASR erzeugte Kompositionsreferenz; keine menschliche Hörprüfung. Grenzen umfassen innere Pausen."}
    for name, data in [("referenz.json", reference), ("herkunft.json", metadata)]:
        (args.output / name).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (args.output / "dialog.txt").write_text("\n\n".join(
        f"{t['start']:.2f}–{t['end']:.2f} | {t['speaker']}: {t['text']}" for t in reference["turns"]
    ) + "\n", encoding="utf-8")
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-n", "-i", str(audio_path),
                    "-codec:a", "libmp3lame", "-q:a", "3", str(args.output / "testgespraech.mp3")], check=True)
    if not 120 <= metadata["duration_seconds"] <= 180:
        raise RuntimeError(f"Testgespräch außerhalb des Zielbereichs: {metadata['duration_seconds']:.2f} s")
    print(json.dumps(metadata, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
