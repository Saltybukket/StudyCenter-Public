"""Read source archive contents without extracting paths or following links.

Inventories and findings belong in an explicitly selected private report folder.
Only filenames, rules and content hashes are emitted; matched values are omitted.
"""
from __future__ import annotations

import argparse
import base64
import bz2
import gzip
import hashlib
import io
import json
import lzma
import re
from pathlib import Path
import stat
import struct
import tarfile
import tempfile
import zipfile
import zlib

try:
    from scripts.check_public import check, RULES
except ModuleNotFoundError:
    from check_public import check, RULES


def safe_label(value: str) -> str:
    encoded = value.encode("utf-8", errors="replace")
    for name in ("credential", "personal mail"):
        encoded = RULES[name].sub(b"[redacted-match]", encoded)
    return encoded.decode("utf-8", errors="replace")


def container_kind(name: str, prefix: bytes) -> str | None:
    lower = name.lower()
    if prefix.startswith((b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")):
        return "zip"
    if prefix.startswith(b"\x1f\x8b"):
        return "gzip"
    if prefix.startswith(b"BZh"):
        return "bzip2"
    if prefix.startswith(b"\xfd7zXZ\x00"):
        return "xz"
    if prefix.startswith(b"\x28\xb5\x2f\xfd"):
        return "zstandard"
    if prefix.startswith(b"LZIP"):
        return "lzip"
    if len(prefix) >= 512 and (prefix[257:262] == b"ustar" or lower.endswith(".tar")):
        return "tar"
    # Historical GNU/V7 archives need not carry the POSIX magic. Validate the
    # first header checksum before treating arbitrary binary bytes as a TAR.
    if len(prefix) >= 512 and prefix[:100].strip(b"\0"):
        try:
            tarfile.TarInfo.frombuf(prefix[:512], encoding="utf-8", errors="replace")
        except (tarfile.HeaderError, ValueError, OverflowError):
            pass
        else:
            return "tar"
    return None


def lzip_chunks(source):
    """Decode LZIP v1 using Python's raw LZMA1 codec, checking its trailer."""
    header = source.read(6)
    if len(header) != 6 or header[:5] != b"LZIP\x01":
        raise ValueError("Unsupported lzip header")
    code = header[5]
    exponent = code & 31
    if not 12 <= exponent <= 29:
        raise ValueError("Invalid lzip dictionary")
    dictionary = (1 << exponent) - ((1 << exponent) // 16) * (code >> 5)
    decoder = lzma.LZMADecompressor(format=lzma.FORMAT_RAW, filters=[{
        "id": lzma.FILTER_LZMA1, "dict_size": dictionary, "lc": 3, "lp": 0, "pb": 2,
    }])
    crc = size = consumed = 0
    while not decoder.eof:
        block = source.read(65536) if decoder.needs_input else b""
        if decoder.needs_input and not block:
            raise ValueError("Truncated lzip stream")
        consumed += len(block)
        output = decoder.decompress(block, max_length=65536)
        crc = zlib.crc32(output, crc)
        size += len(output)
        if output:
            yield output
    trailer = decoder.unused_data + source.read()
    if len(trailer) != 20:
        raise ValueError("Lzip trailing data or concatenated member requires review")
    expected_crc, expected_size, member_size = struct.unpack("<IQQ", trailer)
    if crc != expected_crc or size != expected_size or member_size != consumed + 6:
        raise ValueError("Lzip checksum or size mismatch")


class ArchiveScan:
    def __init__(self, report_dir, *, max_file_bytes=512 * 1024**2,
                 max_container_bytes=2 * 1024**3, max_total_bytes=80 * 1024**3,
                 max_depth=12, max_members=1000000, private_patterns=None,
                 literal_only=False):
        self.report_dir = Path(report_dir).resolve()
        self.report_dir.mkdir(parents=True, exist_ok=True)
        self.scratch = self.report_dir / "scratch"
        self.scratch.mkdir(exist_ok=True)
        self.max_file_bytes = max_file_bytes
        self.max_container_bytes = max_container_bytes
        self.max_total_bytes = max_total_bytes
        self.max_depth = max_depth
        self.max_members = max_members
        self.literal_only = literal_only
        self.private_rules = {}
        if private_patterns:
            records = json.loads(Path(private_patterns).read_text(encoding="utf-8"))
            for record in records:
                value = base64.b64decode(record["literal_base64"], validate=True)
                if not 3 <= len(value) <= 512:
                    raise ValueError("Private indicator length outside bounded range")
                self.private_rules[record["rule"]] = re.compile(re.escape(value), re.I)
        if literal_only and not self.private_rules:
            raise ValueError("Supplemental-only scan requires explicit private indicators")
        self.total_bytes = self.files = self.links = self.members = 0
        self.completed = False
        self.findings = []
        self.limits = []
        self.containers = []
        self.inventory = (self.report_dir / "files.jsonl").open("w", encoding="utf-8")

    def record(self, record):
        record = dict(record)
        record["path"] = self.label(record["path"])
        self.inventory.write(json.dumps(record, ensure_ascii=True) + "\n")

    def label(self, path):
        encoded = path.encode("utf-8", errors="replace")
        for pattern in self.private_rules.values():
            encoded = pattern.sub(b"[redacted-private-indicator]", encoded)
        return safe_label(encoded.decode("utf-8", errors="replace"))

    def content_rules(self, path, content):
        found = set() if self.literal_only else {
            rule for _, rule in check(path, content, artifacts=b"\0" in content)
        }
        if self.private_rules:
            variants = [content, path.encode("utf-8", errors="replace")]
            if b"\0" in content:
                variants.extend(content[offset:].decode("utf-16-le", errors="replace").encode("utf-8")
                                for offset in (0, 1))
            found.update(rule for rule, pattern in self.private_rules.items()
                         if any(pattern.search(value) for value in variants))
        return found

    def limited(self, path, reason):
        self.limits.append({"path": self.label(path), "reason": reason})

    def spool(self):
        return tempfile.SpooledTemporaryFile(max_size=8 * 1024**2, dir=self.scratch)

    def consume(self, source, path, *, depth=0, size=None):
        self.members += 1
        if self.members > self.max_members or depth > self.max_depth:
            self.limited(path, "member or nesting quota reached")
            return
        prefix = source.read(65536)
        kind = container_kind(path, prefix)
        cap = self.max_container_bytes if kind else self.max_file_bytes
        if size is not None and size > cap:
            self.limited(path, "declared member size exceeds quota")
            return
        digest = hashlib.sha256()
        read = 0
        rules = set()
        overlap = b""
        target = self.spool() if kind else None
        try:
            block = prefix
            while block:
                read += len(block)
                self.total_bytes += len(block)
                if read > cap or self.total_bytes > self.max_total_bytes:
                    self.limited(path, "actual read exceeds byte quota")
                    return
                digest.update(block)
                if target is not None:
                    target.write(block)
                else:
                    content = overlap + block
                    # Every rule value is ASCII. A UTF16-decoded ASCII character
                    # requires a NUL high byte, so NUL-free chunks cannot add a
                    # UTF16 finding. Binary bytes still receive the raw scan.
                    rules.update(self.content_rules(path, content))
                    overlap = (overlap + block)[-65536:]
                block = source.read(128 * 1024)
            sha = digest.hexdigest()
            if size is not None and read != size:
                self.limited(path, "member length differs from declared size")
                return
            if target is not None:
                target.seek(0)
                self.containers.append({"path": self.label(path), "kind": kind, "sha256": sha, "bytes": read})
                self.record({"path": path, "kind": kind, "sha256": sha, "bytes": read})
                self.expand(target, path, kind, depth + 1)
            else:
                self.files += 1
                self.record({"path": path, "kind": "regular_file", "sha256": sha, "bytes": read, "rules": sorted(rules)})
                self.findings.extend({"path": self.label(path), "rule": rule, "sha256": sha} for rule in sorted(rules))
        except Exception as error:
            # Deliberately omit exception text: archive names or bytes can be secret.
            self.limited(path, "container/read failure: " + type(error).__name__)
        finally:
            if target is not None:
                target.close()

    def expand(self, source, path, kind, depth):
        if kind == "zip":
            with zipfile.ZipFile(source) as archive:
                for member in archive.infolist():
                    name = path + "!" + member.filename
                    mode = member.external_attr >> 16
                    if member.is_dir():
                        continue
                    if stat.S_ISLNK(mode):
                        self.links += 1
                        self.record({"path": name, "kind": "link_not_followed"})
                        continue
                    if member.flag_bits & 1:
                        self.limited(name, "encrypted archive member not read")
                        continue
                    with archive.open(member) as stream:
                        self.consume(stream, name, depth=depth, size=member.file_size)
        elif kind == "tar":
            with tarfile.open(fileobj=source, mode="r|*") as archive:
                for member in archive:
                    name = path + "!" + member.name
                    if member.issym() or member.islnk():
                        self.links += 1
                        self.record({"path": name, "kind": "link_not_followed"})
                    elif member.isfile():
                        with archive.extractfile(member) as stream:
                            self.consume(stream, name, depth=depth, size=member.size)
                    elif not member.isdir():
                        self.record({"path": name, "kind": "special_member_not_opened"})
        else:
            with self.spool() as decoded:
                if kind == "lzip":
                    blocks = lzip_chunks(source)
                    reader = None
                else:
                    if kind == "gzip": reader = gzip.GzipFile(fileobj=source, mode="rb")
                    elif kind == "bzip2": reader = bz2.BZ2File(source)
                    elif kind == "xz": reader = lzma.LZMAFile(source)
                    elif kind == "zstandard":
                        import zstandard
                        reader = zstandard.ZstdDecompressor().stream_reader(source)
                    else: raise ValueError("Unsupported container format")
                    blocks = iter(lambda: reader.read(128 * 1024), b"")
                expanded = 0
                try:
                    for block in blocks:
                        expanded += len(block)
                        if expanded > self.max_container_bytes:
                            self.limited(path, "decompressed container exceeds quota")
                            return
                        decoded.write(block)
                    decoded.seek(0)
                    self.consume(decoded, path + "!decoded", depth=depth, size=expanded)
                finally:
                    if reader is not None:
                        reader.close()

    def finish(self, bundle, sha):
        self.inventory.close()
        boundary = (
            "Actual regular member bytes checked only against supplied private literal indicators, "
            "case-insensitively, including UTF16 alignments capable of ASCII matches. "
            "Public rules are not repeated in this supplemental run. "
            if self.literal_only else
            "Actual regular member bytes checked against public rules, including UTF16 alignments "
            "capable of ASCII matches. Binary members also receive these byte/string checks. "
        )
        boundary += (
            "No archive path extracted, no link followed, no downloaded source executed. "
            "No semantic PDF/OCR, executable disassembly or decryption claimed. "
            "Matched values omitted. Findings require individual primary-upstream classification; "
            "none automatically exempted. Listed decode/encryption/quota limits remain unscanned-content boundaries."
        )
        result = {
            "input": bundle.name, "input_sha256": sha,
            "regular_files_scanned": self.files, "bytes_read": self.total_bytes,
            "links_not_followed": self.links, "containers": self.containers,
            "traversal_completed": self.completed,
            "rule_mode": "supplemental_private_literals" if self.literal_only else "public_rules",
            "findings": self.findings, "limits": self.limits,
            "boundary": boundary,
        }
        (self.report_dir / "scan.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        return result


def scan_companion(bundle, report_dir, **limits):
    bundle = Path(bundle)
    scan = ArchiveScan(report_dir, **limits)
    with bundle.open("rb") as stream:
        sha = hashlib.file_digest(stream, "sha256").hexdigest()
    try:
        # ZIP itself is opened directly; only nested members need opaque spools.
        scan.expand(bundle, bundle.name, "zip", 0)
        scan.completed = True
    finally:
        result = scan.finish(bundle, sha)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--report-dir", type=Path, required=True)
    parser.add_argument("--private-patterns", type=Path)
    parser.add_argument("--literal-only", action="store_true")
    args = parser.parse_args()
    result = scan_companion(args.bundle, args.report_dir,
                            private_patterns=args.private_patterns, literal_only=args.literal_only)
    print(json.dumps({key: result[key] for key in ("input_sha256", "regular_files_scanned", "bytes_read", "links_not_followed")}))
    print("Findings:", len(result["findings"]), "explicit read limits:", len(result["limits"]))
    return bool(result["findings"] or result["limits"] or not result["traversal_completed"])


if __name__ == "__main__":
    raise SystemExit(main())
