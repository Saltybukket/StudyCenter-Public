"""Voluntary program updates; never a source sync or an unattended installation."""
import asyncio
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timezone
import time

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from .core.async_utils import finish_thread, settle_cancelled
from .core.backup import create_backup
from .update_catalog import UpdateCatalog, UpdateError
from . import update_install

ERROR_MESSAGES = {
    'network': 'Die Release-Seite ist gerade nicht erreichbar. Bitte später erneut prüfen.',
    'check_failed': 'Die Update-Prüfung ist fehlgeschlagen. Bitte erneut versuchen.',
    'download_failed': 'Der Download ist fehlgeschlagen. Die installierte Version bleibt unverändert.',
    'checksum': 'Die heruntergeladene Datei ist beschädigt oder stimmt nicht mit der Prüfsumme überein. Bitte erneut herunterladen.',
    'not_ready': 'Bitte diese Version zuerst vollständig herunterladen.',
    'missing_assets': 'Das neue Release ist noch nicht vollständig für dieses System veröffentlicht. Bitte später erneut prüfen.',
    'redirect': 'Der Download wurde auf eine nicht erlaubte Adresse umgeleitet.',
    'metadata': 'Die Versionsinformationen sind ungültig.',
    'release': 'Die Versionsinformationen sind ungültig. Bitte erneut nach Updates suchen.',
    'version': 'Diese Version ist nicht mehr zur Installation verfügbar. Bitte erneut nach Updates suchen.',
    'unsupported_platform': 'Für dieses Betriebssystem oder diese Architektur ist kein Update-Paket verfügbar.',
    'size': 'Die Update-Datei überschreitet die erlaubte Größe.',
    'preferences': 'Die Update-Einstellung ist ungültig.',
    'capability': 'Die Update-Informationen passen nicht zur Installationsdatei. Bitte erneut herunterladen.',
}


def error_message(error):
    if isinstance(error, UpdateError):
        return ERROR_MESSAGES.get(error.code, 'Die Update-Aktion konnte nicht abgeschlossen werden. Bitte erneut versuchen.')
    return str(error)


class Preferences(BaseModel):
    model_config = ConfigDict(extra='forbid')
    auto_check: StrictBool


class ReleaseChoice(BaseModel):
    model_config = ConfigDict(extra='forbid')
    version: str = Field(min_length=1, max_length=80)


class InstallChoice(ReleaseChoice):
    confirm: StrictBool


