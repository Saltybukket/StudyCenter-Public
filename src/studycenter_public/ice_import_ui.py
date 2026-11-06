"""Explicit local ICE import into a new sibling instance."""
from __future__ import annotations

import asyncio
import os
import re
import shlex
import sys
import tempfile
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from .core.async_utils import finish_thread
from .ice_migration import IceImportError, MAX_SIZE, import_ice
from .platform.paths import reject_links, restrict_permissions


def _destination(paths, name):
    # A single portable name, never an arbitrary server-side path.
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,60}', name or '') or name.upper() in {
        'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1, 10)),
        *(f'LPT{i}' for i in range(1, 10)),
    }:
        raise ValueError('Bitte einen gültigen neuen Ordnernamen aus Buchstaben, Ziffern, - oder _ wählen.')
    return reject_links(paths.data.parent / name)


def _launch_command(destination):
    install_root = os.environ.get('STUDYCENTER_PUBLIC_INSTALL_ROOT')
    starter = Path(install_root) / ('start.cmd' if sys.platform == 'win32' else 'start.sh') if install_root else None
    args = [str(starter)] if starter and starter.is_file() else [sys.executable]
    if (not starter or not starter.is_file()) and not getattr(sys, 'frozen', False):
        args += ['-m', 'studycenter_public']
    args += ['--data-dir', str(destination)]
    if sys.platform == 'win32':
        # A command for PowerShell, quoting all literal paths and metacharacters.
        return '& ' + ' '.join("'" + arg.replace("'", "''") + "'" for arg in args)
    return shlex.join(args)


def create_ice_import_router(paths):
    router = APIRouter()
    gate = asyncio.Lock()

    @router.post('/api/ice-import')
    async def ice_import(request: Request):
        if request.headers.get('x-ice-confirm') != 'yes':
            raise HTTPException(400, 'Import in einen neuen Ordner ausdrücklich bestätigen.')
        if gate.locked():
            raise HTTPException(409, 'Ein ICE-Import läuft bereits. Bitte warten.')
        async with gate:
            return await run_import(request)

    async def run_import(request):
        try:
            target = _destination(paths, request.headers.get('x-ice-folder', ''))
            if target.exists():
                raise ValueError('Der Zielordner existiert bereits. Bitte einen neuen Namen wählen.')
            cache = reject_links(paths.cache)
            with tempfile.TemporaryDirectory(prefix='.ice-upload-', dir=cache) as temporary:
                stage = Path(temporary)
                restrict_permissions(stage)
                backup = stage / 'backup.zip'
                size = 0
                with backup.open('xb') as output:
                    async for chunk in request.stream():
                        size += len(chunk)
                        if size > MAX_SIZE:
                            raise HTTPException(413, 'Das Backup ist zu groß.')
                        output.write(chunk)
                result = await finish_thread(import_ice, backup, target)
        except (IceImportError, ValueError) as error:
            raise HTTPException(400, str(error)) from None
        except OSError:
            raise HTTPException(400, 'Der Importordner oder das Backup konnte nicht sicher verarbeitet werden.') from None
        return {'destination': str(target), 'command': _launch_command(target),
                'shell': 'in PowerShell' if sys.platform == 'win32' else 'im Terminal',
                'counts': result['counts']}

    return router
