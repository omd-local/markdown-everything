from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest


def _parse_events(stderr: str) -> list[dict]:
    events: list[dict] = []
    for line in stderr.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        events.append(json.loads(line))
    return events


def test_normalize_captured_markdown_repairs_sphinx_progress_output():
    from omd import capture

    raw = (
        '<!DOCTYPE html>\n\n'
        '# Tutorial[#](#tutorial "Link to this heading")\n\n'
        '0%|          | 0.00/44.7M [00:00<?, ?B/s]\n'
        '100%|██████████| 44.7M/44.7M [00:00<00:00, 395MB/s]\n\n'
        '### Train and evaluate[#](#train-and-evaluate "Link to this heading")\n'
    )

    normalized = capture.normalize_captured_markdown(raw)

    assert '<!DOCTYPE html>' not in normalized
    assert '# Tutorial\n' in normalized
    assert '### Train and evaluate\n' in normalized
    assert '```text\n0%|          | 0.00/44.7M [00:00<?, ?B/s]\n' in normalized
    assert '100%|██████████| 44.7M/44.7M [00:00<00:00, 395MB/s]\n```' in normalized


def test_cli_capture_forwards_optional_markdown_polish(tmp_path, monkeypatch):
    from omd import capture, cli

    vault = tmp_path / "vault"
    seen: dict[str, object] = {}

    def fake_capture_one(source, vault_root, **kwargs):
        seen.update(kwargs)
        return capture.CaptureResult(
            source=source,
            output_path=Path(vault_root) / "note.md",
            index_path=Path(vault_root) / "index.json",
            source_type="webpage",
            title="Note",
            tags=[],
            return_code=0,
        )

    monkeypatch.setattr(capture, "capture_one", fake_capture_one)

    rc = cli.main([
        "capture",
        "https://example.com",
        "--vault",
        str(vault),
        "--polish-md",
        "--polish-md-model",
        "qwen3:4b-instruct",
        "--polish-md-host",
        "http://localhost:11434",
    ])

    assert rc == 0
    assert seen["polish_md"] is True
    assert seen["polish_md_model"] == "qwen3:4b-instruct"
    assert seen["polish_md_host"] == "http://localhost:11434"


def test_capture_polishes_only_after_structural_cleanup(tmp_path, monkeypatch):
    from omd import _polish_md, capture, cli

    source = tmp_path / "tutorial.html"
    source.write_text("<h1>Tutorial</h1>", encoding="utf-8")
    vault = tmp_path / "vault"

    def fake_route_one(_target, output, *_args, **_kwargs):
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            '<!DOCTYPE html>\n\n'
            '# Tutorial[#](#tutorial "Link to this heading")\n\n'
            '0%| | 0.00/44.7M [00:00<?, ?B/s]\n',
            encoding="utf-8",
        )
        return 0

    def fake_polish(markdown, *, model, host, allow_remote):
        assert '<!DOCTYPE html>' not in markdown
        assert '# Tutorial\n' in markdown
        assert '```text\n0%| | 0.00/44.7M [00:00<?, ?B/s]\n```' in markdown
        assert model == "qwen3:4b-instruct"
        assert host == "http://localhost:11434"
        assert allow_remote is False
        return markdown + "\nPolished locally.\n"

    monkeypatch.setattr(cli, "route_one", fake_route_one)
    monkeypatch.setattr(_polish_md, "polish_markdown", fake_polish)

    result = capture.capture_one(
        str(source),
        vault,
        polish_md=True,
        polish_md_model="qwen3:4b-instruct",
    )

    assert result.return_code == 0
    text = result.output_path.read_text(encoding="utf-8")
    assert "Polished locally." in text
    manifest = json.loads(result.output_path.with_suffix(".omd.json").read_text(encoding="utf-8"))
    capture_meta = manifest["metadata"]["capture"]
    assert capture_meta["markdown_polish_requested"] is True
    assert capture_meta["markdown_polish_model"] == "qwen3:4b-instruct"
    assert capture_meta["llm_used"] == "qwen3:4b-instruct"
    assert capture_meta["model_endpoint"] == "local_ollama"


