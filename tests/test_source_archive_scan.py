import gzip
import base64
import io
import json
import lzma
import struct
import tarfile
import zipfile
import zlib

from scripts.scan_source_archives import scan_companion


def test_nested_archive_text_is_scanned_and_bound_to_uncompressed_hash(tmp_path):
    nested = io.BytesIO()
    text = b"example " + b"localhost" + b":8000"
    with tarfile.open(fileobj=nested, mode="w") as archive:
        member = tarfile.TarInfo("docs/example.txt")
        member.size = len(text)
        archive.addfile(member, io.BytesIO(text))
    bundle = tmp_path / "companion.zip"
    with zipfile.ZipFile(bundle, "w") as archive:
        archive.writestr("source.tar.gz", gzip.compress(nested.getvalue()))
    report = scan_companion(bundle, tmp_path / "report")
    assert report["limits"] == []
    assert report["findings"][0]["rule"] == "private endpoint"
    assert report["findings"][0]["path"].endswith("docs/example.txt")
    assert report["findings"][0]["sha256"]
    assert report["regular_files_scanned"] == 1


def test_tar_link_does_not_read_its_target(tmp_path):
    foreign = tmp_path / "foreign.txt"
    foreign.write_bytes(b"localhost" + b":8000")
    nested = io.BytesIO()
    with tarfile.open(fileobj=nested, mode="w") as archive:
        member = tarfile.TarInfo("link")
        member.type = tarfile.SYMTYPE
        member.linkname = str(foreign)
        archive.addfile(member)
    bundle = tmp_path / "companion.zip"
    with zipfile.ZipFile(bundle, "w") as archive:
        archive.writestr("source.tar", nested.getvalue())
    report = scan_companion(bundle, tmp_path / "report")
    assert report["findings"] == []
    assert report["links_not_followed"] == 1
    assert report["regular_files_scanned"] == 0


def test_declared_expansion_over_quota_is_reported_not_claimed_scanned(tmp_path):
    nested = io.BytesIO()
    with tarfile.open(fileobj=nested, mode="w") as archive:
        member = tarfile.TarInfo("large.txt")
        member.size = 101
        archive.addfile(member, io.BytesIO(b"x" * 101))
    bundle = tmp_path / "companion.zip"
    with zipfile.ZipFile(bundle, "w") as archive:
        archive.writestr("source.tar", nested.getvalue())
    report = scan_companion(bundle, tmp_path / "report", max_file_bytes=100)
    assert report["limits"]
    assert report["regular_files_scanned"] == 0


def test_lzip_original_source_is_decoded_and_checked(tmp_path):
    text = b"example " + b"localhost" + b":8000"
    compressed = lzma.compress(text, format=lzma.FORMAT_RAW, filters=[{
        "id": lzma.FILTER_LZMA1, "dict_size": 1 << 23, "lc": 3, "lp": 0, "pb": 2,
    }])
    body = b"LZIP\x01\x17" + compressed
    body += struct.pack("<IQQ", zlib.crc32(text), len(text), len(body) + 20)
    bundle = tmp_path / "companion.zip"
    with zipfile.ZipFile(bundle, "w") as archive:
        archive.writestr("source.txt.lz", body)
    report = scan_companion(bundle, tmp_path / "report")
    assert report["limits"] == []
    assert report["findings"][0]["rule"] == "private endpoint"
    assert report["containers"][0]["kind"] == "lzip"


def test_utf16_binary_strings_are_scanned_without_a_binary_exception(tmp_path):
    text = ("localhost" + ":8000").encode("utf-16-le")
    bundle = tmp_path / "companion.zip"
    with zipfile.ZipFile(bundle, "w") as archive:
        archive.writestr("opaque.bin", b"\xff" + text)
    report = scan_companion(bundle, tmp_path / "report")
    assert report["limits"] == []
    assert report["findings"][0]["rule"] == "private endpoint"
    assert report["traversal_completed"] is True


def test_private_indicators_are_supplied_by_report_configuration_and_redacted(tmp_path):
    value = b"synthetic-indicator"
    config = tmp_path / 'indicators.json'
    config.write_text(json.dumps([{
        'rule': 'private_indicator',
        'literal_base64': base64.b64encode(value).decode(),
    }]))
    bundle = tmp_path / 'companion.zip'
    with zipfile.ZipFile(bundle, 'w') as archive:
        archive.writestr(value.decode()+'.txt', value.upper())
    report = scan_companion(bundle, tmp_path / 'report', private_patterns=config, literal_only=True)
    assert report['limits'] == []
    assert report['findings'][0]['rule'] == 'private_indicator'
    assert value.decode() not in json.dumps(report).lower()
    assert report['rule_mode'] == 'supplemental_private_literals'
    assert 'Public rules are not repeated' in report['boundary']
    assert report['findings'][0]['path'].endswith('[redacted-private-indicator].txt')


def test_legacy_tar_without_ustar_magic_still_scans_actual_members(tmp_path):
    nested = io.BytesIO()
    text = b'localhost' + b':8000'
    with tarfile.open(fileobj=nested, mode='w', format=tarfile.GNU_FORMAT) as archive:
        member = tarfile.TarInfo('legacy/example.txt')
        member.size = len(text)
        archive.addfile(member, io.BytesIO(text))
    body = bytearray(nested.getvalue())
    body[257:265] = b'\0' * 8
    body[148:156] = b' ' * 8
    body[148:156] = ('%06o\0 ' % sum(body[:512])).encode()
    bundle = tmp_path / 'companion.zip'
    with zipfile.ZipFile(bundle, 'w') as archive:
        archive.writestr('source.tar.gz', gzip.compress(body))
    report = scan_companion(bundle, tmp_path / 'report')
    assert report['limits'] == []
    assert report['findings'][0]['path'].endswith('!legacy/example.txt')
    assert report['regular_files_scanned'] == 1
