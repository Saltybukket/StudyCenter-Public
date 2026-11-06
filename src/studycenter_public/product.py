"""Run the shared CampusBoard application with Public-owned state and services."""
import json
import shlex
import subprocess
import sys
from pathlib import Path

import portalocker

from .platform.components import configure_browser_runtime
from .platform.paths import private_directory, atomic_json
from .platform.secrets import SecretStore, SecretStoreUnavailable
from .services.documents import default_ocr


class SwitchableSecrets:
    def __init__(self, store, instance_id):
        self.current, self.instance_id = store, instance_id
        self.last_error = ''
    @property
    def persistent(self): return self.current.persistent
    @property
    def session_only(self): return self.current.session_only
    @property
    def status(self): return self.last_error or self.current.status
    def get(self, name):
        try: return self.current.get(name)
        except SecretStoreUnavailable as error:
            self.last_error = str(error)
            raise
    def set(self, name, value):
        try: return self.current.set(name, value)
        except SecretStoreUnavailable as error:
            self.last_error = str(error)
            raise
    def delete(self, name): return self.current.delete(name)
    def use_session(self):
        if not self.current.session_only:
            self.current = SecretStore(self.instance_id, session_only=True)
            self.last_error = ''


class JsonSecret:
    """A named, instance-scoped JSON record in the existing OS secret store."""
    def __init__(self, secrets, name, lock_dir, default=None):
        self.secrets, self.name, self.lock_dir = secrets, name, lock_dir
        self.default = default if default is not None else {}

    def read(self):
        raw = self.secrets.get(self.name)
        return json.loads(raw) if raw else self.default.copy()

    def write(self, value):
        self.secrets.set(self.name, json.dumps(value, ensure_ascii=False))

    def lock(self):
        private_directory(self.lock_dir)
        return portalocker.Lock(str(self.lock_dir / (self.name + '.lock')), timeout=0)

    def status(self):
        values = self.read()
        return {k: values.get(k, '') for k in ('host', 'username', 'address')} | {'configured': bool(values.get('password'))}


class PublicNotifications:
    def send(self, title, message, url, tag, sound):
        if sys.platform == 'win32':
            from .core.reminders import NativeToast
            return NativeToast().send(title, message, url, tag, sound)
        from shutil import which
        executable = which('notify-send')
        if not executable: raise RuntimeError('Desktopmeldung nicht verfügbar: notify-send fehlt.')
        result = subprocess.run([executable, '--app-name=StudyCenter Public', title, message],
                                capture_output=True, timeout=5, check=False)
        if result.returncode: raise RuntimeError('Desktopmeldung konnte nicht zugestellt werden.')


class PublicRuntime:
    def __init__(self, paths): self.paths = paths
    def save_backend_owner(self, data_dir): pass  # Public Runtime owns the lock and socket.
    def autostart_enabled(self):
        from .platform.desktop_lifecycle import autostart_enabled
        return autostart_enabled(self.paths)
    def set_autostart(self, enabled):
        from .platform.desktop_lifecycle import set_autostart
        return set_autostart(enabled, self.paths)
    def install_desktop(self):
        from .platform.desktop_lifecycle import install_desktop
        return install_desktop(self.paths)
    def launch_restart(self, data_dir):
        from .platform.desktop_lifecycle import launch_restart
        return launch_restart(self.paths.data)


def make_settings(paths, secrets=None):
    """Construct explicitly isolated services; never consult private configuration."""
    from .core.config import Settings
    data_dir = private_directory(paths.data / 'core')
    secret_store = SwitchableSecrets(secrets if secrets is not None else SecretStore(paths.instance_id), paths.instance_id)
    locks = data_dir / 'locks'
    record = lambda name, default=None: JsonSecret(secret_store, name, locks, default)
    ocr = default_ocr(paths)
    settings = Settings(data_dir=data_dir, llm_base_url='', llm_model='', llm_api_key='',
                        teachcenter_url='https://tc.tugraz.at/main/', ocr_lang=ocr.ocr_lang,
                        tesseract=ocr.tesseract, stt_engine='whisper', stt_model='small', stt_device='auto')
    settings.llm_trust_env = False
    settings.process_env, settings.working_directory = ocr.process_env, ocr.working_directory
    settings.api_store = record('model-api')
    model_path = paths.config / 'model-api.json'
    if model_path.is_file():
        from .providers.contracts import ProviderProfile
        try:
            saved = json.loads(model_path.read_text(encoding='utf-8'))
            profile = ProviderProfile('compatible', model=saved['model'], base_url=saved['base_url'])
            settings.llm_base_url, settings.llm_model = profile.base_url, profile.model
            settings.llm_api_key = settings.api_store.read().get('api_key', '')
        except SecretStoreUnavailable:
            settings.llm_api_key = ''
        except (ValueError, KeyError, TypeError):
            settings.llm_base_url, settings.llm_model, settings.llm_api_key = '', '', ''
    settings.auth_store = record('chatgpt', {'host_id': paths.host_id, 'active': '', 'profiles': []})
    settings.google_store = record('google')
    settings.mail_store = record('mail')
    settings.browser_store = record('tu-session')
    settings.webmail_store = record('webmail')
    settings.mcp_store = record('mcp')
    settings.tunnel_store = record('tunnel')
    settings.browser_dir = private_directory(paths.profiles / 'core')
    settings.configure_browser = lambda: configure_browser_runtime(paths)
    from .collectors.browser import linux_system_chrome
    settings.system_browser = linux_system_chrome
    settings.speech_dir = private_directory(paths.cache / 'core-speech')
    from .services.speech import SpeechService as PublicSpeech, MODELS
    class Selection:
        def __init__(self, name): self.name = name
        def get(self, key, default=None): return self.name if key == 'speech_model' else default
    def speech_model_path(name):
        return paths.cache / 'speech' / (name + '-' + MODELS[name]['revision'])
    def ensure_speech_model(name):
        service = PublicSpeech(paths, Selection(name))
        service._download()  # Explicit transcription invokes the pinned, verified downloader.
        return service.model_path()
    settings.speech_model_path, settings.ensure_speech_model = speech_model_path, ensure_speech_model
    settings.runtime = PublicRuntime(paths)
    settings.instance_id = paths.instance_id
    settings.background = True
    settings.notifications = PublicNotifications()
    helper_name = 'StudyCenter-Public-MCP.exe' if sys.platform == 'win32' else 'StudyCenter-Public-MCP'
    helper = Path(sys.executable).parent / 'StudyCenter-Public-MCP' / helper_name
    args = ([str(helper)] if getattr(sys, 'frozen', False) else [sys.executable, '-m', 'studycenter_public.core.mcp_client']) + [str(paths.data), 'chatgpt_web']
    settings.bridge_command = subprocess.list2cmdline(args) if sys.platform == 'win32' else shlex.join(args)
    return settings, secret_store