def test_cli_capture_uses_memory_sized_default_text_model(tmp_path, monkeypatch):
    from omd import capture, cli

    vault = tmp_path / "vault"
    vault.mkdir()
    seen: dict[str, str] = {}

    def fake_capture_one(source, vault_root, **kwargs):
        seen["memory_model"] = kwargs["memory_model"]
        return capture.CaptureResult(
            source=source,
            output_path=vault / "note.md",
            index_path=vault / "index.json",
            source_type="webpage",
            title="Note",
            tags=[],
            return_code=0,
        )

    monkeypatch.setenv("OMD_SYSTEM_MEMORY_GB", "8")
    monkeypatch.setattr(capture, "capture_one", fake_capture_one)

    rc = cli.main(["capture", "https://example.com", "--vault", str(vault), "--memory-cards"])

    assert rc == 0
    assert seen["memory_model"] == "qwen2.5:1.5b-instruct"


def test_capture_one_api_uses_memory_sized_default_text_model(tmp_path, monkeypatch):
    from omd import capture, cli, memory_cards

    source = tmp_path / "note.html"
    source.write_text("<h1>Note</h1>", encoding="utf-8")
    seen: dict[str, str] = {}

    def fake_route_one(_target, output, *_args, **_kwargs):
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("# Note\n\nBody\n", encoding="utf-8")
        return 0

    def fake_generate(_markdown, *, model, host, **_kwargs):
        seen["memory_model"] = model
        return memory_cards.MemoryCardsResult(
            summary="Summary",
            tags=[],
            cards_markdown="",
            model=model,
            host=host,
            warnings=[],
        )

    monkeypatch.setenv("OMD_SYSTEM_MEMORY_GB", "8")
    monkeypatch.setattr(cli, "route_one", fake_route_one)
    monkeypatch.setattr(memory_cards, "generate_memory_cards", fake_generate)

    result = capture.capture_one(source, tmp_path / "vault", memory_cards=True)

    assert result.return_code == 0
    assert seen["memory_model"] == "qwen2.5:1.5b-instruct"


def test_capture_batch_api_passes_memory_sized_default_to_each_item(tmp_path, monkeypatch):
    from omd import capture

    batch = tmp_path / "items.txt"
    batch.write_text("https://example.com/one\n", encoding="utf-8")
    seen: dict[str, str] = {}

    def fake_capture(target, vault, **kwargs):
        seen["memory_model"] = kwargs["memory_model"]
        return capture.CaptureResult(
            source=target,
            output_path=vault / "note.md",
            index_path=vault / "index.json",
            source_type="webpage",
            title="Note",
            tags=[],
            return_code=0,
        )

    monkeypatch.setenv("OMD_SYSTEM_MEMORY_GB", "8")
    monkeypatch.setattr(capture, "_capture_one_with_retries", fake_capture)

    result = capture.capture_batch(str(batch), tmp_path / "vault", memory_cards=True)

    assert result.exit_code == 0
    assert seen["memory_model"] == "qwen2.5:1.5b-instruct"


def test_cli_capture_writes_pdf_to_vault_with_frontmatter_index_and_manifest(tmp_path, monkeypatch):
    from omd import capture, cli

    source = tmp_path / "Quarterly Report.pdf"
    source.write_bytes(b"fake pdf")
    vault = tmp_path / "vault"

    def fake_route_one(target, output, lang, reel_extra, *, agent_safe=False, output_format="md"):
        assert target == str(source)
        assert lang == "eng"
        assert reel_extra == []
        assert agent_safe is False
        assert output_format == "md"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("# Demo Report\n\nBody\n", encoding="utf-8")
        return 0

    monkeypatch.setattr(cli, "route_one", fake_route_one)
    monkeypatch.setattr(capture, "_now_iso", lambda: "2026-07-04T00:00:00Z")

    rc = cli.main(["capture", str(source), "--vault", str(vault), "--tags", "research,pdf"])

    assert rc == 0
    outputs = list((vault / "Sources" / "PDFs").glob("*.md"))
    assert len(outputs) == 1
    assert outputs[0].name == "Demo Report.md"
    text = outputs[0].read_text(encoding="utf-8")
    assert text.startswith("---\n")
    assert 'source_type: "pdf"' in text
    assert f'local_source_path: "{source}"' in text
    assert 'captured_at: "2026-07-04T00:00:00Z"' in text
    assert 'title: "Demo Report"' in text
    assert "  - \"research\"\n" in text
    assert "  - \"pdf\"\n" in text
    assert 'manifest_version: "2"' not in text
    assert 'source_id: "src_' not in text
    assert 'source_hash: "' not in text
    assert 'capture_id: "cap_' not in text
    assert 'model_endpoint: "none"' not in text
    assert 'llm_used: "none"' not in text
    assert "## Source\n" not in text
    assert "## Full Content\n" in text
    assert text.endswith("# Demo Report\n\nBody\n")
    assert (vault / "Sources").is_dir()
    assert (vault / "Index").is_dir()
    assert (vault / "_attachments").is_dir()

    index = vault / "Index" / "OMD Captures.md"
    index_text = index.read_text(encoding="utf-8")
    assert "# OMD Captures" in index_text
    assert "Demo Report" in index_text
    assert "`pdf`" in index_text

    manifest = json.loads(outputs[0].with_suffix(".omd.json").read_text(encoding="utf-8"))
    assert manifest["manifest_version"] == 2
    assert manifest["source"] == str(source)
    assert manifest["local_source_path"] == str(source.resolve(strict=False))
    assert manifest["content_checksum"] == hashlib.sha256(outputs[0].read_bytes()).hexdigest()
    assert manifest["backend"] == "markitdown"
    assert manifest["capture_id"] == manifest["metadata"]["capture"]["capture_id"]
    assert manifest["source_hash"] == manifest["metadata"]["capture"]["source_hash"]
    assert manifest["elements"][0]["type"] == "title"
    assert manifest["metadata"]["capture"]["source_type"] == "pdf"
    assert manifest["metadata"]["capture"]["privacy"] == "local_storage"
    assert manifest["metadata"]["capture"]["storage"] == "local"
    assert manifest["metadata"]["capture"]["network_fetch"] is False
    assert manifest["metadata"]["capture"]["model_endpoint"] == "none"
    assert manifest["checksum"] == hashlib.sha256(outputs[0].read_bytes()).hexdigest()


