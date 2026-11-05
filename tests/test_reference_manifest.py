"""Reproducibility helper stays scoped to selected synthetic code trees."""
import importlib.util
import tempfile
from pathlib import Path
import pytest


def test_snapshot_detects_content_and_inventory_changes_without_data():
    spec=importlib.util.spec_from_file_location('reference_manifest',Path(__file__).parents[1]/'scripts/reference_manifest.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    with tempfile.TemporaryDirectory(prefix='synthetic-reference-') as temporary:
        root=Path(temporary)
        code=root/'code';tests=root/'tests';code.mkdir();tests.mkdir()
        (code/'app.py').write_text('first',encoding='utf-8')
        (tests/'test_sample.py').write_text('synthetic',encoding='utf-8')
        (code/'data').mkdir();(code/'data'/'private.py').write_text('excluded',encoding='utf-8')
        (code/'.env').write_text('excluded',encoding='utf-8')
        roots={'code':code,'tests':tests}
        first=module.snapshot(roots)
        assert set(first)=={'code/app.py','tests/test_sample.py'}
        assert module.snapshot(roots)==first
        (code/'app.py').write_text('second',encoding='utf-8')
        assert module.snapshot(roots)['code/app.py']!=first['code/app.py']
        (tests/'test_other.py').write_text('new',encoding='utf-8')
        assert set(module.snapshot(roots))-set(first)=={'tests/test_other.py'}
        with pytest.raises(ValueError):module.snapshot({'code':code/'data'})
