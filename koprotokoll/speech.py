"""Spracherkennung (Parakeet), Sprechererkennung und Zusammenführung zu Äußerungen."""

from __future__ import annotations

import logging
import tarfile
import threading
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

import numpy as np

from .audio import SAMPLE_RATE

log = logging.getLogger(__name__)

ProgressFn = Callable[[float], None]


@dataclass
class Word:
    start: float
    end: float
    text: str


@dataclass
class Turn:
    start: float
    end: float
    speaker: int


class Transcriber(Protocol):
    def transcribe(self, audio: np.ndarray, progress: ProgressFn) -> list[Word]: ...


class Diarizer(Protocol):
    def diarize(self, audio: np.ndarray, num_speakers: int | None, progress: ProgressFn) -> list[Turn]: ...


# --------------------------------------------------------------------------
# Parakeet über onnx-asr
# --------------------------------------------------------------------------
class ParakeetTranscriber:
    """NVIDIA Parakeet TDT (Standard: v3, 25 europäische Sprachen inkl. Deutsch)."""

    def __init__(self, model: str, quantization: str | None, path: str | None = None):
        self._model_name, self._quant, self._path = model, quantization, path
        self._lock = threading.Lock()
        self._asr = None

    def _load(self):
        with self._lock:
            if self._asr is None:
                import onnx_asr

                log.info("Lade ASR-Modell %s (%s) …", self._model_name, self._quant or "fp32")
                model = onnx_asr.load_model(self._model_name, self._path, quantization=self._quant)
                vad = onnx_asr.load_vad("silero")
                self._asr = model.with_vad(vad, max_speech_duration_s=25).with_timestamps()
        return self._asr

    def transcribe(self, audio: np.ndarray, progress: ProgressFn) -> list[Word]:
        asr = self._load()
        total = max(len(audio) / SAMPLE_RATE, 1e-6)
        words: list[Word] = []
        for seg in asr.recognize(audio, sample_rate=SAMPLE_RATE):
            words.extend(tokens_to_words(seg.tokens or [], seg.timestamps or [], seg.start, seg.end, seg.text))
            progress(min(seg.end / total, 1.0))
        return words


PRIMELINE_REPO = "flozen1981/parakeet-primeline-onnx"
PRIMELINE_REVISION = "d548e25b9bfe559aa274f361892dc4ed5d64743a"
PRIMELINE_FILES = ("encoder.int8.onnx", "encoder.int8.onnx.data", "decoder.int8.onnx",
                   "joiner.int8.onnx", "tokens.txt")


def ensure_primeline_model(path: Path) -> Path:
    if not all((path / name).is_file() for name in PRIMELINE_FILES):
        from huggingface_hub import snapshot_download

        snapshot_download(PRIMELINE_REPO, revision=PRIMELINE_REVISION,
                          allow_patterns=list(PRIMELINE_FILES), local_dir=str(path))
    if not all((path / name).is_file() for name in PRIMELINE_FILES):
        raise RuntimeError("Primeline-Modell unvollständig; bitte erneut herunterladen.")
    return path


def split_audio(audio: np.ndarray, max_seconds: float = 90):
    """An der leisesten 400-ms-Stelle vor der Grenze schneiden, ohne Samples zu verlieren."""
    max_n = int(max_seconds * SAMPLE_RATE)
    win_n = int(0.4 * SAMPLE_RATE)
    if max_n < 2 * win_n:
        raise ValueError("Audioabschnitte müssen mindestens 0,8 Sekunden lang sein.")
    search_n = int(min(15, max_seconds / 2) * SAMPLE_RATE)
    start = 0
    while len(audio) - start > max_n:
        tail = audio[start + max_n - search_n:start + max_n]
        windows = len(tail) // win_n
        energy = (tail[:windows * win_n].reshape(windows, win_n).astype(np.float64) ** 2).mean(axis=1)
        cut = max_n - search_n + int(energy.argmin()) * win_n + win_n // 2
        yield start, audio[start:start + cut]
        start += cut
    if start < len(audio):
        yield start, audio[start:]