def test_capture_repeated_same_input_creates_distinct_notes(tmp_path, monkeypatch):
    from omd import capture, cli

    source = tmp_path / "note.png"
    source.write_bytes(b"fake image")
    vault = tmp_path / "vault"

    def fake_route_one(_target, output, *_args, **_kwargs):
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("# Screenshot\n\nOCR text\n", encoding="utf-8")
        return 0

    monkeypatch.setattr(cli, "route_one", fake_route_one)
    monkeypatch.setattr(capture, "_now_iso", lambda: "2026-07-04T00:00:00Z")

    first = capture.capture_one(str(source), vault)
    second = capture.capture_one(str(source), vault)

    assert first.return_code == 0
    assert second.return_code == 0
    assert first.output_path != second.output_path
    assert first.output_path.exists()
    assert second.output_path.exists()
    assert second.output_path.stem.endswith("-2")


def test_capture_renames_noisy_media_title_to_readable_title(tmp_path, monkeypatch):
    from omd import capture, cli

    vault = tmp_path / "vault"
    source = "https://v.douyin.com/m9aSWdLilf4"
    noisy_title = "2026-07-10 19-25-42_第4期*来*给自己做这3种*充电宝\\_\\_#爱自己*......*#爱自己*#找到热爱*#成长充电站*#女性智慧_audio"

    def fake_route_one(_target, output, *_args, **_kwargs):
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(f"# {noisy_title}\n\nTranscript\n", encoding="utf-8")
        return 0

    monkeypatch.setattr(cli, "route_one", fake_route_one)
    monkeypatch.setattr(capture, "_now_iso", lambda: "2026-07-04T00:00:00Z")

    result = capture.capture_one(source, vault)
    text = result.output_path.read_text(encoding="utf-8")

    assert result.return_code == 0
    assert result.output_path.name == "第4期来给自己做这3种充电宝.md"
    assert 'title: "第4期来给自己做这3种充电宝"' in text
    assert "_audio" not in result.output_path.name
    assert "#" not in result.output_path.name


def test_capture_failure_removes_partial_output(tmp_path, monkeypatch):
    from omd import capture, cli
    from omd._manifest import write_manifest_for_output

    source = tmp_path / "broken.html"
    source.write_text("<h1>Broken</h1>", encoding="utf-8")
    vault = tmp_path / "vault"

    def fake_route_one(_target, output, *_args, **_kwargs):
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("# Partial\n", encoding="utf-8")
        write_manifest_for_output(output, source=str(source), backend="markitdown")
        return 7

    monkeypatch.setattr(cli, "route_one", fake_route_one)

    result = capture.capture_one(str(source), vault)

    assert result.return_code == 7
    assert not result.output_path.exists()
    assert not result.output_path.with_suffix(".omd.json").exists()
    assert not (vault / "Index" / "OMD Captures.md").exists()


