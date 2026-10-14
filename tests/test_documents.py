import pytest
from studycenter_public.services.documents import DocumentService
from studycenter_public.storage.db import Database
from studycenter_public.platform.paths import AppPaths
from studycenter_public.services.documents import OCRSettings


def test_original_version_tables_and_full_text_are_retained(tmp_path):
    paths = AppPaths.create(tmp_path)
    service = DocumentService(Database(paths.database), paths)
    raw = b'<main><h1>Schedule</h1><table><tr><th>Week</th><th>Topic</th></tr><tr><td>1</td><td>Topology</td></tr></table><script>secret</script></main>'
    first = service.import_bytes(raw, title='Schedule', source_url='https://example.org/course', mime='text/html')
    assert service.original(first['id']).read_bytes() == raw
    assert 'Week | Topic' in service.source_text(first['id'])
    assert 'secret' not in service.source_text(first['id'])
    assert service.import_bytes(raw, title='Schedule', source_url='https://example.org/course', mime='text/html')['id'] == first['id']
    second = service.import_bytes(raw.replace(b'Topology', b'Algebra'), title='Schedule', source_url='https://example.org/course', mime='text/html')
    assert second['revision'] == 2
    assert 'Topology' in service.source_text(first['id'])
    assert service.search('Topology')[0]['document_id'] == first['id']


def test_pdf_page_reread_keeps_original_reading_and_hash(tmp_path):
    import pymupdf
    paths = AppPaths.create(tmp_path)
    service = DocumentService(Database(paths.database), paths, ocr=OCRSettings('missing-ocr'))
    pdf = pymupdf.open()
    page = pdf.new_page()
    page.insert_text((40, 40), 'Complete native course document. Deadline next Friday.')
    raw = pdf.tobytes()
    pdf.close()
    first = service.import_bytes(raw, title='Course', mime='application/pdf')
    old_text = service.source_text(first['id'])
    second = service.reread_page(first['id'], 1, ignore_grid=False)
    assert second['id'] != first['id']
    assert second['revision'] == 2
    assert first['hash'] == second['hash']
    assert service.source_text(first['id']) == old_text
    assert service.page(second['id'], 1)['metadata']['reading_options']['ignore_grid'] is False
    assert service.page_image(first['id'], 1).startswith(b'\x89PNG')


def test_long_source_is_not_silently_truncated(tmp_path):
    paths = AppPaths.create(tmp_path)
    service = DocumentService(Database(paths.database), paths)
    text = ('retained source paragraph\n' * 15000) + 'FINAL-SENTINEL'
    doc = service.import_bytes(text.encode(), title='Long', mime='text/plain')
    assert service.source_text(doc['id']) == text


def test_excluded_document_stays_readable_but_analysis_refuses_it(tmp_path):
    paths = AppPaths.create(tmp_path)
    db = Database(paths.database)
    source = db.upsert_source(name='Course', kind='web', metadata={'options':{'excluded_urls':['https://example.org/course/private']}})
    service = DocumentService(db, paths)
    doc = service.import_bytes(b'Retained source evidence', title='Page', source_url='https://example.org/course/private', mime='text/plain', source_id=source['id'])
    assert service.source_text(doc['id']) == 'Retained source evidence'
    with pytest.raises(ValueError, match='ausgeschlossen'):
        service.analysis_text(doc['id'])


def test_raster_pdf_reports_missing_ocr_without_losing_native_text(tmp_path):
    import pymupdf
    paths = AppPaths.create(tmp_path)
    service = DocumentService(Database(paths.database), paths, ocr=OCRSettings('missing-ocr'))
    image = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0,0,80,80), False)
    image.clear_with(255)
    pdf = pymupdf.open()
    page = pdf.new_page()
    page.insert_text((40,40), 'This native paragraph remains complete alongside the raster.')
    page.insert_image(pymupdf.Rect(40,80,240,280), stream=image.tobytes('png'))
    raw = pdf.tobytes()
    pdf.close()
    document = service.import_bytes(raw, title='Mixed', mime='application/pdf')
    assert 'native paragraph remains complete' in service.source_text(document['id'])
    assert any('OCR' in gap for gap in document['gaps'])
    assert service.original(document['id']).read_bytes() == raw
