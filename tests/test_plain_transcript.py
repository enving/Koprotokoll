"""Normale Textausgaben enthalten keine technische Segmentaufteilung."""

import re

from fastapi.testclient import TestClient

from koprotokoll.app import create_app
from koprotokoll.config import Settings
from koprotokoll.export import export
from test_koprotokoll import FakeTranscriber, NoLLM


def test_plain_text_exports_join_fragments_and_keep_real_speaker_changes():
    segments = [{'start': 22, 'end': 38, 'speaker': 'S1', 'text': 'Wir sehen uns'},
                {'start': 38, 'end': 77, 'speaker': 'S1', 'text': 'als Anwendungslabor.'}]
    meeting = {'title': 'Lesetext', 'speakers': {'S1': 'Alex', 'S2': 'Sam'}}
    for fmt in ('txt', 'md'):
        body, _ = export(meeting, segments, [], fmt)
        assert 'Wir sehen uns als Anwendungslabor.' in body
        assert '00:22' not in body and '00:38' not in body and 'Alex' not in body
    segments.append({'start': 77, 'end': 80, 'speaker': 'S2', 'text': 'Das stimmt.'})
    body, _ = export(meeting, segments, [], 'txt')
    assert body == 'Alex:\n\nWir sehen uns als Anwendungslabor.\n\nSam:\n\nDas stimmt.\n'
    srt, _ = export(meeting, segments, [], 'srt')
    assert '00:00:22,000 --> 00:00:38,000' in srt


def test_frontend_assets_get_content_versions_and_revalidate(tmp_path, monkeypatch):
    from koprotokoll import app as app_mod

    static = tmp_path / 'static'; static.mkdir()
    (static / 'index.html').write_text('<link href="style.css"><script src="app.js"></script>')
    (static / 'style.css').write_text('body{}')
    script = static / 'app.js'; script.write_text('const version=1;')
    monkeypatch.setattr(app_mod, 'STATIC', static)
    app = create_app(Settings(data_dir=tmp_path / 'data', diarization=False),
                     transcriber=FakeTranscriber(), llm=NoLLM(), background=False)
    with TestClient(app) as client:
        before = client.get('/')
        assert before.headers['cache-control'] == 'no-cache'
        old = re.search(r'app.js\?v=[a-f0-9]+', before.text).group()
        assert client.get('/' + old).headers['cache-control'] == 'no-cache'
        script.write_text('const version=2;')
        after = client.get('/index.html')
        new = re.search(r'app.js\?v=[a-f0-9]+', after.text).group()
        assert old != new
