import importlib.util
import json
from pathlib import Path
import sys

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("sync_product", SCRIPTS / "sync_product.py")
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)


@pytest.fixture
def product(tmp_path):
    source = tmp_path / "source"
    public = tmp_path / "public"
    source.mkdir()
    public.mkdir()
    (source / "app.py").write_text("value = 1\n")
    (public / "app.py").write_text("value = 1\n")
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps({"format": 1, "files": {"app.py": sync.digest((source / "app.py").read_bytes())}}))
    reasons = tmp_path / "reasons.json"
    reasons.write_text(json.dumps({"adaptations": {}, "additions": {}}))
    manifest = tmp_path / "manifest.json"
    sync.capture(baseline, reasons, public, manifest, source)
    return source, public, baseline, reasons, manifest


def test_clean_update_and_offline_verification(product):
    source, public, _, _, manifest = product
    (source / "app.py").write_text("value = 2\n")
    sync.update(sync.load_manifest(manifest), public, source, manifest)
    assert (public / "app.py").read_text() == "value = 2\n"
    sync.verify(sync.load_manifest(manifest), public)
    sync.verify(sync.load_manifest(manifest), public, source)


def test_adaptation_is_not_overwritten(product):
    source, public, baseline, reasons, manifest = product
    (public / "app.py").write_text("value = 3\n")
    reasons.write_text(json.dumps({"adaptations": {"app.py": "Use isolated storage"}, "additions": {}}))
    sync.capture(baseline, reasons, public, manifest, source)
    (source / "app.py").write_text("value = 2\n")
    with pytest.raises(ValueError, match="merge manually"):
        sync.update(sync.load_manifest(manifest), public, source, manifest)
    assert (public / "app.py").read_text() == "value = 3\n"


def test_reject_content_before_any_update(product):
    source, public, _, _, manifest = product
    # A synthetic marker matches a forbidden credential without containing a real one.
    (source / "app.py").write_text("key = '" + "sk-" + "a" * 24 + "'\n")
    with pytest.raises(ValueError, match="content gate"):
        sync.update(sync.load_manifest(manifest), public, source, manifest)
    assert (public / "app.py").read_text() == "value = 1\n"


def test_missing_extra_drift_and_upstream_drift(product):
    source, public, _, _, manifest = product
    doc = sync.load_manifest(manifest)
    (public / "app.py").write_text("changed\n")
    with pytest.raises(ValueError, match="drift"):
        sync.verify(doc, public)
    (public / "app.py").unlink()
    with pytest.raises(ValueError, match="missing"):
        sync.verify(doc, public)
    (public / "app.py").write_text("value = 1\n")
    (public / "extra.py").write_text("extra\n")
    with pytest.raises(ValueError, match="extra"):
        sync.verify(doc, public)
    (public / "extra.py").unlink()
    (source / "app.py").write_text("changed\n")
    with pytest.raises(ValueError, match="upstream content drift"):
        sync.verify(doc, public, source)


def test_never_walk_source_profiles_and_reject_public_profiles(product):
    source, public, _, _, manifest = product
    (source / "profiles").mkdir()
    (source / "profiles" / "secret.py").write_text("secret")
    sync.verify(sync.load_manifest(manifest), public, source)
    (public / "profiles").mkdir()
    with pytest.raises(ValueError, match="unexpected product directory"):
        sync.verify(sync.load_manifest(manifest), public)


def test_manifest_path_traversal_and_missing_reason_rejected(product):
    _, public, baseline, reasons, manifest = product
    doc = json.loads(manifest.read_text())
    doc["files"]["../outside.py"] = doc["files"].pop("app.py")
    manifest.write_text(json.dumps(doc))
    with pytest.raises(ValueError, match="invalid manifest entry"):
        sync.load_manifest(manifest)
    (public / "app.py").write_text("different\n")
    with pytest.raises(ValueError, match="reasons must exactly cover"):
        sync.capture(baseline, reasons, public, manifest)


def test_links_and_public_additions(product, tmp_path):
    _, public, baseline, reasons, manifest = product
    (public / "templates").mkdir()
    (public / "templates" / "tour.html").write_text("tour")
    reasons.write_text(json.dumps({"adaptations": {}, "additions": {"templates/tour.html": "Public onboarding"}}))
    sync.capture(baseline, reasons, public, manifest)
    sync.verify(sync.load_manifest(manifest), public)
    try:
        (public / "linked.py").symlink_to(tmp_path / "outside.py")
    except OSError:
        pytest.skip("symlink creation unavailable")
    with pytest.raises(ValueError, match="linked path"):
        sync.verify(sync.load_manifest(manifest), public)
