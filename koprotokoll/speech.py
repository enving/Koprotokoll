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


class ModelUnavailable(RuntimeError):
    """Das angeforderte lokale Modell kann nicht verwendet werden."""


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

    def __init__(self, model: str, quantization: str | None, path: str | None = None,
                 vad_path: Path | None = None):
        self._model_name, self._quant, self._path = model, quantization, path
        self._vad_path = vad_path
        self._lock = threading.Lock()
        self._inference_lock = threading.Lock()
        self._asr = None

    def _load(self):
        with self._lock:
            if self._asr is None:
                import onnx_asr
                import onnxruntime

                require_model_files(Path(self._path) if self._path else None, v3_files(self._quant))
                require_model_files(self._vad_path, ("silero_vad.onnx",))
                options = onnxruntime.SessionOptions()
                options.intra_op_num_threads = 4
                log.info("Lade ASR-Modell %s (%s) …", self._model_name, self._quant or "fp32")
                model = onnx_asr.load_model(self._model_name, self._path, quantization=self._quant,
                                           providers=["CPUExecutionProvider"], sess_options=options)
                vad = onnx_asr.load_vad("silero", self._vad_path,
                                       providers=["CPUExecutionProvider"], sess_options=options)
                self._asr = model.with_vad(vad, max_speech_duration_s=25).with_timestamps()
        return self._asr

    def transcribe(self, audio: np.ndarray, progress: ProgressFn) -> list[Word]:
        if not len(audio):
            progress(1.0)
            return []
        asr = self._load()
        total = max(len(audio) / SAMPLE_RATE, 1e-6)
        words: list[Word] = []
        with self._inference_lock:
            # Silero benötigt mindestens ein Fenster samt Kontext (576 Samples).
            waveform = audio if len(audio) >= 1024 else np.pad(audio, (0, 1024 - len(audio)))
            for seg in asr.recognize(waveform, sample_rate=SAMPLE_RATE):
                words.extend(tokens_to_words(seg.tokens or [], seg.timestamps or [], seg.start, seg.end, seg.text))
                progress(min(seg.end / total, 1.0))
        for word in words:
            word.start = min(max(0.0, word.start), total)
            word.end = min(max(word.start, word.end), total)
        progress(1.0)
        return words


V3_MODEL = "nemo-parakeet-tdt-0.6b-v3"
V3_REPO = "istupakov/parakeet-tdt-0.6b-v3-onnx"
V3_REVISION = "8f23f0c03c8761650bdb5b40aaf3e40d2c15f1ce"
VAD_REPO = "istupakov/silero-vad-onnx"
VAD_REVISION = "b3e3ee3cce4c11ceb63b1a0b229d916069c1ddf6"
PRIMELINE_REPO = "flozen1981/parakeet-primeline-onnx"
PRIMELINE_REVISION = "d548e25b9bfe559aa274f361892dc4ed5d64743a"
PRIMELINE_FILES = ("encoder.int8.onnx", "encoder.int8.onnx.data", "decoder.int8.onnx",
                   "joiner.int8.onnx", "tokens.txt")


def v3_files(quantization: str | None) -> tuple[str, ...]:
    if quantization not in (None, "int8"):
        raise ValueError("Parakeet v3 unterstützt hier ASR_QUANT=int8 oder leer (fp32).")
    suffix = ".int8" if quantization else ""
    return ("config.json", "vocab.txt", f"encoder-model{suffix}.onnx",
            f"decoder_joint-model{suffix}.onnx", *(("encoder-model.onnx.data",) if not quantization else ()))


def require_model_files(path: Path | None, files: tuple[str, ...]) -> None:
    if path is None:
        raise ModelUnavailable("Kein lokaler Modellpfad konfiguriert; bitte create_transcriber(Settings()) verwenden.")
    missing = [name for name in files if not (path / name).is_file()]
    if missing:
        raise ModelUnavailable(f"Lokales Modell in {path} fehlt oder ist unvollständig ({', '.join(missing)}). "
                           "Bitte python scripts/download_models.py mit derselben Konfiguration ausführen; "
                           "kein automatischer Download während der Transkription.")


