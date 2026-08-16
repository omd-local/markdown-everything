import json
import time


def _observation(**overrides):
    from omd.eta_history import EtaObservation

    values = {
        "stage_id": "transcribe",
        "source_class": "audio",
        "device_tier": "arm64-16gb-8core",
        "runtime": "mlx",
        "model_key": "mlx-community/whisper-large-v3-turbo",
        "cold_start": False,
        "unit": "audio_seconds",
        "work_units": 60.0,
        "duration_seconds": 12.0,
        "outcome": "success",
        "observed_at": time.time(),
    }
    values.update(overrides)
    return EtaObservation(**values)


def test_eta_history_persists_only_allowlisted_observation_fields(tmp_path):
    from omd.eta_history import EtaHistoryStore

    path = tmp_path / "eta-history.json"
    store = EtaHistoryStore(path)
    assert store.record(_observation()) is True

    payload = json.loads(path.read_text(encoding="utf-8"))
    record = payload["observations"][0]

    assert set(record) == {
        "stage_id",
        "source_class",
        "device_tier",
        "runtime",
        "model_key",
        "cold_start",
        "unit",
        "work_units",
        "duration_seconds",
        "outcome",
        "observed_at",
        "pipeline_version",
        "attempt",
        "queue_depth",
        "throughput_per_second",
    }
    serialised = json.dumps(payload)
    assert "url" not in serialised
    assert "filename" not in serialised
    assert "source_text" not in serialised


def test_eta_observation_rejects_invalid_passive_runtime_fields():
    import pytest

    with pytest.raises(ValueError, match="attempt must be"):
        _observation(attempt=0)
    with pytest.raises(ValueError, match="queue_depth must be"):
        _observation(queue_depth=-1)
    with pytest.raises(ValueError, match="throughput_per_second must be"):
        _observation(throughput_per_second=float("inf"))


def test_eta_history_does_not_mix_retry_timing_with_first_attempt(tmp_path):
    from omd.eta_history import EtaHistoryStore

    store = EtaHistoryStore(tmp_path / "eta-history.json")
    for index in range(30):
        store.record(_observation(duration_seconds=20.0 + index, attempt=1))
    assert store.record_calibration_gate(
        benchmark_id="shadow-benchmark-v1",
        sample_count=30,
        baseline_median_error=0.4,
        shadow_median_error=0.2,
        p90_coverage=0.92,
    )

    estimate = store.estimate(_observation(duration_seconds=1.0, attempt=2))

    assert estimate.source == "indeterminate"
    assert estimate.sample_count == 0


def test_eta_history_hashes_path_like_model_identity():
    from omd.eta_history import privacy_safe_identity

    identity = privacy_safe_identity("/Users/private/models/whisper")

    assert identity.startswith("sha256:")
    assert "private" not in identity


def test_eta_history_uses_exact_bucket_after_enough_samples(tmp_path):
    from omd.eta_history import EtaHistoryStore

    store = EtaHistoryStore(tmp_path / "eta-history.json")
    for duration in range(10, 40):
        store.record(_observation(duration_seconds=float(duration)))
    assert (
        store.record_calibration_gate(
            benchmark_id="shadow-benchmark-v1",
            sample_count=30,
            baseline_median_error=0.4,
            shadow_median_error=0.2,
            p90_coverage=0.92,
        )
        is True
    )

    estimate = store.estimate(_observation(duration_seconds=1.0))

    assert estimate.source == "exact"
    assert estimate.sample_count == 30
    assert estimate.confidence == "high"
    assert 23 <= estimate.p50_seconds <= 26
    assert 36 <= estimate.p90_seconds <= 39


def test_eta_history_sparse_exact_bucket_falls_back_to_parent(tmp_path):
    from omd.eta_history import EtaHistoryStore

    store = EtaHistoryStore(tmp_path / "eta-history.json")
    for index in range(30):
        store.record(
            _observation(
                model_key=f"model-{index}",
                duration_seconds=20.0 + index,
            )
        )
    assert (
        store.record_calibration_gate(
            benchmark_id="shadow-benchmark-v1",
            sample_count=30,
            baseline_median_error=0.4,
            shadow_median_error=0.2,
            p90_coverage=0.92,
        )
        is True
    )

    estimate = store.estimate(_observation(model_key="new-model", duration_seconds=1.0))

    assert estimate.source == "parent"
    assert estimate.sample_count == 30
    assert estimate.confidence == "low"


