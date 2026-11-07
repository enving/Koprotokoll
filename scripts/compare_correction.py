"""Lokaler Vergleich mit erfundenem Dialog; lädt keine Modelle herunter."""
import json
import time
from collections import Counter
from pathlib import Path
import argparse

from generate_audio_sample import DIALOGUE
from koprotokoll.config import Settings
from koprotokoll.correction import correct
from koprotokoll.evaluation import words, word_error_rate
from koprotokoll.summarize import LLM


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    reference = [dict(start=i, end=i+1, speaker=speaker, text=text) for i, (speaker, _, text) in enumerate(DIALOGUE)]
    # Kontrolliert eingebaute Fehler, keine Behauptung über echte ASR-Fehler.
    source = [{**s, "text": s["text"].replace("Nachbarschaftsfest", "Nachbarschafts fest").replace("Getränke", "Getrenke").replace("Donnerstag", "Donerstag")} for s in reference]
    result = {"source": source, "reference": reference, "before": word_error_rate(reference, source), "runs": []}
    for model in ("qwen3:4b", "qwen3-vl:4b"):
        settings = Settings(llm_model=model, llm_timeout=60)
        settings.llm_base_url = "http://localhost:11434/v1"
        settings.llm_max_tokens = 700
        settings.llm_reasoning_effort = "none"
        start = time.monotonic()
        texts, report = correct(source, LLM(settings), model, ["Nachbarschaftsfest", "Getränke", "Donnerstag"])
        hypothesis = [{**s, "text": text} for s, text in zip(source, texts)]
        unexpected = Counter(words(hypothesis)) - Counter(words(reference))
        invented = Counter(words(hypothesis)) - (Counter(words(reference)) | Counter(words(source)))
        run = {"model": model, "seconds": round(time.monotonic()-start, 2), "temperature": 0.2, "max_tokens": 700, "reasoning_effort": "none", "after": word_error_rate(reference, hypothesis), "invented_words": dict(invented), "invented_count": sum(invented.values()), "unexpected_words": dict(unexpected), "unexpected_count": sum(unexpected.values()), "report": report, "texts": texts}
        result["runs"].append(run)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2)+"\n")
        print(model, run["seconds"], run["after"], flush=True)


if __name__ == "__main__":
    main()