def install_transcriber(transcriber: Transcriber) -> None:
    """Nur der ausdrücklich gestartete Installer lädt Modelle aus dem Netz."""
    from huggingface_hub import snapshot_download

    if isinstance(transcriber, SherpaParakeetTranscriber):
        ensure_primeline_model(transcriber.path)
    elif isinstance(transcriber, ParakeetTranscriber):
        files = v3_files(transcriber._quant)
        if not all((Path(transcriber._path) / name).is_file() for name in files):
            snapshot_download(V3_REPO, revision=V3_REVISION, local_dir=transcriber._path,
                              allow_patterns=[*files, "README.md"])
        if not (transcriber._vad_path / "silero_vad.onnx").is_file():
            snapshot_download(VAD_REPO, revision=VAD_REVISION, local_dir=str(transcriber._vad_path),
                              allow_patterns=["silero_vad.onnx", "config.json", "LICENSE.txt", "README.md"])
        require_model_files(Path(transcriber._path), files)
        require_model_files(transcriber._vad_path, ("silero_vad.onnx",))
    else:
        raise ValueError("Unbekanntes lokales Transkriptions-Backend.")
    transcriber._load()


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

            path = self.path
            require_model_files(path, PRIMELINE_FILES)
            self._recognizer = sherpa_onnx.OfflineRecognizer.from_transducer(
                encoder=str(path / PRIMELINE_FILES[0]), decoder=str(path / PRIMELINE_FILES[2]),
                joiner=str(path / PRIMELINE_FILES[3]), tokens=str(path / PRIMELINE_FILES[4]),
                model_type="nemo_transducer", num_threads=self.threads,
                decoding_method="greedy_search", provider="cpu",
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
        durations = list(getattr(result, "durations", []))
        return tokens_to_words(list(result.tokens), stamps, start, end, result.text,
                               durations=durations)

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


def language_mode(language: str | None, default: str = "auto") -> str:
    mode = (default if language is None else language).strip().lower()
    if mode not in ("auto", "de", "en"):
        raise ValueError("language muss auto, de oder en sein.")
    return mode


def create_transcriber(settings, language: str | None = None) -> Transcriber:
    mode = language_mode(language, settings.asr_language)
    if settings.asr_model not in ("auto", "parakeet-primeline", V3_MODEL):
        raise ValueError(f"Unbekanntes ASR_MODEL: {settings.asr_model}; erlaubt: auto, parakeet-primeline, {V3_MODEL}.")
    model = "parakeet-primeline" if mode == "de" and settings.asr_model != V3_MODEL else V3_MODEL
    explicit = settings.asr_primeline_path if model == "parakeet-primeline" else settings.asr_v3_path
    path = Path(explicit).expanduser() if explicit else None
    if path is None and settings.asr_model_path:
        legacy = Path(settings.asr_model_path).expanduser()
        # Dateistrukturen trennen: niemals Primeline als v3 oder umgekehrt laden.
        primeline = (legacy / "tokens.txt").is_file() or (legacy / "joiner.int8.onnx").is_file()
        v3 = (legacy / "vocab.txt").is_file() and (legacy / "config.json").is_file()
        if primeline == v3:
            raise ValueError("ASR_PATH ist nicht eindeutig als Primeline oder v3 erkennbar. "
                             "Bitte ASR_PRIMELINE_PATH bzw. ASR_V3_PATH setzen.")
        if primeline == (model == "parakeet-primeline"):
            path = legacy
    path = (path or settings.models_dir / ("parakeet-primeline-onnx" if model == "parakeet-primeline"
                                         else "parakeet-tdt-0.6b-v3-onnx")).resolve()
    wrong_layout = (path / "vocab.txt").exists() if model == "parakeet-primeline" else (path / "tokens.txt").exists()
    if wrong_layout:
        raise ValueError(f"Modellpfad {path} gehört zum anderen Parakeet-Backend; getrennte Modellordner verwenden.")
    if model == "parakeet-primeline":
        transcriber = SherpaParakeetTranscriber(path)
    else:
        v3_files(settings.asr_quantization)
        transcriber = ParakeetTranscriber(model, settings.asr_quantization, str(path),
                                          Path(settings.asr_vad_path).expanduser().resolve() if settings.asr_vad_path
                                          else settings.models_dir / "silero-vad-onnx")
    transcriber.asr_info = {"model": model, "mode": mode, "backend": "sherpa-onnx" if model == "parakeet-primeline" else "onnx-asr",
                            "model_path": str(path), "provider": "CPUExecutionProvider", "language_forced": False,
                            "quantization": "int8" if model == "parakeet-primeline" else settings.asr_quantization,
                            "language_detection": "de-specialized" if model == "parakeet-primeline" else "automatic"}
    return transcriber


def transcription_info(transcriber: Transcriber, mode: str) -> dict:
    """Metadaten beschreiben Auswahl, keine vorgetäuschte erkannte Sprache."""
    return {**getattr(transcriber, "asr_info", {"model": "custom", "backend": type(transcriber).__name__}),
            "mode": mode, "language_forced": False}


def tokens_to_words(tokens: list[str], stamps: list[float], seg_start: float, seg_end: float,
                    fallback_text: str = "", durations: list[float] | None = None) -> list[Word]:
    """Subword-Tokens (Leerzeichen = Wortanfang) mit relativen Zeitstempeln zu Wörtern zusammensetzen."""
    if not tokens or len(tokens) != len(stamps):
        text = fallback_text.strip()
        return [Word(seg_start, seg_end, text)] if text else []
    words: list[Word] = []
    timed_ends = durations is not None and len(durations) == len(tokens)
    cur_text, cur_start, cur_end = "", None, None
    for i, (tok, ts) in enumerate(zip(tokens, stamps)):
        tok = tok.replace("▁", " ")
        t = seg_start + ts
        if cur_start is None or tok.startswith(" "):
            if cur_text.strip():
                words.append(Word(cur_start, cur_end if timed_ends else t, cur_text.strip()))
            cur_text, cur_start = tok, t
        else:
            cur_text += tok
        cur_end = min(seg_end, max(t, t + max(0, durations[i]))) if timed_ends else None
    if cur_text.strip():
        words.append(Word(cur_start, cur_end if timed_ends else max(seg_end, cur_start), cur_text.strip()))
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
                     max_gap: float = 1.5, speaker_labels: dict[int, str] | None = None) -> list[dict]:
    """Wörter zu Äußerungen gruppieren; Sprecher-IDs in Auftrittsreihenfolge nummerieren."""
    labels: dict[int, str] = speaker_labels if speaker_labels is not None else {}

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
            cur = {"start": w.start, "end": w.end, "speaker": spk, "text": w.text, "word_times": []}
        else:
            cur["text"] += " " + w.text
            cur["end"] = max(cur["end"], w.end)
        cur["word_times"].append({"start": w.start, "end": w.end, "text": w.text})
    if cur:
        out.append(cur)
    for u in out:
        u["start"], u["end"] = round(u["start"], 2), round(u["end"], 2)
    return out
