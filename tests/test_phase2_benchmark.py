import json


def test_receipt_benchmark_uses_only_synthetic_sources(tmp_path):
    from omd import __version__
    from omd.phase2_benchmark import run_receipt_benchmark

    report = run_receipt_benchmark(iterations=3, file_sizes=(128,), work_root=tmp_path)

    assert report["schema_version"] == 1
    assert report["corpus_version"] == "phase2-synthetic-v1"
    assert report["app_version"] == __version__
    assert report["status"] in {"pass", "fail"}
    assert {scenario["scenario"] for scenario in report["scenarios"]} == {
        "authored_note_receipt",
        "url_job_receipt",
        "local_file_128b_receipt",
    }
    serialized = json.dumps(report)
    assert str(tmp_path) not in serialized
    assert "source text" not in serialized


def test_receipt_benchmark_writes_an_auditable_json_report(tmp_path):
    from omd.phase2_benchmark import main

    output = tmp_path / "report.json"

    assert main(["--iterations", "2", "--file-size", "64", "--output", str(output)]) in {0, 1}
    report = json.loads(output.read_text(encoding="utf-8"))

    assert report["iterations"] == 2
    assert report["scenarios"]
    assert all("p95_seconds" in scenario for scenario in report["scenarios"])
