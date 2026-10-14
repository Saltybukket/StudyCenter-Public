"""OCR transport regressions; native controls use an explicitly supplied own stage."""
import os
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from studycenter_public.services import documents


def test_windows_own_ocr_uses_relative_language_data_and_child_working_directory(tmp_path, monkeypatch):
    root = tmp_path / 'Program \u00fc space' / 'components'
    ocr = root / 'ocr'
    ocr.mkdir(parents=True)
    (ocr / 'tesseract.exe').touch()
    original = dict(os.environ)
    monkeypatch.setattr(documents, 'os', SimpleNamespace(name='nt', environ=os.environ))
    monkeypatch.setattr('studycenter_public.platform.components.component_root', lambda _: root)
    settings = documents.default_ocr(SimpleNamespace(cache=tmp_path / 'cache'))
    assert settings.process_env['TESSDATA_PREFIX'] == 'tessdata'
    assert settings.working_directory == str(ocr.resolve())
    assert settings.tesseract == str((ocr / 'tesseract.exe').resolve())
    parent_environment_unchanged = dict(os.environ) == original
    assert parent_environment_unchanged


def test_ocr_sends_png_bytes_instead_of_creating_long_filename(tmp_path, monkeypatch):
    executable = tmp_path / 'tesseract'
    executable.touch()
    root = tmp_path / 'Program \u00fc space'
    root.mkdir()
    cache = tmp_path / ('cache-' + 'a' * 60) / ('b' * 60) / ('c' * 60)
    cache.mkdir(parents=True)
    assert len(str(cache / 'temporary-page.png')) > 260
    payload = b'\x89PNG\r\n\x1a\nsynthetic-pixmap-transport'
    class Pixmap:
        def save(self, _):
            pytest.fail('OCR must not create a PNG filename for the native reader')
        def tobytes(self, format):
            assert format == 'png'
            return payload
    settings = documents.OCRSettings(str(executable), 'eng+deu', str(cache), {'TESSDATA_PREFIX': 'tessdata'})
    settings.working_directory = str(root)
    calls = []
    def native_run(args, **options):
        calls.append((args, options))
        assert args == [str(executable), 'stdin', 'stdout', '-l', 'eng+deu']
        assert options['input'] == payload
        assert options['cwd'] == str(root)
        assert options['env'] == settings.process_env
        assert 'encoding' not in options and 'text' not in options
        return subprocess.CompletedProcess(args, 0, stdout='SYNTHETIC \u00fc'.encode(), stderr=b'')
    monkeypatch.setattr(documents.subprocess, 'run', native_run)
    assert documents.ocr_pixmap(Pixmap(), settings) == ('SYNTHETIC \u00fc', None)
    assert len(calls) == 1
    assert list(cache.iterdir()) == []


def test_ocr_stdin_failure_keeps_explicit_gap(tmp_path, monkeypatch):
    executable = tmp_path / 'tesseract'
    executable.touch()
    class Pixmap:
        def tobytes(self, _):return b'synthetic-invalid-image'
        def save(self, _):pytest.fail('No intermediate PNG file expected')
    settings = documents.OCRSettings(str(executable), 'eng+deu', str(tmp_path))
    monkeypatch.setattr(documents.subprocess, 'run', lambda *a, **kw: subprocess.CompletedProcess(a, 1, b'', b'synthetic decode failure'))
    text, gap = documents.ocr_pixmap(Pixmap(), settings)
    assert text is None and 'OCR fehlgeschlagen' in gap


@pytest.mark.parametrize('linked_part', ['root', 'executable', 'language_directory'])
def test_own_ocr_rejects_links_before_using_component_paths(tmp_path, monkeypatch, linked_part):
    components = tmp_path / 'components'
    components.mkdir()
    own = components / 'ocr'
    foreign = tmp_path / 'foreign-fixture'
    foreign.mkdir()
    sentinel = foreign / 'untouched.txt'
    sentinel.write_bytes(b'synthetic foreign fixture must remain untouched')
    original = (sentinel.read_bytes(), sentinel.stat().st_mtime_ns)
    exe_name = Path('tesseract.exe') if os.name == 'nt' else Path('usr/bin/tesseract')
    data_name = Path('tessdata') if os.name == 'nt' else Path('usr/share/tesseract-ocr/5/tessdata')
    if linked_part == 'root':
        target, link, directory = foreign, own, True
    else:
        own.mkdir()
        if linked_part == 'executable':
            target = foreign / 'foreign-tesseract'
            target.touch()
            link, directory = own / exe_name, False
        else:
            target, link, directory = foreign, own / data_name, True
        link.parent.mkdir(parents=True, exist_ok=True)
    try:
        link.symlink_to(target, target_is_directory=directory)
    except OSError as error:
        pytest.skip('Own link fixture creation unavailable: ' + type(error).__name__)
    monkeypatch.setattr('studycenter_public.platform.components.component_root', lambda _: components)
    monkeypatch.setattr(documents.subprocess, 'run', lambda *a, **k: pytest.fail('Linked OCR must never launch'))
    with pytest.raises(ValueError, match='Verkn\u00fcpfung|Reparse'):
        documents.default_ocr(SimpleNamespace(cache=tmp_path / 'cache'))
    assert (sentinel.read_bytes(), sentinel.stat().st_mtime_ns) == original
    assert not (tmp_path / 'cache').exists()


@pytest.mark.skipif(os.name != 'nt' or not os.environ.get('STUDYCENTER_TEST_OWN_OCR_STAGE'), reason='Explicit own portable Windows stage required')
@pytest.mark.parametrize('case', ['unicode_program', 'long_input_cache'])
def test_native_windows_ocr_program_and_long_cache_paths(tmp_path, monkeypatch, case):
    """Real Tesseract, real raster; no installation or host runtime lookup."""
    from PIL import Image, ImageDraw, ImageFont
    import pymupdf
    supplied = Path(os.environ['STUDYCENTER_TEST_OWN_OCR_STAGE']).resolve(strict=True)
    assert (supplied / 'tesseract.exe').is_file()
    assert not supplied.is_symlink()
    program = tmp_path / ('Program \u00fc space' if case == 'unicode_program' else 'Program ASCII')
    component_root = program / 'components'
    shutil.copytree(supplied, component_root / 'ocr', symlinks=False)
    cache = tmp_path / 'cache'
    if case == 'long_input_cache':
        while len(str(cache)) < 270:cache = cache / ('a' * 30)
    cache.mkdir(parents=True)
    monkeypatch.setattr('studycenter_public.platform.components.component_root', lambda _: component_root)
    settings = documents.default_ocr(SimpleNamespace(cache=cache))
    settings.temp_directory = str(cache)
    image = Image.new('RGB', (1500, 500), 'white')
    ImageDraw.Draw(image).text((80, 150), 'SYNTHETIC PACKAGE DOCUMENT 2026', font=ImageFont.load_default(size=42), fill='black')
    import io
    png = io.BytesIO()
    image.save(png, format='PNG')
    pixmap = pymupdf.Pixmap(png.getvalue())
    text, gap = documents.ocr_pixmap(pixmap, settings)
    assert gap is None and 'SYNTHETIC' in text, (text, gap)
    assert list(cache.iterdir()) == []
