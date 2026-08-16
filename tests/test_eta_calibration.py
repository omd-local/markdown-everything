import json
import math
from dataclasses import asdict

import pytest


def _sample(**overrides):
    from omd.eta_calibration import EtaCalibrationSample

    values = {
        "stage": "transcribe",
        "source": "audio",
        "device": "arm64_16gb",
        "runtime": "mlx",
        "model": "mlx-community.whisper-large-v3",
        "cold": False,
        "unit": "audio_seconds",
        "actual_seconds": 40.0,
        "baseline_seconds": 50.0,
        "shadow_p50_seconds": 42.0,
        "shadow_p90_seconds": 45.0,
    }
    values.update(overrides)
    return EtaCalibrationSample(**values)


def _samples(count, **overrides):
    return [_sample(**overrides) for _ in range(count)]


def test_eta_calibration_sample_rejects_path_like_or_unsafe_identities():
    from omd.eta_calibration import EtaCalibrationSample

    with pytest.raises(ValueError, match="model must be a privacy-safe identity"):
        EtaCalibrationSample(
            stage="transcribe",
            source="audio",
            device="arm64_16gb",
            runtime="mlx",
            model="/Users/private/models/secret.gguf",
            cold=False,
            unit="audio_seconds",
            actual_seconds=40.0,
            baseline_seconds=50.0,
            shadow_p50_seconds=42.0,
            shadow_p90_seconds=45.0,
        )

    with pytest.raises(ValueError, match="stage must be a privacy-safe token"):
        EtaCalibrationSample(
            stage="../private",
            source="audio",
            device="arm64_16gb",
            runtime="mlx",
            model="mlx-community.whisper-large-v3",
            cold=False,
            unit="audio_seconds",
            actual_seconds=40.0,
            baseline_seconds=50.0,
            shadow_p50_seconds=42.0,
            shadow_p90_seconds=45.0,
        )


def test_eta_calibration_sample_rejects_nan_and_infinite_numbers():
    with pytest.raises(ValueError, match="actual_seconds must be positive and finite"):
        _sample(actual_seconds=math.nan)

    with pytest.raises(ValueError, match="shadow_p90_seconds must be positive and finite"):
        _sample(shadow_p90_seconds=math.inf)


def test_eta_calibration_rejects_mixed_duration_regimes_for_gate():
    from omd.eta_calibration import (
        REASON_INSUFFICIENT_SAMPLES,
        REASON_MIXED_DURATION_REGIMES,
        evaluate_eta_calibration,
    )

    report = evaluate_eta_calibration(
        _samples(15, actual_seconds=20.0, baseline_seconds=30.0, shadow_p50_seconds=16.0, shadow_p90_seconds=22.0)
        + _samples(15, actual_seconds=40.0, baseline_seconds=50.0, shadow_p50_seconds=42.0, shadow_p90_seconds=45.0)
    )

    assert report["eligible"] is False
    assert report["ineligibility_reasons"] == [
        REASON_MIXED_DURATION_REGIMES,
        REASON_INSUFFICIENT_SAMPLES,
    ]
    assert report["sample_count"] == 30
    assert set(report["regimes"]) == {"long", "short"}


def test_eta_calibration_rejects_insufficient_samples():
    from omd.eta_calibration import REASON_INSUFFICIENT_SAMPLES, evaluate_eta_calibration

    report = evaluate_eta_calibration(_samples(29))

    assert report["eligible"] is False
    assert report["selected_regime"] == "long"
    assert report["sample_count"] == 29
    assert report["ineligibility_reasons"] == [REASON_INSUFFICIENT_SAMPLES]


def test_eta_calibration_rejects_weak_improvement():
    from omd.eta_calibration import REASON_WEAK_IMPROVEMENT, evaluate_eta_calibration

    report = evaluate_eta_calibration(_samples(30, shadow_p50_seconds=49.2, shadow_p90_seconds=50.0))

    assert report["eligible"] is False
    assert report["ineligibility_reasons"] == [REASON_WEAK_IMPROVEMENT]
    assert report["baseline_median_error"] == 0.25
    assert report["shadow_median_error"] == 0.23
    assert report["improvement_ratio"] == 0.08


def test_eta_calibration_rejects_poor_p90_coverage():
    from omd.eta_calibration import REASON_POOR_P90_COVERAGE, evaluate_eta_calibration

    report = evaluate_eta_calibration(_samples(30, shadow_p50_seconds=38.0, shadow_p90_seconds=39.0))

    assert report["eligible"] is False
    assert report["ineligibility_reasons"] == [REASON_POOR_P90_COVERAGE]
    assert report["p90_upper_bound_coverage"] == 0.0


def test_eta_calibration_reports_eligible_homogeneous_regime():
    from omd.eta_calibration import evaluate_eta_calibration

    report = evaluate_eta_calibration(_samples(30))

    assert report["schema_version"] == 1
    assert report["eligible"] is True
    assert report["selected_regime"] == "long"
    assert report["ineligibility_reasons"] == []
    assert report["sample_count"] == 30
    assert report["baseline_median_error"] == 0.25
    assert report["shadow_median_error"] == 0.05
    assert report["p90_upper_bound_coverage"] == 1.0
    assert report["improvement_ratio"] == 0.8
    assert report["regimes"]["long"]["error_metric"] == "relative_absolute_error"