def test_eta_history_does_not_publish_uncalibrated_ranges(tmp_path):
    from omd.eta_history import EtaHistoryStore

    store = EtaHistoryStore(tmp_path / "eta-history.json")
    for index in range(29):
        store.record(
            _observation(
                model_key=f"model-{index}",
                duration_seconds=20.0 + index,
            )
        )

    estimate = store.estimate(_observation(model_key="new-model", duration_seconds=1.0))

    assert estimate.p50_seconds is None
    assert estimate.p90_seconds is None
    assert estimate.sample_count == 29
    assert estimate.confidence == "collecting"
    assert estimate.source == "uncalibrated"


def test_eta_history_shadow_estimate_returns_range_without_public_gate(tmp_path):
    from omd.eta_history import EtaHistoryStore

    store = EtaHistoryStore(tmp_path / "eta-history.json")
    for duration in range(10, 40):
        store.record(_observation(duration_seconds=float(duration)))

    estimate = store.shadow_estimate(_observation(duration_seconds=1.0))
    public_estimate = store.estimate(_observation(duration_seconds=1.0))

    assert estimate.source == "exact"
    assert estimate.p50_seconds is not None
    assert estimate.p90_seconds is not None
    assert public_estimate.p50_seconds is None
    assert public_estimate.source == "uncalibrated"


