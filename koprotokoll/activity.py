"""Lokale Sprachaktivität und Zeitgrenze für unbeaufsichtigte Aufnahmen."""

import threading
from pathlib import Path

import numpy as np

from .speech import require_model_files

SILENCE_SECONDS = 5 * 60


class SilenceTimer:
    def __init__(self, now: float):
        self.last_speech = now

    def check(self, speech: bool | None, now: float) -> bool:
        # Bei fehlenden Samples oder Detektorfehlern niemals vorzeitig stoppen.
        if speech is not False:
            self.last_speech = now
        return now - self.last_speech >= SILENCE_SECONDS


class SpeechDetector:
    def __init__(self, path: Path):
        self.path = path
        self._vad = None
        self._lock = threading.Lock()

    def has_speech(self, audio: np.ndarray) -> bool:
        with self._lock:
            if self._vad is None:
                import onnx_asr
                import onnxruntime

                require_model_files(self.path, ('silero_vad.onnx',))
                options = onnxruntime.SessionOptions()
                options.intra_op_num_threads = 1
                self._vad = onnx_asr.load_vad('silero', self.path,
                                             providers=['CPUExecutionProvider'], sess_options=options)
            segments = self._vad.segment_batch(audio[np.newaxis, :], np.array([len(audio)]), 16000)
            return any(list(part) for part in segments)
