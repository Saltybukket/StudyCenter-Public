"""Only installation-owned portable components; never run system installers."""
import hashlib
import json
from pathlib import Path
import os
import sys

def bundle_root():
    return Path(sys.executable).resolve().parent if getattr(sys,'frozen',False) else None

def component_root(paths):
    root=bundle_root()
    return root/'components' if root else paths.cache/'components'

def browser_directory(paths):return component_root(paths)/'browsers'

def configure_browser_runtime(paths):
    # Overrides any inherited generic browser cache setting. No profile migration.
    directory=browser_directory(paths)
    os.environ['PLAYWRIGHT_BROWSERS_PATH']=str(directory.resolve())
    return directory

class ComponentService:
    def __init__(self,paths):self.paths=paths

    def status(self):
        root=component_root(self.paths);browser=root/'browsers';ocr=root/'ocr'
        tesseract=ocr/('tesseract.exe' if sys.platform=='win32' else 'usr/bin/tesseract')
        languages=ocr/('tessdata' if sys.platform=='win32' else 'usr/share/tesseract-ocr/5/tessdata')
        return {'browser':bool(list(browser.glob('chromium-*')) and list(browser.glob('chromium_headless_shell-*'))),
                'ocr':tesseract.is_file() and all((languages/(lang+'.traineddata')).is_file() for lang in ('eng','deu')),
                'status':'Portable Komponenten aus dieser Public-Installation; kein Zugriff auf vorhandene Systeminstallationen.'}

    def verify(self):
        root=bundle_root()
        if not root:
            result=self.status()
            if not (result['browser'] and result['ocr']):
                raise ValueError('Entwicklungsbestand ohne portable Komponenten. Ein vollständiges Release-Paket verwenden oder den getrennten dokumentierten Buildweg ausführen.')
            return result
        manifest_path=root/'BUILD-MANIFEST.json'
        if not manifest_path.is_file():raise ValueError('Komponentenmanifest fehlt; Release-Paket reparieren.')
        manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
        count=0
        for relative,record in manifest.get('files',{}).items():
            if not relative.startswith('components/'):continue
            path=(root/relative).resolve()
            if not path.is_relative_to(root.resolve()) or not path.is_file() or path.stat().st_size!=record['size']:
                raise ValueError('Portable Komponente fehlt oder ist beschädigt; Release-Paket reparieren.')
            with path.open('rb') as file:actual=hashlib.file_digest(file,'sha256').hexdigest()
            if actual!=record['sha256']:raise ValueError('Komponentenprüfsumme stimmt nicht; Release-Paket reparieren.')
            count+=1
        result=self.status()
        if not (result['browser'] and result['ocr']):raise ValueError('Browser/OCR-Komponenten fehlen; vollständiges Release-Paket verwenden.')
        return result|{'verified_files':count,'status':'Eigene portable Browser- und OCR-Komponenten vorhanden; alle Komponentenprüfsummen bestätigt.'}
