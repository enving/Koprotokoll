"""Sprachwahl, CPU-Laden, Altpfade und API ohne echte Modelle prüfen."""

from types import SimpleNamespace

import numpy as np
import pytest
from fastapi.testclient import TestClient

from koprotokoll import app as app_mod
from koprotokoll.audio import SAMPLE_RATE
from koprotokoll.config import Settings
from koprotokoll.speech import (ModelUnavailable, ParakeetTranscriber, PRIMELINE_FILES,
                               SherpaParakeetTranscriber, V3_MODEL, create_transcriber,
                               install_transcriber, transcription_info, v3_files)
from test_koprotokoll import FakeTranscriber, NoLLM, wav_bytes


@pytest.mark.parametrize('configured', ['auto', 'parakeet-primeline', V3_MODEL])
@pytest.mark.parametrize('mode', ['auto', 'de', 'en'])
def test_selection_never_sends_auto_or_english_to_primeline(tmp_path, configured, mode):
    s = Settings(data_dir=tmp_path, asr_model=configured, asr_language=mode)
    asr = create_transcriber(s)
    expected = 'parakeet-primeline' if mode == 'de' and configured != V3_MODEL else V3_MODEL
    info = transcription_info(asr, mode)
    assert info['model'] == expected and info['mode'] == mode
    assert info['language_forced'] is False
    assert info['provider'] == 'CPUExecutionProvider'
    assert isinstance(asr, SherpaParakeetTranscriber if expected == 'parakeet-primeline' else ParakeetTranscriber)


def test_environment_defaults_and_overrides(tmp_path, monkeypatch):
    for key in ('MODEL', 'LANGUAGE', 'PATH', 'V3_PATH', 'PRIMELINE_PATH', 'VAD_PATH'):
        monkeypatch.delenv('KOPROTOKOLL_ASR_' + key, raising=False)
    assert Settings().asr_language == Settings().asr_model == 'auto'
    monkeypatch.setenv('KOPROTOKOLL_ASR_LANGUAGE', 'de')
    monkeypatch.setenv('KOPROTOKOLL_ASR_MODEL', V3_MODEL)
    monkeypatch.setenv('KOPROTOKOLL_ASR_V3_PATH', str(tmp_path))
    info = create_transcriber(Settings()).asr_info
    assert info['mode'] == 'de' and info['model'] == V3_MODEL and info['model_path'] == str(tmp_path)


@pytest.mark.parametrize('kind', ['primeline', 'v3'])
def test_legacy_model_path_only_applies_to_its_backend(tmp_path, kind):
    legacy = tmp_path / 'legacy'
    legacy.mkdir()
    for filename in (PRIMELINE_FILES if kind == 'primeline' else v3_files('int8')):
        (legacy / filename).write_bytes(b'modell')
    s = Settings(data_dir=tmp_path, asr_model_path=str(legacy))
    assert (create_transcriber(s, 'de').asr_info['model_path'] == str(legacy)) == (kind == 'primeline')
    for mode in ('auto', 'en'):
        assert (create_transcriber(s, mode).asr_info['model_path'] == str(legacy)) == (kind == 'v3')


def test_dedicated_paths_take_precedence_and_reject_other_backend(tmp_path):
    old, new = tmp_path / 'old', tmp_path / 'new'
    old.mkdir(); new.mkdir()
    (old / 'tokens.txt').touch()
    s = Settings(data_dir=tmp_path, asr_model_path=str(old), asr_v3_path=str(new))
    assert create_transcriber(s, 'auto').asr_info['model_path'] == str(new)
    s.asr_v3_path = str(old)
    with pytest.raises(ValueError, match='anderen Parakeet-Backend'):
        create_transcriber(s)
    s.asr_v3_path = None
    (old / 'tokens.txt').unlink()
    with pytest.raises(ValueError, match='nicht eindeutig'):
        create_transcriber(s)
    s.asr_model_path = str(tmp_path / 'missing')
    with pytest.raises(ValueError, match='nicht eindeutig'):
        create_transcriber(s)


@pytest.mark.parametrize('field,value', [('asr_language', 'fr'), ('asr_model', 'unknown'), ('asr_quantization', 'int4')])
def test_invalid_configuration_is_visible(tmp_path, field, value):
    with pytest.raises(ValueError):
        create_transcriber(Settings(data_dir=tmp_path, **{field: value}))


def test_missing_models_fail_without_downloading(tmp_path, monkeypatch):
    monkeypatch.setattr('huggingface_hub.snapshot_download', lambda *a, **k: pytest.fail('Unerlaubter Download'))
    for mode in ('auto', 'de'):
        with pytest.raises(ModelUnavailable, match='download_models.py'):
            create_transcriber(Settings(data_dir=tmp_path), mode)._load()


def test_v3_runtime_and_vad_explicitly_use_cpu_and_keep_offsets(tmp_path, monkeypatch):
    s = Settings(data_dir=tmp_path)
    asr = create_transcriber(s)
    paths = [(asr._path, v3_files('int8')), (asr._vad_path, ('silero_vad.onnx',))]
    from pathlib import Path
    for path, files in paths:
        Path(path).mkdir(parents=True)
        for filename in files:
            (Path(path) / filename).write_bytes(b'modell')
    calls = []
    class Model:
        def with_vad(self, vad, **kwargs):
            assert kwargs['max_speech_duration_s'] == 25
            return self
        def with_timestamps(self):
            return self
        def recognize(self, audio, **kwargs):
            assert kwargs['sample_rate'] == SAMPLE_RATE
            return [SimpleNamespace(tokens=['▁Hello', '▁world'], timestamps=[0.1, 0.5],
                                    start=2, end=3, text='Hello world')]
    def load(*args, **kwargs):
        calls.append((args, kwargs))
        assert kwargs['providers'] == ['CPUExecutionProvider']
        assert kwargs['sess_options'].intra_op_num_threads == 4
        return Model()
    monkeypatch.setattr('onnx_asr.load_model', load)
    monkeypatch.setattr('onnx_asr.load_vad', load)
    progress = []
    words = asr.transcribe(np.ones(4 * SAMPLE_RATE, dtype=np.float32), progress.append)
    assert len(calls) == 2 and calls[0][0][1] == asr._path
    assert calls[1][0][1] == asr._vad_path
    assert [(w.text, w.start, w.end) for w in words] == [('Hello', 2.1, 2.5), ('world', 2.5, 3)]
    assert progress[-1] == 1


