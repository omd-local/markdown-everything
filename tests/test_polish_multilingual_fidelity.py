"""Replay local-model fidelity failures and preserve valid formatting-only edits."""
import pytest

from omd import _polish_md

SOURCE = '''# Mixed language compatibility

Morgan owns OMD-742. Review date: 2026-09-18 at 10:30. Dense embeddings preserve semantic meaning.
负责人是小林。编号 OMD-742，复查日期是 2026-09-18，时间是 10:30。稠密向量表示语义信息。
المسؤولة هي ليلى. الرقم OMD-742. موعد المراجعة هو 2026-09-18 الساعة 10:30. تمثل المتجهات المعنى.

```python
CANARY = "OMD-742"
```
'''
# Exact prose chunks recorded from qwen3:0.6b and qwen3:4b-instruct.
RECORDED_OUTPUTS = [
    '''# Mixed language compatibility

Morgan owns OMD-742. Review date: 2026-09-18 at 10:30. Dense embeddings preserve semantic meaning.
负责人是小林。编号 OMD-742，复查日期是 2026-09-18，时间是 10:30。稠密向量表示语义信息。
المسؤولة هي ليلى. الرقم OMD-742. موعد المراجعة هو 2026-09-18 الساعة 10:30.''',
    (
        '# Mixed language compatibility\n\n'
        'Morgan owns OMD-742. Review date: 2026-09--18 at 10:30. '
        'Dense embeddings preserve semantic meaning.\u0020\u0020\n'
        '负责人是小林。编号 OMD-742，复查日期是 2026-09-18，时间是 10:30。'
        '稠密向量表示语义信息。\u0020\u0020\n'
        'المسؤولة هي ليلى. الرقم OMD-742. موعد المراجعة هو '
        '2026-09-18 الساعة 10:30. تمثل المتجهات المعنى。'
    ),
]


def response(monkeypatch, text):
    monkeypatch.setattr(_polish_md, 'request_ollama_json', lambda *_a, **_kw: {
        'response': text, 'done_reason': 'stop',
    })


@pytest.mark.parametrize('output', RECORDED_OUTPUTS)
def test_recorded_fidelity_failure_keeps_original_file_and_code(tmp_path, monkeypatch, capsys, output):
    response(monkeypatch, output)
    monkeypatch.setattr(_polish_md, '_ollama_ready', lambda *_a, **_kw: (True, 'ready'))
    path = tmp_path / '中文 العربية.md'
    path.write_text(SOURCE, encoding='utf-8')
    _polish_md.polish_file(path, keep_raw=True)
    assert path.read_text(encoding='utf-8') == SOURCE
    assert path.with_suffix('.raw.md').read_text(encoding='utf-8') == SOURCE
    warning = capsys.readouterr().err
    assert 'Keeping' in warning
    assert 'content' in warning or 'numeric' in warning


@pytest.mark.parametrize(('source', 'output'), [
    ('Sentence.\nالمعنى محفوظ.\n한국어 내용입니다.', 'Sentence.\nالمعنى محفوظ.'),
    ('Alpha beta gamma.', 'Alpha gamma beta.'),
    ('Count: 42. Next: 42.', 'Count: 43. Next: 42.'),
    ('Dates: 2026-09-18 and 2026-09-18.', 'Dates: 2026-09--18 and 2026-09-18.'),
    ('Value: -3.5', 'Value: 3.5'),
    ('Value: ١٢٫٥', 'Value: ١٢٬٥'),
    ('Read [source](https://example.com/a-b).', 'Read [source](https://example.com/ab).'),
    ('Read [file](docs/a_b.md).', 'Read [file](docs/ab.md).'),
    ('Run `a_b = x-y` next.', 'Run `ab = xy` next.'),
])
def test_chunk_rejects_content_numeric_link_and_code_changes(monkeypatch, source, output):
    response(monkeypatch, output)
    with pytest.raises(RuntimeError, match='content|numeric|protected'):
        _polish_md._polish_chunk(source, 'qwen3:0.6b', 'http://localhost:11434')


@pytest.mark.parametrize(('source', 'output'), [
    ('This  is a test .', 'This is a test.'),
    ('Embed-\ndings  preserve\nmeaning.', 'Embeddings preserve meaning.'),
    ('中文  内容。\nالعربية  محفوظة.', '中文 内容。\n\nالعربية محفوظة.'),
    ('cafe\u0301 is here.', 'café is here.'),
    ('Use `a_b = x-y`  and [source](https://example.com/a-b).', 'Use `a_b = x-y` and [source](https://example.com/a-b).'),
])
def test_chunk_accepts_formatting_repairs(monkeypatch, source, output):
    response(monkeypatch, output)
    assert _polish_md._polish_chunk(source, 'qwen3:0.6b', 'http://localhost:11434') == output


