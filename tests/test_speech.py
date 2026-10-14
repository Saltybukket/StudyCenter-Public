import hashlib
import json
from types import SimpleNamespace
from pathlib import Path

import httpx
import pytest
from studycenter_public.services.speech import SpeechService, SpeechError, MODELS
from test_providers import run


@pytest.fixture
def service(tmp_path):
    values={}
    settings=SimpleNamespace(get=lambda key,default=None:values.get(key,default),set=lambda key,value:values.__setitem__(key,value))
    paths=SimpleNamespace(cache=tmp_path/'cache');paths.cache.mkdir()
    return SpeechService(paths,settings),values


def synthetic_model(service,monkeypatch):
    data={'config.json':b'{}','model.bin':b'synthetic-weights','tokenizer.json':b'{}','vocabulary.txt':b'synthetic'}
    manifest={'repo':'Systran/faster-whisper-base','revision':'1'*40,'files':{name:{'bytes':len(value),'sha256':hashlib.sha256(value).hexdigest()} for name,value in data.items()}}
    monkeypatch.setitem(MODELS,'base',manifest)
    return data


def test_speech_offline_default_and_explicit_download_consent(service):
    speech,settings=service
    status=speech.status()
    assert not status['enabled'] and not status['model_ready']
    assert status['models']['base']['download_bytes']>100_000_000
    assert status['models']['small']['download_bytes']>400_000_000
    with pytest.raises(SpeechError) as error:run(speech.download(allow_download=False))
    assert error.value.code=='download_consent'
    with pytest.raises(SpeechError):run(speech.transcribe(b'not-audio','webm'))
    assert not list(speech.paths.cache.iterdir())
    with pytest.raises(ValueError):speech.configure(True,'large-v3')


def test_pinned_download_checksum_manifest_and_cache(service,monkeypatch):
    speech,settings=service;data=synthetic_model(speech,monkeypatch)
    seen=[]
    def handler(req):
        seen.append(str(req.url));return httpx.Response(200,content=data[req.url.path.rsplit('/',1)[-1]])
    speech.transport=httpx.MockTransport(handler)
    result=run(speech.download(allow_download=True))
    assert result['model_ready']
    assert all('/resolve/'+('1'*40)+'/' in url for url in seen)
    assert (speech.model_path()/'manifest.json').exists()
    assert speech.status()['model_ready']
    (speech.model_path()/'model.bin').write_bytes(b'tampered')
    assert not speech.status()['model_ready']


def test_download_failure_never_promotes_partial_model(service,monkeypatch):
    speech,settings=service;synthetic_model(speech,monkeypatch)
    speech.transport=httpx.MockTransport(lambda req:httpx.Response(200,content=b'wrong'))
    with pytest.raises(SpeechError) as error:run(speech.download(allow_download=True))
    assert error.value.code in ('checksum','download_size')
    assert not speech.model_path().exists()
    assert not list((speech.paths.cache/'speech').glob('*.partial'))


def test_download_rejects_arbitrary_redirect(service,monkeypatch):
    speech,settings=service;synthetic_model(speech,monkeypatch)
    seen=[]
    def handler(req):seen.append(req);return httpx.Response(302,headers={'Location':'https://evil.invalid/model'})
    speech.transport=httpx.MockTransport(handler)
    with pytest.raises(SpeechError) as error:run(speech.download(allow_download=True))
    assert error.value.code=='download_origin' and len(seen)==1


def test_transcription_cpu_local_only_complete_cleanup_and_errors(service,monkeypatch):
    speech,settings=service;data=synthetic_model(speech,monkeypatch)
    speech.transport=httpx.MockTransport(lambda req:httpx.Response(200,content=data[req.url.path.rsplit('/',1)[-1]]))
    run(speech.download(allow_download=True));speech.configure(True,'base')
    calls=[]
    class Model:
        def __init__(self,path,**kwargs):
            calls.append((path,kwargs));assert kwargs['device']=='cpu' and kwargs['compute_type']=='int8' and kwargs['local_files_only']
        def transcribe(self,path,**kwargs):
            assert Path(path).exists();assert kwargs['vad_filter'] is False
            return iter([SimpleNamespace(text=' first',start=0,end=1),SimpleNamespace(text=' second',start=1,end=2)]),SimpleNamespace(language='en',duration=2)
    speech.model_factory=Model
    monkeypatch.setattr(speech, '_validate_audio_duration', lambda path: None)
    result=run(speech.transcribe(b'RIFF'+b'\0'*4+b'WAVE'+b'\0'*16,'wav'))
    assert result['text']=='first second' and result['language']=='en'
    assert len(calls)==1 and not list((speech.paths.cache/'speech').glob('audio-*'))
    with pytest.raises(SpeechError) as error:run(speech.transcribe(b'x'*(10*1024*1024+1),'wav'))
    assert error.value.code=='audio_limit'
    with pytest.raises(SpeechError):run(speech.transcribe(b'invalid','wav'))
    class Broken(Model):
        def transcribe(self,*args,**kwargs):raise RuntimeError('secret-machine-path')
    speech.model_factory=Broken;speech._model=None
    with pytest.raises(SpeechError) as error:run(speech.transcribe(b'\x1aE\xdf\xa3'+b'\0'*20,'webm'))
    assert error.value.code=='transcription' and 'secret-machine-path' not in str(error.value)
    assert not list((speech.paths.cache/'speech').glob('audio-*'))
