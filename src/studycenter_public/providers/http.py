"""Bounded network transport shared by protocol adapters and OAuth."""
import asyncio
import json
import httpx
from .contracts import ProviderError


class BoundedHTTP:
    def __init__(self, transport=None, max_bytes=2_000_000, max_seconds=60):
        self.transport = transport
        self.max_bytes = max_bytes
        self.max_seconds = max_seconds

    async def request(self, method, url, **kwargs):
        try:
            async with asyncio.timeout(self.max_seconds):
                async with httpx.AsyncClient(transport=self.transport, follow_redirects=False, trust_env=False, timeout=httpx.Timeout(45, connect=10)) as client:
                    async with client.stream(method, url, **kwargs) as response:
                        if 300 <= response.status_code < 400: raise ProviderError('redirect')
                        if response.status_code in (401,403): raise ProviderError('authentication')
                        if response.status_code == 429: raise ProviderError('rate_limit')
                        if response.status_code >= 500: raise ProviderError('unavailable')
                        if response.status_code >= 400: raise ProviderError('request')
                        data = bytearray()
                        async for chunk in response.aiter_bytes():
                            data.extend(chunk)
                            if len(data) > self.max_bytes: raise ProviderError('response_limit')
                        return bytes(data), response.headers.get('content-type','')
        except (httpx.TimeoutException, TimeoutError): raise ProviderError('timeout') from None
        except httpx.HTTPError: raise ProviderError('network') from None

    async def json(self, method, url, **kwargs):
        data, _ = await self.request(method,url,**kwargs)
        try:
            result = json.loads(data)
            if not isinstance(result, dict): raise ValueError()
            return result
        except (ValueError, UnicodeError): raise ProviderError('protocol') from None
