import io
import json
import sys
import wave
from types import SimpleNamespace

import numpy as np
import pytest

from studycenter_public.core import mcp_client
from studycenter_public.core.speech import SpeechService


class Stream(io.StringIO):
    def __init__(self, value=''):
        super().__init__(value)
        self.encodings = []

    def reconfigure(self, *, encoding):
        self.encodings.append(encoding)


def test_bridge_parse_errors_request_ids_notifications_and_utf8(tmp_path, monkeypatch):
    incoming = Stream('{bad json}\n' + json.dumps({'jsonrpc': '2.0', 'id': 'ä-1', 'method': 'test'}, ensure_ascii=False) + '\n'
                      + json.dumps({'jsonrpc': '2.0', 'method': 'notification'}) + '\n')
    outgoing = Stream()
    (tmp_path / 'runtime.json').write_text(json.dumps({'instance_id': 'public', 'url': 'http://127.0.0.1:49123'}), encoding='utf-8')
    calls = []

    class Secrets:
        def __init__(self, identity): assert identity == 'public'
        def get(self, name):
            assert name == 'mcp'
            return json.dumps({'codex': 'fixture-token'})

    class Client:
        def __init__(self, **kwargs):
            assert kwargs['trust_env'] is False
            assert kwargs['headers']['Authorization'] == 'Bearer fixture-token'
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def post(self, url, *, json):
            calls.append((url, json))
            raise OSError('fixture')

    monkeypatch.setattr(mcp_client, 'read_owned_identity', lambda path: {'instance_id': 'public'})
    monkeypatch.setattr(mcp_client, 'SecretStore', Secrets)
    monkeypatch.setattr('httpx.Client', Client)
    monkeypatch.setattr(sys, 'stdin', incoming)
    monkeypatch.setattr(sys, 'stdout', outgoing)
    monkeypatch.setattr(sys, 'argv', ['bridge', str(tmp_path)])
    mcp_client.main()
    replies = [json.loads(line) for line in outgoing.getvalue().splitlines()]
    assert incoming.encodings == outgoing.encodings == ['utf-8']
    assert [(r['id'], r['error']['code']) for r in replies] == [(None, -32700), ('ä-1', -32603)]
    assert len(calls) == 2 and all(url == 'http://127.0.0.1:49123/mcp' for url, _ in calls)


def recording():
    audio = (1000 * np.sin(np.arange(16000) * 2 * np.pi * 220 / 16000)).astype('<i2')
    raw = io.BytesIO()
    with wave.open(raw, 'wb') as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16000)
        output.writeframes(audio.tobytes())
    return raw.getvalue()


@pytest.mark.parametrize(('failed_device', 'expected'), [('cpu', 'base'), ('cuda', 'small')])
def test_speech_fallback_uses_instance_model_hook(tmp_path, monkeypatch, failed_device, expected):
    ensured, paths, loaded = [], [], []
    def model_path(name):
        paths.append(name)
        return tmp_path / ('verified-' + name)
    def ensure_model(name):
        ensured.append(name)
        return tmp_path / ('verified-' + name)

    class Model:
        def __init__(self, directory, **kwargs): loaded.append(directory)
        def transcribe(self, audio, **kwargs):
            return iter([SimpleNamespace(text='Aufgabe morgen', no_speech_prob=0, avg_logprob=0)]), None

    monkeypatch.setitem(sys.modules, 'faster_whisper', SimpleNamespace(WhisperModel=Model))
    monkeypatch.setitem(sys.modules, 'ctranslate2', SimpleNamespace(get_cuda_device_count=lambda: 0))
    original = SpeechService.recognize
    def recognize(service, audio):
        if service.engine == 'parakeet':
            service.device = failed_device
            raise RuntimeError('fixture primary failure')
        return original(service, audio)
    monkeypatch.setattr(SpeechService, 'recognize', recognize)
    service = SpeechService(engine='parakeet', name='small', device='auto', root=tmp_path,
                            model_path=model_path, ensure_model=ensure_model, vocabulary=lambda: ['Synthetic vocabulary'])
    result = service.transcribe(recording())
    assert result['model'] == expected and result['engine'] == 'whisper'
    assert ensured == [expected] and paths == [expected]
    assert loaded == [str(tmp_path / ('verified-' + expected))]
    assert service.fallback.ensure_model is ensure_model

    assert service.fallback.vocabulary() == ['Synthetic vocabulary']
