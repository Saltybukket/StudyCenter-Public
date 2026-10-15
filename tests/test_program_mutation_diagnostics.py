import json

import pytest

from scripts import smoke_release


def record(letter, size=3):
    return {'size': size, 'sha256': letter * 64}


def changed_diagnostic(monkeypatch, before, after):
    monkeypatch.setattr(smoke_release, '_program_files', lambda program: after)
    with pytest.raises(ValueError, match='Program files changed') as failure:
        smoke_release.require_program_unchanged('synthetic-unused-program', before)
    message = str(failure.value)
    assert 'program_delta=' in message
    return json.loads(message.split('program_delta=', 1)[1]), message


def test_added_removed_changed_output_contains_only_relative_paths_hash_and_size(monkeypatch):
    before = {'changed.bin': record('a'), 'removed.bin': record('b')}
    after = {'changed.bin': record('c', 4), 'new/log.txt': record('d', 0)}
    delta, _ = changed_diagnostic(monkeypatch, before, after)
    assert delta['counts'] == {'added': 1, 'removed': 1, 'changed': 1}
    assert len(delta['changes']) == 3 and delta['truncated'] is False
    assert {value['path'] for value in delta['changes']} == {'changed.bin', 'removed.bin', 'new/log.txt'}
    assert all(set(value) <= {'path', 'kind', 'before', 'after', 'link_target_changed'} for value in delta['changes'])
    assert all(set(value[key]) == {'size', 'sha256'}
               for value in delta['changes'] for key in ('before', 'after') if value[key] is not None)


def test_global_limit_is_32_across_all_mutation_categories(monkeypatch):
    before = {f'old-{n}.bin': record('a') for n in range(40)} | {f'same-{n}.bin': record('b') for n in range(40)}
    after = {f'new-{n}.bin': record('c') for n in range(40)} | {f'same-{n}.bin': record('d') for n in range(40)}
    delta, _ = changed_diagnostic(monkeypatch, before, after)
    assert delta['counts'] == {'added': 40, 'removed': 40, 'changed': 40}
    assert len(delta['changes']) == 32 and delta['total_changes'] == 120 and delta['truncated'] is True


def test_link_target_and_unknown_metadata_values_are_never_output(monkeypatch):
    before = {'lib.so': dict(record('a'), link_target='synthetic-sensitive-name-a', metadata='synthetic-sensitive-value')}
    after = {'lib.so': dict(record('a'), link_target='synthetic-sensitive-name-b', metadata='synthetic-sensitive-value')}
    delta, message = changed_diagnostic(monkeypatch, before, after)
    assert delta['counts']['changed'] == 1 and delta['changes'][0]['link_target_changed'] is True
    assert 'synthetic-sensitive' not in message and 'link_target"' not in message


def test_unchanged_tree_remains_positive(monkeypatch):
    unchanged = {'own.bin': record('a')}
    monkeypatch.setattr(smoke_release, '_program_files', lambda program: unchanged)
    assert smoke_release.require_program_unchanged('synthetic-unused-program', unchanged) is None


def test_invalid_snapshot_still_refuses_without_disclosing_values(monkeypatch):
    delta, message = changed_diagnostic(monkeypatch, {'own.bin': record('a')},
                                        {'../synthetic-private': {'size': 1, 'sha256': 'synthetic-sensitive-not-a-hash'}})
    assert delta == {'status': 'invalid_snapshot_records'}
    assert 'synthetic-sensitive' not in message and '../' not in message
