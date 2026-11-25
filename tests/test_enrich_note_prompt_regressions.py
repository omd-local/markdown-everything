import hashlib
import json

import pytest

from omd.enrich_note import (
    _build_model_input,
    _resolve_model_references,
    decode_request,
    validate_model_output,
)
from omd.structured_output import StructuredOutputError, parse_structured_output


def request_for(content, *, candidates=(), tags=()):
    return decode_request(json.dumps({
        'schema_version': 1,
        'request_id': 'webpage-regression',
        'action': 'enrich_note_preview',
        'vault_path': '/vault',
        'note': {
            'path': 'Sources/Web/Embeddings.md',
            'content': content,
            'content_sha256': hashlib.sha256(content.encode()).hexdigest(),
        },
        'candidates': list(candidates),
        'vault_tags': list(tags),
        'model': 'qwen3:0.6b',
        'host': 'http://localhost:11434',
    }).encode())


def empty_proposal(**changes):
    return dict(summary='', existing_links=[], existing_tags=[], new_concepts=[], new_tags=[], **changes)


def test_converted_webpage_prompt_reaches_article_after_long_navigation():
    paragraph = 'Sparse embeddings contain mostly zero values; dense embeddings use compact learned vectors.'
    source = (
        '---\ntitle: Embeddings\nsource_url: https://example.test/article\n---\n'
        '# Embeddings\n\n> [Source](https://example.test/article) · Captured: `2026-09-16`\n'
        '## Full Content\n\n'
        + '[Sign in](https://example.test/login?redirect=' + 'x' * 12000 + ')\n\n'
        + '1. [Contents](https://example.test/article#contents)\n\n'
        + '# Embeddings\n\n' + paragraph + '\n'
    )
    request = request_for(source)
    encoded, _, _, _, evidence, _ = _build_model_input(request)
    prompt = json.loads(encoded)
    assert paragraph in prompt['untrusted_note']['content']
    assert 'example.test/login' not in prompt['untrusted_note']['content']
    assert 'Captured:' not in prompt['untrusted_note']['content']
    assert any('Sparse embeddings contain mostly zero values' in excerpt for excerpt in evidence.values())
    assert all(excerpt in source for excerpt in evidence.values())
    assert request.note.content == source


def test_prompt_schema_limits_links_to_candidates_actually_supplied():
    request = request_for('Sparse embeddings contain mostly zero values.', candidates=[{
        'id': 'candidate-known', 'path': 'Notes/Sparse.md', 'title': 'Sparse embeddings',
        'aliases': [], 'tags': [], 'evidence': 'Sparse embeddings contain zero values.',
    }])
    _, _, candidate_ids, schema, evidence, _ = _build_model_input(request)
    output = empty_proposal()
    output['existing_links'] = [{
        'candidate_id': 'candidate-invented', 'evidence': next(iter(evidence)),
        'reason': 'Shared topic', 'recommended': True,
    }]
    assert candidate_ids == {'candidate-known'}
    with pytest.raises(StructuredOutputError, match='allowed enum'):
        parse_structured_output(json.dumps(output), schema)


@pytest.mark.parametrize("tag_id", ["tag-1", "#Tag-1", " tag_1 "])
def test_internal_tag_id_in_new_tags_resolves_to_existing_unchecked_tag(tag_id):
    request = request_for('Embeddings support semantic search.', tags=['machine-learning'])
    output = empty_proposal()
    output['new_tags'] = [{'tag': tag_id, 'reason': 'Describes the source topic'}]
    resolved = _resolve_model_references(
        output,
        {},
        {'tag-1': 'machine-learning'},
        warnings=[],
        request_id=request.request_id,
    )
    validated = validate_model_output(resolved, request)
    assert validated['new_tags'] == []
    assert validated['existing_tags'] == [{
        'tag': 'machine-learning', 'reason': 'Describes the source topic', 'recommended': False,
    }]


