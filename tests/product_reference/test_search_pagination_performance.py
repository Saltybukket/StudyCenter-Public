import json
import sqlite3

import pytest
from contextlib import closing

from studycenter_public.core.db import Database
from studycenter_public.core.search import SearchIndex


@pytest.mark.parametrize('prioritize_current',[False,True])
def test_search_fetches_full_text_only_for_selected_groups(tmp_path, monkeypatch,prioritize_current):
    index = SearchIndex(Database(tmp_path / 'data.sqlite3'))
    index.refresh()
    with closing(sqlite3.connect(index.path)) as con, con:
        for identifier in range(25):
            meta = json.dumps({'hash': f'{identifier:064x}', 'course': 'Test', 'label': 'PDF'})
            for page in ([1, 2] if identifier == 0 else [1]):
                con.execute('INSERT INTO search VALUES(?,?,?,?,?,?,?,?,?,?)',
                            ('document', identifier, page, 'pdf', 1, 1, f'Dokument {identifier}',
                             'Prüfung AB-12 <script> Beispiel', 'AB12', meta))
    queries = []
    connect = sqlite3.connect
    def traced(*args, **kwargs):
        con = connect(*args, **kwargs)
        con.set_trace_callback(queries.append)
        return con
    monkeypatch.setattr(sqlite3, 'connect', traced)
    first, total, page = index.search('AB12',prioritize_current=prioritize_current)
    assert (total, page, len(first)) == (25, 1, 20)
    grouped = next(row for row in first if row['entity_id'] == 0)
    assert grouped['matches'] == 2 and [p['page'] for p in grouped['pages']] == [1, 2]
    assert '<mark>AB-12</mark>' in grouped['excerpt'] and '&lt;script&gt;' in grouped['excerpt']
    assert grouped['body'].startswith('Prüfung AB-12') and grouped['details'] == 'AB12'
    assert any('SELECT rowid,body,details' in query for query in queries)
    matching = next(query for query in queries if 'snippet(search' in query)
    assert 'SELECT *' not in matching and ',body,' not in matching and ',details,' not in matching
    second, total, page = index.search('AB12', page=2,prioritize_current=prioritize_current)
    assert (total, page, len(second)) == (25, 2, 5)
    assert {r['entity_id'] for r in first}.isdisjoint({r['entity_id'] for r in second})