def test_capture_local_source_path_is_absolute_for_relative_input(tmp_path, monkeypatch):
    from omd import capture, cli

    source = tmp_path / "relative.html"
    source.write_text("<h1>Relative</h1>", encoding="utf-8")
    vault = tmp_path / "vault"
    monkeypatch.chdir(tmp_path)

    def fake_route_one(_target, output, *_args, **_kwargs):
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("# Relative\n\nBody\n", encoding="utf-8")
        return 0

    monkeypatch.setattr(cli, "route_one", fake_route_one)
    monkeypatch.setattr(capture, "_now_iso", lambda: "2026-07-04T00:00:00Z")

    result = capture.capture_one("relative.html", vault)

    text = result.output_path.read_text(encoding="utf-8")
    assert f'local_source_path: "{source.resolve(strict=False)}"' in text
    assert 'local_source_path: "relative.html"' not in text


def test_capture_source_type_for_url_and_file_inputs(tmp_path):
    from omd import capture
    from omd._preflight import inspect_target

    pdf = tmp_path / "report.pdf"
    pdf.write_bytes(b"fake")

    assert capture.source_type_for("https://youtu.be/abc123", inspect_target("https://youtu.be/abc123")) == "youtube"
    assert capture.source_type_for("https://v.douyin.com/abc123/", inspect_target("https://v.douyin.com/abc123/")) == "douyin"
    assert capture.source_type_for(str(pdf), inspect_target(str(pdf))) == "pdf"


def test_capture_metadata_records_memory_sized_model_for_bare_polish(monkeypatch):
    from omd import capture

    monkeypatch.setenv("OMD_SYSTEM_MEMORY_GB", "8")

    metadata = capture.capture_metadata(
        source="https://youtu.be/abc123",
        source_type="youtube",
        title="Video",
        tags=[],
        captured_at="2026-07-04T00:00:00Z",
        preflight={
            "detected_type": "reel_url",
            "metadata": {"url": "https://youtu.be/abc123"},
        },
        reel_extra=["--polish"],
    )

    assert metadata["llm_used"] == "qwen2.5:1.5b-instruct"
    assert metadata["model_endpoint"] == "local_ollama"


def test_cli_capture_memory_cards_append_generated_sections_and_preserve_raw(tmp_path, monkeypatch, capsys):
    from omd import capture, cli, memory_cards

    monkeypatch.setenv("OMD_SYSTEM_MEMORY_GB", "16")
    source = tmp_path / "research.html"
    source.write_text("<h1>Research</h1>", encoding="utf-8")
    vault = tmp_path / "vault"

    def fake_route_one(_target, output, *_args, **_kwargs):
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("# Research\n\nRaw source body with enough detail for cards.\n", encoding="utf-8")
        return 0

    def fake_generate(markdown, *, model, host, timeout, title, source_type):
        assert "Raw source body" in markdown
        assert model == "qwen3:4b-instruct"
        assert host == "http://localhost:11434"
        assert timeout == 180
        assert title == "Research"
        assert source_type == "office_doc"
        return memory_cards.MemoryCardsResult(
            summary="This source explains why local AI memory should preserve raw context.",
            tags=["local-ai", "obsidian"],
            cards_markdown=(
                "### Concepts\n"
                "- [[Local AI Memory]]: Structured Markdown can be reused by AI tools. "
                "Evidence: Source section above."
            ),
            model=model,
            host=host,
            warnings=[],
        )

    monkeypatch.setattr(cli, "route_one", fake_route_one)
    monkeypatch.setattr(memory_cards, "generate_memory_cards", fake_generate)
    monkeypatch.setattr(capture, "_now_iso", lambda: "2026-07-04T00:00:00Z")

    rc = cli.main(["capture", str(source), "--vault", str(vault), "--memory-cards"])
    _ = capsys.readouterr()

    assert rc == 0
    output = next((vault / "Sources" / "Documents").glob("*.md"))
    text = output.read_text(encoding="utf-8")
    assert "memory_cards: true" not in text
    assert 'memory_model: "qwen3:4b-instruct"' not in text
    assert "summary_generated: true" not in text
    assert "  - \"local-ai\"\n" in text
    assert "  - \"obsidian\"\n" in text
    assert "  - \"office-doc\"\n" in text
    assert "## Memory Cards\n" in text
    assert "[[Local AI Memory]]" in text
    assert "## Full Content\n\n# Research\n\nRaw source body" in text

    manifest = json.loads(output.with_suffix(".omd.json").read_text(encoding="utf-8"))
    capture_meta = manifest["metadata"]["capture"]
    assert capture_meta["memory_cards"] is True
    assert capture_meta["memory_model"] == "qwen3:4b-instruct"
    assert capture_meta["summary_generated"] is True
    assert capture_meta["generated_tags"] == ["local-ai", "obsidian"]
    assert capture_meta["llm_used"] == "qwen3:4b-instruct"
    assert capture_meta["model_endpoint"] == "local_ollama"