def install(app, paths):
    catalog = UpdateCatalog(paths)
    app.state.updates = catalog
    job = None
    installing = False
    error = ''
    backup = None
    next_auto_attempt = 0.0

    def status():
        value = catalog.status()
        supported, reason = update_install.installation_support(paths)
        if value.get('state') == 'ready' and not value.get('installer_compatible'):
            supported = False
            reason = 'Dieses ältere Release unterstützt den sicheren App-Updater noch nicht. Bitte über die Release-Seite manuell installieren.'
        value.update(can_install=supported, install_reason=reason, backup=backup,
                     outcome=update_install.outcome_status(paths))
        if value.get('error'):
            value['error'] = ERROR_MESSAGES.get(value['error'], 'Die Update-Aktion ist fehlgeschlagen.')
        if installing:
            value['state'] = 'installing'
        elif error:
            value.update(state='error', error=error)
        return value

    async def run_job(operation):
        nonlocal error, next_auto_attempt
        try:
            await operation()
        except asyncio.CancelledError:
            raise
        except (UpdateError, OSError, ValueError) as exc:
            error = error_message(exc)
        finally:
            next_auto_attempt = time.time() + 86400

    def start_job(operation, state):
        nonlocal job, error
        if installing:
            raise HTTPException(409, 'Installation wird bereits vorbereitet.')
        if job is not None and not job.done():
            raise HTTPException(409, 'Eine Update-Prüfung oder ein Download läuft bereits.')
        error = ''
        job = asyncio.create_task(run_job(operation))
        return status() | {'state': state}

    @app.get('/api/updates', include_in_schema=False)
    async def update_status():
        # Reading the UI status does not make a remote request.
        return status()

    @app.post('/api/updates/preferences', include_in_schema=False)
    async def update_preferences(value: Preferences):
        try:
            catalog.set_preferences(value.auto_check)
        except (UpdateError, OSError, ValueError) as exc:
            raise HTTPException(400, error_message(exc)) from None
        return status()

    @app.post('/api/updates/check', include_in_schema=False)
    async def check_updates():
        return start_job(catalog.check, 'checking')

    @app.post('/api/updates/skip', include_in_schema=False)
    async def skip_update(value: ReleaseChoice):
        nonlocal error
        if job is not None and not job.done():
            raise HTTPException(409, 'Bitte die laufende Update-Aktion abwarten.')
        try:
            catalog.skip(value.version)
            error = ''
        except (UpdateError, OSError, ValueError) as exc:
            raise HTTPException(400, error_message(exc)) from None
        return status()

    @app.post('/api/updates/download', include_in_schema=False)
    async def download_update(value: ReleaseChoice):
        return start_job(lambda: catalog.download(value.version), 'downloading')

    async def perform_install(version):
        nonlocal installing, error, backup
        if installing or (job is not None and not job.done()):
            raise HTTPException(409, 'Eine Update-Aktion läuft bereits.')
        if not hasattr(app.state, 'stop_callback'):
            raise HTTPException(409, 'Installation nur in der regulär gestarteten Public-App möglich.')
        running = app.state.product_update_activities()
        if running:
            raise HTTPException(409, 'Gerade läuft: ' + ', '.join(running) + '. Bitte abschließen lassen.')
        installing = True
        error = ''
        app.state.maintenance = True
        handed_off = False
        try:
            # Recheck the downloaded hash and installation ownership before backup/shutdown.
            artifact = await finish_thread(catalog.get_ready, version)
            if artifact.get("installer_compatible") is not True:
                raise ValueError("Dieses Paket unterstützt kein sicheres In-App-Update. Bitte die Release-Seite verwenden.")
            prepared = await finish_thread(update_install.prepare_handoff, paths, artifact)
            snapshot = await finish_thread(create_backup, app.state.db, app.state.settings.data_dir)
            backup = snapshot.name
            await finish_thread(update_install.launch_handoff, prepared)
            handed_off = True
            app.state.begin_stop()
            return status() | {'message': 'Sicherung erstellt. Die App wird für das bestätigte Update geschlossen.'}
        except (UpdateError, OSError, ValueError, RuntimeError) as exc:
            error = error_message(exc)
            raise HTTPException(400, error) from None
        finally:
            if not handed_off:
                installing = False
                app.state.maintenance = False

    @app.post('/api/updates/install', include_in_schema=False)
    async def install_update(value: InstallChoice):
        if not value.confirm:
            raise HTTPException(400, 'Die Installation muss ausdrücklich bestätigt werden.')
        # Keep the maintenance admission until an authorized handoff finishes,
        # even if the browser disconnects while the backup is being written.
        task = asyncio.create_task(perform_install(value.version))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            await settle_cancelled(task)
            raise

    def last_check_time():
        value = catalog.status().get('last_checked')
        try:
            if isinstance(value, (float, int)):
                return float(value)
            return datetime.fromisoformat(value.replace('Z', '+00:00')).replace(tzinfo=timezone.utc).timestamp()
        except (ValueError, TypeError, AttributeError):
            return 0.0

    async def automatic_checks():
        while True:
            if (catalog.status().get('auto_check') and not installing
                    and not app.state.maintenance and not app.state.lifecycle_request
                    and (job is None or job.done())
                    and time.time() >= max(next_auto_attempt, last_check_time() + 86400)):
                start_job(catalog.check, 'checking')
            await asyncio.sleep(60)

    original_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application):
        async with original_lifespan(application):
            timer = asyncio.create_task(automatic_checks())
            try:
                yield
            finally:
                timer.cancel()
                with suppress(asyncio.CancelledError):
                    await timer
                if job is not None:
                    job.cancel()
                    with suppress(asyncio.CancelledError):
                        await job

    app.router.lifespan_context = lifespan
