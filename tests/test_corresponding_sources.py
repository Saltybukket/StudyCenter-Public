"""Bounded source downloads cannot escape their directory or admit changed bytes."""
import hashlib
import io
import tarfile

import pytest

from scripts.collect_corresponding_sources import SourceCollector


def test_cached_source_must_match_published_recipe(tmp_path):
    collector = SourceCollector(tmp_path / "sources")
    (collector.root / "source.tar.gz").write_bytes(b"altered source")
    expected = hashlib.sha256(b"published source").hexdigest()
    with pytest.raises(ValueError, match="SHA256"):
        collector.fetch("https://example.invalid/source.tar.gz", "source.tar.gz", expected)
    assert collector.records == []


def test_source_target_escape_rejected_before_directory_creation(tmp_path):
    collector = SourceCollector(tmp_path / "sources")
    with pytest.raises(ValueError, match="outside owned"):
        collector.fetch("https://example.invalid/source", "../foreign/nested/source")
    assert not (tmp_path / "foreign").exists()


def test_selected_archive_notice_escape_never_written(tmp_path):
    collector = SourceCollector(tmp_path / "sources")
    archive = collector.root / "upstream.tar"
    with tarfile.open(archive, "w") as target:
        member = tarfile.TarInfo("../LICENSE")
        member.size = 4
        target.addfile(member, io.BytesIO(b"text"))
    with pytest.raises(ValueError, match="Unsafe notice"):
        collector.notices(archive, "synthetic")
    assert not (tmp_path / "LICENSE").exists()
