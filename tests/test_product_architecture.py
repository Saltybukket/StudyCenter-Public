import importlib.util
from pathlib import Path


spec = importlib.util.spec_from_file_location(
    "check_product_architecture", Path(__file__).resolve().parents[1] / "scripts/check_product_architecture.py"
)
architecture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(architecture)


def package_at(tmp_path):
    package = tmp_path / "studycenter_public"
    core = package / "core"
    core.mkdir(parents=True)
    (core / "__init__.py").write_text("", encoding="utf-8")
    (package / "platform").mkdir()
    (package / "platform/setup.py").write_text("", encoding="utf-8")
    (package / "__main__.py").write_text("from .product import create_app\n", encoding="utf-8")
    (package / "product.py").write_text("from .core.app import create_app\nfrom .services.documents import default_ocr\n", encoding="utf-8")
    return package


def test_legacy_product_imports_are_blocked_but_adapter_bridges_are_allowed(tmp_path):
    package = package_at(tmp_path)
    (package / "core/app.py").write_text("from .db import Database\n", encoding="utf-8")
    assert architecture.check_architecture(package) == []
    (package / "core/app.py").write_text("from .. import web\n", encoding="utf-8")
    assert any("legacy product layer studycenter_public.web" in item for item in architecture.check_architecture(package))
    (package / "core/app.py").write_text("from ..services.actions import ActionService\n", encoding="utf-8")
    assert any("legacy service studycenter_public.services.actions" in item for item in architecture.check_architecture(package))
    (package / "core/app.py").write_text("", encoding="utf-8")
    (package / "product.py").write_text("import studycenter_public.storage.db\n", encoding="utf-8")
    assert any("legacy product layer studycenter_public.storage.db" in item for item in architecture.check_architecture(package))


def test_setup_readiness_python_snippets_use_current_product(tmp_path):
    package = package_at(tmp_path)
    setup = package / "platform/setup.py"
    setup.write_text("run(['python', '-c', 'from studycenter_public.product import create_app'])\n", encoding="utf-8")
    assert architecture.check_architecture(package) == []
    setup.write_text("run(['python', '-c', 'from studycenter_public.web import create_app'])\n", encoding="utf-8")
    assert any("legacy product layer studycenter_public.web" in item for item in architecture.check_architecture(package))