def create_app(paths, secrets=None):
    from .migration import migrate_legacy
    from .core.app import create_app as create_shared_app
    migration_notice = migrate_legacy(paths)
    settings, secret_store = make_settings(paths, secrets)
    app = create_shared_app(settings)
    app.state.paths, app.state.secrets = paths, secret_store
    app.state.migration_notice = migration_notice
    app.state.tunnel.persistent_secrets = lambda: secret_store.persistent
    from .platform.components import ComponentService
    app.state.components = ComponentService(paths)
    from fastapi import Form, HTTPException, Request
    from fastapi.responses import JSONResponse
    @app.exception_handler(SecretStoreUnavailable)
    async def secret_store_error(request: Request, error: SecretStoreUnavailable):
        return JSONResponse({'detail': str(error)}, status_code=409)
    @app.post('/settings/secrets/session')
    async def session_secrets(confirm: str = Form('')):
        if confirm != 'yes': raise HTTPException(400, 'Sitzungsspeicherung ausdrücklich bestätigen.')
        secret_store.use_session()
        return {'status': secret_store.status, 'message': 'Zugänge bleiben nur für diese Sitzung im Speicher.'}
    @app.post('/settings/model-api')
    async def configure_model_api(base_url: str = Form(''), model: str = Form(''),
                                  api_key: str = Form(''), cloud_consent: str = Form('')):
        import ipaddress
        from urllib.parse import urlsplit
        from .providers.contracts import ProviderProfile
        try:
            profile = ProviderProfile('compatible', model=model.strip(), base_url=base_url.strip())
            if not profile.model: raise ValueError('Modellkennung angeben.')
            host = urlsplit(profile.base_url).hostname
            try: loopback = ipaddress.ip_address(host).is_loopback
            except ValueError: loopback = host == 'localhost'
            if not loopback and cloud_consent != 'yes':
                raise ValueError('Für diesen externen Modelldienst die Übertragung ausdrücklich bestätigen.')
            if len(api_key) > 16000 or any(c in api_key for c in '\r\n'):
                raise ValueError('Ungültiger API-Schlüssel.')
        except ValueError as error:
            raise HTTPException(400, str(error)) from None
        unchanged = profile.base_url == settings.llm_base_url
        if api_key:
            settings.api_store.write({'api_key': api_key})
        elif not unchanged:
            if settings.api_store.read().get('api_key'):
                secret_store.delete('model-api')
        atomic_json(paths.config / 'model-api.json', {'base_url':profile.base_url, 'model':profile.model})
        settings.llm_base_url, settings.llm_model = profile.base_url, profile.model
        settings.llm_api_key = api_key or (settings.llm_api_key if unchanged else '')
        return {'message':'Modellverbindung gespeichert. Anbieter unter Modell auswählen.', 'base_url':profile.base_url, 'model':profile.model}
    @app.get('/setup')
    async def public_setup(request: Request):
        return app.state.render(request, 'public_setup.html', components=app.state.components.status(), secrets=secret_store.status)
    @app.get('/api/components')
    async def component_status():
        return app.state.components.status()
    @app.post('/api/components/install')
    async def component_install(request: Request):
        import asyncio
        value = await request.json()
        try:
            result = await asyncio.to_thread(app.state.components.install, value.get('component'), allow_download=value.get('allow_download') is True)
        except ValueError as error:
            raise HTTPException(400, str(error)) from None
        current = default_ocr(paths)
        settings.tesseract, settings.ocr_lang = current.tesseract, current.ocr_lang
        settings.process_env, settings.working_directory = current.process_env, current.working_directory
        return result
    @app.get('/api/health')
    async def health():
        from . import __version__
        return {'application': 'StudyCenter.Public', 'version': __version__, 'instance_id': paths.instance_id}
    from .legacy import create_legacy_router
    app.include_router(create_legacy_router(paths))
    from .ice_import_ui import create_ice_import_router
    from .ice_migration import MAX_SIZE
    app.state.upload_limits = {'/api/ice-import': MAX_SIZE}
    app.include_router(create_ice_import_router(paths))
    from .onboarding import install
    install(app)
    return app
