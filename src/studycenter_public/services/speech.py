"""Optional CPU speech, explicit downloads and installation-owned temporary audio."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import threading
import time
from urllib.parse import urljoin, urlsplit
from uuid import uuid4

import httpx
from ..platform.paths import owned_path
from ..platform.components import module_available
from fastapi import APIRouter, File, HTTPException, Request, UploadFile

_COMMON = {
    'tokenizer.json': {'bytes':2203239,'sha256':'fb7b63191e9bb045082c79fd742a3106a12c99513ab30df4a0d47fa6cb6fd0ab'},
    'vocabulary.txt': {'bytes':459861,'sha256':'34ce3fe1c5041027b3f8d42912270993f986dbc4bb34cf27f951e34a1e453913'},
}
MODELS = {
    'base': {'repo':'Systran/faster-whisper-base','revision':'ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66','files':_COMMON | {
        'config.json':{'bytes':2309,'sha256':'56a6d8110d311f19c8f0471e562832c7527f146b567275bfca59fcf7c184da9a'},
        'model.bin':{'bytes':145217532,'sha256':'d01c3014881c9c6f3133c182f3d2887eb6ca1c789a7538c5c007196857a0a6a9'}}},
    'small': {'repo':'Systran/faster-whisper-small','revision':'536b0662742c02347bc0e980a01041f333bce120','files':_COMMON | {
        'config.json':{'bytes':2370,'sha256':'b55496ac7940a7ae47d2c01eab40edfd8701feec1229d9cce3b40014383fb828'},
        'model.bin':{'bytes':483546902,'sha256':'3e305921506d8872816023e4c273e75d2419fb89b24da97b4fe7bce14170d671'}}},
}
AUDIO_LIMIT=10*1024*1024


class SpeechError(Exception):
    def __init__(self, code):
        self.code=code
        super().__init__('Local speech: '+code)


class SpeechService:
    def __init__(self, paths, settings, *, transport=None, model_factory=None):
        self.paths=paths;self.settings=settings;self.transport=transport;self.model_factory=model_factory
        self._model=None;self._model_key=None;self._lock=threading.Lock()

    def _selection(self):
        model=self.settings.get('speech_model','base')
        if model not in MODELS: raise SpeechError('model')
        return model, MODELS[model]

    def model_path(self):
        name,manifest=self._selection()
        path=Path(self.paths.cache)/'speech'/(name+'-'+manifest['revision'])
        try:owned_path(path,self.paths.cache)
        except ValueError:raise SpeechError('cache_path') from None
        if not path.resolve().is_relative_to(Path(self.paths.cache).resolve()):raise SpeechError('cache_path')
        return path

    def _ready(self):
        path=self.model_path();_,manifest=self._selection()
        if not path.is_dir() or path.is_symlink():return False
        try:
            if json.loads((path/'manifest.json').read_text())!=manifest:return False
            for name,record in manifest['files'].items():
                file=path/name
                if file.is_symlink() or file.stat().st_size!=record['bytes']:return False
                with file.open('rb') as data:
                    if hashlib.file_digest(data,'sha256').hexdigest()!=record['sha256']:return False
            return True
        except (OSError,ValueError):return False

    def status(self):
        installed=module_available('faster_whisper') and module_available('av')
        enabled=self.settings.get('speech_enabled',False) is True
        return {'enabled':enabled,'model':self.settings.get('speech_model','base'), 'model_ready':self._ready(),
                'runtime_installed':installed,'native_runtime_checked':self._model is not None,
                'available':enabled and self._model is not None,'device':'cpu','platform':sys.platform,
                'audio_max_bytes':AUDIO_LIMIT,'audio_max_seconds':180,
                'models':{name:{'download_bytes':sum(f['bytes'] for f in m['files'].values()),'repository':m['repo'],'revision':m['revision']} for name,m in MODELS.items()}}

    def configure(self, enabled, model='base', allow_download=False):
        if not isinstance(enabled,bool) or model not in MODELS:raise ValueError('Choose explicit speech activation and base or small')
        self.settings.set('speech_enabled',enabled);self.settings.set('speech_model',model)
        self._model=None;self._model_key=None
        # Configuration never initiates a download. Consent is required on download().
        return self.status()

    @staticmethod
    def _download_url(url):
        parts=urlsplit(url)
        hosts={'huggingface.co','cdn-lfs.huggingface.co','cdn-lfs-us-1.hf.co','cdn-lfs-eu-1.hf.co','cas-bridge.xethub.hf.co'}
        if parts.scheme!='https' or parts.hostname not in hosts or parts.port not in (None,443) or parts.username or parts.password:
            raise SpeechError('download_origin')
        return url

    def _download_file(self, client, url, destination, record):
        deadline=time.monotonic()+300
        for _ in range(6):
            self._download_url(url)
            with client.stream('GET',url) as response:
                if response.status_code in (301,302,303,307,308):
                    if 'location' not in response.headers:raise SpeechError('download')
                    url=urljoin(url,response.headers['location']);continue
                if response.status_code!=200:raise SpeechError('download')
                digest=hashlib.sha256();size=0
                with destination.open('xb') as target:
                    for chunk in response.iter_bytes():
                        if time.monotonic()>deadline:raise SpeechError('download_timeout')
                        size+=len(chunk)
                        if size>record['bytes']:raise SpeechError('download_size')
                        target.write(chunk);digest.update(chunk)
                if size!=record['bytes']:raise SpeechError('download_size')
                if digest.hexdigest()!=record['sha256']:raise SpeechError('checksum')
                return
        raise SpeechError('download_redirects')

    def _download(self):
        with self._lock:
            if self._ready():return self.status()
            target=self.model_path();_,manifest=self._selection();root=target.parent
            root.mkdir(parents=True,exist_ok=True)
            if root.is_symlink() or not root.resolve().is_relative_to(Path(self.paths.cache).resolve()):raise SpeechError('cache_path')
            staging=root/(uuid4().hex+'.partial');staging.mkdir()
            try:
                with httpx.Client(transport=self.transport,follow_redirects=False,trust_env=False,timeout=httpx.Timeout(30,connect=10)) as client:
                    for name,record in manifest['files'].items():
                        url='https://huggingface.co/'+manifest['repo']+'/resolve/'+manifest['revision']+'/'+name
                        self._download_file(client,url,staging/name,record)
                (staging/'manifest.json').write_text(json.dumps(manifest,sort_keys=True),encoding='utf-8')
                if target.exists():
                    if target.is_symlink():raise SpeechError('cache_path')
                    target.rename(root/(target.name+'.corrupt-'+uuid4().hex))
                staging.rename(target)
                return self.status()
            except httpx.TimeoutException:raise SpeechError('download_timeout') from None
            except httpx.HTTPError:raise SpeechError('download_network') from None
            except OSError:raise SpeechError('cache_storage') from None
            finally:
                if staging.exists() and staging.resolve().is_relative_to(root.resolve()) and not staging.is_symlink():
                    shutil.rmtree(staging)

    async def download(self, *, allow_download=False):
        if allow_download is not True:raise SpeechError('download_consent')
        return await asyncio.to_thread(self._download)

    @staticmethod
    def _validate_audio_duration(path):
        import av
        try:
            with av.open(str(path)) as container:
                audio=next((s for s in container.streams if s.type=='audio'),None)
                if audio is None:raise SpeechError('audio_format')
                duration=0
                for frame in container.decode(audio):
                    if not frame.sample_rate:raise SpeechError('audio_format')
                    duration+=frame.samples/frame.sample_rate
                    if duration>180:raise SpeechError('audio_duration')
        except SpeechError:raise
        except Exception:raise SpeechError('audio_format') from None

    def _transcribe(self, audio_bytes, format):
        with self._lock:
            if not self._ready():raise SpeechError('model_missing')
            try:
                factory=self.model_factory
                if factory is None:
                    from faster_whisper import WhisperModel
                    factory=WhisperModel
                path=self.model_path()
                key=str(path)
                if self._model is None or self._model_key!=key:
                    self._model=factory(str(path),device='cpu',compute_type='int8',cpu_threads=2,local_files_only=True)
                    self._model_key=key
            except Exception:raise SpeechError('runtime') from None
            temporary=path.parent/('audio-'+uuid4().hex+'.'+format)
            try:
                temporary.write_bytes(audio_bytes)
                if sys.platform!='win32':temporary.chmod(0o600)
                self._validate_audio_duration(temporary)
                segments,info=self._model.transcribe(str(temporary),beam_size=1,vad_filter=False,condition_on_previous_text=False)
                if info.duration>180:raise SpeechError('audio_duration')
                output=[]
                for segment in segments:
                    if segment.end>180:raise SpeechError('audio_duration')
                    output.append(segment.text.strip())
                    if sum(map(len,output))>50000:raise SpeechError('text_limit')
                return {'text':' '.join(output).strip(),'language':info.language,'duration':info.duration,'model':self._selection()[0],'local':True}
            except SpeechError:raise
            except Exception:raise SpeechError('transcription') from None
            finally:temporary.unlink(missing_ok=True)

    async def transcribe(self, audio_bytes, format='webm'):
        if self.settings.get('speech_enabled',False) is not True:raise SpeechError('disabled')
        if not isinstance(audio_bytes,bytes) or len(audio_bytes)>AUDIO_LIMIT:raise SpeechError('audio_limit')
        if format not in ('wav','webm','ogg'):raise SpeechError('audio_format')
        signatures={'webm':audio_bytes.startswith(b'\x1aE\xdf\xa3'),'wav':audio_bytes.startswith(b'RIFF') and audio_bytes[8:12]==b'WAVE','ogg':audio_bytes.startswith(b'OggS')}
        if not audio_bytes or not signatures[format]:raise SpeechError('audio_format')
        return await asyncio.to_thread(self._transcribe,audio_bytes,format)


router=APIRouter(prefix='/api/speech')


def service(request):
    state=request.app.state
    if not hasattr(state,'speech'):state.speech=SpeechService(state.paths,state.settings)
    return state.speech


@router.get('/status')
async def speech_status(request:Request):return service(request).status()


@router.post('/configure')
async def configure_speech(request:Request):
    data=await request.json()
    return service(request).configure(data.get('enabled',False),data.get('model','base'))


@router.post('/download')
async def download_speech(request:Request):
    data=await request.json()
    try:return await service(request).download(allow_download=data.get('allow_download',False))
    except SpeechError as error:raise HTTPException(400,error.code) from None


@router.post('/transcribe')
async def transcribe_speech(request:Request,file:UploadFile=File(...)):
    mime=(file.content_type or '').split(';')[0]
    format={'audio/webm':'webm','video/webm':'webm','audio/wav':'wav','audio/x-wav':'wav','audio/ogg':'ogg'}.get(mime)
    if format is None:raise HTTPException(400,'audio_format')
    audio=await file.read(AUDIO_LIMIT+1)
    try:return await service(request).transcribe(audio,format)
    except SpeechError as error:raise HTTPException(400,error.code) from None