@pytest.mark.parametrize("tag_id", ["tag-1", "#Tag-1", " tag_1 "])
def test_internal_tag_id_in_existing_tags_resolves_to_catalog_tag(tag_id):
    request = request_for('Embeddings support semantic search.', tags=['machine-learning'])
    output = empty_proposal()
    output['existing_tags'] = [{
        'tag': tag_id, 'reason': 'Matches the note topic.', 'recommended': True,
    }]
    resolved = _resolve_model_references(
        output,
        {},
        {'tag-1': 'machine-learning'},
        warnings=[],
        request_id=request.request_id,
    )
    validated = validate_model_output(resolved, request)
    assert validated['existing_tags'] == [{
        'tag': 'machine-learning', 'reason': 'Matches the note topic.',
        'recommended': True,
    }]
    assert validated['new_tags'] == []


@pytest.mark.parametrize('catalog_tag', ['machine-learning', 'hidden-catalog-tag'])
def test_freeform_existing_catalog_tag_is_never_recommended(catalog_tag):
    request = request_for(
        'Embeddings support semantic search.',
        tags=['machine-learning', 'hidden-catalog-tag'],
    )
    output = empty_proposal()
    output['existing_tags'] = [{
        'tag': catalog_tag,
        'reason': 'Matches the note topic.',
        'recommended': True,
    }]

    resolved = _resolve_model_references(
        output,
        {},
        {'tag-1': 'machine-learning'},
        warnings=[],
        request_id=request.request_id,
    )
    validated = validate_model_output(resolved, request)

    assert validated['existing_tags'] == [{
        'tag': catalog_tag,
        'reason': 'Matches the note topic.',
        'recommended': False,
    }]


def test_semantic_tag_in_existing_tags_becomes_normalized_new_tag_without_recommendation():
    request = request_for('Embeddings support semantic search.', tags=['machine-learning'])
    output = empty_proposal()
    output['existing_tags'] = [{
        'tag': '#Vector Search',
        'reason': 'Describes the retrieval method in this note.',
        'recommended': True,
    }]
    resolved = _resolve_model_references(
        output,
        {},
        {'tag-1': 'machine-learning'},
        warnings=[],
        request_id=request.request_id,
    )
    validated = validate_model_output(resolved, request)
    assert validated['existing_tags'] == []
    assert validated['new_tags'] == [{
        'tag': 'vector-search',
        'reason': 'Describes the retrieval method in this note.',
    }]


def test_reclassified_semantic_tags_respect_new_tag_response_bound():
    request = request_for('Embeddings support semantic search.')
    output = empty_proposal()
    output['existing_tags'] = [
        {
            'tag': f'misclassified-{index}',
            'reason': f'Misclassified topic {index}.',
            'recommended': True,
        }
        for index in range(20)
    ]
    output['new_tags'] = [
        {'tag': f'explicit-{index}', 'reason': f'Explicit topic {index}.'}
        for index in range(12)
    ]

    validated = validate_model_output(output, request)

    assert validated['existing_tags'] == []
    assert validated['new_tags'] == [
        {'tag': f'explicit-{index}', 'reason': f'Explicit topic {index}.'}
        for index in range(12)
    ]
    assert all('recommended' not in item for item in validated['new_tags'])


def test_catalog_tags_reclassified_from_new_tags_respect_existing_tag_response_bound():
    catalog = [f'catalog-{index}' for index in range(32)]
    request = request_for('Embeddings support semantic search.', tags=catalog)
    output = empty_proposal()
    output['existing_tags'] = [
        {
            'tag': tag,
            'reason': f'Existing topic {index}.',
            'recommended': True,
        }
        for index, tag in enumerate(catalog[:20])
    ]
    output['new_tags'] = [
        {'tag': tag, 'reason': f'Misclassified existing topic {index}.'}
        for index, tag in enumerate(catalog[20:], start=20)
    ]

    validated = validate_model_output(output, request)

    assert len(validated['existing_tags']) == 20
    assert [item['tag'] for item in validated['existing_tags']] == catalog[:20]
    assert validated['new_tags'] == []