def test_cli_capture_memory_cards_records_drift_warnings(tmp_path, monkeypatch, capsys):
    from omd import capture, cli, memory_cards

    monkeypatch.setenv("OMD_SYSTEM_MEMORY_GB", "16")
    source = tmp_path / "long.html"
    source.write_text("<h1>Long</h1>", encoding="utf-8")
    vault = tmp_path / "vault"

    def fake_route_one(_target, output, *_args, **_kwargs):
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("# Long\n\n" + ("raw detail " * 80), encoding="utf-8")
        return 0

    def fake_generate(markdown, *, model, host, timeout, title, source_type):
        assert timeout == 180
        return memory_cards.MemoryCardsResult(
            summary="Short.",
            tags=[],
            cards_markdown="- A card without a citation.",
            model=model,
            host=host,
            warnings=[
                "memory cards are very short relative to the source; review before relying on them",
                "memory cards do not include explicit Evidence references",
                "no generated tags were returned",
            ],
        )

    monkeypatch.setattr(cli, "route_one", fake_route_one)
    monkeypatch.setattr(memory_cards, "generate_memory_cards", fake_generate)
    monkeypatch.setattr(capture, "_now_iso", lambda: "2026-07-04T00:00:00Z")

    rc = cli.main(["capture", str(source), "--vault", str(vault), "--memory-cards"])
    stderr = capsys.readouterr().err

    assert rc == 0
    assert "very short" in stderr
    output = next((vault / "Sources" / "Documents").glob("*.md"))
    text = output.read_text(encoding="utf-8")
    assert "memory_warnings:" not in text
    assert "## Memory Warnings\n" in text
    assert "do not include explicit Evidence" in text
    manifest = json.loads(output.with_suffix(".omd.json").read_text(encoding="utf-8"))
    assert "memory_warnings" in manifest["metadata"]["capture"]


def test_cli_capture_memory_card_failure_keeps_raw_capture(tmp_path, monkeypatch, capsys):
    from omd import capture, cli, memory_cards

    monkeypatch.setenv("OMD_SYSTEM_MEMORY_GB", "16")
    source = tmp_path / "offline.html"
    source.write_text("<h1>Offline</h1>", encoding="utf-8")
    vault = tmp_path / "vault"

    def fake_route_one(_target, output, *_args, **_kwargs):
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("# Offline\n\nRaw capture still matters.\n", encoding="utf-8")
        return 0

    def fail_generate(*_args, **_kwargs):
        raise RuntimeError("Ollama connection refused")

    monkeypatch.setattr(cli, "route_one", fake_route_one)
    monkeypatch.setattr(memory_cards, "generate_memory_cards", fail_generate)
    monkeypatch.setattr(capture, "_now_iso", lambda: "2026-07-04T00:00:00Z")

    rc = cli.main(["capture", str(source), "--vault", str(vault), "--memory-cards"])
    stderr = capsys.readouterr().err

    assert rc == 0
    assert "memory cards failed" in stderr
    assert "ollama pull qwen3:4b-instruct" in stderr
    output = next((vault / "Sources" / "Documents").glob("*.md"))
    text = output.read_text(encoding="utf-8")
    assert "memory_attempted: true" not in text
    assert "memory_cards: false" not in text
    assert 'model_endpoint: "local_ollama"' not in text
    assert 'llm_used: "qwen3:4b-instruct"' not in text
    assert "summary_generated: false" not in text
    assert 'memory_error: "Ollama connection refused"' not in text
    assert "## Full Content\n\n# Offline\n\nRaw capture still matters." in text
    manifest = json.loads(output.with_suffix(".omd.json").read_text(encoding="utf-8"))
    capture_meta = manifest["metadata"]["capture"]
    assert capture_meta["memory_attempted"] is True
    assert capture_meta["memory_cards"] is False
    assert capture_meta["model_endpoint"] == "local_ollama"
    assert capture_meta["llm_used"] == "qwen3:4b-instruct"
    assert capture_meta["summary_generated"] is False
    assert capture_meta["memory_error"] == "Ollama connection refused"


