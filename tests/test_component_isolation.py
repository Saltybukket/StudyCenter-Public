from types import SimpleNamespace
from studycenter_public.platform.components import configure_browser_runtime,ComponentService
from studycenter_public.services.documents import default_ocr

def test_public_ocr_and_browser_never_reuse_generic_system_paths(tmp_path,monkeypatch):
    monkeypatch.setenv('PATH',str(tmp_path/'another-app'))
    monkeypatch.setenv('PLAYWRIGHT_BROWSERS_PATH',str(tmp_path/'another-browser-cache'))
    paths=SimpleNamespace(cache=tmp_path/'public-cache')
    configured=configure_browser_runtime(paths)
    assert configured==paths.cache/'components'/'browsers'
    assert default_ocr(paths).tesseract is None
    assert ComponentService(paths).status()['ocr'] is False

def test_missing_portable_component_is_explicit_and_no_install_fallback(tmp_path):
    import pytest
    paths=SimpleNamespace(cache=tmp_path/'public-cache')
    with pytest.raises(ValueError,match='Release'):ComponentService(paths).verify()
    assert not paths.cache.exists()
