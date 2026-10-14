import pytest
from pathlib import Path
from studycenter_public.platform.paths import AppPaths
from studycenter_public.platform.secrets import SecretStore
from studycenter_public.platform.cleanup import purge_public_data
from studycenter_public.platform.secrets import SecretStoreUnavailable


class MemoryKeyring:
    def __init__(self):self.values={};self.deletes=[]
    def get_password(self,service,user):return self.values.get((service,user))
    def set_password(self,service,user,value):self.values[service,user]=value
    def delete_password(self,service,user):self.deletes.append((service,user));self.values.pop((service,user),None)


def test_unknown_nested_document_blocks_before_credentials_are_removed(tmp_path):
    paths=AppPaths.create(tmp_path/'own')
    backend=MemoryKeyring();secrets=SecretStore(paths.instance_id,backend=backend)
    secrets.set('mail','synthetic')
    unrelated=paths.documents/'user-kept.txt';unrelated.write_text('keep')
    with pytest.raises(ValueError):purge_public_data(paths,secrets,confirmed=True)
    assert unrelated.read_text()=='keep'
    assert (paths.data/'instance.json').exists()
    assert secrets.get('mail')=='synthetic'
    assert backend.deletes==[]


def test_unavailable_persistent_backend_blocks_and_keeps_identity(tmp_path):
    paths=AppPaths.create(tmp_path/'own')
    backend=MemoryKeyring();secrets=SecretStore(paths.instance_id,backend=backend)
    secrets.set('mail','synthetic');secrets._backend=False
    with pytest.raises(SecretStoreUnavailable):purge_public_data(paths,secrets,confirmed=True)
    assert (paths.data/'instance.json').exists()
    assert backend.values and not backend.deletes

def test_only_explicit_owned_data_and_credentials_are_removed(tmp_path):
    from studycenter_public.storage.db import Database
    from studycenter_public.services.documents import DocumentService
    from studycenter_public.platform.cleanup import cleanup_preflight
    paths=AppPaths.create(tmp_path/'own-public')
    secrets=SecretStore(paths.instance_id,backend=MemoryKeyring())
    secrets.set('mail','synthetic own secret')
    db=Database(paths.database)
    document=DocumentService(db,paths).import_bytes(b'Synthetic retained original',title='Original',mime='text/plain')
    original=Path(document['original_path'])
    neighbor=tmp_path/'neighbor';neighbor.mkdir();(neighbor/'keep.txt').write_text('keep')
    with pytest.raises(ValueError):purge_public_data(paths,secrets)
    assert paths.database.exists()
    assert cleanup_preflight(paths,secrets,confirmed=True)['ready']
    assert original.exists() and secrets.get('mail') is not None
    assert purge_public_data(paths,secrets,confirmed=True)['removed_own_data']
    assert secrets.get('mail') is None
    assert (neighbor/'keep.txt').read_text()=='keep'
    assert not paths.database.exists()
    assert not original.exists()

def test_unrecognized_instance_and_links_fail_before_data_removal(tmp_path):
    paths=AppPaths.create(tmp_path/'public')
    (paths.data/'instance.json').write_text('{"application":"different","instance_id":"different"}')
    with pytest.raises(ValueError):purge_public_data(paths,SecretStore(paths.instance_id,session_only=True),confirmed=True)
    assert paths.documents.exists()


@pytest.mark.parametrize('directory',['cache','profiles','logs','data'])
def test_unknown_descendants_in_every_managed_area_block_deletion(tmp_path,directory):
    paths=AppPaths.create(tmp_path/'own')
    backend=MemoryKeyring();secrets=SecretStore(paths.instance_id,backend=backend)
    secrets.set('mail','synthetic')
    unknown=getattr(paths,directory)/'user-kept.txt';unknown.write_text('keep')
    with pytest.raises(ValueError,match='Dateibesitz'):purge_public_data(paths,secrets,confirmed=True)
    assert unknown.read_text()=='keep' and (paths.data/'instance.json').exists()
    assert not backend.deletes


def test_registered_original_hash_mismatch_blocks_before_credential_deletion(tmp_path):
    from studycenter_public.storage.db import Database
    from studycenter_public.services.documents import DocumentService
    paths=AppPaths.create(tmp_path/'own')
    db=Database(paths.database)
    document=DocumentService(db,paths).import_bytes(b'Original',title='Original',mime='text/plain')
    Path(document['original_path']).write_bytes(b'User replaced contents')
    backend=MemoryKeyring();secrets=SecretStore(paths.instance_id,backend=backend)
    with pytest.raises(ValueError,match='SHA256'):purge_public_data(paths,secrets,confirmed=True)
    assert not backend.deletes and paths.database.exists()
    assert (paths.data/'instance.json').exists()


def test_session_only_mode_does_not_claim_removal_of_prior_os_credentials(tmp_path):
    paths=AppPaths.create(tmp_path/'own')
    backend=MemoryKeyring();persistent=SecretStore(paths.instance_id,backend=backend)
    persistent.set('mail','synthetic previous OS secret')
    session=SecretStore(paths.instance_id,session_only=True,backend=backend)
    session.set('mail','synthetic current session')
    with pytest.raises(SecretStoreUnavailable):purge_public_data(paths,session,confirmed=True)
    assert persistent.get('mail')=='synthetic previous OS secret'
    assert session.get('mail')=='synthetic current session'
    assert not backend.deletes and (paths.data/'instance.json').exists()


def test_reparse_point_blocks_preflight_before_credentials(tmp_path,monkeypatch):
    from types import SimpleNamespace
    paths=AppPaths.create(tmp_path/'own')
    backend=MemoryKeyring();secrets=SecretStore(paths.instance_id,backend=backend)
    nested=paths.documents/'linked';nested.mkdir()
    original=Path.lstat
    def lstat(path,*args,**kwargs):
        value=original(path,*args,**kwargs)
        return SimpleNamespace(st_mode=value.st_mode,st_file_attributes=0x400) if path==nested else value
    monkeypatch.setattr(Path,'lstat',lstat)
    with pytest.raises(ValueError,match='Reparse'):purge_public_data(paths,secrets,confirmed=True)
    assert not backend.deletes and nested.exists()


def test_validated_generated_backup_is_removed_with_registered_originals(tmp_path):
    from studycenter_public.storage.db import Database
    from studycenter_public.services.documents import DocumentService
    from studycenter_public.services.backup import BackupService
    from studycenter_public.platform.cleanup import cleanup_preflight
    paths=AppPaths.create(tmp_path/'own')
    db=Database(paths.database)
    DocumentService(db,paths).import_bytes(b'Original',title='Original',mime='text/plain')
    backup=BackupService(db,paths).create()
    secrets=SecretStore(paths.instance_id,backend=MemoryKeyring())
    assert cleanup_preflight(paths,secrets,confirmed=True)['ready']
    assert backup.exists()
    assert purge_public_data(paths,secrets,confirmed=True)['removed_own_data']
    assert not backup.exists()


def test_inaccessible_keyring_read_blocks_before_any_delete(tmp_path):
    from studycenter_public.platform.cleanup import cleanup_preflight
    paths=AppPaths.create(tmp_path/'own')
    class Unreadable(MemoryKeyring):
        def get_password(self,*args):raise RuntimeError('Synthetic unavailable backend')
    backend=Unreadable();secrets=SecretStore(paths.instance_id,backend=backend)
    with pytest.raises(SecretStoreUnavailable):cleanup_preflight(paths,secrets,confirmed=True)
    assert not backend.deletes and (paths.data/'instance.json').exists()
