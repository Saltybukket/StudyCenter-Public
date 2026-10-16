"""Optional components belonging to this Public installation only."""
import hashlib
import importlib.util
import json
from pathlib import Path
import os
import sys

from .paths import reject_links

COMPONENTS = {'browser': 'TU-Quellenbrowser', 'ocr': 'OCR', 'speech': 'Whisper-Sprache'}


def bundle_root():
    return Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else None


def installation():
    """Only a setup receipt for the running dedicated environment grants writes."""
    if getattr(sys, 'frozen', False):
        return None
    configured = os.environ.get('STUDYCENTER_PUBLIC_INSTALL_ROOT')
    if not configured:
        return None
    try:
        root = reject_links(configured).resolve()
        receipt = json.loads(reject_links(root/'.local/installation.json').read_text(encoding='utf-8'))
        environment = reject_links(receipt['environment']).resolve()
        # Linux venv interpreters normally symlink to their base Python. The
        # dedicated environment and running executable path grant ownership;
        # resolving that final link would incorrectly select the system path.
        python = Path(os.path.abspath(receipt['python']))
        reject_links(python.parent)
        expected_python = environment/('Scripts/python.exe' if sys.platform == 'win32' else 'bin/python')
        owned_environment = (environment.is_relative_to(root/'.local/environments')
            or (environment == root/'.venv' and receipt.get('python_origin') == 'existing-own'))
        if (receipt.get('schema') != 1 or receipt.get('application') != 'StudyCenter.Public'
                or Path(receipt['root']).resolve() != root
                or not owned_environment
                or python != expected_python
                or Path(sys.prefix).resolve() != environment
                or Path(os.path.abspath(sys.executable)) != python):
            return None
        return root, receipt
    except (OSError, ValueError, KeyError, TypeError):
        return None


def component_root(paths):
    root = bundle_root()
    if root:
        return root/'components'
    own = installation()
    return own[0]/'.local/components' if own else paths.cache/'components'


def browser_directory(paths):
    return component_root(paths)/'browsers'


def configure_browser_runtime(paths):
    directory = reject_links(browser_directory(paths))
    os.environ['PLAYWRIGHT_BROWSERS_PATH'] = str(directory.resolve())
    return directory


def module_available(name):
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError, AttributeError):
        return False


def system_browser_available():
    if sys.platform != 'linux' or not module_available('playwright'):
        return False
    from ..collectors.browser import linux_system_chrome
    return bool(linux_system_chrome())


