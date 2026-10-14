"""Explicit, provider-neutral model contracts. No credentials in profiles."""
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import urlsplit
import ipaddress
import re


PRESETS = {
    'openai': 'https://api.openai.com/v1',
    'openai-chat': 'https://api.openai.com/v1',
    'chatgpt': 'https://api.openai.com/v1',
    'anthropic': 'https://api.anthropic.com/v1',
    'gemini': 'https://generativelanguage.googleapis.com/v1beta',
    'gemini-compatible': 'https://generativelanguage.googleapis.com/v1beta/openai',
    'deepseek': 'https://api.deepseek.com',
}


class ProviderError(Exception):
    """Safe error category; never embeds remote bodies or credentials."""
    def __init__(self, code: str):
        self.code = code
        super().__init__('Model provider: ' + code)


@dataclass(frozen=True)
class ProviderProfile:
    provider: str
    model: str = ''
    base_url: str = ''
    profile_id: str = 'default'
    allow_lan: bool = False

    def __post_init__(self):
        if self.provider not in {*PRESETS, 'compatible', 'ollama', 'lmstudio'}:
            raise ValueError('Unknown provider')
        if not re.fullmatch(r'[a-zA-Z0-9_-]{1,100}', self.profile_id):
            raise ValueError('Invalid profile identifier')
        if self.model and not re.fullmatch(r'[a-zA-Z0-9_.:/-]{1,200}', self.model):
            raise ValueError('Invalid model identifier')
        base = self.base_url or PRESETS.get(self.provider, '')
        if not base: raise ValueError('Explicit server URL required')
        parts = urlsplit(base)
        if parts.username or parts.password or parts.query or parts.fragment or not parts.hostname:
            raise ValueError('Server URL must not contain credentials, query or fragment')
        if parts.scheme not in ('http', 'https'): raise ValueError('HTTP(S) URL required')
        try: address = ipaddress.ip_address(parts.hostname)
        except ValueError: address = None
        loopback = parts.hostname == 'localhost' or (address is not None and address.is_loopback)
        lan = address is not None and address.is_private and not address.is_link_local and not address.is_unspecified and not address.is_multicast
        if address is not None and (address.is_link_local or address.is_unspecified or address.is_multicast):
            raise ValueError('Invalid server address')
        if parts.scheme == 'http' and not loopback and not (self.allow_lan and lan):
            raise ValueError('Cleartext HTTP requires explicit local/LAN server')
        if self.provider in ('ollama', 'lmstudio') and not loopback and not (self.allow_lan and lan):
            raise ValueError('Local provider requires own local/LAN server')
        if self.provider == 'chatgpt' and base.rstrip('/') != PRESETS['chatgpt']:
            raise ValueError('ChatGPT OAuth requires the official API')
        if self.provider == 'lmstudio' and parts.path in ('', '/'):
            base = base.rstrip('/') + '/v1'
        object.__setattr__(self, 'base_url', base.rstrip('/'))


@dataclass(frozen=True)
class ModelRequest:
    task: str
    instructions: str
    source_data: Any
    schema: dict | None = None
    max_input_chars: int = 100_000


@dataclass(frozen=True)
class ModelResult:
    content: Any
    complete: bool
    provider: str
    model: str
    usage: dict = field(default_factory=dict)


class ModelProvider(Protocol):
    async def list_models(self) -> list[dict]: ...
    async def probe(self) -> dict: ...
    async def generate(self, request: ModelRequest) -> ModelResult: ...