class SherpaParakeetTranscriber:
    """Deutsche Primeline-Nachschulung; Abschnitte und Pegel nach Kodiktats lokalem Audioweg."""

    def __init__(self, path: Path, threads: int = 4):
        self.path, self.threads = path, threads
        self._recognizer = None
        self._lock = threading.Lock()

    def _load(self):
        if self._recognizer is None:
            import sherpa_onnx

            path = ensure_primeline_model(self.path)
            self._recognizer = sherpa_onnx.OfflineRecognizer.from_transducer(
                encoder=str(path / PRIMELINE_FILES[0]), decoder=str(path / PRIMELINE_FILES[2]),
                joiner=str(path / PRIMELINE_FILES[3]), tokens=str(path / PRIMELINE_FILES[4]),
                model_type="nemo_transducer", num_threads=self.threads,
                decoding_method="greedy_search",
            )
        return self._recognizer

    def _decode(self, audio: np.ndarray, offset: int) -> list[Word]:
        peak = float(np.abs(audio).max()) if len(audio) else 0.0
        if peak == 0:
            return []
        if peak < 0.5:
            audio = audio * min(0.5 / peak, 30)
        rec = self._load()
        stream = rec.create_stream()
        stream.accept_waveform(SAMPLE_RATE, audio)
        rec.decode_stream(stream)
        result = stream.result
        start, end = offset / SAMPLE_RATE, (offset + len(audio)) / SAMPLE_RATE
        stamps = [min(max(float(t), 0), len(audio) / SAMPLE_RATE) for t in result.timestamps]
        return tokens_to_words(list(result.tokens), stamps, start, end, result.text)

    def transcribe(self, audio: np.ndarray, progress: ProgressFn) -> list[Word]:
        words: list[Word] = []
        with self._lock:
            for offset, chunk in split_audio(audio):
                decoded = self._decode(chunk, offset)
                if not decoded and len(chunk) > 20 * SAMPLE_RATE:
                    for suboffset, part in split_audio(chunk, 20):
                        decoded.extend(self._decode(part, offset + suboffset))
                words.extend(decoded)
                progress((offset + len(chunk)) / len(audio))
        progress(1.0)
        return words


def create_transcriber(settings) -> Transcriber:
    if settings.asr_model == "parakeet-primeline":
        path = Path(settings.asr_model_path) if settings.asr_model_path else settings.models_dir / "parakeet-primeline-onnx"
        return SherpaParakeetTranscriber(path)
    return ParakeetTranscriber(settings.asr_model, settings.asr_quantization, settings.asr_model_path)


def tokens_to_words(tokens: list[str], stamps: list[float], seg_start: float, seg_end: float,
                    fallback_text: str = "") -> list[Word]:
    """Subword-Tokens (Leerzeichen = Wortanfang) mit relativen Zeitstempeln zu Wörtern zusammensetzen."""
    if not tokens or len(tokens) != len(stamps):
        text = fallback_text.strip()
        return [Word(seg_start, seg_end, text)] if text else []
    words: list[Word] = []
    cur_text, cur_start = "", None
    for i, (tok, ts) in enumerate(zip(tokens, stamps)):
        tok = tok.replace("▁", " ")
        t = seg_start + ts
        if cur_start is None or tok.startswith(" "):
            if cur_text.strip():
                words.append(Word(cur_start, t, cur_text.strip()))
            cur_text, cur_start = tok, t
        else:
            cur_text += tok
    if cur_text.strip():
        words.append(Word(cur_start, max(seg_end, cur_start), cur_text.strip()))
    return words


# --------------------------------------------------------------------------
# Sprechererkennung über sherpa-onnx
# --------------------------------------------------------------------------
SEGMENTATION_URL = ("https://github.com/k2-fsa/sherpa-onnx/releases/download/"
                    "speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2")
EMBEDDING_URL = ("https://github.com/k2-fsa/sherpa-onnx/releases/download/"
                 "speaker-recongition-models/3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx")