def test_v3_empty_input_does_not_load_and_short_input_is_padded(tmp_path):
    asr = create_transcriber(Settings(data_dir=tmp_path))
    progress = []
    assert asr.transcribe(np.array([], dtype=np.float32), progress.append) == []
    assert asr._asr is None and progress == [1]
    def recognize(audio, **kwargs):
        assert len(audio) == 1024
        return []
    asr._asr = SimpleNamespace(recognize=recognize)
    assert asr.transcribe(np.zeros(16, dtype=np.float32), progress.append) == []


def test_installer_pins_v3_and_vad(tmp_path, monkeypatch):
    from pathlib import Path
    from koprotokoll.speech import V3_REPO, V3_REVISION, VAD_REPO, VAD_REVISION
    calls = []
    def download(repo, **kwargs):
        calls.append((repo, kwargs))
        path = Path(kwargs['local_dir']); path.mkdir(parents=True, exist_ok=True)
        for name in kwargs['allow_patterns']:
            (path / name).write_bytes(b'modell')
    monkeypatch.setattr('huggingface_hub.snapshot_download', download)
    asr = create_transcriber(Settings(data_dir=tmp_path))
    monkeypatch.setattr(asr, '_load', lambda: None)
    install_transcriber(asr)
    assert [(repo, kw['revision']) for repo, kw in calls] == [(V3_REPO, V3_REVISION), (VAD_REPO, VAD_REVISION)]
    install_transcriber(asr)
    assert len(calls) == 2  # Vollständige lokale Modelle bleiben erhalten.


def test_api_selects_per_request_and_retains_queue_metadata(tmp_path, monkeypatch):
    calls = []
    def factory(settings, language=None):
        asr = FakeTranscriber()
        mode = language or settings.asr_language
        model = 'parakeet-primeline' if mode == 'de' else V3_MODEL
        asr.asr_info = {'model': model, 'backend': 'test-cpu', 'model_path': '/invented/models/' + model,
                        'quantization': 'int8', 'provider': 'CPUExecutionProvider'}
        original = asr.transcribe
        def run(*args):
            calls.append(model)
            return original(*args)
        asr.transcribe = run
        return asr
    monkeypatch.setattr(app_mod, 'create_transcriber', factory)
    settings = Settings(data_dir=tmp_path, diarization=False)
    with TestClient(app_mod.create_app(settings, llm=NoLLM(), background=False)) as client:
        payload = {'file': ('sample.wav', wav_bytes(tmp_path, seconds=2), 'audio/wav')}
        assert client.get('/api/status').json()['asr']['model'] == V3_MODEL
        for mode in ('auto', 'de', 'en'):
            response = client.post('/v1/audio/transcriptions', files=payload,
                                   data={'language': mode, 'response_format': 'verbose_json'})
            assert response.status_code == 200
            body = response.json()
            expected = 'parakeet-primeline' if mode == 'de' else V3_MODEL
            assert body['language'] == '' and body['asr']['mode'] == mode
            assert body['asr']['model'] == calls[-1] == expected
            assert response.headers['X-Koprotokoll-ASR-Model'] == expected
        response = client.post('/v1/audio/transcriptions', files=payload,
                               data={'language': 'en', 'response_format': 'text'})
        assert response.headers['X-Koprotokoll-ASR-Mode'] == 'en'
        for endpoint in ('/v1/audio/transcriptions', '/api/meetings'):
            assert client.post(endpoint, files=payload, data={'language': 'fr'}).status_code == 400
        for mode in ('de', 'en'):
            uploaded = client.post('/api/meetings', files=payload, data={'language': mode}).json()
            client.app.state.pipeline.join()
            result = client.get(f"/api/meetings/{uploaded['id']}").json()
            assert result['status'] == 'done' and result['segments']
            assert result['meta']['asr_language'] == result['meta']['asr']['mode'] == mode
            assert result['meta']['asr']['model'] == calls[-1]
        assert list((tmp_path / 'tmp').iterdir()) == []


def test_api_reports_missing_model_and_cleans_upload(tmp_path):
    with TestClient(app_mod.create_app(Settings(data_dir=tmp_path, diarization=False),
                                       llm=NoLLM(), background=False)) as client:
        payload = {'file': ('sample.wav', wav_bytes(tmp_path, seconds=2), 'audio/wav')}
        response = client.post('/v1/audio/transcriptions', files=payload, data={'language': 'en'})
        assert response.status_code == 503 and 'download_models.py' in response.json()['detail']
        assert list((tmp_path / 'tmp').iterdir()) == []
        uploaded = client.post('/api/meetings', files=payload, data={'language': 'en'}).json()
        client.app.state.pipeline.join()
        result = client.get(f"/api/meetings/{uploaded['id']}").json()
        assert result['status'] == 'error' and 'download_models.py' in result['error']
        assert result['meta']['asr']['model'] == V3_MODEL
