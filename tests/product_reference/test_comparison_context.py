import json

from fastapi.testclient import TestClient

from studycenter_public.core.app import create_app
from studycenter_public.core.comparison import comparison, page_context, save
from .reference_settings import Settings
from studycenter_public.core.db import encode
from .test_source_comparison import source


def test_context_is_bound_to_document_and_page_and_not_stored_in_decision(tmp_path):
    app = create_app(Settings(data_dir=tmp_path))
    db = app.state.db
    iid, doc = source(db, text='Lab 1 (max 15 points)')
    text = 'Lab 1 (max 15 points)\nSeitenkontext zu Lab 1.'
    db.execute('UPDATE documents SET pages=? WHERE id=?',
               (encode([{'page': 1, 'text': text}, {'page': 2, 'text': 'Andere Seite'}]), doc['id']))
    view = comparison(db, iid, 'obligation')
    card = view['cards'][0]
    assert card['context']['text'] == text and card['context']['matched']
    response = TestClient(app).get(f'/items/{iid}/compare?fact=obligation')
    assert response.status_code == 200
    assert 'Lab 1 (max 15 points)' in response.text
    assert 'Seitenkontext zu Lab 1.' in response.text and 'Andere Seite' not in response.text
    assert f'/documents/{doc["id"]}?page=1' in response.text and 'Zurück zum Eintrag' in response.text
    closing = TestClient(app).get(f'/items/{iid}/compare?fact=closing')
    assert 'Das Datum ist durch dieses kurze Zitat nicht eindeutig belegt.' in closing.text
    assert save(db, iid, 'obligation', view['token'], card['key'])
    profile = json.loads(db.one('SELECT value FROM item_preferences WHERE item_id=?', (iid,))['value'])
    assert 'context' not in profile['source_decisions']['obligation']['proofs'][0]


def test_context_is_bounded_and_reports_unmatched_or_missing():
    context = page_context('a' * 16000, 'nicht vorhanden')
    assert len(context['text']) == 12000 and context['truncated'] and not context['matched']
    assert page_context('', 'Zitat') == {'text': '', 'matched': False, 'truncated': False}
