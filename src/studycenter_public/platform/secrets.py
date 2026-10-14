"""OS keyring or explicit memory-only sessions. No plaintext fallback."""
import json
import re
import uuid

class SecretStoreUnavailable(RuntimeError):
    pass

class SecretStore:
    def __init__(self, instance_id, session_only=False, backend=None):
        uuid.UUID(instance_id)
        self.instance_id=instance_id
        self.session_only=session_only
        self._session={}
        self._backend=backend
        if backend is None and not session_only:
            try:
                import keyring
                candidate=keyring.get_keyring()
                module=type(candidate).__module__
                allowed=('keyring.backends.Windows','keyring.backends.SecretService','keyring.backends.kwallet')
                # Reject optional file/plaintext/fallback/chainer backends.
                if module.startswith(allowed) and candidate.priority>0: self._backend=candidate
                elif module=='keyring.backends.chainer':
                    # Select a functioning secure child, never a chained plaintext fallback.
                    from keyring.backend import get_all_keyring
                    choices=[]
                    for secure in get_all_keyring():
                        if not type(secure).__module__.startswith(allowed):continue
                        try:
                            if secure.priority>0:choices.append(secure)
                        except Exception:pass
                    if choices:self._backend=max(choices,key=lambda secure:secure.priority)
            except Exception: self._backend=False

    @property
    def persistent(self): return bool(self._backend and not self.session_only)

    @property
    def status(self):
        return 'OS-Schlüsselspeicher' if self.persistent else ('Nur diese Sitzung' if self.session_only else 'Schlüsselspeicher nicht verfügbar; Sitzungsspeicherung ausdrücklich wählen.')

    def _name(self,service):
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9/_-]{0,160}',service): raise ValueError('Ungültiger Dienstname')
        return f'StudyCenter-Public/{self.instance_id}/{service}'

    def get(self,service):
        name=self._name(service)
        if self.session_only: return self._session.get(service)
        if not self.persistent: return None
        try:
            raw=self._backend.get_password(name,'manifest')
            if not raw: return None
            manifest=json.loads(raw)
            values=[self._backend.get_password(name,f"{manifest['generation']}/{i}") for i in range(manifest['parts'])]
            if any(v is None for v in values): raise SecretStoreUnavailable('Zugang unvollständig; neu verbinden.')
            return ''.join(values)
        except SecretStoreUnavailable: raise
        except Exception: raise SecretStoreUnavailable('Schlüsselspeicher nicht zugänglich; neu verbinden.') from None

    def set(self,service,value):
        name=self._name(service)
        if not isinstance(value,str) or len(value)>200000: raise ValueError('Zugang zu groß oder ungültig')
        if self.session_only: self._session[service]=value; return
        if not self.persistent: raise SecretStoreUnavailable(self.status)
        previous=self._backend.get_password(name,'manifest')
        generation=uuid.uuid4().hex
        parts=[value[i:i+800] for i in range(0,len(value),800)] or ['']
        try:
            for i,part in enumerate(parts): self._backend.set_password(name,f'{generation}/{i}',part)
            self._backend.set_password(name,'manifest',json.dumps({'generation':generation,'parts':len(parts)}))
        except Exception:
            for i in range(len(parts)):
                try:self._backend.delete_password(name,f'{generation}/{i}')
                except Exception:pass
            raise SecretStoreUnavailable('Zugang konnte nicht sicher gespeichert werden.') from None
        if previous:
            old=json.loads(previous)
            for i in range(old['parts']):
                try:self._backend.delete_password(name,f"{old['generation']}/{i}")
                except Exception:pass

    def delete(self,service):
        name=self._name(service)
        if self.session_only: self._session.pop(service,None);return
        if not self.persistent:raise SecretStoreUnavailable('Schlüsselspeicher nicht verfügbar; Zugang wurde nicht entfernt.')
        try:
            raw=self._backend.get_password(name,'manifest')
            if raw:
                manifest=json.loads(raw)
                for i in range(manifest['parts']):self._backend.delete_password(name,f"{manifest['generation']}/{i}")
                self._backend.delete_password(name,'manifest')
        except Exception:raise SecretStoreUnavailable('Zugang konnte nicht entfernt werden.') from None
