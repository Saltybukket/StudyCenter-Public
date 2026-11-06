import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("sync_product", SCRIPTS / "sync_product.py")
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args], stderr=subprocess.PIPE).decode().strip()


def commit(repo, message="synthetic source"):
    git(repo, "add", "campusboard")
    git(repo, "-c", "user.name=Synthetic", "-c", "user.email=synthetic@example.invalid",
        "-c", "commit.gpgsign=false", "commit", "-qm", message)
    return git(repo, "rev-parse", "HEAD")


@pytest.fixture
def product(tmp_path):
    repo = tmp_path / "source"
    repo.mkdir()
    git(repo, "init", "-q", "--object-format=sha1")
    git(repo, "config", "--local", "core.autocrlf", "false")
    package = repo / "campusboard"
    package.mkdir()
    (package / "app.py").write_bytes(b"value = 1\n")
    first = commit(repo)
    public = tmp_path / "public"
    public.mkdir()
    # A Windows checkout can differ in line endings without being an adapter.
    (public / "app.py").write_bytes(b"value = 1\r\n")
    policy = tmp_path / "adapters.json"
    policy.write_text(json.dumps({"format": 1, "adaptations": {}, "additions": {}}), encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    sync.capture(repo, first, policy, public, manifest)
    return repo, package, public, policy, manifest, first


def test_offline_tree_and_real_commit_binding(product):
    repo, package, public, policy, manifest, first = product
    doc = sync.load_manifest(manifest)
    assert doc["source"]["commit"] == first
    assert doc["source"]["tree"] == git(repo, "rev-parse", "HEAD:campusboard")
    sync.verify(doc, public, policy)
    sync.verify(doc, public, policy, repo)
    # The explicitly pinned committed source, not a mutable working copy, wins.
    (package / "app.py").write_text("uncommitted change\n", encoding="utf-8")
    sync.verify(doc, public, policy, repo)


def test_optional_worktree_check_detects_changes_without_reading_extra_or_profile_data(product, monkeypatch):
    repo, package, public, policy, manifest, _ = product
    doc = sync.load_manifest(manifest)
    sync.verify(doc, public, policy, repo, check_worktree=True)
    (package / "app.py").write_text("changed working tree\n", encoding="utf-8")
    sync.verify(doc, public, policy, repo)  # The Git-only proof stays immutable.
    with pytest.raises(ValueError, match="worktree content drift"):
        sync.verify(doc, public, policy, repo, check_worktree=True)
    (package / "app.py").write_bytes(b"value = 1\r\n")
    profile = package / "profiles"
    profile.mkdir()
    (profile / "secret.py").write_text("synthetic private data\n", encoding="utf-8")
    extra = package / "untracked.py"
    extra.write_text("synthetic untracked product\n", encoding="utf-8")
    original = Path.read_bytes

    def guarded_read(path):
        if path == extra or profile in path.parents:
            raise AssertionError("extra or private content must not be read")
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", guarded_read)
    with pytest.raises(ValueError, match="worktree file set differs.*untracked.py"):
        sync.verify(doc, public, policy, repo, check_worktree=True)
    extra.unlink()
    sync.verify(doc, public, policy, repo, check_worktree=True)
    with pytest.raises(ValueError, match="requires a source repository"):
        sync.verify(doc, public, policy, check_worktree=True)


def test_commit_read_ignores_replace_refs_and_git_environment(product, tmp_path, monkeypatch):
    repo, package, _, _, _, first = product
    original_tree = git(repo, "rev-parse", "HEAD:campusboard")
    (package / "app.py").write_bytes(b"value = 2\n")
    second = commit(repo)
    git(repo, "replace", first, second)
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "not-the-repository"))
    source, entries = sync.source_snapshot(repo, first)
    assert source["tree"] == original_tree
    assert entries["app.py"]["data"] == b"value = 1\n"


def test_clean_update_is_reviewable_candidate_and_leaves_live_export_unchanged(product, tmp_path):
    repo, package, public, policy, manifest, first = product
    (package / "app.py").write_bytes(b"value = 2\n")
    second = commit(repo, "second source")
    old_manifest = manifest.read_bytes()
    old_product = (public / "app.py").read_bytes()
    result = sync.update(sync.load_manifest(manifest), public, policy, repo, second, tmp_path / "prepared")
    assert result == tmp_path / "prepared" / "candidate"
    assert (result / "core" / "app.py").read_bytes() == b"value = 2\n"
    assert manifest.read_bytes() == old_manifest
    assert (public / "app.py").read_bytes() == old_product
    changed = sync.load_manifest(result / "product-source.json")
    assert changed["source"]["commit"] == second != first
    sync.verify(changed, result / "core", result / "product-adapters.json", repo)