def test_cli_capture_memory_card_failure_records_remote_endpoint(tmp_path, monkeypatch, capsys):
    from omd import capture, cli, memory_cards

    monkeypatch.setenv("OMD_SYSTEM_MEMORY_GB", "16")
    source = tmp_path / "remote.html"
    source.write_text("<h1>Remote</h1>", encoding="utf-8")
    vault = tmp_path / "vault"

    def fake_route_one(_target, output, *_args, **_kwargs):
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("# Remote\n\nRaw capture.\n", encoding="utf-8")
        return 0

    def fail_generate(*_args, **kwargs):
        assert kwargs["allow_remote"] is True
        raise RuntimeError("remote Ollama unavailable")

    monkeypatch.setattr(cli, "route_one", fake_route_one)
    monkeypatch.setattr(memory_cards, "generate_memory_cards", fail_generate)
    monkeypatch.setattr(capture, "_now_iso", lambda: "2026-07-04T00:00:00Z")

    rc = cli.main([
        "capture",
        str(source),
        "--vault",
        str(vault),
        "--memory-cards",
        "--memory-host",
        "https://models.example.com",
        "--allow-remote-ollama",
    ])
    _ = capsys.readouterr()

    assert rc == 0
    output = next((vault / "Sources" / "Documents").glob("*.md"))
    text = output.read_text(encoding="utf-8")
    assert "memory_attempted: true" not in text
    assert "memory_cards: false" not in text
    assert 'model_endpoint: "remote_ollama"' not in text
    assert 'llm_used: "qwen3:4b-instruct"' not in text
    assert 'memory_error: "remote Ollama unavailable"' not in text
    manifest = json.loads(output.with_suffix(".omd.json").read_text(encoding="utf-8"))
    capture_meta = manifest["metadata"]["capture"]
    assert capture_meta["memory_attempted"] is True
    assert capture_meta["memory_cards"] is False
    assert capture_meta["model_endpoint"] == "remote_ollama"
    assert capture_meta["llm_used"] == "qwen3:4b-instruct"
    assert capture_meta["memory_error"] == "remote Ollama unavailable"


def test_cli_capture_memory_cards_prints_explicit_model_install_boundary(tmp_path, monkeypatch, capsys):
    from omd import capture, cli, memory_cards

    monkeypatch.setenv("OMD_SYSTEM_MEMORY_GB", "16")
    source = tmp_path / "research.html"
    source.write_text("<h1>Research</h1>", encoding="utf-8")
    vault = tmp_path / "vault"

    def fake_route_one(_target, output, *_args, **_kwargs):
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("# Research\n\nRaw source body.\n", encoding="utf-8")
        return 0

    def fake_generate(markdown, *, model, host, timeout, title, source_type):
        assert timeout == 180
        return memory_cards.MemoryCardsResult(
            summary="Summary with enough context.",
            tags=["local-ai"],
            cards_markdown="### Claims\n- Claim: X. Evidence: source section above.",
            model=model,
            host=host,
            warnings=[],
        )

    monkeypatch.setattr(cli, "route_one", fake_route_one)
    monkeypatch.setattr(memory_cards, "generate_memory_cards", fake_generate)
    monkeypatch.setattr(capture, "_now_iso", lambda: "2026-07-04T00:00:00Z")

    rc = cli.main(["capture", str(source), "--vault", str(vault), "--memory-cards"])
    stderr = capsys.readouterr().err

    assert rc == 0
    assert "will not download models automatically" in stderr
    assert "ollama pull qwen3:4b-instruct" in stderr
    assert "qwen3:4b-instruct for Chinese/mixed text memory cards" in stderr
    assert "gemma3:4b for image/OCR enhancement" in stderr
    assert "bge-m3 for future multilingual search" in stderr


def test_cli_capture_agent_safe_rejects_memory_cards(capsys):
    from omd import cli

    with pytest.raises(SystemExit) as exc:
        cli.main(["capture", "https://example.com", "--vault", "/tmp/vault", "--memory-cards", "--agent-safe"])
    events = _parse_events(capsys.readouterr().err)

    assert exc.value.code == 1
    assert events[-1]["kind"] == "agent_safe_blocked_flag"
    assert "--agent-safe rejects --memory-cards" in events[-1]["message"]


def test_capture_metadata_distinguishes_remote_ollama_endpoint():
    from omd import capture

    metadata = capture.capture_metadata(
        source="https://youtu.be/abc123",
        source_type="youtube",
        title="Video",
        tags=[],
        captured_at="2026-07-04T00:00:00Z",
        preflight={
            "detected_type": "reel_url",
            "needs_network": True,
            "metadata": {"url": "https://youtu.be/abc123"},
        },
        reel_extra=["--polish", "qwen3:4b", "--ollama-host", "http://ollama.local:11434"],
    )

    assert metadata["network_fetch"] is True
    assert metadata["llm_used"] == "qwen3:4b"
    assert metadata["model_endpoint"] == "remote_ollama"


