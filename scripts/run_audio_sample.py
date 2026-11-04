"""Eine vorhandene Audiodatei über die echte lokale Import-Pipeline auswerten."""

from __future__ import annotations

import argparse
import errno
import hashlib
import json
import logging
import socket
import sys
import time
from importlib.metadata import version
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402
from koprotokoll.app import create_app  # noqa: E402
from koprotokoll.config import Settings  # noqa: E402
from koprotokoll.speech import create_transcriber, SherpaDiarizer  # noqa: E402


def verify_network_block() -> dict:
    """OS-Sperre für externe IPv4-/IPv6-Verbindungen und erlaubtes Loopback prüfen."""
    probes = []
    for family, address in [(socket.AF_INET, ("1.1.1.1", 443)),
                            (socket.AF_INET6, ("2606:4700:4700::1111", 443))]:
        with socket.socket(family, socket.SOCK_STREAM) as client:
            client.settimeout(2)
            try:
                client.connect(address)
            except OSError as exc:
                if exc.errno not in (errno.EPERM, errno.EACCES):
                    raise RuntimeError("Keine belegte OS-Netzwerksperre: " + str(exc)) from exc
                probes.append({"address": address[0], "errno": exc.errno, "blocked": True})
            else:
                raise RuntimeError("Externe Verbindung war erlaubt; Offline-Test abgebrochen.")
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        with socket.create_connection(server.getsockname(), timeout=2):
            connection, _ = server.accept()
            connection.close()
    return {"external_probes": probes, "loopback_tcp": "passed",
            "scope": "Nur dieser Prozess samt Kindern; keine systemweite Netzwerksperre."}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path)
    parser.add_argument("output", type=Path, help="Neuer Ausgabeordner")
    parser.add_argument("--models", type=Path, required=True, help="Vorhandene Sherpa-Modelle")
    parser.add_argument("--verify-network-block", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s",
                        handlers=[logging.FileHandler(args.output / "pipeline.log"), logging.StreamHandler()])
    network_events = []

    def audit(event, arguments):
        if event in ("socket.connect", "socket.getaddrinfo"):
            destination = arguments[1] if event == "socket.connect" else arguments[:2]
            network_events.append({"event": event, "destination": destination, "time": time.time()})

    sys.addaudithook(audit)
    proof = verify_network_block() if args.verify_network_block else {"verified": False}
    s = Settings(data_dir=args.output.resolve(), llm_enabled=False)
    if s.asr_model == "parakeet-primeline" and not s.asr_model_path:
        s.asr_model_path = str(args.models.resolve() / "parakeet-primeline-onnx")
    asr = create_transcriber(s)
    diarizer = SherpaDiarizer(args.models.resolve(), s.diarization_threshold)
    timings = {}

    def timed(name, method):
        def call(*a, **kw):
            wall, cpu = time.perf_counter(), time.process_time()
            started_at = time.time()
            logging.info("Start %s", name)
            try:
                return method(*a, **kw)
            finally:
                elapsed, cpu_elapsed = time.perf_counter() - wall, time.process_time() - cpu
                timings[name] = {"started_at": started_at, "ended_at": time.time(),
                                 "wall_seconds": elapsed, "process_cpu_seconds": cpu_elapsed,
                                 "mean_cpu_percent_one_core": 100 * cpu_elapsed / elapsed}
                logging.info("Ende %s: %.3f s", name, elapsed)
        return call

    asr.transcribe = timed("transcription", asr.transcribe)
    diarizer.diarize = timed("diarization", diarizer.diarize)
    digest = hashlib.sha256(args.audio.read_bytes()).hexdigest()
    started = time.perf_counter()
    with TestClient(create_app(s, transcriber=asr, diarizer=diarizer, background=False)) as client:
        with args.audio.open("rb") as source:
            response = client.post("/api/meetings", files={"file": (args.audio.name, source, "audio/wav")},
                                   data={"title": args.audio.stem})
        response.raise_for_status()
        meeting_id = response.json()["id"]
        client.app.state.pipeline.join()
        result = client.get(f"/api/meetings/{meeting_id}").json()
    assert hashlib.sha256(args.audio.read_bytes()).hexdigest() == digest, "Original verändert"
    (args.output / "ergebnis.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    model_hashes = {str(p.relative_to(args.models)): hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in sorted(args.models.rglob("*.onnx"))}
    peak_rss = None
    if sys.platform == "darwin":
        import resource

        peak_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    report = {"audio_sha256": digest, "network": proof, "timings": timings,
              "python_network_events": network_events,
              "network_log_scope": "Python-Audit einschließlich Sperrproben; native Bibliotheken werden dadurch nicht protokolliert.",
              "total_wall_seconds": time.perf_counter() - started,
              "peak_rss_bytes_macos": peak_rss,
              "versions": {name: version(name) for name in ("onnx-asr", "onnxruntime", "sherpa-onnx")},
              "model": s.asr_model, "quantization": s.asr_quantization,
              "diarization_threshold": s.diarization_threshold, "requested_speakers": None,
              "diarization_models_sha256": model_hashes, "llm": "disabled; lokale Heuristik",
              "status": result["status"], "diarization": result.get("meta", {}).get("diarization", {})}
    (args.output / "messung.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    logging.info("Status %s; Sprecherzahl %s", report["status"], report["diarization"].get("speaker_count"))
    if report["status"] != "done" or report["diarization"].get("status") != "completed":
        raise RuntimeError("Pipeline oder Sprechererkennung nicht erfolgreich; siehe gesicherte Ergebnisse.")


if __name__ == "__main__":
    main()
