"""Keep the CI-only CPython classification bound to one published file."""
import json
from pathlib import Path

from scripts.check_public import check, reviewed_artifact_matches


def test_python31210_review_is_an_exact_additional_variant(tmp_path):
    reviews = Path(__file__).parents[1] / 'scripts' / 'artifact_reviews.json'
    records = json.loads(reviews.read_text(encoding='utf-8'))
    variants = records['_internal/python312.dll']
    assert isinstance(variants, list)
    by_hash = {variant['sha256']: variant for variant in variants}
    assert 'c045b57348c21f5f810bae60654ae39490846b487378e917595f1f95438f9546' in by_hash
    current = by_hash['9a0e3435aaa680d868150f87ab3e388ad2eebc22f87e036155c7b4eda8cd2120']
    assert current['rules'] == ['personal path']
    assert current['origin'] == 'https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip'
    assert current['published_archive_sha256'] == '4acbed6dd1c744b0376e3b1cf57ce906f9dc9e95e68824584c8099a63025a3c3'
    assert current['match_sha256'] == ['be1936f2977011e877d40b5aef9f8dce4b320e71d0c3297f68b7eef2064177ba']

    # A matching filename/rule and ordinary public-looking data cannot approve
    # different bytes. This also exercises the real content scanner first.
    component = tmp_path / '_internal' / 'python312.dll'
    component.parent.mkdir()
    component.write_bytes(('C:' + '/Users/' + 'synthetic/example').encode())
    findings = check('_internal/python312.dll', component.read_bytes(), True)
    assert findings == [('_internal/python312.dll', 'personal path')]
    assert reviewed_artifact_matches(tmp_path, findings, reviews) == (findings, [])
    component.write_bytes(component.read_bytes() + b' changed unreviewed bytes')
    assert reviewed_artifact_matches(tmp_path, findings, reviews) == (findings, [])
