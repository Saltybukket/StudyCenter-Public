"""Release tag and cached component identity boundaries."""
import hashlib
import io
import json
import pytest
from scripts import build_slim


@pytest.mark.parametrize('actual,accepted', [('0.1.0b4', True), ('0.1.0b3', False)])
def test_package_readiness_is_bound_to_candidate_version(tmp_path, monkeypatch, actual, accepted):
    from scripts import smoke_slim
    (tmp_path/'runtime.json').write_text(json.dumps({'url':'http://127.0.0.1:8767'}))
    process = type('Running', (), {'poll': lambda self: None})()
    monkeypatch.setattr(smoke_slim.urllib.request, 'urlopen', lambda *a, **kw:
                        io.BytesIO(json.dumps({'version':actual, 'application':'StudyCenter.Public'}).encode()))
    if accepted:
        assert smoke_slim.wait_http(process, tmp_path, '0.1.0b4') == 'http://127.0.0.1:8767'
    else:
        with pytest.raises(AssertionError):
            smoke_slim.wait_http(process, tmp_path, '0.1.0b4')


def test_release_tag_requires_exact_beta_syntax():
    assert build_slim.release_version('v0.1.0-beta.4') == '0.1.0b4'
    for tag in ('v0.1.0-beta.04', 'v0.1.0-beta.4/other', '0.1.0b4', 'v0.1.0-beta.0'):
        with pytest.raises(ValueError):
            build_slim.release_version(tag)


def test_cached_archives_require_pinned_origin_hash_and_size(tmp_path, monkeypatch):
    platform = 'linux'
    hashes = {}
    records = {}
    for component in ('browser', 'ocr', 'speech'):
        suffix = 'speech-notices' if component == 'speech' else component
        name = f'studycenter-public-0.1.0b3-linux-x86_64-{suffix}.tar.gz'
        payload = component.encode()
        (tmp_path/name).write_bytes(payload)
        hashes[component] = hashlib.sha256(payload).hexdigest()
        records[component] = {'archive': {'filename': name, 'url': build_slim.ORIGIN + 'v0.1.0-beta.3/' + name,
                                         'sha256': hashes[component], 'size': len(payload)}}
    monkeypatch.setitem(build_slim.BETA3_SHA256, platform, hashes)
    assert build_slim.verify_cached_components(records, platform, tmp_path) == records
    records['ocr']['archive']['url'] = 'https://example.org/' + records['ocr']['archive']['filename']
    with pytest.raises(ValueError, match='identity or origin'):
        build_slim.verify_cached_components(records, platform, tmp_path)
    records['ocr']['archive']['url'] = build_slim.ORIGIN + 'v0.1.0-beta.3/' + records['ocr']['archive']['filename']
    (tmp_path/records['ocr']['archive']['filename']).write_bytes(b'bad')
    with pytest.raises(ValueError, match='archive changed'):
        build_slim.verify_cached_components(records, platform, tmp_path)