def test_eta_history_v1_files_migrate_safely_and_remain_collection_only(tmp_path):
    from omd.eta_history import EtaHistoryStore

    path = tmp_path / "eta-history.json"
    payload = {
        "schema_version": 1,
        "enabled": True,
        "observations": [
            _observation(duration_seconds=20.0 + index, observed_at=1000.0 + index).to_dict()
            for index in range(30)
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")

    store = EtaHistoryStore(path)

    assert store.summary()["observation_count"] == 30
    estimate = store.estimate(_observation(duration_seconds=1.0, observed_at=2000.0))
    assert estimate.p50_seconds is None
    assert estimate.p90_seconds is None
    assert estimate.sample_count == 30
    assert estimate.confidence == "collecting"
    assert estimate.source == "uncalibrated"

    assert store.record(_observation(duration_seconds=55.0, observed_at=2001.0)) is True
    migrated = json.loads(path.read_text(encoding="utf-8"))
    assert migrated["schema_version"] == 1
    assert migrated["calibration_gates"] == {}


def test_eta_history_rejects_invalid_or_insufficient_calibration_metrics(tmp_path):
    from omd.eta_history import EtaHistoryStore

    path = tmp_path / "eta-history.json"
    store = EtaHistoryStore(path)
    for duration in range(10, 40):
        store.record(_observation(duration_seconds=float(duration)))

    assert (
        store.record_calibration_gate(
            benchmark_id="shadow-benchmark-v1",
            sample_count=30,
            baseline_median_error=float("nan"),
            shadow_median_error=0.2,
            p90_coverage=0.92,
        )
        is False
    )
    assert (
        store.record_calibration_gate(
            benchmark_id="shadow-benchmark-v1",
            sample_count=30,
            baseline_median_error=0.4,
            shadow_median_error=0.38,
            p90_coverage=0.92,
        )
        is False
    )
    assert (
        store.record_calibration_gate(
            benchmark_id="shadow-benchmark-v1",
            sample_count=30,
            baseline_median_error=0.4,
            shadow_median_error=0.2,
            p90_coverage=0.89,
        )
        is False
    )

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["calibration_gates"] == {}


def test_eta_history_rejects_invalid_benchmark_id_and_sample_count(tmp_path):
    from omd.eta_history import EtaHistoryStore

    path = tmp_path / "eta-history.json"
    store = EtaHistoryStore(path)
    for duration in range(10, 40):
        store.record(_observation(duration_seconds=float(duration)))

    assert (
        store.record_calibration_gate(
            benchmark_id="/Users/private/benchmark.json",
            sample_count=30,
            baseline_median_error=0.4,
            shadow_median_error=0.2,
            p90_coverage=0.92,
        )
        is False
    )
    assert (
        store.record_calibration_gate(
            benchmark_id="Users/private/benchmark.json",
            sample_count=30,
            baseline_median_error=0.4,
            shadow_median_error=0.2,
            p90_coverage=0.92,
        )
        is False
    )
    assert (
        store.record_calibration_gate(
            benchmark_id="shadow-benchmark-v1",
            sample_count=29,
            baseline_median_error=0.4,
            shadow_median_error=0.2,
            p90_coverage=0.92,
        )
        is False
    )
    assert (
        store.record_calibration_gate(
            benchmark_id="shadow-benchmark-v1",
            sample_count=True,
            baseline_median_error=0.4,
            shadow_median_error=0.2,
            p90_coverage=0.92,
        )
        is False
    )
    assert (
        store.record_calibration_gate(
            benchmark_id="shadow-benchmark-v1",
            sample_count=30.0,
            baseline_median_error=0.4,
            shadow_median_error=0.2,
            p90_coverage=0.92,
        )
        is False
    )

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["calibration_gates"] == {}


def test_eta_history_rejects_wrong_metric_types_without_raising(tmp_path):
    from omd.eta_history import EtaHistoryStore

    path = tmp_path / "eta-history.json"
    store = EtaHistoryStore(path)
    for duration in range(10, 40):
        store.record(_observation(duration_seconds=float(duration)))

    assert (
        store.record_calibration_gate(
            benchmark_id="shadow-benchmark-v1",
            sample_count=30,
            baseline_median_error="0.4",
            shadow_median_error=0.2,
            p90_coverage=0.92,
        )
        is False
    )
    assert (
        store.record_calibration_gate(
            benchmark_id="shadow-benchmark-v1",
            sample_count=30,
            baseline_median_error=0.4,
            shadow_median_error=False,
            p90_coverage=0.92,
        )
        is False
    )
    assert (
        store.record_calibration_gate(
            benchmark_id="shadow-benchmark-v1",
            sample_count=30,
            baseline_median_error=0.4,
            shadow_median_error=0.2,
            p90_coverage="0.92",
        )
        is False
    )


def test_eta_history_valid_gate_unlocks_historical_ranges(tmp_path):
    from omd.eta_history import EtaHistoryStore

    store = EtaHistoryStore(tmp_path / "eta-history.json")
    for duration in range(10, 40):
        store.record(_observation(duration_seconds=float(duration)))

    assert store.estimate(_observation(duration_seconds=1.0)).p50_seconds is None
    assert (
        store.record_calibration_gate(
            benchmark_id="shadow-benchmark-v1",
            sample_count=30,
            baseline_median_error=0.4,
            shadow_median_error=0.2,
            p90_coverage=0.92,
        )
        is True
    )

    estimate = store.estimate(_observation(duration_seconds=1.0))
    summary = store.summary()

    assert estimate.source == "exact"
    assert estimate.sample_count == 30
    assert estimate.confidence == "high"
    assert 23 <= estimate.p50_seconds <= 26
    assert 36 <= estimate.p90_seconds <= 39
    assert summary["calibrated_pipeline_versions"] == ["omd-phase2-v1"]
    assert summary["calibration_gate_count"] == 1
    assert "calibration_gates" not in summary


def test_eta_history_excludes_failed_cancelled_and_needs_action_observations(tmp_path):
    from omd.eta_history import EtaHistoryStore

    store = EtaHistoryStore(tmp_path / "eta-history.json")
    for outcome in ("failed", "cancelled", "needs_action"):
        store.record(_observation(outcome=outcome))

    estimate = store.estimate(_observation(duration_seconds=1.0))

    assert estimate.source == "indeterminate"
    assert estimate.sample_count == 0


def test_eta_history_disable_stops_new_observations(tmp_path):
    from omd.eta_history import EtaHistoryStore

    store = EtaHistoryStore(tmp_path / "eta-history.json")
    store.set_enabled(False)

    assert store.record(_observation()) is False
    assert store.summary()["observation_count"] == 0
    assert store.summary()["enabled"] is False


def test_eta_history_reset_removes_observations_without_reenabling(tmp_path):
    from omd.eta_history import EtaHistoryStore

    store = EtaHistoryStore(tmp_path / "eta-history.json")
    store.record(_observation())
    assert (
        store.record_calibration_gate(
            benchmark_id="shadow-benchmark-v1",
            sample_count=30,
            baseline_median_error=0.4,
            shadow_median_error=0.2,
            p90_coverage=0.92,
        )
        is True
    )
    store.set_enabled(False)
    store.reset()

    assert store.summary() == {
        "schema_version": 1,
        "enabled": False,
        "observation_count": 0,
        "successful_observation_count": 0,
        "calibrated_pipeline_versions": [],
        "calibration_gate_count": 0,
        "stages": {},
        "warning": None,
    }
    payload = json.loads((tmp_path / "eta-history.json").read_text(encoding="utf-8"))
    assert payload["calibration_gates"] == {}


def test_eta_history_ignores_malformed_gate_entries_without_corrupting_history(tmp_path):
    from omd.eta_history import EtaHistoryStore

    path = tmp_path / "eta-history.json"
    payload = {
        "schema_version": 1,
        "enabled": True,
        "observations": [
            _observation(duration_seconds=20.0 + index, observed_at=1000.0 + index).to_dict()
            for index in range(30)
        ],
        "calibration_gates": {
            "omd-phase2-v1": {
                "benchmark_id": "shadow-benchmark-v1",
                "sample_count": 30,
                "baseline_median_error": 0.4,
                "shadow_median_error": 0.2,
                "p90_coverage": 0.92,
                "recorded_at": 2000.0,
            },
            "unsafe-pipeline": {
                "benchmark_id": "/Users/private/benchmark.json",
                "sample_count": 30,
                "baseline_median_error": 0.4,
                "shadow_median_error": 0.2,
                "p90_coverage": 0.92,
                "recorded_at": 2000.0,
            },
            "broken-types": {
                "benchmark_id": "shadow-benchmark-v2",
                "sample_count": True,
                "baseline_median_error": "bad",
                "shadow_median_error": 0.2,
                "p90_coverage": 0.92,
                "recorded_at": 2000.0,
            },
        },
    }
    path.write_text(json.dumps(payload), encoding="utf-8")

    store = EtaHistoryStore(path)
    summary = store.summary()
    estimate = store.estimate(_observation(duration_seconds=1.0, observed_at=3000.0))

    assert summary["warning"] is None
    assert summary["observation_count"] == 30
    assert summary["calibrated_pipeline_versions"] == ["omd-phase2-v1"]
    assert summary["calibration_gate_count"] == 1
    assert estimate.p50_seconds is not None


def test_eta_history_corruption_is_non_blocking_and_preserved_on_next_write(tmp_path):
    from omd.eta_history import EtaHistoryStore

    path = tmp_path / "eta-history.json"
    path.write_text("{not-json", encoding="utf-8")
    store = EtaHistoryStore(path)

    summary = store.summary()
    assert summary["observation_count"] == 0
    assert "corrupt" in summary["warning"].lower()

    assert store.record(_observation()) is True
    assert store.summary()["observation_count"] == 1
    assert list(tmp_path.glob("eta-history.json.corrupt-*"))


def test_eta_history_rejects_oversized_state_without_parsing(tmp_path, monkeypatch):
    import omd.eta_history as eta_history

    monkeypatch.setattr(eta_history, "_MAX_HISTORY_BYTES", 64)
    path = tmp_path / "eta-history.json"
    path.write_bytes(b"{" + b" " * 64)
    store = eta_history.EtaHistoryStore(path)

    summary = store.summary()

    assert summary["observation_count"] == 0
    assert "corrupt" in summary["warning"].lower()


def test_eta_history_replaces_symlink_without_modifying_its_target(tmp_path):
    from omd.eta_history import EtaHistoryStore

    target = tmp_path / "unrelated.json"
    original = '{"private": "do not overwrite"}\n'
    target.write_text(original, encoding="utf-8")
    path = tmp_path / "eta-history.json"
    path.symlink_to(target)
    store = EtaHistoryStore(path)

    assert store.record(_observation()) is True

    assert target.read_text(encoding="utf-8") == original
    assert not path.is_symlink()
    assert store.summary()["observation_count"] == 1


def test_eta_history_retention_caps_age_and_count(tmp_path):
    from omd.eta_history import EtaHistoryStore

    now = time.time()
    store = EtaHistoryStore(tmp_path / "eta-history.json", max_count=3, max_age_days=30)
    store.record(_observation(observed_at=now - 40 * 86400, duration_seconds=5))
    for index in range(4):
        store.record(_observation(observed_at=now + index, duration_seconds=10 + index))

    assert store.summary()["observation_count"] == 3


def test_live_eta_requires_meaningful_real_work_before_predicting():
    from omd.eta_history import live_stage_estimate

    assert live_stage_estimate(
        completed=5,
        total=100,
        elapsed_seconds=8,
    ) is None
    estimate = live_stage_estimate(
        completed=20,
        total=100,
        elapsed_seconds=10,
    )
    assert estimate == 40.0
