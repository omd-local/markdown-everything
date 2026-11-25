"""Formatting-quality regressions for the Markdown polish path."""

import re

import pytest

from omd import _polish_md


def _response(monkeypatch, text: str) -> None:
    monkeypatch.setattr(
        _polish_md,
        "request_ollama_json",
        lambda *_args, **_kwargs: {"response": text, "done_reason": "stop"},
    )


def _without_whitespace(text: str) -> str:
    return re.sub(r"\s+", "", text)


def test_chunk_discards_punctuation_only_content_loss(monkeypatch):
    source = "The marker OMD-KEEP-417 must retain its final punctuation."
    _response(monkeypatch, source.removesuffix("."))

    output = _polish_md._polish_chunk(
        source,
        "qwen3:4b-instruct",
        "http://localhost:11434",
    )

    assert output == source


def test_dense_prose_noop_gets_lossless_paragraph_structure(monkeypatch):
    dense = (
        "The remote workshop opens at 14:35 and the facilitator checks the attendance list "
        "before sharing https://example.com/runbook?id=KEEP-417 with the group "
        "简体中文内容必须保持原样 English wording must remain unchanged "
        "العربية يجب أن تبقى كما هي and the final handoff explains who owns the follow-up "
        "without changing the original wording or any reference value."
    )
    source = f"# Remote workshop handoff\n\n{dense}\n"
    _response(monkeypatch, source)

    output = _polish_md._polish_chunk(
        source,
        "qwen3:4b-instruct",
        "http://localhost:11434",
    )

    assert output != source
    assert output.count("\n\n") > source.count("\n\n")
    assert _without_whitespace(output) == _without_whitespace(source)
    assert max(map(len, output.splitlines())) <= 240


def test_dense_prose_rewrite_is_discarded_for_lossless_fallback(monkeypatch):
    dense = " ".join(f"original-token-{index}" for index in range(36)) + "."
    source = f"# Shift handoff\n\n{dense}\n"
    rewritten = source.replace("original-token-18", "invented-token-18")
    _response(monkeypatch, rewritten)

    output = _polish_md._polish_chunk(
        source,
        "qwen3:4b-instruct",
        "http://localhost:11434",
    )

    assert "invented-token" not in output
    assert "original-token-18" in output
    assert output.count("\n\n") > source.count("\n\n")
    assert _without_whitespace(output) == _without_whitespace(source)


def test_dense_prose_fallback_preserves_frontmatter_byte_for_byte(monkeypatch):
    frontmatter = (
        "---\n"
        "omd_home_status: inbox\n"
        "captured_at: 2026-11-18T09:00:00Z\n"
        "tags:\n"
        "  - review/formatting\n"
        "---\n"
    )
    dense = " ".join(f"handoff-token-{index}" for index in range(40)) + "."
    source = f"{frontmatter}\n# Handoff\n\n{dense}\n"
    _response(monkeypatch, source)

    output = _polish_md._polish_chunk(
        source,
        "qwen3:4b-instruct",
        "http://localhost:11434",
    )

    assert output.startswith(frontmatter)
    assert _without_whitespace(output) == _without_whitespace(source)
    assert "\n\n" in output.removeprefix(frontmatter)


def test_lossless_fallback_ignores_existing_non_prose_blocks():
    long_cells = " | ".join(f"cell-{index}" for index in range(40))
    long_item = "- " + " ".join(f"list-token-{index}" for index in range(30))
    source = f"| {long_cells} |\n{long_item}\n"

    assert _polish_md._paragraphize_dense_prose(source) == source


def test_model_paragraph_break_is_kept_without_extra_fallback(monkeypatch):
    first = " ".join(f"first-{index}" for index in range(24))
    second = " ".join(f"second-{index}" for index in range(24)) + "."
    source = f"{first} {second}"
    candidate = f"{first}\n\n{second}"
    _response(monkeypatch, candidate)

    output = _polish_md._polish_chunk(
        source,
        "qwen3:4b-instruct",
        "http://localhost:11434",
    )

    assert output == candidate


def test_layout_cleanup_removes_an_invented_numeric_sign_before_full_validation(monkeypatch):
    source = (
        "## Body\n\n"
        "1. Muscles take 4 weeks. Tendons take up to 2 years.\n"
    )
    candidate = (
        "## Body\n\n"
        "1. Muscles take 4 weeks.\n\nTendons take up to -2 years.\n"
    )
    _response(monkeypatch, candidate)

    output = _polish_md._polish_chunk(
        source,
        "qwen3:4b-instruct",
        "http://localhost:11434",
    )

    assert output == candidate.replace("-2 years", "2 years").strip()


