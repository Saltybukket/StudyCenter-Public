"""Best-effort desktop notification; persistent in-app bell remains authoritative."""
import shutil
import subprocess
import sys

desktop_notify=None

def status():
    if sys.platform=='win32':return 'Windows-Desktopmeldung verfügbar; sichtbare Ausgabe separat prüfen.'
    return 'notify-send verfügbar' if shutil.which('notify-send') else 'Nur In-App-Glocke: Desktopmeldungen nicht verfügbar'

def notify(item,endpoint):
    title=item.get('title','StudyCenter Public')
    if desktop_notify:
        try:desktop_notify(item,endpoint)
        except Exception:pass
        return
    if sys.platform!='win32' and shutil.which('notify-send'):
        subprocess.run(['notify-send','--app-name=StudyCenter Public','StudyCenter Public',str(title)],timeout=5,check=False,capture_output=True)