class ComponentService:
    def __init__(self, paths):
        self.paths = paths
        self.errors = {}

    def _manifest(self):
        own = installation()
        if not own:
            return {}
        root, receipt = own
        target = receipt.get('manifest')
        if not target:
            target = root/'packaging'/('slim-windows-x86_64.json' if sys.platform == 'win32' else 'slim-linux-x86_64.json')
        try:
            result = json.loads(reject_links(target).read_text(encoding='utf-8'))
            return result if isinstance(result, dict) else {}
        except (OSError, ValueError, TypeError):
            return {}

    def _receipt_error(self, name):
        if not installation() or (name == 'browser' and system_browser_available()):
            return None
        root = component_root(self.paths)
        receipt_path = root/(name+'.json')
        if not receipt_path.is_file():
            return 'Einrichtung unvollständig; erneut versuchen.'
        try:
            receipt = json.loads(reject_links(receipt_path).read_text(encoding='utf-8'))
            if receipt.get('schema') != 1 or receipt.get('name') != name or not isinstance(receipt.get('files'), dict):
                raise ValueError()
            if name != 'speech' and not receipt['files']:
                raise ValueError()
            for relative, record in receipt['files'].items():
                file = reject_links(root/relative).resolve()
                if not file.is_relative_to(root.resolve()) or file.stat().st_size != record['size']:
                    raise ValueError()
            return None
        except (OSError, ValueError, KeyError, AttributeError, TypeError):
            return 'Komponente unvollständig oder beschädigt; erneut versuchen.'

    def status(self):
        root = component_root(self.paths)
        browser = root/'browsers'
        ocr = root/'ocr'
        tesseract = ocr/('tesseract.exe' if sys.platform == 'win32' else 'usr/bin/tesseract')
        languages = ocr/('tessdata' if sys.platform == 'win32' else 'usr/share/tesseract-ocr/5/tessdata')
        full = bool(list(browser.glob('chromium-*')))
        shell = bool(list(browser.glob('chromium_headless_shell-*')))
        present = {'browser': (full and (sys.platform == 'win32' or shell) and module_available('playwright')) or system_browser_available(),
                   'ocr': tesseract.is_file() and all((languages/(lang+'.traineddata')).is_file() for lang in ('eng', 'deu')),
                   'speech': module_available('faster_whisper') and module_available('av')}
        manifest = self._manifest()
        details = {}
        for name, label in COMPONENTS.items():
            problem = self.errors.get(name)
            if present[name]:
                problem = problem or self._receipt_error(name)
                if problem:
                    present[name] = False
            record = manifest.get('components', {}).get(name, {})
            archive = record.get('archive') or {}
            size = archive.get('size')
            python_size = record.get('python_download_bytes')
            details[name] = {'label': label, 'installed': present[name],
                'state': 'error' if problem else 'ready' if present[name] else 'missing',
                'error': problem, 'download_bytes': size,
                'python_download_bytes': python_size,
                'size_complete': not record.get('extra') or python_size is not None,
                'can_install': installation() is not None,
                'model_separate': name == 'speech'}
        return present | {'components': details,
            'status': 'Optionale Komponenten dieser Public-Installation; Einrichtung nur nach Bestätigung.'}

    def _verify_files(self, component):
        own = installation()
        if component in ('browser', 'speech') and own:
            from .setup import component_valid
            if not component_valid(own[0], component):
                raise ValueError(COMPONENTS[component]+'-Bibliotheken sind unvollständig oder nicht ladbar. Unter Einstellungen erneut einrichten.')
        if component == 'browser' and system_browser_available():
            return 0
        root = bundle_root()
        if not root:
            own = installation()
            if not own:
                return 0
            root = component_root(self.paths)
            manifest_path = reject_links(root/(component+'.json'))
            prefix = ('browsers' if component == 'browser' else component)+'/'
        else:
            manifest_path = reject_links(root/'BUILD-MANIFEST.json')
            prefix = 'components/'+('browsers' if component == 'browser' else component)+'/'
        if not manifest_path.is_file():
            raise ValueError('Komponentenmanifest fehlt; Release-Paket reparieren.')
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        count = 0
        for relative, record in manifest.get('files', {}).items():
            if not relative.startswith(prefix):
                continue
            path = reject_links(root/relative).resolve()
            if not path.is_relative_to(root.resolve()) or not path.is_file() or path.stat().st_size != record['size']:
                raise ValueError('Portable Komponente fehlt oder ist beschädigt; Release-Paket reparieren.')
            with path.open('rb') as file:
                actual = hashlib.file_digest(file, 'sha256').hexdigest()
            if actual != record['sha256']:
                raise ValueError('Komponentenprüfsumme stimmt nicht; Release-Paket reparieren.')
            count += 1
        if component != 'speech' and count == 0:
            raise ValueError('Komponente fehlt im Release-Manifest; Release-Paket reparieren.')
        return count

    def verify(self, component=None):
        if component is not None and component not in COMPONENTS:
            raise ValueError('Unbekannte optionale Komponente.')
        count = 0
        for name in ([component] if component else COMPONENTS):
            if not self.status()[name]:
                if component:
                    raise ValueError(COMPONENTS[name]+' fehlt. Unter Einstellungen einrichten oder Release-Paket reparieren.')
                continue
            try:
                count += self._verify_files(name)
                self.errors.pop(name, None)
            except (ValueError, OSError, KeyError) as error:
                self.errors[name] = str(error)
                if component:
                    raise ValueError(str(error)) from None
        return self.status() | {'verified_files': count}

    def install(self, component, *, allow_download=False):
        if component not in COMPONENTS:
            raise ValueError('Unbekannte optionale Komponente.')
        if self.status()[component] and component not in self.errors:
            return self.verify(component)
        if allow_download is not True:
            raise ValueError('Download und Einrichtung zuerst ausdrücklich bestätigen.')
        own = installation()
        if not own:
            raise ValueError('Diese Umgebung kann keine Komponenten nachladen. Über den Public-Starter einrichten; Full-Pakete über ihren Release-Download reparieren.')
        try:
            from .setup import ensure_component
            ensure_component(own[0], component, data_dir=self.paths.data, allow_download=True)
            importlib.invalidate_caches()
            self.errors.pop(component, None)
            return self.verify(component)
        except Exception:
            self.errors[component] = 'Einrichtung fehlgeschlagen. Verbindung und freien Speicher prüfen; erneut versuchen.'
            raise ValueError(self.errors[component]) from None