@pytest.mark.parametrize("tag_id", ["tag-999", "#Tag-999", " tag_999 "])
def test_unknown_internal_tag_id_in_existing_tags_is_omitted_with_warning(tag_id):
    output = empty_proposal()
    output['existing_tags'] = [{
        'tag': tag_id, 'reason': 'Unknown reference', 'recommended': True,
    }]
    warnings = []

    resolved = _resolve_model_references(
        output,
        {},
        {'tag-1': 'machine-learning'},
        warnings=warnings,
        request_id='regression',
    )

    assert resolved['existing_tags'] == []
    assert warnings == ['unknown_tag_reference_omitted']


def test_unknown_internal_tag_ids_in_new_tags_are_omitted_with_one_warning():
    output = empty_proposal()
    output['new_tags'] = [
        {'tag': 'tag-998', 'reason': 'Unknown reference'},
        {'tag': '#Tag-999', 'reason': 'Another unknown reference'},
    ]
    warnings = []

    resolved = _resolve_model_references(
        output,
        {},
        {'tag-1': 'machine-learning'},
        warnings=warnings,
        request_id='regression',
    )

    assert resolved['new_tags'] == []
    assert warnings == ['unknown_tag_reference_omitted']


def test_source_and_topic_tags_rank_before_other_capture_formats():
    request = request_for(
        '---\ntags: [webpage]\n---\n# Embeddings\nMachine learning uses embeddings.',
        tags=['audio', 'pdf', 'webpage', 'machine-learning', 'fixture/home'],
        candidates=[{
            'id': 'related', 'path': 'Deep learning.md', 'title': 'Deep learning',
            'aliases': [], 'tags': ['pdf'], 'evidence': 'Machine learning.',
        }],
    )
    encoded, _, _, _, _, _ = _build_model_input(request)
    tags = [item['tag'] for item in json.loads(encoded)['vault_tags']]
    assert tags[:2] == ['webpage', 'machine-learning']
    assert tags[-1] == 'pdf'
    assert 'fixture/home' in tags


@pytest.mark.parametrize('label', ['Embeddings', 'Sparse embeddings'])
def test_duplicate_display_concept_does_not_block_valid_proposal(tmp_path, label):
    from omd.ai_service import AITextResult
    from omd.enrich_note import build_standalone_request, run_enrich_note

    note = tmp_path / 'Embeddings.md'
    note.write_text('# Embeddings\nSparse embeddings contain mostly zero values.')
    (tmp_path / 'Sparse embeddings.md').write_text('# Sparse embeddings\nMostly zero values.')
    request, _ = build_standalone_request(
        str(tmp_path), note.name, model='qwen3:0.6b', host='http://localhost:11434',
    )
    before = {path.name: path.read_bytes() for path in tmp_path.glob('*.md')}

    def executor(task, **kwargs):
        proposal = empty_proposal()
        proposal['summary'] = 'Sparse embeddings contain mostly zero values.'
        proposal['new_concepts'] = [{'label': label, 'reason': 'Existing source topic'}]
        proposal['new_tags'] = [{'tag': 'machine-learning', 'reason': 'Relevant topic'}]
        return AITextResult(
            provider='ollama', requested_model=task.model, actual_model=task.model,
            capability=task.capability, privacy_mode='local_only',
            destination_domain='localhost', text=json.dumps(proposal),
            usage={}, timing={}, structured=proposal,
        )

    response = run_enrich_note(request, executor=executor)
    assert response['proposal']['new_concepts'] == []
    assert response['proposal']['new_tags'][0]['tag'] == 'machine-learning'
    assert 'existing_concept_omitted' in response['warnings']
    assert {path.name: path.read_bytes() for path in tmp_path.glob('*.md')} == before


