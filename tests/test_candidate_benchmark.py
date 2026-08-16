import json

import pytest


def _case(tmp_path, *, domain="article", duration_seconds=None, retained_ratio=None):
    from omd.candidate_benchmark import BenchmarkCase

    source = tmp_path / ("sample.wav" if domain in {"asr", "vad"} else "sample.html")
    source.write_bytes(b"synthetic public benchmark fixture")
    minimum = None if retained_ratio is None else retained_ratio[0]
    maximum = None if retained_ratio is None else retained_ratio[1]
    return BenchmarkCase(
        case_id=f"{domain}_sample_1",
        domain=domain,
        source=source,
        required_terms=("public", "fixture"),
        duration_seconds=duration_seconds,
        min_retained_ratio=minimum,
        max_retained_ratio=maximum,
    )


def test_candidate_benchmark_report_omits_source_paths_and_candidate_output(tmp_path):
    from omd.candidate_benchmark import CandidateOutput, CandidateSpec, run_candidate_benchmark

    secret_output = "public fixture PRIVATE TRANSCRIPT BODY"
    report = run_candidate_benchmark(
        cases=[_case(tmp_path)],
        candidates=[
            CandidateSpec(
                candidate_id="article_builtin",
                domain="article",
                run=lambda _source: CandidateOutput(text=secret_output),
            )
        ],
        corpus_version="public_article_v1",
    )

    serialized = json.dumps(report)
    assert report["scope"] == "opt_in_candidate_benchmark_only"
    assert str(tmp_path) not in serialized
    assert secret_output not in serialized
    assert "PRIVATE TRANSCRIPT BODY" not in serialized


def test_article_candidate_fails_when_required_fixture_terms_are_missing(tmp_path):
    from omd.candidate_benchmark import CandidateOutput, CandidateSpec, run_candidate_benchmark

    report = run_candidate_benchmark(
        cases=[_case(tmp_path)],
        candidates=[
            CandidateSpec(
                candidate_id="article_regression",
                domain="article",
                run=lambda _source: CandidateOutput(text="public only"),
            )
        ],
        corpus_version="public_article_v1",
    )

    candidate = report["candidates"][0]
    assert report["status"] == "fail"
    assert candidate["status"] == "fail"
    assert candidate["quality_pass_count"] == 0
    assert candidate["quality_failure_count"] == 1


def test_unavailable_asr_candidate_is_skipped_without_running(tmp_path):
    from omd.candidate_benchmark import CandidateOutput, CandidateSpec, run_candidate_benchmark

    called = False

    def should_not_run(_source):
        nonlocal called
        called = True
        return CandidateOutput(text="public fixture")

    report = run_candidate_benchmark(
        cases=[_case(tmp_path, domain="asr", duration_seconds=2.0)],
        candidates=[
            CandidateSpec(
                candidate_id="asr_optional",
                domain="asr",
                run=should_not_run,
                available=lambda: False,
            )
        ],
        corpus_version="public_audio_v1",
    )

    assert called is False
    assert report["status"] == "incomplete"
    assert report["candidates"][0]["status"] == "skipped"
    assert report["candidates"][0]["reason"] == "runtime_unavailable"


def test_asr_candidate_report_uses_audio_seconds_without_transcript_text(tmp_path):
    from omd.candidate_benchmark import CandidateOutput, CandidateSpec, run_candidate_benchmark

    report = run_candidate_benchmark(
        cases=[_case(tmp_path, domain="asr", duration_seconds=12.5)],
        candidates=[
            CandidateSpec(
                candidate_id="asr_local",
                domain="asr",
                run=lambda _source: CandidateOutput(text="public fixture transcript"),
            )
        ],
        corpus_version="public_audio_v1",
    )

    serialized = json.dumps(report)
    case = report["candidates"][0]["cases"][0]
    assert case["work_unit"] == "audio_seconds"
    assert case["work_units"] == 12.5
    assert "public fixture transcript" not in serialized


def test_vad_candidate_is_excluded_until_explicitly_enabled(tmp_path):
    from omd.candidate_benchmark import CandidateOutput, CandidateSpec, run_candidate_benchmark

    report = run_candidate_benchmark(
        cases=[_case(tmp_path, domain="vad", duration_seconds=10.0, retained_ratio=(0.4, 0.8))],
        candidates=[
            CandidateSpec(
                candidate_id="vad_optional",
                domain="vad",
                run=lambda _source: CandidateOutput(
                    text="public fixture",
                    retained_audio_seconds=6.0,
                ),
            )
        ],
        corpus_version="public_audio_v1",
    )

    assert report["candidates"] == []
    assert report["status"] == "incomplete"


def test_vad_candidate_checks_speech_retention_when_enabled(tmp_path):
    from omd.candidate_benchmark import CandidateOutput, CandidateSpec, run_candidate_benchmark

    report = run_candidate_benchmark(
        cases=[_case(tmp_path, domain="vad", duration_seconds=10.0, retained_ratio=(0.4, 0.8))],
        candidates=[
            CandidateSpec(
                candidate_id="vad_clipping",
                domain="vad",
                run=lambda _source: CandidateOutput(
                    text="public fixture",
                    retained_audio_seconds=2.0,
                ),
            )
        ],
        corpus_version="public_audio_v1",
        include_vad=True,
    )

    candidate = report["candidates"][0]
    assert report["status"] == "fail"
    assert candidate["status"] == "fail"
    assert candidate["cases"][0]["retained_audio_ratio"] == 0.2
    assert candidate["cases"][0]["quality_passed"] is False


def test_candidate_benchmark_rejects_symlinked_sources(tmp_path):
    from omd.candidate_benchmark import BenchmarkCase

    target = tmp_path / "target.html"
    target.write_text("public fixture", encoding="utf-8")
    link = tmp_path / "link.html"
    link.symlink_to(target)

    with pytest.raises(ValueError, match="regular non-symlink"):
        BenchmarkCase(
            case_id="article_sample_1",
            domain="article",
            source=link,
            required_terms=("public",),
        )


def test_candidate_benchmark_rejects_unsafe_report_tokens(tmp_path):
    from omd.candidate_benchmark import CandidateOutput, CandidateSpec

    with pytest.raises(ValueError, match="privacy-safe token"):
        CandidateSpec(
            candidate_id="/Users/private/model",
            domain="article",
            run=lambda _source: CandidateOutput(text="public fixture"),
        )