@pytest.mark.parametrize("sign", ["+", "-", "−"])
def test_numeric_sign_repair_handles_only_signs_absent_from_the_source(sign):
    source = "Tendons take up to 2 years."
    candidate = f"Tendons take up to {sign}2 years."

    assert _polish_md._restore_invented_numeric_signs(source, candidate) == source


@pytest.mark.parametrize("sign", ["+", "-", "−"])
def test_numeric_sign_repair_preserves_a_source_authored_separate_operator(sign):
    source = f"x {sign} 2."
    candidate = f"x {sign}2."

    assert _polish_md._restore_invented_numeric_signs(source, candidate) == candidate
    with pytest.raises(RuntimeError, match="numeric content"):
        _polish_md._assert_content_preserved(source, candidate)


def test_numeric_sign_repair_does_not_hide_another_unsafe_mutation(monkeypatch):
    source = (
        "## Body\n\n"
        "1. See [guide](https://example.com/a). Tendons take up to 2 years.\n"
    )
    candidate = (
        "## Body\n\n"
        "1. See [guide](https:/example.com/a).\n\nTendons take up to -2 years.\n"
    )
    _response(monkeypatch, candidate)

    with pytest.raises(RuntimeError, match="link destinations"):
        _polish_md._polish_chunk(
            source,
            "qwen3:4b-instruct",
            "http://localhost:11434",
        )


def test_transcript_cleanup_accepts_punctuation_only_chinese_segmentation(monkeypatch):
    source = (
        '今天我们来讲远程办公安全首先不要在公共网络上传敏感文件其次每个服务应使用不同密码'
        '最后如果收到意外的多因素认证请求应该立即拒绝并联系安全团队'
    )
    candidate = (
        '今天我们来讲远程办公安全。首先，不要在公共网络上传敏感文件；'
        '其次，每个服务应使用不同密码。\n\n'
        '最后，如果收到意外的多因素认证请求，应该立即拒绝并联系安全团队。'
    )
    _response(monkeypatch, candidate)

    output = _polish_md._polish_chunk(
        source,
        'qwen3:4b-instruct',
        'http://localhost:11434',
        transcript_cleanup=True,
    )

    assert output == candidate
    assert _without_whitespace(re.sub(r'[，。；]', '', output)) == source


def test_transcript_cleanup_still_rejects_changed_words(monkeypatch):
    source = '不要在公共网络上传敏感文件应该使用可信热点'
    _response(monkeypatch, '不要在公共网络上传机密文件。应该使用可信热点。')

    output = _polish_md._polish_chunk(
        source,
        'qwen3:4b-instruct',
        'http://localhost:11434',
        transcript_cleanup=True,
    )

    assert output == source


def test_transcript_cleanup_still_rejects_changed_operators(monkeypatch):
    source = '半年胜率达到87%风险公式保持5+3'
    _response(monkeypatch, '半年胜率达到87%。风险公式保持5-3。')

    output = _polish_md._polish_chunk(
        source,
        'qwen3:4b-instruct',
        'http://localhost:11434',
        transcript_cleanup=True,
    )

    assert output == source


def test_transcript_cleanup_still_rejects_changed_numeric_notation(monkeypatch):
    source = '保留3.14%和2026-09-18以及时间00:42'
    _response(monkeypatch, '保留314%和20260918以及时间0042。')

    output = _polish_md._polish_chunk(
        source,
        'qwen3:4b-instruct',
        'http://localhost:11434',
        transcript_cleanup=True,
    )

    assert output == source