def test_large_vault_tag_catalog_is_bounded_with_final_schema_budget():
    from omd.ai_service import AITextTask, _validate_context_budget
    from omd.enrich_note import MAX_OUTPUT_TOKENS, _SYSTEM_PROMPT

    tags = [f'topic-{index}' for index in range(500)]
    content = '---\ntags: [' + ', '.join(tags) + ']\n---\n# Embeddings\n' + 'Embeddings represent language numerically. ' * 100
    request = request_for(content, tags=tags)
    encoded, warnings, _, schema, _, tag_by_id = _build_model_input(request)
    assert len(tag_by_id) < len(tags)
    assert 'vault_tags_truncated_for_model_context' in warnings
    assert schema.schema['properties']['existing_tags']['items']['properties']['tag'] == {
        'type': 'string',
    }
    assert {
        item['tag_id'] for item in json.loads(encoded)['vault_tags']
    } == set(tag_by_id)
    task = AITextTask(
        provider='ollama', model=request.model, capability='note_organisation',
        operation='enrich_note_preview', system_prompt=_SYSTEM_PROMPT,
        max_output_tokens=MAX_OUTPUT_TOKENS, output_schema=schema,
    )
    _validate_context_budget(task, encoded)


def test_ordinary_resource_note_retains_link_titles_and_mixed_image_prose():
    source = (
        '# Reading list\n'
        '- [Sentence-BERT: Sentence Embeddings](https://example.test/paper)\n'
        '![Vectors](diagram.png) Dense embeddings represent semantic meaning in compact vectors.\n'
    )
    encoded, _, _, _, evidence, _ = _build_model_input(request_for(source))
    assert json.loads(encoded)['untrusted_note']['content'] == source
    assert any('Sentence-BERT' in excerpt for excerpt in evidence.values())
    assert all(excerpt in source for excerpt in evidence.values())


def test_converted_note_retains_prose_after_inline_image():
    content = '![Vectors](diagram.png) Dense embeddings represent semantic meaning in compact vectors.'
    source = (
        '# Embeddings\n> [Source](https://example.test) · Captured: `2026-09-16`\n'
        '## Full Content\n# Embeddings\n' + content + '\n'
    )
    encoded, _, _, _, evidence, _ = _build_model_input(request_for(source))
    assert content in json.loads(encoded)['untrusted_note']['content']
    assert any('Dense embeddings represent semantic meaning' in excerpt for excerpt in evidence.values())
    assert all(excerpt in source for excerpt in evidence.values())


def test_inline_citation_does_not_erase_substantive_evidence():
    source = 'Sentence embeddings encode semantic meaning in compact vectors [research](https://example.test/paper).'
    _, _, _, _, evidence, _ = _build_model_input(request_for(source))
    assert any('Sentence embeddings encode semantic meaning' in excerpt for excerpt in evidence.values())
    assert all(excerpt in source for excerpt in evidence.values())


def test_semantic_topic_tags_remain_available_without_literal_word_overlap():
    tags = ['nlp', 'machine-learning', 'semantic-search', 'research/language-models']
    source = 'Sentence vectors place phrases with the same meaning close together.'
    encoded, _, _, _, _, _ = _build_model_input(request_for(source, tags=tags))
    assert {item['tag'] for item in json.loads(encoded)['vault_tags']} == set(tags)


def test_tag_catalogue_echo_is_omitted_without_suppressing_explained_semantic_tags():
    from omd.enrich_note import build_proposal_response

    request = request_for('Sentence vectors place phrases with the same meaning close together.', tags=['nlp', 'semantic-search', 'fixture/home'])
    proposal = empty_proposal()
    proposal['existing_tags'] = [
        {'tag': 'nlp', 'reason': 'nlp', 'recommended': True},
        {'tag': 'fixture/home', 'reason': 'fixture/home', 'recommended': True},
        {'tag': 'semantic-search', 'reason': 'Sentence similarity allows retrieval by meaning rather than exact words.', 'recommended': True},
    ]
    proposal['new_tags'] = [{'tag': 'vectors', 'reason': '#vectors'}]
    response = build_proposal_response(
        request, validate_model_output(proposal, request), provider='ollama',
        actual_model=request.model, endpoint_class='local_loopback',
    )
    assert [item['tag'] for item in response['proposal']['existing_tags']] == ['semantic-search']
    assert response['proposal']['new_tags'] == []
    assert response['warnings'] == ['unexplained_tag_omitted']