@pytest.mark.parametrize(
    "host",
    [
        "localhost:11434",
        "worker.localhost:11434",
        "127.0.0.1:11434",
        "127.0.0.2:11434",
        "[::1]:11434",
    ],
)
def test_model_endpoint_recognises_scheme_less_loopback_hosts(host):
    from omd import capture

    assert capture._model_endpoint_for_host(host) == "local_ollama"


def test_cli_capture_rejects_directory_inputs(tmp_path):
    from omd import cli

    inbox = tmp_path / "inbox"
    inbox.mkdir()

    with pytest.raises(SystemExit) as exc:
        cli.main(["capture", str(inbox), "--vault", str(tmp_path / "vault")])

    assert "directories only with --batch" in str(exc.value)


def test_cli_capture_rejects_file_vault_without_traceback(tmp_path):
    from omd import cli

    source = tmp_path / "report.html"
    source.write_text("<h1>Report</h1>", encoding="utf-8")
    vault_file = tmp_path / "vault-file"
    vault_file.write_text("not a directory", encoding="utf-8")

    with pytest.raises(SystemExit) as exc:
        cli.main(["capture", str(source), "--vault", str(vault_file)])

    assert "Vault path must be a directory path" in str(exc.value)


def test_cli_capture_accepts_directory_with_batch_flag(tmp_path, monkeypatch):
    from omd import capture, cli

    inbox = tmp_path / "inbox"
    inbox.mkdir()
    first = inbox / "a.html"
    second = inbox / "b.json"
    ignored = inbox / "notes.txt"
    first.write_text("<h1>A</h1>", encoding="utf-8")
    second.write_text('{"title":"B"}', encoding="utf-8")
    ignored.write_text("unsupported", encoding="utf-8")
    vault = tmp_path / "vault"
    seen: list[str] = []

    def fake_route_one(target, output, *_args, **_kwargs):
        seen.append(target)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(f"# {Path(target).stem.upper()}\n\nBody\n", encoding="utf-8")
        return 0

    monkeypatch.setattr(cli, "route_one", fake_route_one)
    monkeypatch.setattr(capture, "_now_iso", lambda: "2026-07-04T00:00:00Z")

    rc = cli.main(["capture", str(inbox), "--vault", str(vault), "--batch", "--tags", "batch"])

    assert rc == 0
    assert seen == [str(first), str(second)]
    outputs = sorted((vault / "Sources" / "Documents").glob("*.md"))
    assert len(outputs) == 2
    assert not list(vault.rglob("*notes*"))
    assert (vault / "_attachments").is_dir()
    index_text = (vault / "Index" / "OMD Captures.md").read_text(encoding="utf-8")
    assert index_text.count("`office_doc`") == 2


def test_cli_capture_batch_warns_when_skipping_local_video_files(tmp_path, monkeypatch, capsys):
    from omd import capture, cli

    inbox = tmp_path / "inbox"
    inbox.mkdir()
    html = inbox / "a.html"
    video = inbox / "clip.mp4"
    html.write_text("<h1>A</h1>", encoding="utf-8")
    video.write_bytes(b"fake video")
    vault = tmp_path / "vault"

    def fake_route_one(target, output, *_args, **_kwargs):
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(f"# {Path(target).stem.upper()}\n\nBody\n", encoding="utf-8")
        return 0

    monkeypatch.setattr(cli, "route_one", fake_route_one)
    monkeypatch.setattr(capture, "_now_iso", lambda: "2026-07-04T00:00:00Z")

    rc = cli.main(["capture", str(inbox), "--vault", str(vault), "--batch"])
    stderr = capsys.readouterr().err

    assert rc == 0
    assert "skipped local video files" in stderr
    assert str(video) in stderr
    assert len(list((vault / "Sources" / "Documents").glob("*.md"))) == 1
    assert not list(vault.rglob("*clip*"))


