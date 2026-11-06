"""Verify a versioned product export or prepare an update without touching it.

Source bytes come only from an explicitly named Git commit under campusboard/.
Offline verification recomputes the package tree from its Git blob IDs; checking
that the commit actually contains that tree additionally needs the source repo.
Text files may have checkout CRLFs; shipped bytes are hashed independently.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tempfile

from check_public import check

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "src/studycenter_public/core"
MANIFEST = ROOT / "packaging/product-source.json"
POLICY = ROOT / "packaging/product-adapters.json"
HASH = re.compile(r"[0-9a-f]{64}\Z")
OBJECT = {"sha1": re.compile(r"[0-9a-f]{40}\Z"), "sha256": HASH}
FORBIDDEN = {".local", ".env", ".venv", "browser-profiles", "profiles", "data", "logs"}
TEXT = {".py", ".html", ".css", ".js", ".txt", ".json"}
ASSETS = {"static/quest-journey.webp", "static/studycenter.ico"}


def valid_name(name: str) -> bool:
    if not isinstance(name, str) or "\\" in name or ":" in name:
        return False
    p = PurePosixPath(name)
    if not name or str(p) != name or p.is_absolute() or any(
        x in {"", ".", ".."} or x.startswith(".") or x.lower() in FORBIDDEN for x in p.parts
    ):
        return False
    return ((len(p.parts) == 1 and p.suffix == ".py") or
            (len(p.parts) == 2 and p.parts[0] == "templates" and p.suffix == ".html") or
            (p.parts[0] == "static" and len(p.parts) in (2, 3) and
             (len(p.parts) == 2 or p.parts[1] == "vendor") and
             (p.suffix in TEXT or name in ASSETS)))


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def linked(path: Path) -> bool:
    return path.is_symlink() or bool(getattr(path.lstat(), "st_file_attributes", 0) & 0x400)


def inventory(directory: Path) -> dict[str, bytes]:
    """Read the Public product only, refusing links and unexpected content."""
    if not directory.is_dir() or linked(directory):
        raise ValueError("product directory missing or linked")
    result: dict[str, bytes] = {}

    def walk(folder: Path) -> None:
        for item in sorted(folder.iterdir()):
            name = item.relative_to(directory).as_posix()
            if linked(item):
                raise ValueError(f"linked path: {name}")
            if item.is_dir():
                if item.name == "__pycache__":
                    for cached in item.iterdir():
                        if linked(cached) or not cached.is_file() or cached.suffix != ".pyc":
                            raise ValueError(f"unexpected cache content: {cached.relative_to(directory).as_posix()}")
                elif name in ("static", "static/vendor", "templates"):
                    walk(item)
                else:
                    raise ValueError(f"unexpected product directory: {name}")
            elif item.is_file() and valid_name(name):
                result[name] = item.read_bytes()
            else:
                raise ValueError(f"unexpected product file: {name}")

    walk(directory)
    check_names(result)
    return result


def check_names(records: dict) -> None:
    if len({name.casefold() for name in records}) != len(records):
        raise ValueError("case-colliding product paths")


def gate(name: str, data: bytes) -> None:
    failures = check(name, data)
    if failures:
        raise ValueError(f"content gate failed: {name}: " + ", ".join(rule for _, rule in failures))


def read_json(path: Path) -> dict:
    def no_duplicates(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key in {path.name}: {key}")
            result[key] = value
        return result

    raw = path.read_bytes()
    gate(path.name, raw)
    value = json.loads(raw.decode("utf-8"), object_pairs_hook=no_duplicates)
    if not isinstance(value, dict):
        raise ValueError(f"invalid JSON object: {path.name}")
    return value


def load_policy(path: Path) -> dict:
    doc = read_json(path)
    if set(doc) != {"format", "adaptations", "additions"} or doc["format"] != 1:
        raise ValueError("invalid adapter policy")
    for kind in ("adaptations", "additions"):
        records = doc[kind]
        if not isinstance(records, dict):
            raise ValueError(f"invalid {kind} policy")
        for name, reason in records.items():
            if not valid_name(name) or not isinstance(reason, str) or not reason.strip():
                raise ValueError(f"invalid {kind} policy entry: {name}")
    if set(doc["adaptations"]) & set(doc["additions"]):
        raise ValueError("overlapping adapter policy paths")
    check_names(doc["adaptations"] | doc["additions"])
    return doc


def policy_hash(policy: dict) -> str:
    # Bind semantic policy, independent of JSON indentation and checkout EOLs.
    canonical = json.dumps(policy, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return digest(canonical)


def object_hash(kind: str, data: bytes, algorithm: str) -> str:
    return hashlib.new(algorithm, kind.encode("ascii") + b" " + str(len(data)).encode("ascii") + b"\0" + data).hexdigest()


def tree_hash(files: dict, algorithm: str) -> str:
    """Compute a Git tree object ID without writing objects to any repository."""
    root: dict = {}
    for name, record in files.items():
        node = root
        parts = name.split("/")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = (record["mode"], record["git_blob"])

    def build(node: dict) -> str:
        entries = []
        for name, value in node.items():
            raw_name = name.encode("utf-8")
            if isinstance(value, dict):
                mode, oid, sort_name = "40000", build(value), raw_name + b"/"
            else:
                mode, oid, sort_name = value[0], value[1], raw_name
            entries.append((sort_name, mode.encode("ascii") + b" " + raw_name + b"\0" + bytes.fromhex(oid)))
        return object_hash("tree", b"".join(entry for _, entry in sorted(entries)), algorithm)

    return build(root)


def load_manifest(path: Path) -> dict:
    doc = read_json(path)
    if set(doc) != {"format", "source", "policy_sha256", "files", "additions"} or doc["format"] != 2:
        raise ValueError("invalid manifest format; an immutable source binding is required")
    source = doc["source"]
    if not isinstance(source, dict) or set(source) != {"commit", "tree", "object_format", "path"}:
        raise ValueError("invalid source binding")
    algorithm = source["object_format"]
    if algorithm not in OBJECT or source["path"] != "campusboard" or any(
        not isinstance(source[key], str) or not OBJECT[algorithm].fullmatch(source[key]) for key in ("commit", "tree")
    ):
        raise ValueError("invalid source commit or tree")
    if not isinstance(doc["policy_sha256"], str) or not HASH.fullmatch(doc["policy_sha256"]):
        raise ValueError("invalid adapter policy hash")
    if not isinstance(doc["files"], dict) or not isinstance(doc["additions"], dict) or not doc["files"]:
        raise ValueError("invalid manifest records")
    if set(doc["files"]) & set(doc["additions"]):
        raise ValueError("overlapping manifest paths")
    for name, record in doc["files"].items():
        if not valid_name(name) or not isinstance(record, dict) or set(record) != {"git_blob", "mode", "shipped_sha256"}:
            raise ValueError(f"invalid manifest entry: {name}")
        if (record["mode"] not in {"100644", "100755"} or not isinstance(record["git_blob"], str)
            or not OBJECT[algorithm].fullmatch(record["git_blob"]) or not isinstance(record["shipped_sha256"], str)
            or not HASH.fullmatch(record["shipped_sha256"])):
            raise ValueError(f"invalid manifest object or hash: {name}")
    for name, record in doc["additions"].items():
        if (not valid_name(name) or not isinstance(record, dict) or set(record) != {"shipped_sha256"}
            or not isinstance(record["shipped_sha256"], str) or not HASH.fullmatch(record["shipped_sha256"])):
            raise ValueError(f"invalid addition: {name}")
    check_names(doc["files"] | doc["additions"])
    if tree_hash(doc["files"], algorithm) != source["tree"]:
        raise ValueError("declared upstream files do not reconstruct the source tree")
    return doc


def git(repo: Path, *args: str, input_data: bytes | None = None) -> bytes:
    # Ambient Git variables and replacement refs must not redirect the named
    # repository or substitute different objects for the immutable IDs.
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
    env.update(GIT_NO_REPLACE_OBJECTS="1", GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
    try:
        result = subprocess.run(["git", "--no-optional-locks", "-C", str(repo), *args],
                                input=input_data, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                check=True, timeout=120, env=env)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError(f"cannot read requested Git object ({args[0]})") from exc
    return result.stdout


def source_locations(repo: Path) -> tuple[Path, Path, Path]:
    """Require the source worktree root and identify its Git storage paths."""
    if not repo.is_dir():
        raise ValueError("source repository does not exist")
    try:
        lines = git(repo, "rev-parse", "--show-toplevel", "--absolute-git-dir",
                    "--path-format=absolute", "--git-common-dir").decode("utf-8").splitlines()
    except ValueError as exc:
        raise ValueError("source repository must be a non-bare worktree root, not its Git directory") from exc
    if len(lines) != 3:
        raise ValueError("cannot identify source repository locations")
    root, git_dir, common_dir = (Path(line).resolve() for line in lines)
    if repo.resolve() != root:
        raise ValueError("source repository must be the worktree root, not a subdirectory")
    return root, git_dir, common_dir


def source_snapshot(repo: Path, commit: str) -> tuple[dict, dict[str, dict]]:
    """Read committed package blobs, never the source working tree or user data."""
    source_locations(repo)
    algorithm = git(repo, "rev-parse", "--show-object-format").decode("ascii").strip()
    if algorithm not in OBJECT or not isinstance(commit, str) or not OBJECT[algorithm].fullmatch(commit):
        raise ValueError("revision must be a full immutable Git commit ID")
    resolved = git(repo, "rev-parse", "--verify", "--end-of-options", commit + "^{commit}").decode("ascii").strip()
    if resolved != commit:
        raise ValueError("revision does not identify the requested commit")
    tree = git(repo, "rev-parse", "--verify", "--end-of-options", commit + ":campusboard").decode("ascii").strip()
    if not OBJECT[algorithm].fullmatch(tree):
        raise ValueError("invalid package tree ID")
    entries: dict[str, dict] = {}
    listing = git(repo, "ls-tree", "-r", "-z", tree)
    for entry in listing.split(b"\0"):
        if not entry:
            continue
        metadata, raw_name = entry.split(b"\t", 1)
        mode, kind, oid = metadata.decode("ascii").split(" ")
        name = raw_name.decode("utf-8")
        if not valid_name(name) or mode not in {"100644", "100755"} or kind != "blob" or not OBJECT[algorithm].fullmatch(oid):
            raise ValueError(f"unsupported path or object in source package: {name}")
        entries[name] = {"mode": mode, "git_blob": oid}
    if not entries:
        raise ValueError("source package is empty")
    check_names(entries)
    if tree_hash(entries, algorithm) != tree:
        raise ValueError("source package tree is incomplete")
    requested = list(entries.items())
    batch = git(repo, "cat-file", "--batch", input_data=b"".join((record["git_blob"] + "\n").encode("ascii") for _, record in requested))
    offset = 0
    for name, record in requested:
        end = batch.find(b"\n", offset)
        if end < 0:
            raise ValueError("incomplete Git blob batch")
        header = batch[offset:end].decode("ascii").split(" ")
        if len(header) != 3 or header[:2] != [record["git_blob"], "blob"] or not header[2].isdigit():
            raise ValueError(f"invalid Git blob header: {name}")
        size = int(header[2])
        start, end = end + 1, end + 1 + size
        data = batch[start:end]
        if len(data) != size or batch[end:end + 1] != b"\n" or object_hash("blob", data, algorithm) != record["git_blob"]:
            raise ValueError(f"invalid Git blob content: {name}")
        record["data"] = data
        offset = end + 1
    if offset != len(batch):
        raise ValueError("unexpected Git blob batch content")
    return {"commit": commit, "tree": tree, "object_format": algorithm, "path": "campusboard"}, entries


def matches_blob(name: str, data: bytes, oid: str, algorithm: str) -> bool:
    if object_hash("blob", data, algorithm) == oid:
        return True
    # Git text checkout may add CRLF. Raw bytes are separately fixed by SHA-256.
    return PurePosixPath(name).suffix in TEXT and object_hash("blob", data.replace(b"\r\n", b"\n"), algorithm) == oid


def verify_worktree(source_repo: Path, doc: dict) -> None:
    """Compare only allowlisted source paths; never enter other directories."""
    root = source_locations(source_repo)[0] / "campusboard"
    if not root.is_dir() or linked(root):
        raise ValueError("source worktree package missing or linked")
    paths: dict[str, Path] = {}

    def walk(folder: Path) -> None:
        for item in sorted(folder.iterdir()):
            name = item.relative_to(root).as_posix()
            if name in ("static", "static/vendor", "templates"):
                if linked(item) or not item.is_dir():
                    raise ValueError(f"linked or invalid source directory: {name}")
                walk(item)
            elif valid_name(name):
                if linked(item) or not item.is_file():
                    raise ValueError(f"linked or invalid source file: {name}")
                paths[name] = item
            # Do not enter unknown directories or read non-source files.

    walk(root)
    check_names(paths)
    expected = set(doc["files"])
    if set(paths) != expected:
        raise ValueError(f"source worktree file set differs; missing={sorted(expected-set(paths))}; extra={sorted(set(paths)-expected)}")
    for name, path in paths.items():
        # Recheck before opening; no extra/untracked file is opened.
        if linked(path) or not path.is_file():
            raise ValueError(f"linked or invalid source file: {name}")
        if not matches_blob(name, path.read_bytes(), doc["files"][name]["git_blob"], doc["source"]["object_format"]):
            raise ValueError(f"source worktree content drift: {name}")


def verify(doc: dict, public: Path, policy_path: Path, source_repo: Path | None = None, *, check_worktree: bool = False) -> None:
    policy = load_policy(policy_path)
    if policy_hash(policy) != doc["policy_sha256"]:
        raise ValueError("adapter policy drift")
    shipped = inventory(public)
    expected = set(doc["files"]) | set(doc["additions"])
    if set(shipped) != expected:
        raise ValueError(f"product file set differs; missing={sorted(expected-set(shipped))}; extra={sorted(set(shipped)-expected)}")
    adapted = set()
    for name, data in shipped.items():
        record = doc["files"].get(name) or doc["additions"][name]
        if digest(data) != record["shipped_sha256"]:
            raise ValueError(f"shipped content drift: {name}")
        gate(name, data)
        if name in doc["files"] and not matches_blob(name, data, record["git_blob"], doc["source"]["object_format"]):
            adapted.add(name)
    if adapted != set(policy["adaptations"]) or set(doc["additions"]) != set(policy["additions"]):
        raise ValueError(f"adapter policy does not exactly cover adaptations={sorted(adapted)} and additions={sorted(doc['additions'])}")
    if source_repo is not None:
        source, upstream = source_snapshot(source_repo, doc["source"]["commit"])
        if source != doc["source"] or set(upstream) != set(doc["files"]):
            raise ValueError("source commit or tree differs from manifest")
        for name, record in upstream.items():
            if any(record[key] != doc["files"][name][key] for key in ("git_blob", "mode")):
                raise ValueError(f"upstream object drift: {name}")
    if check_worktree:
        if source_repo is None:
            raise ValueError("worktree verification requires a source repository")
        verify_worktree(source_repo, doc)


def make_manifest(source: dict, upstream: dict, shipped: dict, policy_path: Path) -> dict:
    policy = load_policy(policy_path)
    if not set(upstream) <= set(shipped):
        raise ValueError(f"upstream file missing from product: {sorted(set(upstream)-set(shipped))}")
    adapted = {name for name, record in upstream.items()
               if not matches_blob(name, shipped[name], record["git_blob"], source["object_format"])}
    added = set(shipped) - set(upstream)
    if adapted != set(policy["adaptations"]) or added != set(policy["additions"]):
        raise ValueError(f"adapter policy must exactly cover adaptations={sorted(adapted)} and additions={sorted(added)}")
    for name, data in shipped.items():
        gate(name, data)
    return {"format": 2, "source": source, "policy_sha256": policy_hash(policy),
            "files": {name: {"git_blob": record["git_blob"], "mode": record["mode"],
                             "shipped_sha256": digest(shipped[name])} for name, record in sorted(upstream.items())},
            "additions": {name: {"shipped_sha256": digest(shipped[name])} for name in sorted(added)}}


def encoded_manifest(doc: dict) -> bytes:
    data = (json.dumps(doc, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    gate("product-source.json", data)
    return data


def capture(source_repo: Path, commit: str, policy_path: Path, public: Path, output: Path) -> None:
    """Write a new, reviewable manifest; never overwrite an existing baseline."""
    if any(output.resolve().is_relative_to(location) for location in source_locations(source_repo)):
        raise ValueError("manifest output cannot be in the source repository or its Git storage")
    source, upstream = source_snapshot(source_repo, commit)
    doc = make_manifest(source, upstream, inventory(public), policy_path)
    data = encoded_manifest(doc)
    if output.exists():
        raise ValueError("manifest output already exists; choose a new candidate path")
    created = False
    try:
        with output.open("xb") as stream:
            created = True
            stream.write(data)
        verify(load_manifest(output), public, policy_path, source_repo)
    except BaseException:
        if created:
            output.unlink(missing_ok=True)
        raise


def update(doc: dict, public: Path, policy_path: Path, source_repo: Path, commit: str, output: Path) -> Path:
    """Prepare and validate a complete candidate; never mutate the live export."""
    verify(doc, public, policy_path, source_repo)
    source, upstream = source_snapshot(source_repo, commit)
    if set(upstream) != set(doc["files"]):
        raise ValueError("upstream file set changed; review additions or removals manually")
    policy = load_policy(policy_path)
    shipped = inventory(public)
    for name, record in upstream.items():
        previous = doc["files"][name]
        if any(record[key] != previous[key] for key in ("git_blob", "mode")):
            if name in policy["adaptations"]:
                raise ValueError(f"upstream changed adapted file; merge manually: {name}")
            gate(name, record["data"])
            shipped[name] = record["data"]
    candidate = make_manifest(source, upstream, shipped, policy_path)
    manifest_data = encoded_manifest(candidate)
    target = output.resolve()
    for protected in (public.resolve(), *source_locations(source_repo)):
        if target.is_relative_to(protected) or protected.is_relative_to(target):
            raise ValueError("candidate must be outside the live product and source repository")
    if output.exists():
        raise ValueError("candidate output already exists")
    output.mkdir()
    stage = None
    try:
        stage = Path(tempfile.mkdtemp(prefix=".product-update-", dir=output))
        staged_product = stage / "core"
        staged_product.mkdir()
        for name, data in shipped.items():
            destination = staged_product.joinpath(*PurePosixPath(name).parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
            if os.name != "nt" and name in upstream and upstream[name]["mode"] == "100755":
                destination.chmod(0o755)
        (stage / "product-adapters.json").write_bytes(policy_path.read_bytes())
        (stage / "product-source.json").write_bytes(manifest_data)
        verify(load_manifest(stage / "product-source.json"), staged_product, stage / "product-adapters.json", source_repo)
        final = output / "candidate"
        os.replace(stage, final)
        return final
    except BaseException:
        if stage is not None and stage.exists():
            if not stage.resolve().is_relative_to(output.resolve()):
                raise RuntimeError("unsafe candidate cleanup path")
            shutil.rmtree(stage)
        output.rmdir()
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("verify", "capture", "update"))
    parser.add_argument("--source-repo", type=Path, help="root of the non-bare Git worktree containing the source commit; never its data")
    parser.add_argument("--revision", help="full immutable source commit ID (capture/update)")
    parser.add_argument("--check-worktree", action="store_true", help="also compare allowlisted source worktree files (verify only)")
    parser.add_argument("--product", type=Path, default=PUBLIC)
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    parser.add_argument("--policy", type=Path, default=POLICY)
    parser.add_argument("--output", type=Path, help="new candidate manifest (capture) or directory (update)")
    args = parser.parse_args()
    try:
        if args.action == "verify":
            if args.revision or args.output:
                parser.error("verify reads the manifest's commit and takes no --revision or --output")
            if args.check_worktree and not args.source_repo:
                parser.error("--check-worktree requires --source-repo")
            verify(load_manifest(args.manifest), args.product, args.policy, args.source_repo, check_worktree=args.check_worktree)
            evidence = "commit and tree checked against source Git objects" if args.source_repo else "offline tree checked; commit-to-tree binding requires --source-repo"
            if args.check_worktree:
                evidence += "; source worktree matches the pin"
            print(f"product source verify: OK ({evidence}; hashes do not establish behavioral parity)")
        else:
            if args.check_worktree:
                parser.error("--check-worktree is only available for verify")
            if not args.source_repo or not args.revision or not args.output:
                parser.error(f"{args.action} requires --source-repo, --revision and a new --output")
            if args.action == "capture":
                capture(args.source_repo, args.revision, args.policy, args.product, args.output)
                print(f"candidate manifest created: {args.output}")
            else:
                candidate = update(load_manifest(args.manifest), args.product, args.policy,
                                   args.source_repo, args.revision, args.output)
                print(f"candidate product and manifest created: {candidate}")
        return 0
    except (ValueError, OSError, KeyError, TypeError, UnicodeError, json.JSONDecodeError) as exc:
        print(f"product source {args.action} failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
