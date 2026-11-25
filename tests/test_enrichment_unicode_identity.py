"""Tag identity matches Home's NFC lowercase policy without casefold expansion."""
import hashlib
import json

import pytest

from omd.enrich_note import (
    EnrichNoteError, build_proposal_response, build_standalone_request,
    decode_request, validate_model_output,
)
from omd.retrieval import extract_obsidian_tags

TAGS = ['straße', 'strasse', 'οσ', 'ος']


def request_payload(content='A source note.', tags=None):
    # Same payload shape/values as unicode-review-request.json built by Home.
    return {
        'schema_version': 1, 'request_id': 'unicode-review',
        'action': 'enrich_note_preview', 'vault_path': '/vault',
        'note': {'path': 'Inbox/Test.md', 'content': content,
                 'content_sha256': hashlib.sha256(content.encode()).hexdigest()},
        'candidates': [], 'vault_tags': TAGS if tags is None else tags,
        'model': 'qwen3:0.6b', 'host': 'http://localhost:11434',
    }


def decode(payload):
    return decode_request(json.dumps(payload, ensure_ascii=False).encode())


def proposal(tags, *, existing=True):
    items=[{'tag': tag, 'reason': 'A supported topic label.'} for tag in tags]
    if existing:
        items=[{**item, 'recommended': True} for item in items]
    return {'summary': 'A source note.', 'existing_links': [], 'new_concepts': [],
            'existing_tags': items if existing else [], 'new_tags': [] if existing else items}


def test_home_wire_tags_remain_distinct_through_proposal_validation():
    request=decode(request_payload())
    assert list(request.vault_tags)==TAGS
    result=validate_model_output(proposal(TAGS), request)
    assert [item['tag'] for item in result['existing_tags']]==TAGS
    generated=validate_model_output(proposal(TAGS, existing=False), decode(request_payload(tags=[])))
    assert [item['tag'] for item in generated['new_tags']]==TAGS


def test_standalone_catalog_preserves_same_distinct_tags(tmp_path):
    (tmp_path/'Source.md').write_text('A source note.')
    (tmp_path/'Tags.md').write_text('---\ntags: [straße, strasse, οσ, ος]\n---\nA source note.')
    raw, _warnings=build_standalone_request(str(tmp_path), 'Source.md', model='qwen3:0.6b', host='http://localhost:11434')
    assert set(raw.vault_tags)==set(TAGS)


def test_source_filter_does_not_hide_distinct_unicode_tags():
    content='---\ntags: [straße, οσ]\n---\nA source note.'
    request=decode(request_payload(content))
    response=build_proposal_response(request, validate_model_output(proposal(TAGS), request),
        provider='ollama', actual_model=request.model, endpoint_class='local_loopback')
    assert [item['tag'] for item in response['proposal']['existing_tags']]==['strasse', 'ος']


def test_extraction_keeps_distinct_forms_but_deduplicates_nfc_and_case():
    text='---\ntags: [straße, strasse, οσ, ος, café, cafe\u0301, CAFÉ]\n---\n#straße #strasse #οσ #ος'
    assert extract_obsidian_tags(text)==(*TAGS, 'café')


@pytest.mark.parametrize('tags', [['CAFÉ', 'café'], ['café', 'cafe\u0301'], ['Topic', 'topic']])
def test_wire_still_rejects_duplicate_canonical_tag_identities(tags):
    with pytest.raises(EnrichNoteError, match='unique'):
        decode(request_payload(tags=tags))


def test_inline_tags_retain_all_combining_marks_and_existing_boundaries():
    text = '''#بَحْث #हिन्दी #cafe\u0301 #topic/sub-tag #under_score
word#ignored #-invalid #/invalid #topic.with-punctuation
`#inline-code` #العَرَبِيَّة
~~~text
#fenced-code
~~~
'''
    assert extract_obsidian_tags(text) == (
        'بَحْث', 'हिन्दी', 'cafe\u0301', 'topic/sub-tag', 'under_score',
        'topic', 'العَرَبِيَّة',
    )
