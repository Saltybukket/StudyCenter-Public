from pathlib import Path
from types import SimpleNamespace
import pytest

from studycenter_public.platform.paths import AppPaths
from studycenter_public.storage.db import Database
from studycenter_public.services.documents import DocumentService
from studycenter_public.services.backup import BackupService


@pytest.mark.parametrize('operation',['import','restore','backup'])
def test_reparse_document_or_backup_ancestor_blocks_before_writes(tmp_path,monkeypatch,operation):
    paths=AppPaths.create(tmp_path/'own')
    db=Database(paths.database)
    service=DocumentService(db,paths)
    backup=BackupService(db,paths)
    archive=backup.create(tmp_path/'valid.zip')
    blocked=paths.data/'backups' if operation=='backup' else paths.documents
    original=Path.lstat
    def lstat(path,*args,**kwargs):
        info=original(path,*args,**kwargs)
        return SimpleNamespace(st_mode=info.st_mode,st_file_attributes=0x400) if path==blocked else info
    before={path.relative_to(paths.data).as_posix():path.read_bytes() for path in paths.data.rglob('*') if path.is_file()}
    monkeypatch.setattr(Path,'lstat',lstat)
    with pytest.raises(ValueError,match='Verknüpfung|Reparse'):
        if operation=='import':service.import_bytes(b'Synthetic original',title='Synthetic',mime='text/plain')
        elif operation=='restore':backup.restore(archive,confirmed=True)
        else:backup.create()
    after={path.relative_to(paths.data).as_posix():path.read_bytes() for path in paths.data.rglob('*') if path.is_file()}
    assert before==after
