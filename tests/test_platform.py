from pathlib import Path
import os
from uuid import UUID
import pytest

from studycenter_public.platform.paths import AppPaths
from studycenter_public.platform.secrets import SecretStore, SecretStoreUnavailable

def test_isolated_empty_paths_reuse_only_own_identity(tmp_path, monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'unrelated-secret')
    paths = AppPaths.create(tmp_path / 'Eigene Daten ü')
    again = AppPaths.create(paths.data)
    assert paths.instance_id == again.instance_id
    assert paths.host_id == again.host_id
    assert not paths.database.exists()
    assert paths.profiles.is_relative_to(paths.data)
    assert UUID(paths.instance_id)

def test_session_storage_is_explicit_and_does_not_write_secrets(tmp_path):
    store = SecretStore('00000000-0000-4000-8000-000000000001', session_only=True)
    store.set('provider/default', 'my-secret')
    assert store.get('provider/default') == 'my-secret'
    store.delete('provider/default')
    assert store.get('provider/default') is None
    assert store.persistent is False

def test_no_unsafe_backend_plaintext_fallback():
    store = SecretStore('00000000-0000-4000-8000-000000000001', backend=False)
    with pytest.raises(SecretStoreUnavailable): store.set('provider/default', 'secret')
    with pytest.raises(SecretStoreUnavailable): store.delete('provider/default')

def test_foreign_data_folder_remains_untouched(tmp_path):
    folder=tmp_path/'existing-data';folder.mkdir();file=folder/'existing.txt';file.write_text('keep',encoding='utf-8')
    with pytest.raises(ValueError,match='nicht leere'):AppPaths.create(folder)
    assert file.read_text(encoding='utf-8')=='keep'
    assert sorted(p.name for p in folder.iterdir())==['existing.txt']

def test_foreign_identity_is_rejected_before_creating_subdirectories(tmp_path):
    import json
    folder=tmp_path/'foreign';folder.mkdir()
    identity={'instance_id':'00000000-0000-4000-8000-000000000001','host_id':'urn:uuid:00000000-0000-4000-8000-000000000002'}
    (folder/'instance.json').write_text(json.dumps(identity))
    with pytest.raises(ValueError):AppPaths.create(folder)
    assert sorted(p.name for p in folder.iterdir())==['instance.json']
    identity['application']='Unrelated.Application'
    (folder/'instance.json').write_text(json.dumps(identity))
    with pytest.raises(ValueError):AppPaths.create(folder)
    assert sorted(p.name for p in folder.iterdir())==['instance.json']

def test_chunked_credentials_are_namespaced_and_do_not_collide():
    class MemoryKeyring:
        priority = 1
        def __init__(self): self.values = {}
        def set_password(self, s, u, v): self.values[s,u] = v
        def get_password(self, s, u): return self.values.get((s,u))
        def delete_password(self,s,u): self.values.pop((s,u),None)
    backend=MemoryKeyring()
    a=SecretStore('00000000-0000-4000-8000-000000000001',backend=backend)
    b=SecretStore('00000000-0000-4000-8000-000000000002',backend=backend)
    a.set('oauth/account', 'ü'*7000)
    assert a.get('oauth/account') == 'ü'*7000
    assert b.get('oauth/account') is None
    assert all(len(v)<1200 for v in backend.values.values())
    a.delete('oauth/account')
    assert not backend.values
