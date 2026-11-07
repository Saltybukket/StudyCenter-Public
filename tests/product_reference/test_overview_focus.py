from copy import deepcopy
from datetime import datetime

from bs4 import BeautifulSoup
from fastapi.testclient import TestClient

from studycenter_public.core.app import create_app
from .reference_settings import Settings
from studycenter_public.core.config import VIENNA
from studycenter_public.core.models import Candidate


def test_overview_separates_dated_work_from_uncertain_sources(tmp_path, monkeypatch):
    from studycenter_public.core import app as app_module

    fixed = datetime(2030, 11, 2, 9, tzinfo=VIENNA)
    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls.fromtimestamp(fixed.timestamp(), tz)
    monkeypatch.setattr(app_module, 'datetime', FixedDatetime)
    app = create_app(Settings(data_dir=tmp_path))

    def item(identifier, title, date, **changes):
        data = Candidate(title=title, kind='task', date=date, date_meaning='deadline',
                         obligation='required', quote='Beleg', page=1, anchor='test').model_dump(mode='json')
        data.update(changes)
        return {'id': identifier, 'identity': f'fixture:{identifier}', 'data': data,
                'status': 'open', 'ignored': 0, 'conflict': 0, 'pinned': 0,
                'seen': 1, 'changed_at': '2030-11-01T08:00:00+00:00', 'history': []}

    rows = [item(1, 'Konkrete Abgabe', '2030-11-05')]
    rows += [item(n, f'Offene Angabe {n}', None, source_state='unclear') for n in range(2, 10)]
    conflict = item(10, 'Dringender Widerspruch', '2030-11-02')
    conflict.update(conflict=1, date_conflict=True)
    rows.append(conflict)
    rows.append(item(11, 'Datierte unklare Quelle', '2030-11-03', source_state='unclear'))
    original = deepcopy(rows)
    app.state.db.items = lambda active_only=False: rows
    response = TestClient(app).get('/overview')
    assert response.status_code == 200
    page = BeautifulSoup(response.text, 'html.parser')
    assert 'Konkrete Abgabe' in page.select_one('#actions').get_text()
    assert 'Offene Angabe' not in page.select_one('#actions').get_text()
    assert 'Dringender Widerspruch' in page.select_one('#dated-checks').get_text()
    assert 'Datierte unklare Quelle' in page.select_one('#dated-checks').get_text()
    assert 'Datierte unklare Quelle' not in page.select_one('#undated-checks').get_text()
    assert 'Dringender Widerspruch' not in page.select_one('#actions').get_text()
    assert 'Termine offen' in page.select_one('#undated-checks').get_text()
    assert page.select_one('#actions form input[name=status]')['value'] == 'done'
    assert rows == original
