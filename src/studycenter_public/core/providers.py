"""OpenAI-compatible providers; never fall back to another host or account."""
import ipaddress
from urllib.parse import urlsplit

import httpx

from .chatgpt import ChatGPTError


def validate_endpoint(value):
    value = value.strip().rstrip('/')
    parts = urlsplit(value)
    if parts.scheme not in ('http', 'https') or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError('API-Basis-URL mit http(s), ohne Zugangsdaten, Query oder Fragment angeben.')
    try:
        parts.port
    except ValueError:
        raise ValueError('Ungültiger API-Port.') from None
    if parts.scheme == 'http':
        try:
            local = ipaddress.ip_address(parts.hostname).is_private or ipaddress.ip_address(parts.hostname).is_loopback
        except ValueError:
            local = parts.hostname in ('localhost',) or parts.hostname.endswith(('.local', '.localhost'))
        if not local:
            raise ValueError('Für externe APIs HTTPS verwenden; HTTP ist für lokale Server und private IP-Adressen verfügbar.')
    if parts.path.endswith('/chat/completions'):
        value = value[:-len('/chat/completions')]
    return value


async def completion(settings, instructions, payload):
    base = validate_endpoint(settings.llm_base_url)
    headers = {'Authorization': 'Bearer ' + settings.llm_api_key} if settings.llm_api_key else {}
    body = {'model': settings.llm_model, 'messages': [
        {'role': 'system', 'content': instructions}, {'role': 'user', 'content': payload}], 'stream': False}
    if getattr(settings, 'llm_json_mode', False):
        body['response_format'] = {'type': 'json_object'}
    # Explicit endpoint only; redirects and proxy environment must not reroute local data.
    try:
        async with httpx.AsyncClient(timeout=300, trust_env=False, follow_redirects=False) as client:
            response = await client.post(base + '/chat/completions', headers=headers, json=body)
    except httpx.TimeoutException:
        raise ChatGPTError('Modell antwortet nicht innerhalb von 300 Sekunden. Serverlast oder Modellgröße prüfen.') from None
    except httpx.HTTPError:
        raise ChatGPTError('Modellserver nicht erreichbar. API-Adresse und gestarteten Server prüfen.') from None
    if not 200 <= response.status_code < 300:
        # Never expose provider bodies: they can contain echoed prompts, URLs or credentials.
        hints = {400: 'Modellparameter oder Kontextlänge prüfen; gegebenenfalls JSON-Modus ausschalten.',
                 401: 'API-Schlüssel prüfen.', 403: 'Modellzugriff und API-Schlüssel prüfen.',
                 404: 'API-Basis-URL und Modellkennung prüfen.', 429: 'Anbieterlimit erreicht; später erneut versuchen.'}
        raise ChatGPTError(f'Modell-API HTTP {response.status_code}. ' + hints.get(response.status_code, 'Anbieter oder Server prüfen.'))
    try:
        data = response.json()
        choice = data['choices'][0]
        if choice.get('finish_reason') in ('length', 'content_filter'):
            raise ChatGPTError('Modellantwort wurde abgebrochen oder gefiltert; keine Änderung übernommen.')
        content = choice['message']['content']
        if not isinstance(content, str) or not content.strip():
            raise ValueError()
    except (ValueError, KeyError, IndexError, TypeError):
        raise ChatGPTError('Modell-API lieferte keine verwendbare Textantwort.') from None
    # A whole fenced JSON answer is common with small instruct models. Do not
    # extract arbitrary substrings or discard reasoning text to manufacture validity.
    content = content.strip()
    if content.startswith('```json\n') and content.endswith('\n```'):
        content = content[8:-4].strip()
    return content