def test_adaptation_change_stops_before_any_candidate_write(product, tmp_path):
    repo, package, public, policy, _, first = product
    (public / "app.py").write_text("isolated = True\n", encoding="utf-8")
    policy.write_text(json.dumps({"format": 1, "adaptations": {"app.py": "Use isolated storage"}, "additions": {}}), encoding="utf-8")
    baseline = tmp_path / "adapted.json"
    sync.capture(repo, first, policy, public, baseline)
    (package / "app.py").write_bytes(b"value = 2\n")
    second = commit(repo)
    with pytest.raises(ValueError, match="merge manually"):
        sync.update(sync.load_manifest(baseline), public, policy, repo, second, tmp_path / "prepared")
    assert not (tmp_path / "prepared").exists()
    assert (public / "app.py").read_text() == "isolated = True\n"


def test_update_rejects_content_and_inventory_changes_before_writing(product, tmp_path):
    repo, package, public, policy, manifest, _ = product
    (package / "app.py").write_text("key = '" + "sk-" + "a" * 24 + "'\n", encoding="utf-8")
    second = commit(repo)
    with pytest.raises(ValueError, match="content gate"):
        sync.update(sync.load_manifest(manifest), public, policy, repo, second, tmp_path / "prepared")
    assert not (tmp_path / "prepared").exists()
    assert (public / "app.py").read_bytes() == b"value = 1\r\n"
    (package / "app.py").write_bytes(b"value = 2\n")
    (package / "new.py").write_text("value = 3\n", encoding="utf-8")
    third = commit(repo)
    with pytest.raises(ValueError, match="file set changed"):
        sync.update(sync.load_manifest(manifest), public, policy, repo, third, tmp_path / "prepared")
    assert not (tmp_path / "prepared").exists()


def test_failed_candidate_write_is_removed_and_live_export_preserved(product, tmp_path, monkeypatch):
    repo, package, public, policy, manifest, _ = product
    (package / "app.py").write_bytes(b"value = 2\n")
    second = commit(repo)
    original = Path.write_bytes

    def fail_staged_write(path, data):
        if path.name == "product-source.json":
            raise OSError("synthetic write failure")
        return original(path, data)

    monkeypatch.setattr(Path, "write_bytes", fail_staged_write)
    before = manifest.read_bytes()
    with pytest.raises(OSError, match="synthetic write failure"):
        sync.update(sync.load_manifest(manifest), public, policy, repo, second, tmp_path / "prepared")
    assert not (tmp_path / "prepared").exists()
    assert manifest.read_bytes() == before
    assert (public / "app.py").read_bytes() == b"value = 1\r\n"


def test_failed_staging_directory_creation_removes_candidate_container(product, tmp_path, monkeypatch):
    repo, package, public, policy, manifest, _ = product
    (package / "app.py").write_bytes(b"value = 2\n")
    second = commit(repo)

    def cannot_stage(*args, **kwargs):
        raise OSError("synthetic staging failure")

    monkeypatch.setattr(sync.tempfile, "mkdtemp", cannot_stage)
    with pytest.raises(OSError, match="synthetic staging failure"):
        sync.update(sync.load_manifest(manifest), public, policy, repo, second, tmp_path / "prepared")
    assert not (tmp_path / "prepared").exists()
    assert (public / "app.py").read_bytes() == b"value = 1\r\n"


def test_policy_binding_is_stable_across_json_formatting_and_checkout_eol(product):
    _, _, public, policy, manifest, _ = product
    doc = sync.load_manifest(manifest)
    policy.write_bytes(b'{\r\n  "additions": {},\r\n  "adaptations": {},\r\n  "format": 1\r\n}\r\n')
    sync.verify(doc, public, policy)


def test_public_drift_and_policy_drift_are_rejected(product):
    _, _, public, policy, manifest, _ = product
    doc = sync.load_manifest(manifest)
    (public / "app.py").write_text("changed\n", encoding="utf-8")
    with pytest.raises(ValueError, match="shipped content drift"):
        sync.verify(doc, public, policy)
    (public / "app.py").write_bytes(b"value = 1\r\n")
    (public / "extra.py").write_text("extra\n", encoding="utf-8")
    with pytest.raises(ValueError, match="extra"):
        sync.verify(doc, public, policy)
    (public / "extra.py").unlink()
    policy.write_text(json.dumps({"format": 1, "adaptations": {"app.py": "Unreviewed"}, "additions": {}}), encoding="utf-8")
    with pytest.raises(ValueError, match="policy drift"):
        sync.verify(doc, public, policy)