def ensure_diarization_models(models_dir: Path) -> tuple[Path, Path]:
    seg = models_dir / "sherpa-onnx-pyannote-segmentation-3-0" / "model.onnx"
    emb = models_dir / "speaker-embedding-eres2net.onnx"
    models_dir.mkdir(parents=True, exist_ok=True)
    if not seg.exists():
        archive = models_dir / "segmentation.tar.bz2"
        log.info("Lade Segmentierungsmodell …")
        urllib.request.urlretrieve(SEGMENTATION_URL, archive)
        with tarfile.open(archive) as tar:
            tar.extractall(models_dir, filter="data")
        archive.unlink()
    if not emb.exists():
        log.info("Lade Sprecher-Embedding-Modell …")
        tmp = emb.with_suffix(".part")
        urllib.request.urlretrieve(EMBEDDING_URL, tmp)
        tmp.rename(emb)
    return seg, emb


class SherpaDiarizer:
    def __init__(self, models_dir: Path, threshold: float):
        self._models_dir, self._threshold = models_dir, threshold

    def diarize(self, audio: np.ndarray, num_speakers: int | None, progress: ProgressFn) -> list[Turn]:
        import sherpa_onnx

        seg, emb = ensure_diarization_models(self._models_dir)
        cfg = sherpa_onnx.OfflineSpeakerDiarizationConfig(
            segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
                pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(model=str(seg))),
            embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(emb)),
            clustering=sherpa_onnx.FastClusteringConfig(
                num_clusters=num_speakers if num_speakers else -1, threshold=self._threshold),
            min_duration_on=0.3,
            min_duration_off=0.5,
        )
        if not cfg.validate():
            raise RuntimeError("Ungültige Diarisierungs-Konfiguration")
        sd = sherpa_onnx.OfflineSpeakerDiarization(cfg)

        def cb(done: int, total: int) -> int:
            progress(done / max(total, 1))
            return 0

        result = sd.process(audio, callback=cb).sort_by_start_time()
        return [Turn(r.start, r.end, r.speaker) for r in result]


# --------------------------------------------------------------------------
# Wörter + Sprecherwechsel → lesbare Äußerungen
# --------------------------------------------------------------------------
SENTENCE_END = (".", "?", "!", "…")


def speaker_for(word: Word, turns: list[Turn]) -> int | None:
    """Sprecher mit der größten zeitlichen Überlappung; sonst der nächstgelegene."""
    best, best_overlap = None, 0.0
    for t in turns:
        overlap = min(word.end, t.end) - max(word.start, t.start)
        if overlap > best_overlap:
            best, best_overlap = t.speaker, overlap
    if best is not None or not turns:
        return best
    mid = (word.start + word.end) / 2
    return min(turns, key=lambda t: min(abs(mid - t.start), abs(mid - t.end))).speaker


def build_utterances(words: list[Word], turns: list[Turn], max_len: float = 30.0,
                     max_gap: float = 1.5) -> list[dict]:
    """Wörter zu Äußerungen gruppieren; Sprecher-IDs in Auftrittsreihenfolge nummerieren."""
    labels: dict[int, str] = {}

    def label(spk: int | None) -> str:
        if spk is None:
            spk = -1
        if spk not in labels:
            labels[spk] = f"S{len(labels) + 1}"
        return labels[spk]

    out: list[dict] = []
    cur: dict | None = None
    for w in words:
        spk = label(speaker_for(w, turns) if turns else None)
        new = (
            cur is None
            or spk != cur["speaker"]
            or w.start - cur["end"] > max_gap
            or (cur["end"] - cur["start"] > max_len and cur["text"].endswith(SENTENCE_END))
        )
        if new:
            if cur:
                out.append(cur)
            cur = {"start": w.start, "end": w.end, "speaker": spk, "text": w.text}
        else:
            cur["text"] += " " + w.text
            cur["end"] = max(cur["end"], w.end)
    if cur:
        out.append(cur)
    for u in out:
        u["start"], u["end"] = round(u["start"], 2), round(u["end"], 2)
    return out