def test_transcript_cleanup_uses_matching_timestamp_boundaries_without_model_rewrites():
    segments = [
        '今天我们来讲远程办公账号安全',
        '首先不要在公共网络上传敏感文件',
        '应该使用可信热点或者公司批准的网络',
        '第二不要在多个服务重复使用密码',
        '每个账号都应该有独立的强密码',
        '并且保存在可信的密码管理器里',
        '第三不要接受意外出现的认证请求',
        '应该先拒绝请求并联系安全团队',
        '确认账号没有被其他人尝试登录',
        '完成检查以后再恢复正常工作',
        '这些步骤适用于在家和出差办公',
        '也适用于临时使用共享办公空间',
        '具体的执行方法是什么呢',
        '你是想沿用原来的做法',
        '还是采用更安全的方案',
        '比如段永平',
        '巴菲特',
        '索罗斯',
        '都使用过类似方法',
        '第三种情况是这头牛',
        '它朝着你慢慢走过来',
    ]
    transcript = ''.join(segments)
    timestamped = '\n'.join(
        f'- [00:{index:02d} → 00:{index + 1:02d}] {segment}'
        for index, segment in enumerate(segments)
    )
    source = f'# Capture\n\n## Transcript\n\n{transcript}\n\n### Timestamped\n\n{timestamped}\n'

    output = _polish_md.polish_markdown(
        source,
        transcript_cleanup=True,
        _polish_fn=lambda text, _model, _host: text,
    )

    cleaned = output.split('## Transcript\n', 1)[1].split('### Timestamped', 1)[0].strip()
    assert cleaned != transcript
    assert '。' in cleaned
    assert '呢？' in cleaned
    assert '做法。还是' not in cleaned
    assert '段永平。巴菲特' not in cleaned
    assert '牛。它' not in cleaned
    assert '\n\n' in cleaned
    _polish_md._assert_content_preserved(
        transcript,
        cleaned,
        allow_punctuation_changes=True,
    )
    assert output.split('### Timestamped\n', 1)[1] == source.split('### Timestamped\n', 1)[1]


def test_timestamp_cleanup_accepts_punctuation_between_adjacent_numeric_segments():
    segments = [
        '这是美国投资锦标赛到八月底统计的官方成绩',
        '收益率2854%',
        '2万美元以上的增强组里他排名第一',
        '增强组就是可以买期权做期货的那一组',
        '一个月他就从126涨到了157',
        '2月中他回调了三天',
    ]
    transcript = ''.join(segments)
    timestamped = '\n'.join(
        f'- [00:{index:02d} → 00:{index + 1:02d}] {segment}'
        for index, segment in enumerate(segments)
    )

    cleaned = _polish_md._cleanup_cjk_transcript_from_timestamps(
        f'\n\n{transcript}\n\n',
        f'\n\n{timestamped}\n',
    )

    assert cleaned is not None
    assert '2854%，2万美元' in cleaned
    assert re.search(r'157[，。]2月中', cleaned)
    assert _polish_md._ordered_letter_number_content(cleaned) == (
        _polish_md._ordered_letter_number_content(transcript)
    )


def test_numeric_separator_insertion_requires_a_trusted_timestamp_boundary():
    with pytest.raises(RuntimeError, match='numeric content'):
        _polish_md._assert_content_preserved(
            '编号314保持不变',
            '编号3，14保持不变',
            allow_punctuation_changes=True,
            allowed_numeric_separator_anchors={4},
        )


def test_transcript_cleanup_does_not_send_capture_metadata_to_the_model():
    segments = [
        '今天先说明远程会议的准备工作',
        '然后检查共享文件是否已经上传',
        '最后确认所有参与者都收到链接',
        '完成以后再开始正式讨论',
    ]
    transcript = ''.join(segments)
    timestamped = '\n'.join(
        f'- [00:{index:02d} → 00:{index + 1:02d}] {segment}'
        for index, segment in enumerate(segments)
    )
    source = (
        '# Capture title must stay exact\n\n'
        '## Summary\n\nDo not rewrite this generated summary.\n\n'
        f'## Transcript\n\n{transcript}\n\n'
        f'### Timestamped\n\n{timestamped}\n'
    )
    model_calls: list[str] = []

    output = _polish_md.polish_markdown(
        source,
        transcript_cleanup=True,
        _polish_fn=lambda text, _model, _host: model_calls.append(text) or text,
    )

    assert model_calls == []
    assert output != source
    assert output.split('## Transcript\n', 1)[0] == source.split('## Transcript\n', 1)[0]
    assert output.split('### Timestamped\n', 1)[1] == source.split('### Timestamped\n', 1)[1]
    cleaned = output.split('## Transcript\n', 1)[1].split('### Timestamped', 1)[0]
    assert '。' in cleaned
    _polish_md._assert_content_preserved(
        transcript,
        cleaned,
        allow_punctuation_changes=True,
    )


def test_transcript_cleanup_flag_keeps_normal_polish_for_notes_without_a_transcript():
    source = '# Web capture\n\nA short article body stays on the ordinary formatting path.\n'
    model_calls: list[str] = []

    output = _polish_md.polish_markdown(
        source,
        transcript_cleanup=True,
        _polish_fn=lambda text, _model, _host: model_calls.append(text) or text,
    )

    assert output == source
    assert model_calls == [source]