def test_eta_calibration_report_excludes_raw_samples_and_private_fields():
    from omd.eta_calibration import evaluate_eta_calibration

    report = evaluate_eta_calibration(_samples(30))
    serialised = json.dumps(report, sort_keys=True)

    assert "samples" not in report
    assert "source_text" not in serialised
    assert "title" not in serialised
    assert "url" not in serialised
    assert "path" not in serialised
    assert "credential" not in serialised
    assert "mlx-community.whisper-large-v3" not in serialised
    assert report["segments"]["by_stage"] == [
        {"regime_counts": {"long": 30}, "sample_count": 30, "stage": "transcribe"}
    ]
    assert report["segments"]["by_source"] == [
        {"regime_counts": {"long": 30}, "sample_count": 30, "source": "audio"}
    ]
    assert report["segments"]["by_device"] == [
        {"device": "arm64_16gb", "regime_counts": {"long": 30}, "sample_count": 30}
    ]
    assert report["segments"]["by_cold_warm"] == [
        {"cold_warm": "warm", "regime_counts": {"long": 30}, "sample_count": 30}
    ]


def test_eta_calibration_apply_records_eligible_gate():
    from omd.eta_calibration import apply_eta_calibration_gate, evaluate_eta_calibration

    class StubStore:
        def __init__(self):
            self.calls = []

        def record_calibration_gate(self, **kwargs):
            self.calls.append(kwargs)
            return True

    store = StubStore()
    report = evaluate_eta_calibration(_samples(30))

    assert apply_eta_calibration_gate(store, report) is True
    assert store.calls == [
        {
            "benchmark_id": "shadow-calibration-v1",
            "pipeline_version": "omd-phase2-v1",
            "sample_count": 30,
            "baseline_median_error": 0.25,
            "shadow_median_error": 0.05,
            "p90_coverage": 1.0,
        }
    ]


def test_eta_calibration_apply_skips_ineligible_gate():
    from omd.eta_calibration import apply_eta_calibration_gate, evaluate_eta_calibration

    class StubStore:
        def __init__(self):
            self.called = False

        def record_calibration_gate(self, **kwargs):
            self.called = True
            return True

    store = StubStore()
    report = evaluate_eta_calibration(_samples(29))

    assert apply_eta_calibration_gate(store, report) is False
    assert store.called is False


def test_eta_calibration_loader_rejects_unexpected_private_fields(tmp_path):
    from omd.eta_calibration import load_eta_calibration_samples

    payload = asdict(_sample())
    payload["source_text"] = "private note"
    source = tmp_path / "samples.json"
    source.write_text(json.dumps({"samples": [payload]}), encoding="utf-8")

    with pytest.raises(ValueError, match="unexpected calibration sample fields"):
        load_eta_calibration_samples(source)


def test_eta_calibration_loader_rejects_symlink(tmp_path):
    from omd.eta_calibration import load_eta_calibration_samples

    target = tmp_path / "samples.json"
    target.write_text(json.dumps({"samples": [asdict(_sample())]}), encoding="utf-8")
    link = tmp_path / "linked.json"
    link.symlink_to(target)

    with pytest.raises(ValueError, match="regular non-symlink file"):
        load_eta_calibration_samples(link)


def test_eta_calibration_main_writes_report_and_applies_eligible_gate(tmp_path):
    from omd.eta_calibration import main
    from omd.eta_history import EtaHistoryStore

    source = tmp_path / "samples.json"
    source.write_text(
        json.dumps({"samples": [asdict(sample) for sample in _samples(30)]}),
        encoding="utf-8",
    )
    report_path = tmp_path / "report.json"
    history_path = tmp_path / "history.json"

    rc = main(
        [
            "--input",
            str(source),
            "--output",
            str(report_path),
            "--history",
            str(history_path),
            "--apply",
        ]
    )

    assert rc == 0
    assert json.loads(report_path.read_text(encoding="utf-8"))["eligible"] is True
    assert EtaHistoryStore(history_path).summary()["calibrated_pipeline_versions"] == [
        "omd-phase2-v1"
    ]


def test_eta_calibration_main_does_not_apply_ineligible_report(tmp_path):
    from omd.eta_calibration import main
    from omd.eta_history import EtaHistoryStore

    source = tmp_path / "samples.json"
    source.write_text(
        json.dumps({"samples": [asdict(sample) for sample in _samples(29)]}),
        encoding="utf-8",
    )
    report_path = tmp_path / "report.json"
    history_path = tmp_path / "history.json"

    rc = main(
        [
            "--input",
            str(source),
            "--output",
            str(report_path),
            "--history",
            str(history_path),
            "--apply",
        ]
    )

    assert rc == 1
    assert json.loads(report_path.read_text(encoding="utf-8"))["eligible"] is False
    assert EtaHistoryStore(history_path).summary()["calibration_gate_count"] == 0


def test_eta_calibration_store_records_bounded_privacy_safe_samples(tmp_path):
    from omd.eta_calibration import EtaCalibrationStore, load_eta_calibration_samples

    store = EtaCalibrationStore(tmp_path / "shadow-samples.json", max_count=2)
    store.record(_sample(actual_seconds=41.0))
    store.record(_sample(actual_seconds=42.0))
    store.record(_sample(actual_seconds=43.0))

    samples = load_eta_calibration_samples(store.path)
    assert [sample.actual_seconds for sample in samples] == [42.0, 43.0]
    assert store.summary() == {"schema_version": 1, "sample_count": 2}

    store.reset()
    assert store.summary() == {"schema_version": 1, "sample_count": 0}


def test_eta_calibration_store_rejects_symlink_destination(tmp_path):
    from omd.eta_calibration import EtaCalibrationStore

    target = tmp_path / "target.json"
    target.write_text(json.dumps({"schema_version": 1, "samples": []}), encoding="utf-8")
    link = tmp_path / "samples.json"
    link.symlink_to(target)

    with pytest.raises(ValueError, match="non-symlink"):
        EtaCalibrationStore(link).record(_sample())
