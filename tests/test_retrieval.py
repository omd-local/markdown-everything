from __future__ import annotations

from dataclasses import FrozenInstanceError, fields, is_dataclass
from pathlib import Path
import json

import pytest


def _write_markdown(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _write_text(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _paths(hits) -> list[str]:
    return [hit.path for hit in hits]


def test_search_hit_is_a_frozen_dataclass():
    from omd.retrieval import SearchHit

    assert is_dataclass(SearchHit)
    assert SearchHit.__dataclass_params__.frozen is True


def test_search_hit_exposes_contract_fields():
    from omd.retrieval import SearchHit

    assert [field.name for field in fields(SearchHit)] == ["path", "title", "score", "evidence"]


def test_search_hit_rejects_mutation():
    from omd.retrieval import SearchHit

    hit = SearchHit(path="Notes/example.md", title="Example", score=1.0, evidence="match")

    with pytest.raises(FrozenInstanceError):
        hit.score = 2.0


def test_search_notes_returns_no_results_for_blank_query(tmp_path):
    from omd.retrieval import search_notes

    _write_markdown(tmp_path / "note.md", "# Example\n\nRetrieval appears here.\n")

    assert search_notes(tmp_path, "   \n\t  ") == []


def test_search_notes_rejects_missing_root(tmp_path):
    from omd.retrieval import search_notes

    with pytest.raises(ValueError, match="root"):
        search_notes(tmp_path / "missing", "retrieval")


def test_search_notes_rejects_root_that_is_not_a_directory(tmp_path):
    from omd.retrieval import search_notes

    root_file = _write_markdown(tmp_path / "note.md", "# Example\n\nRetrieval appears here.\n")

    with pytest.raises(ValueError, match="root"):
        search_notes(root_file, "retrieval")


def test_search_notes_recursively_finds_matching_markdown_files(tmp_path):
    from omd.retrieval import search_notes

    _write_markdown(tmp_path / "nested" / "topic.md", "# Topic\n\nLocal lexical retrieval works.\n")

    hits = search_notes(tmp_path, "lexical")

    assert _paths(hits) == ["nested/topic.md"]


def test_search_notes_ignores_non_markdown_files(tmp_path):
    from omd.retrieval import search_notes

    _write_text(tmp_path / "plain.txt", "local lexical retrieval works")

    assert search_notes(tmp_path, "lexical") == []


def test_search_notes_ignores_hidden_directories(tmp_path):
    from omd.retrieval import search_notes

    _write_markdown(tmp_path / ".hidden" / "secret.md", "# Secret\n\nLocal lexical retrieval works.\n")

    assert search_notes(tmp_path, "lexical") == []


def test_search_notes_ignores_omd_sidecar_directories(tmp_path):
    from omd.retrieval import search_notes

    _write_markdown(tmp_path / ".omd" / "index.md", "# Index\n\nLocal lexical retrieval works.\n")

    assert search_notes(tmp_path, "lexical") == []


def test_search_notes_matches_tokens_case_insensitively(tmp_path):
    from omd.retrieval import search_notes

    _write_markdown(tmp_path / "note.md", "# Example\n\nLocal Lexical Retrieval Works.\n")

    hits = search_notes(tmp_path, "lexical retrieval")

    assert _paths(hits) == ["note.md"]


def test_search_notes_matches_multilingual_tokens(tmp_path):
    from omd.retrieval import search_notes

    _write_markdown(tmp_path / "japanese.md", "# 旅行\n\n東京で静かな喫茶店を見つけた。\n")

    hits = search_notes(tmp_path, "東京")

    assert _paths(hits) == ["japanese.md"]


def test_search_notes_returns_paths_relative_to_root(tmp_path):
    from omd.retrieval import search_notes

    _write_markdown(tmp_path / "Notes" / "topic.md", "# Topic\n\nLexical retrieval works.\n")

    hits = search_notes(tmp_path, "lexical")

    assert hits[0].path == "Notes/topic.md"


def test_search_notes_ranking_is_deterministic_across_repeated_calls(tmp_path):
    from omd.retrieval import search_notes

    _write_markdown(tmp_path / "a.md", "# Alpha\n\nretrieval retrieval lexical\n")
    _write_markdown(tmp_path / "b.md", "# Beta\n\nretrieval lexical\n")
    _write_markdown(tmp_path / "c.md", "# Gamma\n\nlexical only once\n")

    first = search_notes(tmp_path, "retrieval lexical")
    second = search_notes(tmp_path, "retrieval lexical")

    assert second == first


def test_search_notes_evidence_contains_the_match(tmp_path):
    from omd.retrieval import search_notes

    _write_markdown(
        tmp_path / "note.md",
        "# Example\n\nBefore context. The lexical marker is here. After context.\n",
    )

    hits = search_notes(tmp_path, "marker")

    assert "marker" in hits[0].evidence.lower()


def test_search_notes_evidence_is_bounded_for_long_matches(tmp_path):
    from omd.retrieval import search_notes

    long_prefix = "prefix " * 200
    long_suffix = " suffix" * 200
    _write_markdown(
        tmp_path / "note.md",
        f"# Example\n\n{long_prefix}needle{long_suffix}\n",
    )

    hits = search_notes(tmp_path, "needle")

    assert len(hits[0].evidence) <= 1_600


def test_search_notes_ignores_question_intent_words_and_returns_section_outline(tmp_path):
    from omd.retrieval import search_notes

    _write_markdown(
        tmp_path / "noise.md",
        "# Survival analysis\n\n" + "how many for you summarise and list them all " * 200,
    )
    _write_markdown(tmp_path / "partial.md", "# Transfer learning\n\nA beginner tutorial.\n")
    headings = [
        "Strength comes with time",
        "Learn efficient technique",
        "Let your legs work",
        "Keep long arms",
        "Try harder problems",
        "Warm up",
        "Take breaks and eat",
        "Choose smart gear",
        "Manage your mindset",
        "Follow safety rules",
    ]
    body = "\n\n".join(
        f"## **{index}. {heading}**\n\nBeginner bouldering advice for {heading.lower()}."
        for index, heading in enumerate(headings, start=1)
    )
    _write_markdown(
        tmp_path / "bouldering.md",
        "---\ntitle: Bouldering tips for beginners\n---\n\n"
        "# Bouldering tips for beginners\n\n"
        "> [Source](https://example.com/bouldering-tips-for-beginners)\n\n"
        "This guide contains 10 tips.\n\n"
        + body,
    )

    hits = search_notes(
        tmp_path,
        "how many beginner tips for bouldering, could you summarise and list them all",
    )

    assert _paths(hits) == ["bouldering.md"]
    assert "title:" not in hits[0].evidence
    assert "https://" not in hits[0].evidence
    for index, heading in enumerate(headings, start=1):
        assert f"{index}. {heading}" in hits[0].evidence


def test_search_notes_suppresses_duplicate_content_from_result_budget(tmp_path):
    from omd.retrieval import search_notes

    duplicate = "# Retrieval guide\n\nLocal lexical retrieval with useful context.\n"
    _write_markdown(tmp_path / "a-copy.md", duplicate)
    _write_markdown(tmp_path / "b-copy.md", duplicate)
    _write_markdown(tmp_path / "unique.md", "# Unique\n\nLocal retrieval has different context.\n")

    hits = search_notes(tmp_path, "local retrieval", limit=10)

    assert _paths(hits) == ["a-copy.md", "unique.md"]


def test_answer_context_uses_bm25_and_preserves_a_complete_numbered_outline(tmp_path):
    from omd.retrieval import build_answer_context

    headings = [
        "Strength comes with time",
        "Learn efficient technique",
        "Let your legs work",
        "Keep long arms",
        "Try harder problems",
        "Warm up",
        "Take breaks and eat",
        "Choose smart gear",
        "Manage your mindset",
        "Follow safety rules",
    ]
    sections = "\n\n".join(
        f"## {index}. {heading}\n\nActionable detail for {heading.lower()}."
        for index, heading in enumerate(headings, start=1)
    )
    _write_markdown(
        tmp_path / "bouldering.md",
        "# Bouldering tips for beginners\n\nThis guide contains 10 tips.\n\n" + sections,
    )
    _write_markdown(
        tmp_path / "noise.md",
        "# Generic beginner page\n\n" + "tips mentioned without bouldering detail " * 120,
    )

    context = build_answer_context(
        tmp_path,
        "how many beginner tips for bouldering, summarise and list them all",
    )

    assert 1 <= len(context.blocks) <= 8
    assert context.hits[0].path == "bouldering.md"
    outline = next(block.text for block in context.blocks if block.kind == "outline")
    for index, heading in enumerate(headings, start=1):
        assert f"{index}. {heading}" in outline
    assert "This guide contains 10 tips." in outline


def test_answer_context_outline_keeps_intro_that_labels_beginner_tips(tmp_path):
    from omd.retrieval import build_answer_context

    _write_markdown(
        tmp_path / "bouldering.md",
        """# Bouldering tips for beginners

This guide contains 10 tips to make your first time in the climbing gym easier and more enjoyable.

## 1. Strength comes with time

Actionable detail.

## 2. Let your legs do the work

Actionable detail.
""",
    )

    context = build_answer_context(
        tmp_path,
        "How many explicit numbered beginner tips are present? List each once.",
    )

    outline = next(block.text for block in context.blocks if block.kind == "outline")
    assert "# Bouldering tips for beginners" in outline
    assert "first time in the climbing gym easier and more enjoyable" in outline
    assert "1. Strength comes with time" in outline
    assert "2. Let your legs do the work" in outline


def test_answer_context_expands_nested_matches_to_full_mistake_sections(tmp_path):
    from omd.retrieval import build_answer_context

    _write_markdown(
        tmp_path / "mistakes.md",
        """# Three beginner bouldering mistakes

## Mistake #1: Only trying hard projects

Try a wider variety of moves and project lengths instead.

## Mistake #2: Forgetting beta

#### Beta recall matters

Remember the sequence before the next attempt.

#### A practical correction

Put your phone down and watch. Treat each boulder like a new routine or puzzle.

## Mistake #3: Always starting at the bottom

#### Project checklist

- Try a flash attempt.
- Do all of the individual moves.
- Do the obvious links and sections.
- Try from the bottom.
- Reassess.
""",
    )

    context = build_answer_context(
        tmp_path,
        "What are the three beginner mistakes, and what should the climber do instead?",
    )
    combined = "\n".join(block.text for block in context.blocks)

    assert "Try a wider variety of moves" in combined
    assert "Put your phone down and watch" in combined
    assert "Treat each boulder like a new routine or puzzle" in combined
    assert "Try a flash attempt" in combined
    assert "Reassess" in combined


def test_answer_context_prefers_mistake_sources_for_three_mistakes_queries(tmp_path):
    from omd.retrieval import build_answer_context

    _write_markdown(
        tmp_path / "tips.md",
        """# Bouldering tips for beginners

This guide contains 10 tips.

## 1. Long arms

Keep a straight arm to save energy.
""",
    )
    _write_markdown(
        tmp_path / "mistakes.md",
        """# Three beginner bouldering mistakes

## Mistake #1: Only trying hard projects

Try a wider variety of moves and project lengths instead.

## Mistake #2: Forgetting beta

Put your phone down and watch.

## Mistake #3: Always starting at the bottom

- Try a flash attempt.
- Reassess.
""",
    )

    context = build_answer_context(
        tmp_path,
        "What are the three beginner mistakes, and what should the climber do instead?",
    )

    assert [hit.path for hit in context.hits] == ["mistakes.md"]
    assert {block.path for block in context.blocks} == {"mistakes.md"}


def test_answer_context_phrase_and_heading_boost_beats_repeated_polysemy(tmp_path):
    from omd.retrieval import build_answer_context

    _write_markdown(
        tmp_path / "bouldering.md",
        """# Bouldering performance

## Mistake #2: Forgetting beta

#### Beta recall is more important than strength training

Put your phone down and watch other climbers. Ask questions and remember the sequence.
""",
    )
    _write_markdown(
        tmp_path / "statistics.md",
        "# Model evaluation\n\n" + "Recall measures classification performance. " * 100,
    )

    context = build_answer_context(
        tmp_path,
        "Which note discusses beta recall, and what does it recommend?",
    )

    assert [hit.path for hit in context.hits] == ["bouldering.md"]
    assert "Put your phone down and watch" in context.blocks[0].text


def test_answer_context_keeps_both_sources_for_explicit_overlap_queries(tmp_path):
    from omd.retrieval import build_answer_context

    _write_markdown(
        tmp_path / "mistakes.md",
        """# Three beginner bouldering mistakes

## Mistake #2: Forgetting beta

Put your phone down and watch other people climb the problem.

#### Treat each boulder like a puzzle

Remember the movement sequence before pulling on.
""",
    )
    _write_markdown(
        tmp_path / "tips.md",
        """# Bouldering tips for beginners

## 1. Let your legs do the work

Technique beats brute force.

## 2. Bouldering is a mind game

Climbing rewards problem-solving and observation.
""",
    )

    context = build_answer_context(
        tmp_path,
        "Across the two bouldering notes, which recommendations overlap?",
    )

    assert {hit.path for hit in context.hits} == {"mistakes.md", "tips.md"}
    assert {block.path for block in context.blocks} == {"mistakes.md", "tips.md"}


def test_answer_context_keeps_all_three_mistakes_without_unrelated_tip_blocks(tmp_path):
    from omd.retrieval import build_answer_context

    _write_markdown(
        tmp_path / "tips.md",
        """# Bouldering tips for beginners

## 1. Long arms

Keep a straight arm to save energy.
""",
    )
    _write_markdown(
        tmp_path / "mistakes.md",
        """# Three beginner bouldering mistakes

## Mistake #1: Only trying hard projects

Try a wider variety of moves and project lengths instead.

## Mistake #2: Forgetting beta

Put your phone down and watch.

## Mistake #3: Always starting at the bottom

- Try a flash attempt.
- Reassess.
""",
    )

    context = build_answer_context(
        tmp_path,
        "What are the three beginner mistakes, and what should the climber do instead?",
    )
    combined = "\n".join(block.text for block in context.blocks)

    assert "Only trying hard projects" in combined
    assert "Forgetting beta" in combined
    assert "Always starting at the bottom" in combined
    assert "Long arms" not in combined
    assert {block.path for block in context.blocks} == {"mistakes.md"}


def test_answer_context_combines_both_outlines_before_detail_when_query_requests_deduped_advice(tmp_path):
    from omd.retrieval import build_answer_context

    _write_markdown(
        tmp_path / "mistakes.md",
        """# Three beginner bouldering mistakes

## Mistake #1: Only trying hard projects

Try easier climbs too.

## Mistake #2: Forgetting beta

Put your phone down and watch.

## Mistake #3: Always starting at the bottom

Try a flash attempt first.
""",
    )
    _write_markdown(
        tmp_path / "tips.md",
        """# Bouldering tips for beginners

This guide contains 3 tips for first-timers.

## 1. Warm up

Warm up well before climbing.

## 2. Let your legs do the work

Technique beats brute force.

## 3. Smart gear

Shoes matter more than flashy extras.
""",
    )

    context = build_answer_context(
        tmp_path,
        "Combine actionable advice from both bouldering notes, separate explicit tips from lessons inferred from mistakes, and remove duplicates.",
    )

    outlines = [block for block in context.blocks if block.kind == "outline"]
    assert {block.path for block in outlines} == {"mistakes.md", "tips.md"}
    assert [block.kind for block in context.blocks] == ["outline", "digest", "outline", "digest"]
    assert [block.path for block in context.blocks] == [
        "tips.md",
        "tips.md",
        "mistakes.md",
        "mistakes.md",
    ]
    assert not any(block.kind == "overview" for block in context.blocks)
    assert all(
        "Source-local rule" in block.text
        for block in context.blocks
        if block.kind == "digest"
    )
    assert all(
        "Comparison rule" not in block.text
        for block in context.blocks
        if block.kind == "digest"
    )
    assert any(
        "Answer category: explicit tips. Required coverage: 3 of 3." in block.text
        for block in context.blocks
        if block.kind == "digest"
    )
    assert any(
        "Answer category: inferred lessons. Required coverage: 3 of 3." in block.text
        for block in context.blocks
        if block.kind == "digest"
    )
    assert all(
        "Create a separate response section" in block.text
        for block in context.blocks
        if block.kind == "digest"
    )
    assert len(context.blocks) == 4
    combined = "\n".join(block.text for block in outlines)
    assert "Mistake #1: Only trying hard projects" in combined
    assert "1. Warm up" in combined
    assert "Answer category: inferred lessons. Required coverage: 3 of 3." in combined
    assert "Answer category: explicit tips. Required coverage: 3 of 3." in combined


def test_answer_context_adds_note_overviews_for_overlap_queries(tmp_path):
    from omd.retrieval import build_answer_context

    _write_markdown(
        tmp_path / "mistakes.md",
        """# Three beginner bouldering mistakes

Climb with intention and learn from each attempt.

## Mistake #2: Forgetting beta

Put your phone down and watch other people climb the problem.

#### Treat each boulder like a puzzle

Remember the movement sequence before pulling on.

## Mistake #3: Always starting at the bottom

Read the whole problem before pulling on.
""",
    )
    _write_markdown(
        tmp_path / "tips.md",
        """# Bouldering tips for beginners

## 1. Bouldering is a mind game

Observation and problem-solving matter as much as strength.

## 2. Warm up

Build an intentional routine before trying hard moves.
""",
    )

    context = build_answer_context(
        tmp_path,
        "Across the two bouldering notes, which recommendations overlap? Cite each claim.",
    )

    overviews = [block for block in context.blocks if block.kind == "overview"]
    assert {block.path for block in overviews} == {"mistakes.md", "tips.md"}
    assert len(context.blocks) == len(overviews)
    assert [block.kind for block in context.blocks[:2]] == ["overview", "overview"]
    assert {block.path for block in context.blocks[:2]} == {"mistakes.md", "tips.md"}
    assert all("wording differs" in block.text for block in overviews)
    assert all("Report only matches" in block.text for block in overviews)
    assert any("Observation and problem-solving" in block.text for block in overviews)
    assert any("watch other people climb the problem" in block.text for block in overviews)
    assert any("Treat each boulder like a puzzle" in block.text for block in overviews)


def test_answer_context_limits_explicit_two_note_queries_to_two_sources(tmp_path):
    from omd.retrieval import build_answer_context

    for name in ("alpha", "beta", "gamma"):
        _write_markdown(
            tmp_path / f"{name}.md",
            f"# Beginner bouldering {name}\n\n## 1. Warm up\n\nWarm up before climbing.\n",
        )

    context = build_answer_context(
        tmp_path,
        "Across the two beginner bouldering notes, which recommendations overlap?",
    )

    assert len({block.path for block in context.blocks}) == 2


def test_answer_context_overviews_keep_nested_action_lists_for_cross_source_comparison(tmp_path):
    from omd.retrieval import build_answer_context

    _write_markdown(
        tmp_path / "mistakes.md",
        """# Beginner mistakes

## Mistake #1: Ignore other climbers

Put your phone down and watch other people climb.
""",
    )
    _write_markdown(
        tmp_path / "tips.md",
        """# Beginner tips

## 1. Learn technique

Movement experience matters.

- Sign up for a technique class
- Chat with experienced climbers and learn from their insights
- Find a motivated training group

## 2. Warm up

Start with easy movement.

- Climbing shoes
- Chalk bags

## Learn more

- Find a retailer
""",
    )

    context = build_answer_context(
        tmp_path,
        "Across the two beginner notes, which recommendations overlap? Cite each claim.",
    )

    tips_overview = next(
        block for block in context.blocks if block.kind == "overview" and block.path == "tips.md"
    )
    assert "Key actions: Sign up for a technique class" in tips_overview.text
    assert "Chat with experienced climbers and learn from their insights" in tips_overview.text
    assert "Key actions: Climbing shoes" not in tips_overview.text
    assert "Find a retailer" not in tips_overview.text


def test_answer_context_overview_keeps_the_last_item_in_a_ten_item_outline(tmp_path):
    from omd.retrieval import build_answer_context

    tips = "\n\n".join(
        f"## {index}. Tip {index}\n\nActionable guidance for item {index}."
        for index in range(1, 11)
    )
    _write_markdown(tmp_path / "tips.md", f"# Beginner tips\n\n{tips}\n")
    _write_markdown(
        tmp_path / "mistakes.md",
        "# Beginner mistakes\n\n## Mistake #1: Skip the plan\n\nPlan each attempt.\n",
    )

    context = build_answer_context(
        tmp_path,
        "Across both beginner notes, which recommendations overlap? Cite each claim.",
    )

    tips_overview = next(
        block for block in context.blocks if block.kind == "overview" and block.path == "tips.md"
    )
    assert "- 1. Tip 1:" in tips_overview.text
    assert "- 10. Tip 10:" in tips_overview.text


def test_answer_context_overview_keeps_later_supporting_actions(tmp_path):
    from omd.retrieval import build_answer_context

    _write_markdown(
        tmp_path / "mistakes.md",
        """# Beginner mistakes

## Mistake #1: Poor preparation

#### Doing a much wider variety of carefully selected moves improves progression

#### This background observation is descriptive rather than a recommendation

#### Make a point of climbing well on the warmups
""",
    )
    _write_markdown(
        tmp_path / "tips.md",
        "# Beginner tips\n\n## 1. Warm up deliberately\n\nPrepare before hard movement.\n",
    )

    context = build_answer_context(
        tmp_path,
        "Across both beginner notes, which recommendations overlap? Cite each claim.",
    )

    mistake_overview = next(
        block for block in context.blocks if block.kind == "overview" and block.path == "mistakes.md"
    )
    assert "Make a point of climbing well on the warmups" in mistake_overview.text


def test_answer_context_treats_three_beginner_mistakes_as_a_list_query(tmp_path):
    from omd.retrieval import build_answer_context

    _write_markdown(
        tmp_path / "mistakes.md",
        """# Three beginner bouldering mistakes

## Mistake #1: Only trying hard projects

Try a wider variety of moves and project lengths instead.

## Mistake #2: Forgetting beta

Put your phone down and watch.

## Mistake #3: Always starting at the bottom

- Try a flash attempt.
- Reassess.
""",
    )

    context = build_answer_context(
        tmp_path,
        "What are the three beginner mistakes, and what should the climber do instead?",
    )

    outline = next(block for block in context.blocks if block.kind == "outline")
    assert outline.heading == "Mistake outline"
    assert "Mistake #1: Only trying hard projects" in outline.text
    assert "Mistake #2: Forgetting beta" in outline.text
    assert "Mistake #3: Always starting at the bottom" in outline.text


def test_answer_context_mixed_language_two_note_summary_keeps_both_outline_roles(tmp_path):
    from omd.retrieval import build_answer_context

    _write_markdown(
        tmp_path / "mistakes.md",
        """# Beginner bouldering mistakes

## Mistake #1: Hard projects

Try varied moves.

## Mistake #2: Forgetting beta

Watch and remember.

## Mistake #3: Starting at the bottom

Work moves first.
""",
    )
    _write_markdown(
        tmp_path / "tips.md",
        """# Beginner bouldering tips

## 1. Warm up

Prepare before climbing.

## 2. Read the problem

Plan the sequence.
""",
    )

    context = build_answer_context(
        tmp_path,
        "summarise 这两篇 bouldering notes 里的 beginner mistakes and tips",
    )

    outlines = [block for block in context.blocks if block.kind == "outline"]
    assert {block.path for block in outlines} == {"mistakes.md", "tips.md"}
    assert any("Answer category: explicit tips" in block.text for block in outlines)
    assert any("Answer category: inferred lessons" in block.text for block in outlines)


def test_answer_context_chinese_only_query_fails_closed_without_semantic_recall(tmp_path):
    from omd.retrieval import build_answer_context

    _write_markdown(
        tmp_path / "tips.md",
        "# Beginner bouldering tips\n\n## 1. Warm up\n\nPrepare before climbing.\n",
    )

    context = build_answer_context(tmp_path, "这些抱石笔记给初学者哪些建议？")

    assert context.hits == ()
    assert context.blocks == ()


def test_answer_context_does_not_enable_semantic_recall_from_environment(tmp_path, monkeypatch):
    import omd.retrieval as retrieval

    _write_markdown(
        tmp_path / "tips.md",
        "# Beginner bouldering tips\n\n## 1. Warm up\n\nPrepare before climbing.\n",
    )
    monkeypatch.setenv("OMD_VAULT_QA_EMBED_MODEL", "bge-m3")
    monkeypatch.setenv("OMD_VAULT_QA_OLLAMA_HOST", "http://localhost:11434")
    monkeypatch.setenv("OMD_VAULT_QA_SEMANTIC_RERANK", "1")
    network_calls = 0

    def unexpected_request(*_args, **_kwargs):
        nonlocal network_calls
        network_calls += 1
        raise AssertionError("semantic retrieval must remain explicitly disabled")

    monkeypatch.setattr(retrieval, "request_ollama_json", unexpected_request)

    context = retrieval.build_answer_context(tmp_path, "beginner bouldering tips")

    assert context.retrieval_mode == "sparse"
    assert context.blocks
    assert network_calls == 0


def test_answer_context_chinese_only_query_uses_hybrid_recall_when_embeddings_are_available(
    tmp_path,
    monkeypatch,
):
    import omd.retrieval as retrieval

    _write_markdown(
        tmp_path / "tips.md",
        """# Beginner bouldering tips

## 1. Warm up

Prepare before climbing.

## 2. Use your legs

Push from your feet.
""",
    )

    monkeypatch.setattr(
        retrieval,
        "_semantic_note_scores",
        lambda notes, query, config: {note.path: 0.9 if note.path == "tips.md" else 0.0 for note in notes},
    )
    monkeypatch.setattr(
        retrieval,
        "_semantic_candidate_scores",
        lambda candidates, query, config: {candidate.key: 0.75 for candidate in candidates},
    )

    context = retrieval.build_answer_context(
        tmp_path,
        "这些抱石笔记给初学者哪些建议？",
        semantic_config=retrieval.SemanticRecallConfig(host="http://localhost:11434", model="bge-m3"),
    )

    assert context.retrieval_mode == "hybrid"
    assert context.warnings == ()
    assert context.blocks
    assert {block.path for block in context.blocks} == {"tips.md"}


def test_answer_context_marks_sparse_fallback_when_semantic_recall_fails(tmp_path, monkeypatch):
    import omd.retrieval as retrieval

    _write_markdown(
        tmp_path / "tips.md",
        "# Beginner bouldering tips\n\n## 1. Warm up\n\nPrepare before climbing.\n",
    )

    def fail(*_args, **_kwargs):
        raise RuntimeError("semantic recall offline")

    monkeypatch.setattr(retrieval, "_semantic_note_scores", fail)

    context = retrieval.build_answer_context(
        tmp_path,
        "这些抱石笔记给初学者哪些建议？",
        semantic_config=retrieval.SemanticRecallConfig(host="http://localhost:11434", model="bge-m3"),
    )

    assert context.retrieval_mode == "sparse"
    assert context.warnings == ("semantic_recall_unavailable",)
    assert context.blocks == ()


def test_answer_context_reports_rerank_warning_and_keeps_hybrid_hits(tmp_path, monkeypatch):
    import omd.retrieval as retrieval

    _write_markdown(
        tmp_path / "tips.md",
        """# Beginner bouldering tips

## 1. Warm up

Prepare before climbing.

## 2. Use your legs

Push from your feet.
""",
    )

    monkeypatch.setattr(
        retrieval,
        "_semantic_note_scores",
        lambda notes, query, config: {note.path: 0.8 for note in notes},
    )
    monkeypatch.setattr(
        retrieval,
        "_semantic_candidate_scores",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("rerank offline")),
    )

    context = retrieval.build_answer_context(
        tmp_path,
        "这些抱石笔记给初学者哪些建议？",
        semantic_config=retrieval.SemanticRecallConfig(
            host="http://localhost:11434",
            model="bge-m3",
            rerank=True,
        ),
    )

    assert context.retrieval_mode == "hybrid"
    assert "semantic_rerank_unavailable" in context.warnings
    assert context.blocks


def test_semantic_embeddings_use_batch_api_shape_and_persistent_cache(tmp_path, monkeypatch):
    import omd.retrieval as retrieval

    calls: list[tuple[str, dict]] = []

    def fake_request(request, timeout):
        payload = json.loads(request.data.decode("utf-8"))
        calls.append((request.full_url, payload))
        assert timeout > 0
        return {"embeddings": [[1.0, 0.0], [0.0, 1.0]]}

    monkeypatch.setattr(retrieval, "request_ollama_json", fake_request)
    monkeypatch.setattr(retrieval, "_embedding_cache_path", lambda: tmp_path / "semantic-cache.sqlite3")
    retrieval._EMBEDDING_CACHE.clear()

    config = retrieval.SemanticRecallConfig(host="http://localhost:11434", model="bge-m3")
    first = retrieval._semantic_text_embeddings(config, ["alpha", "beta"])
    second = retrieval._semantic_text_embeddings(config, ["alpha", "beta"])

    assert first == [(1.0, 0.0), (0.0, 1.0)]
    assert second == first
    assert calls == [
        (
            "http://localhost:11434/api/embed",
            {"model": "bge-m3", "input": ["alpha", "beta"]},
        )
    ]


def test_semantic_embeddings_replace_corrupt_persistent_cache_rows(tmp_path, monkeypatch):
    import omd.retrieval as retrieval

    cache_path = tmp_path / "semantic-cache.sqlite3"
    monkeypatch.setattr(retrieval, "_embedding_cache_path", lambda: cache_path)
    retrieval._EMBEDDING_CACHE.clear()

    config = retrieval.SemanticRecallConfig(host="http://localhost:11434", model="bge-m3")
    with retrieval._embedding_cache_connection() as connection:
        assert connection is not None
        connection.execute(
            """
            INSERT OR REPLACE INTO embedding_cache (
                host,
                model,
                text_hash,
                embedding_json,
                updated_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                "http://localhost:11434",
                "bge-m3",
                retrieval._embedding_cache_hash("alpha"),
                "{not-json",
                1,
            ),
        )
        connection.commit()

    calls: list[tuple[str, ...]] = []

    def fake_request(request, timeout):
        payload = json.loads(request.data.decode("utf-8"))
        texts = tuple(payload["input"])
        calls.append(texts)
        assert timeout > 0
        return {"embeddings": [[1.0, 0.0] for _ in texts]}

    monkeypatch.setattr(retrieval, "request_ollama_json", fake_request)

    first = retrieval._semantic_text_embeddings(config, ["alpha"])
    retrieval._EMBEDDING_CACHE.clear()
    second = retrieval._semantic_text_embeddings(config, ["alpha"])

    assert first == [(1.0, 0.0)]
    assert second == first
    assert calls == [("alpha",)]


def test_semantic_embeddings_bound_the_memory_cache_and_keep_recent_entries(monkeypatch):
    import omd.retrieval as retrieval

    retrieval._EMBEDDING_CACHE.clear()
    monkeypatch.setattr(retrieval, "_EMBEDDING_MEMORY_CACHE_MAX_ROWS", 2)
    first = ("http://localhost:11434", "bge-m3", "first")
    second = ("http://localhost:11434", "bge-m3", "second")
    third = ("http://localhost:11434", "bge-m3", "third")

    retrieval._remember_embedding(first, (1.0, 0.0))
    retrieval._remember_embedding(second, (0.0, 1.0))
    assert retrieval._memory_cached_embedding(first) == (1.0, 0.0)
    retrieval._remember_embedding(third, (0.5, 0.5))

    assert list(retrieval._EMBEDDING_CACHE) == [first, third]


def test_semantic_embeddings_reject_routed_hosts_and_invalid_numeric_values(tmp_path, monkeypatch):
    import omd.retrieval as retrieval

    monkeypatch.setattr(retrieval, "_embedding_cache_path", lambda: tmp_path / "semantic-cache.sqlite3")
    retrieval._EMBEDDING_CACHE.clear()

    with pytest.raises(RuntimeError, match="base URL"):
        retrieval._semantic_text_embeddings(
            retrieval.SemanticRecallConfig(host="http://localhost:11434/api", model="bge-m3"),
            ["alpha"],
        )
    with pytest.raises(RuntimeError, match="timeout"):
        retrieval._semantic_text_embeddings(
            retrieval.SemanticRecallConfig(
                host="http://localhost:11434",
                model="bge-m3",
                timeout_seconds=float("nan"),
            ),
            ["alpha"],
        )
    with pytest.raises(RuntimeError, match="model revision"):
        retrieval._semantic_text_embeddings(
            retrieval.SemanticRecallConfig(
                host="http://localhost:11434",
                model="bge-m3",
                model_revision="x" * 513,
            ),
            ["alpha"],
        )

    monkeypatch.setattr(
        retrieval,
        "request_ollama_json",
        lambda *_args, **_kwargs: {"embeddings": [[1.0, float("nan")]]},
    )
    with pytest.raises(RuntimeError, match="malformed"):
        retrieval._semantic_text_embeddings(
            retrieval.SemanticRecallConfig(host="http://localhost:11434", model="bge-m3"),
            ["alpha"],
        )


def test_semantic_cache_invalidates_on_host_model_and_content(tmp_path, monkeypatch):
    import omd.retrieval as retrieval

    calls: list[tuple[str, tuple[str, ...]]] = []

    def fake_request(request, timeout):
        payload = json.loads(request.data.decode("utf-8"))
        texts = tuple(payload["input"])
        calls.append((payload["model"], texts))
        assert timeout > 0
        return {"embeddings": [[float(len(text)), 1.0] for text in texts]}

    monkeypatch.setattr(retrieval, "request_ollama_json", fake_request)
    monkeypatch.setattr(retrieval, "_embedding_cache_path", lambda: tmp_path / "semantic-cache.sqlite3")
    retrieval._EMBEDDING_CACHE.clear()

    config = retrieval.SemanticRecallConfig(host="http://localhost:11434", model="bge-m3")
    retrieval._semantic_text_embeddings(config, ["alpha"])
    retrieval._semantic_text_embeddings(config, ["alpha"])
    retrieval._semantic_text_embeddings(
        retrieval.SemanticRecallConfig(host="http://127.0.0.1:11434", model="bge-m3"),
        ["alpha"],
    )
    retrieval._semantic_text_embeddings(
        retrieval.SemanticRecallConfig(host="http://localhost:11434", model="nomic-embed-text"),
        ["alpha"],
    )
    retrieval._semantic_text_embeddings(
        retrieval.SemanticRecallConfig(
            host="http://localhost:11434",
            model="bge-m3",
            model_revision="sha256:newer-build",
        ),
        ["alpha"],
    )
    retrieval._semantic_text_embeddings(config, ["beta"])

    assert calls == [
        ("bge-m3", ("alpha",)),
        ("bge-m3", ("alpha",)),
        ("nomic-embed-text", ("alpha",)),
        ("bge-m3", ("alpha",)),
        ("bge-m3", ("beta",)),
    ]


def test_semantic_cache_reuses_rows_with_the_same_model_revision(tmp_path, monkeypatch):
    import omd.retrieval as retrieval

    calls: list[tuple[str, tuple[str, ...]]] = []

    def fake_request(request, timeout):
        payload = json.loads(request.data.decode("utf-8"))
        texts = tuple(payload["input"])
        calls.append((payload["model"], texts))
        assert timeout > 0
        return {"embeddings": [[1.0, 0.0] for _ in texts]}

    monkeypatch.setattr(retrieval, "request_ollama_json", fake_request)
    monkeypatch.setattr(retrieval, "_embedding_cache_path", lambda: tmp_path / "semantic-cache.sqlite3")
    retrieval._EMBEDDING_CACHE.clear()

    config = retrieval.SemanticRecallConfig(
        host="http://localhost:11434",
        model="bge-m3",
        model_revision="sha256:same-build",
    )
    retrieval._semantic_text_embeddings(config, ["alpha"])
    retrieval._EMBEDDING_CACHE.clear()
    retrieval._semantic_text_embeddings(config, ["alpha"])

    assert calls == [("bge-m3", ("alpha",))]


def test_semantic_text_embeddings_reject_dimension_mismatch_when_scoring(tmp_path, monkeypatch):
    import omd.retrieval as retrieval

    _write_markdown(
        tmp_path / "tips.md",
        """# Beginner bouldering tips

## 1. Warm up

Prepare before climbing.

## 2. Read the problem

Plan the sequence.
""",
    )

    def fake_request(request, _timeout):
        payload = json.loads(request.data.decode("utf-8"))
        if payload["input"] == ["这些抱石笔记给初学者哪些建议？"]:
            return {"embeddings": [[1.0, 0.0]]}
        return {"embeddings": [[1.0, 0.0, 0.0]]}

    monkeypatch.setattr(retrieval, "request_ollama_json", fake_request)
    monkeypatch.setattr(retrieval, "_embedding_cache_path", lambda: tmp_path / "semantic-cache.sqlite3")

    context = retrieval.build_answer_context(
        tmp_path,
        "这些抱石笔记给初学者哪些建议？",
        semantic_config=retrieval.SemanticRecallConfig(host="http://localhost:11434", model="bge-m3"),
    )

    assert context.retrieval_mode == "sparse"
    assert "semantic_recall_unavailable" in context.warnings


def test_hybrid_note_recall_uses_deterministic_rank_fusion(tmp_path):
    import omd.retrieval as retrieval

    notes = [
        retrieval._AnswerNote(
            path="a.md",
            title="Alpha",
            body="alpha",
            headings=(),
            preference_bonus=0.0,
            has_outline=False,
            has_mistake_outline=False,
            has_numbered_outline=False,
        ),
        retrieval._AnswerNote(
            path="b.md",
            title="Beta",
            body="beta",
            headings=(),
            preference_bonus=0.0,
            has_outline=False,
            has_mistake_outline=False,
            has_numbered_outline=False,
        ),
        retrieval._AnswerNote(
            path="c.md",
            title="Gamma",
            body="gamma",
            headings=(),
            preference_bonus=0.0,
            has_outline=False,
            has_mistake_outline=False,
            has_numbered_outline=False,
        ),
    ]
    lexical_ranked = [
        retrieval._ScoredAnswerNote(note=notes[0], score=10.0, matched_terms=2),
        retrieval._ScoredAnswerNote(note=notes[1], score=8.0, matched_terms=2),
    ]
    semantic_scores = {"b.md": 0.9, "c.md": 0.8}

    fused = retrieval._fuse_answer_note_ranks(notes, lexical_ranked, semantic_scores)

    assert [item.note.path for item in fused] == ["b.md", "a.md", "c.md"]


def test_answer_context_shoe_size_question_rejects_numeric_size_distractor(tmp_path):
    from omd.retrieval import build_answer_context

    _write_markdown(
        tmp_path / "tips.md",
        """# Beginner bouldering tips

## 8. Smart gear

Try climbing shoes in a store and choose gear that feels comfortable.
""",
    )
    _write_markdown(
        tmp_path / "storage.md",
        "# Storage page size\n\nThe configured database page size is 4096 bytes.\n",
    )

    context = build_answer_context(
        tmp_path,
        "What shoe size should a beginner buy according to these bouldering notes?",
    )

    assert context.blocks
    assert {block.path for block in context.blocks} == {"tips.md"}
    assert "4096" not in "\n".join(block.text for block in context.blocks)


def test_answer_context_dedupes_candidate_keys_before_truncating_ranked_candidates(tmp_path, monkeypatch):
    import omd.retrieval as retrieval

    _write_markdown(tmp_path / "a.md", "# Alpha\n\nLocal retrieval alpha.\n")
    _write_markdown(tmp_path / "b.md", "# Beta\n\nLocal retrieval beta.\n")

    def fake_ranked(
        _candidates,
        _terms,
        _phrases,
        *,
        list_intent,
        mistake_intent,
        comparison_intent,
        semantic_scores=None,
    ):
        return [
            retrieval._ScoredAnswerCandidate(
                candidate=retrieval._AnswerCandidate(
                    path="a.md",
                    title="Alpha",
                    heading=f"Duplicate {index}",
                    kind="section",
                    ranking_text=f"alpha duplicate {index}",
                    evidence_text=f"alpha duplicate {index}",
                    focus=0,
                    key="a.md:section:shared",
                    preference_bonus=0.0,
                ),
                score=100.0 - index,
                matched_terms=2,
            )
            for index in range(24)
        ] + [
            retrieval._ScoredAnswerCandidate(
                candidate=retrieval._AnswerCandidate(
                    path="b.md",
                    title="Beta",
                    heading="Unique",
                    kind="section",
                    ranking_text="beta unique",
                    evidence_text="beta unique",
                    focus=0,
                    key="b.md:section:unique",
                    preference_bonus=0.0,
                ),
                score=50.0,
                matched_terms=2,
            )
        ]

    monkeypatch.setattr(retrieval, "_rank_answer_candidates", fake_ranked)

    context = retrieval.build_answer_context(tmp_path, "local retrieval", candidate_limit=24)

    assert context.candidate_count == 2
    assert {block.path for block in context.blocks} == {"a.md", "b.md"}


def test_answer_context_bounds_candidates_blocks_and_total_text_deterministically(tmp_path):
    from omd.retrieval import build_answer_context

    for index in range(40):
        _write_markdown(
            tmp_path / f"note-{index:02d}.md",
            f"# Retrieval note {index}\n\nLocal retrieval context marker {index}.\n",
        )

    first = build_answer_context(tmp_path, "local retrieval context")
    second = build_answer_context(tmp_path, "local retrieval context")

    assert first == second
    assert first.candidate_count == 24
    assert len(first.blocks) == 8
    assert sum(len(block.text) for block in first.blocks) <= 14_000


def test_answer_context_bounds_note_expansion_before_section_recall(tmp_path, monkeypatch):
    import omd.retrieval as retrieval

    for index in range(48):
        _write_markdown(
            tmp_path / f"note-{index:02d}.md",
            f"# Beginner bouldering note {index}\n\n"
            f"## {index + 1}. Warm up\n\nActionable beginner bouldering tip {index}.\n",
        )

    expanded_paths: list[str] = []
    original = retrieval._answer_candidates_for_note

    def recording_candidates_for_note(*args, **kwargs):
        expanded_paths.append(args[0])
        return original(*args, **kwargs)

    monkeypatch.setattr(
        retrieval,
        "_answer_candidates_for_note",
        recording_candidates_for_note,
    )

    context = retrieval.build_answer_context(
        tmp_path,
        "how many beginner bouldering tips are in these notes",
    )

    assert len(expanded_paths) == 24
    assert len(set(expanded_paths)) == 24
    assert context.candidate_count == 24


def test_answer_context_does_not_turn_list_intent_into_zero_match_evidence(tmp_path):
    from omd.retrieval import build_answer_context

    _write_markdown(
        tmp_path / "english.md",
        "# Bouldering tips\n\n## 1. Warm up\n\nPrepare before climbing.\n",
    )
    _write_markdown(
        tmp_path / "unrelated.md",
        "# 数据科学\n\n## 1. 模型训练\n\n评估机器学习模型。\n",
    )

    context = build_answer_context(tmp_path, "这些抱石笔记给初学者哪些建议？")

    assert context.hits == ()
    assert context.blocks == ()
    assert context.candidate_count == 0


def test_search_notes_refuses_symlinked_files_that_resolve_outside_root(tmp_path):
    from omd.retrieval import search_notes

    outside = _write_markdown(tmp_path.parent / "outside-note.md", "# Escape\n\nlexical retrieval\n")
    (tmp_path / "escape.md").symlink_to(outside)

    with pytest.raises(ValueError, match="outside root"):
        search_notes(tmp_path, "lexical")


def test_search_notes_ignores_broken_markdown_symlinks(tmp_path):
    from omd.retrieval import search_notes

    _write_markdown(tmp_path / "valid.md", "# Valid\n\nlocal lexical retrieval\n")
    (tmp_path / "broken.md").symlink_to(tmp_path / "missing.md")

    hits = search_notes(tmp_path, "lexical")

    assert _paths(hits) == ["valid.md"]


def test_find_duplicate_notes_groups_markdown_files_with_identical_content(tmp_path):
    from omd.retrieval import find_duplicate_notes

    text = "# Duplicate\n\nSame note body.\n"
    _write_markdown(tmp_path / "first.md", text)
    _write_markdown(tmp_path / "nested" / "second.md", text)
    _write_markdown(tmp_path / "unique.md", "# Unique\n\nDifferent body.\n")

    duplicates = find_duplicate_notes(tmp_path)

    assert duplicates == [["first.md", "nested/second.md"]]


def test_search_notes_preference_profile_cannot_introduce_unmatched_notes(tmp_path):
    from omd.preferences import PreferenceProfile
    from omd.retrieval import search_notes

    _write_markdown(tmp_path / "matched.md", "# Reference\n\nlexical retrieval source\n")
    _write_markdown(tmp_path / "unmatched.md", "# Reference\n\ncompletely different words\n")
    profile = PreferenceProfile(signals={"source_type": {"webpage": 10}})

    baseline = search_notes(tmp_path, "lexical")
    reranked = search_notes(tmp_path, "lexical", preference_profile=profile)

    assert _paths(reranked) == _paths(baseline)


def test_source_type_preference_uses_manifest_not_body_text(tmp_path):
    from omd.preferences import PreferenceProfile
    from omd.retrieval import search_notes

    body_only = _write_markdown(
        tmp_path / "a-body.md", "# Body mention\n\nlexical webpage webpage webpage\n"
    )
    structured = _write_markdown(tmp_path / "b-structured.md", "# Structured\n\nlexical\n")
    structured.with_suffix(".omd.json").write_text(
        json.dumps({"metadata": {"source_type": "webpage", "tags": []}}), encoding="utf-8"
    )
    profile = PreferenceProfile(signals={"source_type": {"webpage": 10}})

    hits = search_notes(tmp_path, "lexical", preference_profile=profile)

    assert _paths(hits) == ["b-structured.md", "a-body.md"]
    assert body_only.exists()


def test_source_type_preference_uses_nested_capture_manifest_metadata(tmp_path):
    from omd._manifest import write_manifest_for_output
    from omd.preferences import PreferenceProfile
    from omd.retrieval import search_notes

    plain = _write_markdown(tmp_path / "a-plain.md", "# Plain\n\nlexical\n")
    captured = _write_markdown(tmp_path / "b-captured.md", "# Captured\n\nlexical\n")
    write_manifest_for_output(
        captured,
        source="https://example.com/article",
        backend="markitdown",
        metadata={"capture": {"source_type": "webpage", "tags": ["research"]}},
    )
    profile = PreferenceProfile(signals={"source_type": {"webpage": 10}})

    hits = search_notes(tmp_path, "lexical", preference_profile=profile)

    assert _paths(hits) == ["b-captured.md", "a-plain.md"]
    assert hits[0].score > hits[1].score
    assert plain.exists()


def test_find_duplicate_notes_normalizes_cross_platform_newlines(tmp_path):
    from omd.retrieval import find_duplicate_notes

    (tmp_path / "lf.md").write_bytes(b"# Note\n\nSame body.\n")
    (tmp_path / "crlf.md").write_bytes(b"# Note\r\n\r\nSame body.\r\n")

    assert find_duplicate_notes(tmp_path) == [["crlf.md", "lf.md"]]


def test_search_notes_evidence_preserves_unicode_casefold_location(tmp_path):
    from omd.retrieval import search_notes

    _write_markdown(tmp_path / "note.md", "# German\n\nA Straße leads to the station.\n")

    hits = search_notes(tmp_path, "strasse")

    assert "Straße" in hits[0].evidence


def test_related_notes_returns_bounded_candidates_without_rewriting_files(tmp_path):
    from omd.retrieval import related_notes

    source = _write_markdown(tmp_path / "source.md", "# Source\n\nLocal retrieval and context.\n")
    related = _write_markdown(tmp_path / "related.md", "# Related\n\nContext retrieval notes.\n")
    before = related.read_bytes()

    hits = related_notes(
        tmp_path,
        "Local context retrieval should connect these notes.",
        exclude_path="source.md",
        limit=3,
    )

    assert _paths(hits) == ["related.md"]
    assert related.read_bytes() == before
    assert source.exists()


def test_related_notes_uses_a_bounded_query_for_large_source_text(tmp_path):
    from omd.retrieval import related_notes

    _write_markdown(tmp_path / "match.md", "# Match\n\nlexical context\n")
    source_text = "lexical context " + "unique-token " * 10_000

    hits = related_notes(tmp_path, source_text, limit=2)

    assert _paths(hits) == ["match.md"]


def test_search_notes_does_not_follow_symlinked_manifest_sidecar(tmp_path):
    from omd.preferences import PreferenceProfile
    from omd.retrieval import search_notes

    note = _write_markdown(tmp_path / "note.md", "# Note\n\nlexical context\n")
    outside = tmp_path.parent / "outside-sidecar.json"
    outside.write_text(json.dumps({"metadata": {"source_type": "webpage"}}), encoding="utf-8")
    note.with_suffix(".omd.json").symlink_to(outside)
    profile = PreferenceProfile(signals={"source_type": {"webpage": 10}})

    hits = search_notes(tmp_path, "lexical", preference_profile=profile)

    assert hits[0].score == 3.0


def test_search_notes_keeps_limit_and_order_deterministic_in_large_vault(tmp_path):
    from omd.retrieval import search_notes

    for index in range(1_000):
        count = index % 7 + 1
        _write_markdown(
            tmp_path / f"folder-{index % 20:02d}" / f"note-{index:04d}.md",
            f"# Note {index}\n\n" + ("context " * count) + "retrieval\n",
        )

    first = search_notes(tmp_path, "context retrieval", limit=12)
    second = search_notes(tmp_path, "context retrieval", limit=12)

    assert len(first) == 12
    assert second == first
    assert [hit.score for hit in first] == sorted(
        (hit.score for hit in first), reverse=True
    )


def _vault_snapshot(root: Path) -> list[tuple[str, bytes | None, int, int]]:
    snapshot = []
    for path in sorted(root.rglob("*")):
        stat = path.lstat()
        snapshot.append(
            (
                path.relative_to(root).as_posix(),
                path.read_bytes() if path.is_file() and not path.is_symlink() else None,
                stat.st_mode,
                stat.st_mtime_ns,
            )
        )
    return snapshot


def test_build_vault_catalog_extracts_multilingual_frontmatter_and_inline_tags(tmp_path):
    from omd.retrieval import build_vault_catalog

    _write_markdown(tmp_path / "Inbox" / "source.md", "# Source\n\n本地 AI 知识工作流\n")
    _write_markdown(
        tmp_path / "Notes" / "local-ai.md",
        """---
title: 本地 AI
aliases:
  - Local AI
  - 本地智能
tags: [ai/local, research]
---
# Ignored H1

本地模型支持知识工作流。 #workflow
```text
#not-a-tag
```
""",
    )

    catalog = build_vault_catalog(
        tmp_path,
        "我正在设计 Local AI 与 #workflow 的知识工作流。",
        exclude_path="Inbox/source.md",
    )

    assert len(catalog.candidates) == 1
    candidate = catalog.candidates[0]
    assert candidate.path == "Notes/local-ai.md"
    assert candidate.title == "本地 AI"
    assert candidate.aliases == ("Local AI", "本地智能")
    assert candidate.tags == ("ai/local", "research", "workflow")
    assert "local-ai.md" not in candidate.id
    assert catalog.vault_tags == ("ai/local", "research", "workflow")


def test_build_vault_catalog_excludes_target_hidden_and_all_symlinks(tmp_path):
    from omd.retrieval import build_vault_catalog

    target = _write_markdown(tmp_path / "Inbox" / "source.md", "# Source\n\nlocal AI\n")
    real = _write_markdown(tmp_path / "Notes" / "real.md", "# Local AI\n\nlocal AI\n")
    _write_markdown(tmp_path / ".obsidian" / "secret.md", "# Local AI\n")
    (tmp_path / "Notes" / "linked.md").symlink_to(real)
    outside = _write_markdown(tmp_path.parent / "outside-catalog.md", "# Local AI\n")
    (tmp_path / "Notes" / "outside.md").symlink_to(outside)

    catalog = build_vault_catalog(
        tmp_path,
        "local AI",
        exclude_path="Inbox/source.md",
    )

    assert [candidate.path for candidate in catalog.candidates] == ["Notes/real.md"]
    assert target.exists()


def test_build_vault_catalog_refuses_post_scan_symlink_swap(tmp_path, monkeypatch):
    import omd.retrieval as retrieval

    note = _write_markdown(tmp_path / "Notes" / "candidate.md", "# Safe\n\nlocal AI\n")
    outside = _write_markdown(tmp_path.parent / "outside-race.md", "# Secret\n\nlocal AI\n")
    original = retrieval._catalog_markdown_paths

    def swap_after_scan(root, *, excluded):
        paths = original(root, excluded=excluded)
        note.unlink()
        note.symlink_to(outside)
        return paths

    monkeypatch.setattr(retrieval, "_catalog_markdown_paths", swap_after_scan)

    with pytest.raises(retrieval.VaultCatalogError) as excinfo:
        retrieval.build_vault_catalog(tmp_path, "local AI")

    assert excinfo.value.code == "vault_read_failed"


def test_build_vault_catalog_is_deterministic_bounded_and_read_only(tmp_path):
    from omd.retrieval import build_vault_catalog

    _write_markdown(tmp_path / "source.md", "# Source\n\ncontext retrieval\n")
    for index in range(12):
        _write_markdown(
            tmp_path / "Notes" / f"note-{index:02d}.md",
            f"# Note {index}\n\ncontext retrieval {index}\n",
        )
    before = _vault_snapshot(tmp_path)

    first = build_vault_catalog(tmp_path, "context retrieval", exclude_path="source.md", limit=5)
    second = build_vault_catalog(tmp_path, "context retrieval", exclude_path="source.md", limit=5)

    assert len(first.candidates) == 5
    assert second == first
    assert len({candidate.id for candidate in first.candidates}) == 5
    assert _vault_snapshot(tmp_path) == before


def test_build_vault_catalog_uses_bounded_metadata_for_large_notes(tmp_path):
    from omd.retrieval import build_vault_catalog

    _write_markdown(
        tmp_path / "large.md",
        "---\ntitle: Large Local AI\ntags: [large]\n---\n" + "x" * (256 * 1024),
    )

    catalog = build_vault_catalog(tmp_path, "Large Local AI")

    assert [candidate.title for candidate in catalog.candidates] == ["Large Local AI"]
    assert "large_note_body_skipped" in catalog.warnings


def test_build_vault_catalog_fails_closed_when_file_gate_is_exceeded(tmp_path, monkeypatch):
    import omd.retrieval as retrieval

    monkeypatch.setattr(retrieval, "MAX_CATALOG_NOTES", 2)
    for index in range(3):
        _write_markdown(tmp_path / f"note-{index}.md", f"# Note {index}\n\ncontext\n")

    with pytest.raises(retrieval.VaultCatalogError) as excinfo:
        retrieval.build_vault_catalog(tmp_path, "context")

    assert excinfo.value.code == "vault_catalog_too_large"


def test_build_vault_catalog_rejects_non_utf8_markdown(tmp_path):
    from omd.retrieval import VaultCatalogError, build_vault_catalog

    (tmp_path / "invalid.md").write_bytes(b"# Invalid\n\xff\n")

    with pytest.raises(VaultCatalogError) as excinfo:
        build_vault_catalog(tmp_path, "Invalid")

    assert excinfo.value.code == "invalid_utf8"


def test_read_vault_markdown_is_bounded_and_rejects_symlinks(tmp_path):
    from omd.retrieval import VaultCatalogError, read_vault_markdown

    _write_markdown(tmp_path / "Notes" / "safe.md", "# Safe\n")
    assert read_vault_markdown(tmp_path, "Notes/safe.md", max_bytes=64) == "# Safe\n"

    with pytest.raises(VaultCatalogError) as too_large:
        read_vault_markdown(tmp_path, "Notes/safe.md", max_bytes=4)
    assert too_large.value.code == "request_too_large"

    outside = _write_markdown(tmp_path.parent / "outside-read.md", "# Secret\n")
    linked = tmp_path / "Notes" / "linked.md"
    linked.symlink_to(outside)
    with pytest.raises(VaultCatalogError) as unsafe:
        read_vault_markdown(tmp_path, "Notes/linked.md", max_bytes=64)
    assert unsafe.value.code == "note_not_found"


def test_validate_vault_markdown_path_rejects_missing_and_escape(tmp_path):
    from omd.retrieval import VaultCatalogError, validate_vault_markdown_path

    _write_markdown(tmp_path / "Notes" / "safe.md", "# Safe\n")
    validate_vault_markdown_path(tmp_path, "Notes/safe.md")

    with pytest.raises(VaultCatalogError) as missing:
        validate_vault_markdown_path(tmp_path, "Notes/missing.md")
    assert missing.value.code == "note_not_found"

    with pytest.raises(ValueError, match="relative Markdown"):
        validate_vault_markdown_path(tmp_path, "../outside.md")


def test_read_vault_markdown_rechecks_actual_bytes_after_fstat(tmp_path, monkeypatch):
    import omd.retrieval as retrieval

    _write_markdown(tmp_path / "note.md", "safe")
    original_read = retrieval.os.read

    def grown_read(file_descriptor, count):
        if count == 5:
            return b"grown"
        return original_read(file_descriptor, count)

    monkeypatch.setattr(retrieval.os, "read", grown_read)

    with pytest.raises(retrieval.VaultCatalogError) as excinfo:
        retrieval.read_vault_markdown(tmp_path, "note.md", max_bytes=4)

    assert excinfo.value.code == "request_too_large"


def test_catalog_treats_actual_over_limit_read_as_metadata_only(tmp_path, monkeypatch):
    import omd.retrieval as retrieval

    _write_markdown(tmp_path / "note.md", "# Note\n")
    monkeypatch.setattr(retrieval, "MAX_CATALOG_FILE_BYTES", 8)
    monkeypatch.setattr(retrieval, "_METADATA_PREFIX_BYTES", 4)
    original_read = retrieval.os.read

    def grown_read(file_descriptor, count):
        if count == 9:
            return b"context!!"
        return original_read(file_descriptor, count)

    monkeypatch.setattr(retrieval.os, "read", grown_read)

    catalog = retrieval.build_vault_catalog(tmp_path, "note")

    assert catalog.candidates
    assert "large_note_body_skipped" in catalog.warnings
