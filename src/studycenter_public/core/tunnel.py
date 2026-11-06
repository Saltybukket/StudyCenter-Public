"""Local supervision of the existing OpenAI tunnel; never registers a remote tunnel."""
import asyncio
import json
import os
import re
import subprocess
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import portalocker

from .config import ROOT

# The user configures their own tunnel; no pre-existing identity is bundled.
ALIAS = 'studycenter-public'
NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


def atomic_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    try:
        temporary.write_bytes(data)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def connection_state(status, metrics, mcp_ok, current=None):
    current = time.time() if current is None else current
    match = re.search(r'^commands_poll_last_successful_timestamp_seconds(?:\{[^\n]*\})?\s+(\S+)', metrics, re.M)
    successful = float(match[1]) if match else 0
    # Local readiness is not authentication. A completed long poll is the proof.
    online = successful > 0 and 0 <= current - successful < 100
    channel = any(c.get('name') == 'main' and c.get('enabled') and c.get('probe_status') == 'ok'
                  for c in status.get('channels', []))
    return {'openai': online, 'mcp': bool(mcp_ok and channel),
            'connected': bool(online and mcp_ok and channel), 'last_poll': successful}


class TunnelService:
    def __init__(self, directory, secret_store, mcp_store, endpoint):
        self.directory = Path(directory)
        self.secret_store, self.mcp_store, self.endpoint = secret_store, mcp_store, endpoint
        if self.directory.resolve().is_relative_to(ROOT):
            raise ValueError('Tunnel-Zugang muss außerhalb des Repository liegen.')
        self.config_path = self.directory / 'settings.json'
        self.gate = asyncio.Lock()
        self.stop_event = asyncio.Event()
        self.wanted = False
        self.error = ''
        self.retry_at = 0
        self.retry_delay = 5
        self.state = {'state': 'Prüfung ausstehend', 'connected': False, 'openai': False, 'mcp': False}

    def settings(self):
        defaults = {'auto_start': False, 'executable': '', 'tunnel_id': ''}
        if self.config_path.exists():
            defaults.update(json.loads(self.config_path.read_text(encoding='utf-8')))
        return defaults

    def public(self):
        try: saved = bool(self.secret_store.read().get('key'))
        except RuntimeError as error:
            return self.state | self.settings() | {'key_saved': False, 'error': str(error), 'wanted': self.wanted}
        return self.state | self.settings() | {'key_saved': saved, 'error': self.error, 'wanted': self.wanted}

    def key(self):
        key = self.secret_store.read().get('key', '')
        if not key: raise ValueError('Runtime-Key einmal lokal eingeben und sicher speichern.')
        return key

    def environment(self, key=None):
        env = os.environ.copy()
        # Never inherit an unrelated API/admin key or a debug override.
        for name in list(env):
            if name.startswith(('CONTROL_PLANE_', 'OPENAI_', 'LOG_', 'MCP_', 'HEALTH_', 'ADMIN_UI_', 'TUNNEL_CLIENT_')):
                env.pop(name)
        env['TUNNEL_CLIENT_STATE_DIR'] = str(self.directory / 'runtime')
        env['PYTHONPATH'] = str(ROOT.parent)
        env['PYTHONIOENCODING'] = 'utf-8'
        if key:
            env['CONTROL_PLANE_API_KEY'] = key
        return env

    async def cli(self, *args, key=None):
        executable = Path(self.settings()['executable'])
        if not executable.is_file() or executable.name.lower() not in ('tunnel-client.exe', 'tunnel-client'):
            raise ValueError('Bitte den vorhandenen Pfad zum eigenen tunnel-client einstellen.')
        def invoke():
            return subprocess.run([str(executable), 'runtimes', *args, '--json'], cwd=ROOT,
                                  env=self.environment(key), capture_output=True, timeout=45,
                                  creationflags=NO_WINDOW)
        try:
            result = await asyncio.to_thread(invoke)
            if args[0] == 'status' and result.returncode and not result.stdout.strip():
                return {}
            value = json.loads(result.stdout)
        except (OSError, subprocess.TimeoutExpired, ValueError):
            raise ValueError('Native Tunnel-Verwaltung fehlgeschlagen. Client-Pfad und Version prüfen.') from None
        # Native output may contain diagnostic logs. Never expose or persist it.
        if result.returncode and args[0] != 'status':
            raise ValueError('Tunnel konnte nicht gestartet/gestoppt werden. Lokalen Status prüfen.')
        return value

    async def validate_key(self, key, tunnel_id=None):
        tunnel_id = tunnel_id or self.settings()['tunnel_id']
        if not re.fullmatch(r'tunnel_[A-Za-z0-9]+', tunnel_id): raise ValueError('Eigene Tunnel-ID angeben.')
        if not key or len(key) > 4096 or any(c.isspace() for c in key):
            raise ValueError('Vollständigen Runtime-Key ohne Leerzeichen lokal eingeben.')
        try:
            async with httpx.AsyncClient(timeout=15, trust_env=False) as client:
                response = await client.get('https://api.openai.com/v1/tunnels/' + tunnel_id,
                                            headers={'Authorization': 'Bearer ' + key})
        except httpx.HTTPError:
            raise ValueError('OpenAI momentan nicht erreichbar; Key wurde nicht ersetzt.') from None
        if response.status_code != 200:
            raise ValueError(f'OpenAI verweigert Tunnel-Zugriff (HTTP {response.status_code}). Runtime-Key und Tunnels Read + Use prüfen.')

    async def configure(self, executable, auto_start, key='', tunnel_id=''):
        async with self.gate:
            path = Path(executable.strip().strip('"')).resolve()
            if not path.is_file() or path.name.lower() not in ('tunnel-client.exe', 'tunnel-client'):
                raise ValueError('tunnel-client wurde unter diesem Pfad nicht gefunden.')
            if self.state.get('managed') and path != Path(self.settings()['executable']).resolve():
                raise ValueError('Den verwalteten Tunnel vor einem Client-Pfadwechsel stoppen.')
            key = key.strip()
            tunnel_id = tunnel_id.strip() or self.settings()['tunnel_id']
            if not re.fullmatch(r'tunnel_[A-Za-z0-9]+', tunnel_id): raise ValueError('Eigene Tunnel-ID angeben.')
            if key:
                await self.validate_key(key, tunnel_id)
                self.secret_store.write({'key': key})
            atomic_write(self.config_path, json.dumps({'auto_start': auto_start, 'executable': str(path), 'tunnel_id': tunnel_id}).encode())
            self.error = ''

    async def runtimes(self):
        if not (self.directory / 'runtime/aliases').exists():
            # status also handles absent aliases, but avoid spawning CLI before setup.
            if not self.config_path.exists():
                return {}
        value = await self.cli('status', ALIAS)
        return value

    async def competing(self, own_pid=0):
        # Runtime CLI is scoped by the Public-owned state directory and alias.
        # Never inspect or stop other applications' tunnel processes.
        return []

    async def mcp_probe(self):
        try:
            token = self.mcp_store.read().get('chatgpt_web')
            if not token:
                return False
            async with httpx.AsyncClient(timeout=5, trust_env=False) as client:
                response = await client.post(self.endpoint() + '/mcp',
                    headers={'Authorization': 'Bearer ' + token},
                    json={'jsonrpc': '2.0', 'id': 'tunnel-health', 'method': 'tools/list'})
            return response.status_code == 200 and any(t['name'] == 'list_courses' for t in response.json().get('result', {}).get('tools', []))
        except (httpx.HTTPError, OSError, RuntimeError, ValueError):
            return False

    async def refresh(self):
        value = await self.runtimes()
        pid = value.get('process', {}).get('pid', value.get('pid', 0)) if value.get('process_running') else 0
        external = await self.competing(pid)
        base = value.get('health_url', '')
        if external and not pid:
            base = 'http://127.0.0.1:18080'
        parsed = urlsplit(base)
        state = {'state': 'Gestoppt', 'managed': bool(pid), 'pid': pid,
                 'external': bool(external), 'connected': False, 'openai': False, 'mcp': False, 'last_poll': 0, 'ui_url': ''}
        if pid or external:
            state['state'] = 'Manuell gestartet' if external and not pid else 'Verbindung wird geprüft'
            if parsed.scheme == 'http' and parsed.hostname == '127.0.0.1' and parsed.port:
                base = f'http://127.0.0.1:{parsed.port}'
                try:
                    async with httpx.AsyncClient(timeout=4, trust_env=False) as client:
                        status = (await client.get(base.rstrip('/') + '/api/status')).json()
                        metrics = (await client.get(base.rstrip('/') + '/metrics')).text
                    if status.get('control_plane_tunnel_id') == self.settings()['tunnel_id']:
                        state.update(connection_state(status, metrics, await self.mcp_probe()))
                        state['ui_url'] = base.rstrip('/') + '/ui'
                        if state['connected']:
                            state['state'] = 'Verbunden · manuell gestartet' if external and not pid else 'Verbunden'
                        else:
                            state['state'] = 'OpenAI-Verbindung ausstehend' if not state['openai'] else 'MCP nicht erreichbar / Leserecht fehlt'
                except (httpx.HTTPError, ValueError):
                    state['state'] = 'Lokaler Tunnel nicht erreichbar'
        if external and pid:
            state['state'] = 'Mehrere Tunnel erkannt · manuelle Instanz beenden'
            state['connected'] = False
        state['checked_at'] = time.time()
        self.state = state
        if pid and getattr(self,'ownership_directory',None):
            pass  # Public runtime owns its own lifecycle.
        if state['connected']:
            self.error = ''
        return state

    async def start(self, takeover=False, automatic=False):
        async with self.gate:
            if automatic and (not self.wanted or self.stop_event.is_set()):
                return
            self.directory.mkdir(parents=True, exist_ok=True)
            with portalocker.Lock(str(self.directory / 'start.lock'), timeout=0):
                native = await self.runtimes()
                pid = native.get('process', {}).get('pid', native.get('pid', 0)) if native.get('process_running') else 0
                other = await self.competing(pid)
                if other and not takeover:
                    raise ValueError('Ein manueller Tunnel läuft bereits. Zum Wechsel „Manuellen Tunnel ablösen“ verwenden.')
                if native.get('process_running') and not other:
                    self.wanted = True
                    return
                if not self.persistent_secrets():
                    raise ValueError('Tunnel benötigt den OS-Schlüsselspeicher: Der separate MCP-Prozess kann Sitzungsspeicher nicht lesen.')
                key = self.key()
                await self.validate_key(key)
                if not await self.mcp_probe():
                    raise ValueError('ChatGPT-Web-MCP nicht erreichbar. Bestehenden lokalen Zugang und Leserecht prüfen.')
                if native.get('process_running'):
                    self.wanted = True
                    return
                command = self.bridge_command
                await self.cli('connect', '--alias', ALIAS, '--tunnel-id', self.settings()['tunnel_id'],
                               '--profile', ALIAS, '--profile-dir', str(self.directory / 'runtime/profiles'),
                               '--mcp-command', command, '--runtime-api-key', 'env:CONTROL_PLANE_API_KEY', key=key)
                self.wanted = True
                self.error = ''
                self.retry_delay = 5
        await self.refresh()

    async def stop(self):
        async with self.gate:
            self.wanted = False
            if self.config_path.exists():
                value = await self.runtimes()
                if value.get('process_running'):
                    await self.cli('stop', ALIAS)
            self.error = ''
        await self.refresh()

    async def run(self):
        self.wanted = self.settings()['auto_start']
        try:
            while not self.stop_event.is_set():
                try:
                    if self.config_path.exists():
                        async with self.gate:
                            await self.refresh()
                    if self.wanted and not self.state.get('managed') and not self.state.get('external') and time.monotonic() >= self.retry_at:
                        await self.start(automatic=True)
                        self.retry_at = time.monotonic() + self.retry_delay
                except Exception as error:
                    self.error = str(error) if isinstance(error, ValueError) else 'Tunnel-Prüfung fehlgeschlagen; erneuter Versuch folgt.'
                    self.state.update(connected=False, openai=False, mcp=False, state='Tunnel-Prüfung fehlgeschlagen')
                    self.retry_at = time.monotonic() + self.retry_delay
                    self.retry_delay = min(self.retry_delay * 2, 300)
                try:
                    await asyncio.wait_for(self.stop_event.wait(), timeout=10)
                except TimeoutError:
                    pass
        finally:
            # Managed runtimes survive their launcher. Stop only our named alias.
            try:
                await self.stop()
            except Exception:
                pass