def test_chunk_accepts_indicated_multilingual_markdown_structure(monkeypatch):
    source = (
        'Transcript\n'
        '第一段。 第二段。\n'
        'Items:\n'
        'Alpha\n'
        'Beta\n'
        'العربية محفوظة.\n'
    )
    output = (
        '# Transcript\n\n'
        '第一段。\n\n'
        '第二段。\n\n'
        'Items:\n'
        '- Alpha\n'
        '- Beta\n\n'
        'العربية محفوظة.'
    )
    response(monkeypatch, output)

    assert _polish_md._polish_chunk(
        source, 'qwen3:0.6b', 'http://localhost:11434'
    ) == output


def test_markdown_structure_prompt_preserves_words_and_order():
    prompt = _polish_md.SYSTEM_PROMPT

    assert 'Restore paragraph breaks, unordered-list layout, and heading markers' in prompt
    assert 'only when the source wording, labels, enumeration' in prompt
    assert 'may change whitespace and Markdown heading/list markers only' in prompt
    assert 'Do not invent headings, list items, labels, or facts' in prompt
    assert 'do not add, remove, reorder, or rewrite any words' in prompt


@pytest.mark.parametrize(('source', 'output'), [
    ('Threshold p < 0.05.', 'Threshold p > 0.05.'),
    ('A != B', 'A == B'),
    ('left <= right', 'left >= right'),
    ('ready && valid', 'ready || valid'),
    ('total + fee', 'total - fee'),
    ('value * rate', 'value / rate'),
    ('mask & flag', 'mask | flag'),
    ('x = y', 'x == y'),
    ('score ≥ limit', 'score ≤ limit'),
    ('x -> y', 'x <- y'),
    ('!ready', 'ready'),
    ('A < B, C.', 'A, B < C.'),
])
def test_chunk_rejects_semantic_operator_changes(monkeypatch, source, output):
    response(monkeypatch, output)
    with pytest.raises(RuntimeError, match='semantic operators'):
        _polish_md._polish_chunk(source, 'qwen3:0.6b', 'http://localhost:11434')


@pytest.mark.parametrize(('source', 'output'), [
    ('Threshold  p < 0.05 .', 'Threshold p < 0.05.'),
    ('A  !=  B', 'A != B'),
    ('Embed-\ndings  preserve meaning.', 'Embeddings preserve meaning.'),
])
def test_chunk_allows_formatting_repairs_around_preserved_operators(
    monkeypatch, source, output,
):
    response(monkeypatch, output)
    assert _polish_md._polish_chunk(
        source, 'qwen3:0.6b', 'http://localhost:11434'
    ) == output


@pytest.mark.parametrize(('source', 'output'), [
    ('[paper](docs/topic_(draft).md)', '[paper](docs/topic_(draft)/md)'),
    ('[paper](docs/topic_\\(draft\\).md)', '[paper](docs/topic_\\(draft\\)/md)'),
    ('$a < b$', '$a > b$'),
    ('$$a - b$$', '$$a + b$$'),
    ('\\(a < b\\)', '\\(a > b\\)'),
    ('\\[a - b\\]', '\\[a + b\\]'),
])
def test_polish_path_preserves_nested_links_and_math(monkeypatch, capsys, source, output):
    response(monkeypatch, output)
    monkeypatch.setattr(_polish_md, '_ollama_ready', lambda *_a, **_kw: (True, 'ready'))
    assert _polish_md.polish_markdown(source) == source
    assert 'Keeping' in capsys.readouterr().err


@pytest.mark.parametrize('fence', ['```', '~~~', '````', '~~~~'])
def test_polish_never_sends_fenced_code_or_its_headings_to_model(fence):
    code = f'{fence}python\n## not a section\nvalue = a-b\n{fence}\n'
    source = 'Before.\n\n' + code + '\n## Actual section\nAfter.\n'
    sent=[]
    def polish(chunk, _model, _host):
        sent.append(chunk)
        return chunk.strip()
    assert _polish_md.polish_markdown(source, _polish_fn=polish) == source
    assert sent == ['Before.\n\n', '\nAfter.\n']


def test_unclosed_tilde_fence_is_preserved_without_model_calls():
    source = '~~~python\nvalue = a-b\n## still code\n'
    sent = []
    def polish(*_args):
        sent.append(_args)
        return source
    assert _polish_md.polish_markdown(source, _polish_fn=polish) == source
    assert not sent