def test_offline_tree_rejects_forged_blob_and_git_check_rejects_wrong_commit(product):
    repo, _, public, policy, manifest, _ = product
    doc = json.loads(manifest.read_text(encoding="utf-8"))
    doc["files"]["app.py"]["git_blob"] = "0" * 40
    manifest.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(ValueError, match="reconstruct the source tree"):
        sync.load_manifest(manifest)
    doc["files"]["app.py"]["git_blob"] = git(repo, "rev-parse", "HEAD:campusboard/app.py")
    doc["source"]["commit"] = "0" * 40
    manifest.write_text(json.dumps(doc), encoding="utf-8")
    # Tree arithmetic alone cannot authenticate an asserted commit.
    sync.verify(sync.load_manifest(manifest), public, policy)
    with pytest.raises(ValueError, match="Git object"):
        sync.verify(sync.load_manifest(manifest), public, policy, repo)


def test_sha256_object_format_tree_reconstruction(tmp_path):
    repo = tmp_path / "sha256-source"
    repo.mkdir()
    result = subprocess.run(["git", "-C", str(repo), "init", "-q", "--object-format=sha256"],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode:
        pytest.skip("Git does not support SHA-256 repositories")
    git(repo, "config", "--local", "core.autocrlf", "false")
    package = repo / "campusboard"
    package.mkdir()
    (package / "app.py").write_bytes(b"synthetic = True\n")
    revision = commit(repo)
    source, entries = sync.source_snapshot(repo, revision)
    assert source["object_format"] == "sha256"
    assert len(source["commit"]) == 64
    assert source["tree"] == git(repo, "rev-parse", "HEAD:campusboard")
    assert sync.tree_hash(entries, "sha256") == source["tree"]


def test_unknown_source_paths_and_mutable_revisions_are_refused(product):
    repo, package, _, _, _, _ = product
    (package / "profiles").mkdir()
    (package / "profiles" / "secret.py").write_text("synthetic\n", encoding="utf-8")
    revision = commit(repo)
    with pytest.raises(ValueError, match="unsupported path"):
        sync.source_snapshot(repo, revision)
    with pytest.raises(ValueError, match="full immutable"):
        sync.source_snapshot(repo, "HEAD")


def test_source_root_and_git_storage_cannot_be_candidate_destinations(product, tmp_path):
    repo, package, public, policy, manifest, first = product
    for invalid_source in (package, repo / ".git"):
        with pytest.raises(ValueError, match="worktree root"):
            sync.capture(invalid_source, first, policy, public, repo / "bad.json")
        with pytest.raises(ValueError, match="worktree root"):
            sync.update(sync.load_manifest(manifest), public, policy, invalid_source, first, repo / "prepared")
    assert not (repo / "bad.json").exists()
    assert not (repo / "prepared").exists()

    # A linked worktree has a Git common directory outside its own root.
    linked_worktree = tmp_path / "linked-source"
    git(repo, "worktree", "add", "--quiet", "--detach", str(linked_worktree), first)
    forbidden = repo / ".git" / "candidate.json"
    with pytest.raises(ValueError, match="source repository or its Git storage"):
        sync.capture(linked_worktree, first, policy, public, forbidden)
    assert not forbidden.exists()


def test_capture_requires_exact_policy_and_never_overwrites_a_baseline(product, tmp_path):
    repo, _, public, policy, manifest, first = product
    before = manifest.read_bytes()
    with pytest.raises(ValueError, match="already exists"):
        sync.capture(repo, first, policy, public, manifest)
    assert manifest.read_bytes() == before
    (public / "app.py").write_text("changed\n", encoding="utf-8")
    with pytest.raises(ValueError, match="policy must exactly cover"):
        sync.capture(repo, first, policy, public, tmp_path / "other.json")
    assert not (tmp_path / "other.json").exists()


def test_links_and_public_additions(product, tmp_path):
    repo, _, public, policy, _, first = product
    (public / "templates").mkdir()
    (public / "templates" / "tour.html").write_text("tour", encoding="utf-8")
    policy.write_text(json.dumps({"format": 1, "adaptations": {}, "additions": {"templates/tour.html": "Public onboarding"}}), encoding="utf-8")
    manifest = tmp_path / "addition.json"
    sync.capture(repo, first, policy, public, manifest)
    sync.verify(sync.load_manifest(manifest), public, policy)
    try:
        (public / "linked.py").symlink_to(tmp_path / "outside.py")
    except OSError:
        pytest.skip("symlink creation unavailable")
    with pytest.raises(ValueError, match="linked path"):
        sync.verify(sync.load_manifest(manifest), public, policy)
