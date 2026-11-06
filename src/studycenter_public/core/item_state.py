"""Transactional personal item changes shared by the UI and assistant.

Callers own the transaction, including any assistant receipt written with it.
Source evidence and personal decisions are deliberately separate from these fields.
"""
import json
import re

from .db import encode, now
from .models import Candidate


REVISIONS = {
    'status': 'status_revision',
    'pinned': 'pin_revision',
    'ignored': 'visibility_revision',
    'overrides': 'content_revision',
    'payload': 'content_revision',
}


class StaleItem(ValueError):
    pass


def revision(value):
    if value is None:
        return None  # Compatibility for existing tokenless clients.
    if not re.fullmatch(r'0|[1-9][0-9]{0,18}', str(value)):
        raise ValueError('Ungültiger Bearbeitungsstand.')
    result = int(value)
    if result > 9223372036854775807:
        raise ValueError('Ungültiger Bearbeitungsstand.')
    return result


def _row(con, item_id):
    row = con.execute('SELECT * FROM items WHERE id=?', (item_id,)).fetchone()
    if row is None:
        raise LookupError('Eintrag nicht gefunden.')
    return dict(row)


def _check_revision(row, field, expected, same_value):
    expected = revision(expected)
    actual = row[REVISIONS[field]]
    if expected is None or expected == actual:
        return False
    # A duplicate delivery of the immediately preceding idempotent operation
    # succeeds without applying it again. Later changes, including ABA, fail.
    if expected + 1 == actual and same_value:
        return True
    raise StaleItem('Dieser Eintrag wurde inzwischen geändert. Bitte die Seite neu laden.')


def set_personal_field(con, item_id, field, value, *, expected_revision=None):
    if field not in ('status', 'pinned', 'ignored'):
        raise ValueError('Ungültige Änderung.')
    if field == 'status':
        if value not in ('open', 'doing', 'done'):
            raise ValueError('Ungültiger Status.')
    elif type(value) not in (int, bool) or value not in (0, 1):
        raise ValueError('Ungültige Einstellung.')
    else:
        value = int(value)
    row = _row(con, item_id)
    duplicate = _check_revision(row, field, expected_revision, row[field] == value)
    if not duplicate and row[field] != value:
        con.execute(f'UPDATE items SET {field}=? WHERE id=?', (value, item_id))
    if field == 'status' and value == 'done':
        # An explicit personal confirmation outlives a later source date change.
        con.execute("UPDATE class_completions SET state='confirmed' WHERE item_id=? AND state='automatic'", (item_id,))
    return _row(con, item_id)


def set_correction(con, item_id, overrides, *, expected_revision=None):
    row = _row(con, item_id)
    if not isinstance(overrides, dict):
        raise ValueError('Korrektur ungültig.')
    if overrides:
        Candidate.model_validate(json.loads(row['payload']) | overrides)
    if _check_revision(row, 'overrides', expected_revision, json.loads(row['overrides']) == overrides):
        return row
    if json.loads(row['overrides']) != overrides:
        con.execute('UPDATE items SET overrides=? WHERE id=?', (encode(overrides), item_id))
    return _row(con, item_id)


def patch_personal_item(con, item_id, patch):
    row = _row(con, item_id)
    current = json.loads(row['payload']) | json.loads(row['overrides'])
    value = Candidate.model_validate(current | patch)
    if row['identity'].startswith('note:'):
        con.execute("UPDATE items SET payload=?,overrides='{}',changed_at=? WHERE id=?",
                    (value.model_dump_json(), now(), item_id))
    else:
        overrides = json.loads(row['overrides']) | patch
        con.execute('UPDATE items SET overrides=? WHERE id=?', (encode(overrides), item_id))
    return _row(con, item_id)