def test_cli_capture_batch_list_captures_urls_and_continues_after_failure(tmp_path, monkeypatch):
    from omd import capture, cli

    batch = tmp_path / "sources.txt"
    batch.write_text(
        "\n".join([
            "https://youtu.be/demo",
            "https://example.invalid/fail",
            "# skipped",
        ]),
        encoding="utf-8",
    )
    vault = tmp_path / "vault"

    def fake_route_one(target, output, *_args, **_kwargs):
        if "fail" in target:
            return 7
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("# Demo Video\n\nTranscript\n", encoding="utf-8")
        return 0

    monkeypatch.setattr(cli, "route_one", fake_route_one)
    monkeypatch.setattr(capture, "_now_iso", lambda: "2026-07-04T00:00:00Z")

    rc = cli.main(["capture", str(batch), "--vault", str(vault), "--batch", "--retries", "1"])

    assert rc == 1
    outputs = list((vault / "Sources" / "YouTube").glob("*.md"))
    assert len(outputs) == 1
    text = outputs[0].read_text(encoding="utf-8")
    assert 'source_url: "https://youtu.be/demo"' in text
    assert "local_source_path:" not in text
    assert text.endswith("# Demo Video\n\nTranscript\n")
    manifest = json.loads(outputs[0].with_suffix(".omd.json").read_text(encoding="utf-8"))
    capture_meta = manifest["metadata"]["capture"]
    assert capture_meta["network_fetch"] is True
    assert capture_meta["model_endpoint"] == "none"


def test_cli_capture_json_events_do_not_emit_wrapper_done_path(tmp_path, monkeypatch, capsys):
    from omd import _events, _progress, capture, cli

    source = tmp_path / "report.html"
    source.write_text("<h1>Report</h1>", encoding="utf-8")
    vault = tmp_path / "vault"

    def fake_route_one(_target, output, *_args, **_kwargs):
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("# Report\n\nBody\n", encoding="utf-8")
        _progress.done(f"wrote {output}")
        return 0

    monkeypatch.setattr(cli, "route_one", fake_route_one)
    monkeypatch.setattr(capture, "_now_iso", lambda: "2026-07-04T00:00:00Z")

    rc = cli.main(["capture", str(source), "--vault", str(vault), "--json-events"])
    events = _parse_events(capsys.readouterr().err)
    _events.configure(False)

    assert rc == 0
    done_events = [event for event in events if event["event"] == "done"]
    assert len(done_events) == 1
    assert done_events[0]["output"].endswith(".md")
    assert "captured " not in done_events[0]["output"]


def test_cli_capture_batch_json_events_do_not_emit_summary_as_done(tmp_path, monkeypatch, capsys):
    from omd import _events, _progress, capture, cli

    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "a.html").write_text("<h1>A</h1>", encoding="utf-8")
    (inbox / "b.html").write_text("<h1>B</h1>", encoding="utf-8")
    vault = tmp_path / "vault"

    def fake_route_one(target, output, *_args, **_kwargs):
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(f"# {Path(target).stem.upper()}\n\nBody\n", encoding="utf-8")
        _progress.done(f"wrote {output}")
        return 0

    monkeypatch.setattr(cli, "route_one", fake_route_one)
    monkeypatch.setattr(capture, "_now_iso", lambda: "2026-07-04T00:00:00Z")

    rc = cli.main(["capture", str(inbox), "--vault", str(vault), "--batch", "--json-events"])
    events = _parse_events(capsys.readouterr().err)
    _events.configure(False)

    assert rc == 0
    done_outputs = [event["output"] for event in events if event["event"] == "done"]
    assert len(done_outputs) == 2
    assert all(output.endswith(".md") for output in done_outputs)
    assert all("captured " not in output for output in done_outputs)


def test_capture_refreshes_manifest_but_preserves_created_at(tmp_path, monkeypatch):
    from omd import capture, cli
    from omd._manifest import write_manifest_for_output

    source = tmp_path / "scan.jpg"
    source.write_bytes(b"fake")
    vault = tmp_path / "vault"
    first_created = datetime(2024, 1, 2, 3, 4, 5, tzinfo=timezone.utc)

    def fake_route_one(_target, output, *_args, **_kwargs):
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("# Scan\n\nOCR text\n", encoding="utf-8")
        write_manifest_for_output(
            output,
            source=str(source),
            backend="tesseract",
            now=first_created,
        )
        return 0

    monkeypatch.setattr(cli, "route_one", fake_route_one)
    monkeypatch.setattr(capture, "_now_iso", lambda: "2026-07-04T00:00:00Z")

    result = capture.capture_one(str(source), vault)

    manifest = json.loads(result.output_path.with_suffix(".omd.json").read_text(encoding="utf-8"))
    assert manifest["created_at"] == "2024-01-02T03:04:05Z"
    assert manifest["updated_at"] != manifest["created_at"]
    assert manifest["output"] == str(result.output_path)
    assert manifest["capture_id"] == manifest["metadata"]["capture"]["capture_id"]
    assert manifest["metadata"]["capture"]["source_type"] == "image"
