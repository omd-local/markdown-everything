import json
import time


def test_telemetry_context_from_argv_keeps_only_coarse_source_and_model_metadata():
    from omd.run_telemetry import telemetry_context_from_argv

    private_url = "https://example.com/private/article?id=user-secret"
    context = telemetry_context_from_argv(
        [
            "python",
            "-m",
            "omd.cli",
            private_url,
            "-o",
            "/Users/private/Secret Note.md",
            "--polish-md",
            "--polish-md-model",
            "qwen-test:4b",
        ]
    )

    payload = json.dumps(context.to_dict())
    assert context.source_class == "web"
    assert context.runtime_for("polish") == "ollama"
    assert context.model_for("polish") == "qwen-test:4b"
    assert private_url not in payload
    assert "/Users/private" not in payload


def test_run_telemetry_records_completed_real_stage_units(tmp_path):
    from omd.eta_history import EtaHistoryStore
    from omd.run_telemetry import RunTelemetrySession, TelemetryContext
    from omd.stage_progress import StageProgress

    store = EtaHistoryStore(tmp_path / "eta-history.json")
    session = RunTelemetrySession(
        store,
        TelemetryContext(
            source_class="audio",
            device_tier="arm64-16gb-8core",
            runtimes={"transcribe": "mlx"},
            models={"transcribe": "whisper-test"},
        ),
    )

    update = session.consume(
        StageProgress(
            stage_id="transcribe",
            state="completed",
            unit="audio_seconds",
            completed=60,
            total=60,
            elapsed_seconds=12,
        ).to_event()
    )

    assert update.recorded_observation is True
    assert store.summary()["observation_count"] == 1
    assert update.percent == 100


def test_run_telemetry_records_passive_throughput_retry_and_queue_depth():
    from omd.run_telemetry import RunTelemetrySession, TelemetryContext
    from omd.stage_progress import StageProgress

    class FakeHistory:
        def __init__(self):
            self.records = []

        def record(self, observation):
            self.records.append(observation)
            return True

        def estimate(self, _query):
            from omd.eta_history import EtaEstimate

            return EtaEstimate(None, None, 0, "indeterminate", "indeterminate")

    history = FakeHistory()
    session = RunTelemetrySession(
        history,
        TelemetryContext(source_class="web", runtimes={"download": "network"}),
    )

    session.consume(
        StageProgress(
            stage_id="download",
            state="completed",
            unit="bytes",
            completed=60,
            total=60,
            elapsed_seconds=12,
            item_index=1,
            item_total=3,
            attempt=2,
        ).to_event()
    )

    observation = history.records[0]
    assert observation.attempt == 2
    assert observation.queue_depth == 2
    assert observation.throughput_per_second == 5.0


def test_run_telemetry_records_baseline_vs_shadow_sample_after_meaningful_work():
    from omd.eta_history import EtaEstimate
    from omd.run_telemetry import RunTelemetrySession, TelemetryContext
    from omd.stage_progress import StageProgress

    class FakeHistory:
        def record(self, _observation):
            return True

        def estimate(self, _query):
            return EtaEstimate(None, None, 30, "collecting", "uncalibrated")

        def shadow_estimate(self, _query):
            return EtaEstimate(45.0, 60.0, 30, "high", "exact")

    class FakeCalibrationStore:
        def __init__(self):
            self.samples = []

        def record(self, sample):
            self.samples.append(sample)
            return True

    calibration = FakeCalibrationStore()
    session = RunTelemetrySession(
        FakeHistory(),
        TelemetryContext(
            source_class="audio",
            device_tier="arm64-16gb-8core",
            runtimes={"transcribe": "mlx"},
            models={"transcribe": "whisper-test"},
        ),
        calibration_store=calibration,
    )

    session.consume(
        StageProgress(
            stage_id="transcribe",
            state="determinate",
            unit="audio_seconds",
            completed=20,
            total=100,
            elapsed_seconds=10,
        ).to_event()
    )
    session.consume(
        StageProgress(
            stage_id="transcribe",
            state="completed",
            unit="audio_seconds",
            completed=100,
            total=100,
            elapsed_seconds=50,
        ).to_event()
    )

    assert len(calibration.samples) == 1
    sample = calibration.samples[0]
    assert sample.actual_seconds == 50.0
    assert sample.baseline_seconds == 50.0
    assert sample.shadow_p50_seconds == 45.0
    assert sample.shadow_p90_seconds == 60.0
    assert sample.model == "whisper-test"


def test_run_telemetry_does_not_record_shadow_sample_without_mature_history():
    from omd.eta_history import EtaEstimate
    from omd.run_telemetry import RunTelemetrySession, TelemetryContext
    from omd.stage_progress import StageProgress

    class FakeHistory:
        def record(self, _observation):
            return True

        def estimate(self, _query):
            return EtaEstimate(None, None, 5, "collecting", "uncalibrated")

        def shadow_estimate(self, _query):
            return EtaEstimate(None, None, 5, "collecting", "uncalibrated")

    class FakeCalibrationStore:
        def __init__(self):
            self.samples = []

        def record(self, sample):
            self.samples.append(sample)
            return True

    calibration = FakeCalibrationStore()
    session = RunTelemetrySession(
        FakeHistory(),
        TelemetryContext(source_class="file"),
        calibration_store=calibration,
    )
    session.consume(
        StageProgress(
            stage_id="convert",
            state="determinate",
            unit="items",
            completed=1,
            total=2,
            elapsed_seconds=10,
        ).to_event()
    )
    session.consume(
        StageProgress(
            stage_id="convert",
            state="completed",
            unit="items",
            completed=2,
            total=2,
            elapsed_seconds=20,
        ).to_event()
    )

    assert calibration.samples == []


def test_run_telemetry_deduplicates_repeated_terminal_progress_events(tmp_path):
    from omd.eta_history import EtaHistoryStore
    from omd.run_telemetry import RunTelemetrySession, TelemetryContext
    from omd.stage_progress import StageProgress

    store = EtaHistoryStore(tmp_path / "eta-history.json")
    session = RunTelemetrySession(store, TelemetryContext(source_class="file"))
    event = StageProgress(
        stage_id="convert",
        state="determinate",
        unit="items",
        completed=1,
        total=1,
        elapsed_seconds=3,
    ).to_event()

    session.consume(event)
    session.consume(event)

    assert store.summary()["observation_count"] == 1


def test_run_telemetry_uses_truthful_indeterminate_label_without_history(tmp_path):
    from omd.eta_history import EtaHistoryStore
    from omd.run_telemetry import RunTelemetrySession, TelemetryContext
    from omd.stage_progress import StageProgress

    session = RunTelemetrySession(
        EtaHistoryStore(tmp_path / "eta-history.json"),
        TelemetryContext(source_class="audio"),
    )
    update = session.consume(
        StageProgress(
            stage_id="transcribe",
            state="indeterminate",
            unit="audio_seconds",
            total=120,
        ).to_event()
    )

    assert update.percent is None
    assert update.eta_label == "ETA: estimating after transcribing starts"


def test_run_telemetry_keeps_eta_indeterminate_while_history_is_uncalibrated(tmp_path):
    from omd.eta_history import EtaHistoryStore, EtaObservation
    from omd.run_telemetry import RunTelemetrySession, TelemetryContext
    from omd.stage_progress import StageProgress

    store = EtaHistoryStore(tmp_path / "eta-history.json")
    now = time.time()
    for index, duration in enumerate((20, 22, 24, 26, 30)):
        store.record(
            EtaObservation(
                stage_id="transcribe",
                source_class="audio",
                device_tier="arm64-16gb-8core",
                runtime="mlx",
                model_key="whisper-test",
                cold_start=None,
                unit="audio_seconds",
                work_units=120,
                duration_seconds=duration,
                outcome="success",
                observed_at=now + index,
            )
        )
    session = RunTelemetrySession(
        store,
        TelemetryContext(
            source_class="audio",
            device_tier="arm64-16gb-8core",
            runtimes={"transcribe": "mlx"},
            models={"transcribe": "whisper-test"},
        ),
    )

    update = session.consume(
        StageProgress(
            stage_id="transcribe",
            state="indeterminate",
            unit="audio_seconds",
            total=120,
        ).to_event()
    )

    assert update.eta_label == "ETA: collecting timings for transcribing"


def test_run_telemetry_marks_first_model_stage_cold_and_later_stage_warm():
    from omd.run_telemetry import RunTelemetrySession, TelemetryContext
    from omd.stage_progress import StageProgress

    class FakeHistory:
        def __init__(self) -> None:
            self.queries = []
            self.records = []

        def estimate(self, query):
            self.queries.append(query)
            from omd.eta_history import EtaEstimate

            return EtaEstimate(None, None, 1, "collecting", "uncalibrated")

        def record(self, observation):
            self.records.append(observation)
            return True

    history = FakeHistory()
    session = RunTelemetrySession(
        history,
        TelemetryContext(
            source_class="audio",
            device_tier="arm64-16gb-8core",
            runtimes={"transcribe": "mlx"},
            models={"transcribe": "whisper-test"},
        ),
    )

    session.consume(
        StageProgress(
            stage_id="transcribe",
            state="indeterminate",
            unit="audio_seconds",
            total=120,
            item_index=1,
            item_total=2,
        ).to_event()
    )
    session.consume(
        StageProgress(
            stage_id="transcribe",
            state="completed",
            unit="audio_seconds",
            completed=120,
            total=120,
            elapsed_seconds=12,
            item_index=1,
            item_total=2,
        ).to_event()
    )
    session.consume(
        StageProgress(
            stage_id="transcribe",
            state="indeterminate",
            unit="audio_seconds",
            total=120,
            item_index=2,
            item_total=2,
        ).to_event()
    )
    session.consume(
        StageProgress(
            stage_id="transcribe",
            state="completed",
            unit="audio_seconds",
            completed=120,
            total=120,
            elapsed_seconds=11,
            item_index=2,
            item_total=2,
        ).to_event()
    )

    assert history.queries[0].cold_start is True
    assert history.records[0].cold_start is True
    assert history.queries[1].cold_start is False
    assert history.records[1].cold_start is False


def test_run_telemetry_leaves_non_model_stage_cold_start_unset():
    from omd.run_telemetry import RunTelemetrySession, TelemetryContext
    from omd.stage_progress import StageProgress

    class FakeHistory:
        def __init__(self) -> None:
            self.queries = []
            self.records = []

        def estimate(self, query):
            self.queries.append(query)
            from omd.eta_history import EtaEstimate

            return EtaEstimate(None, None, 0, "indeterminate", "indeterminate")

        def record(self, observation):
            self.records.append(observation)
            return True

    history = FakeHistory()
    session = RunTelemetrySession(history, TelemetryContext(source_class="file"))

    session.consume(
        StageProgress(
            stage_id="convert",
            state="indeterminate",
            unit="items",
            total=1,
        ).to_event()
    )
    session.consume(
        StageProgress(
            stage_id="convert",
            state="completed",
            unit="items",
            completed=1,
            total=1,
            elapsed_seconds=3,
        ).to_event()
    )

    assert history.queries[0].cold_start is None
    assert history.records[0].cold_start is None


def test_structured_log_line_never_echoes_batch_source_or_output_path():
    from omd.run_telemetry import event_log_line

    source = "https://example.com/private?token=secret"
    output = "/Users/private/Secret Note.md"
    line = event_log_line(
        {
            "event": "batch_item_started",
            "item": source,
            "output": output,
            "index": 2,
            "total": 5,
        }
    )

    assert line == "→ Processing item 2 of 5"
    assert source not in line
    assert output not in line


def test_structured_log_line_tolerates_invalid_batch_counters():
    from omd.run_telemetry import event_log_line

    line = event_log_line(
        {
            "event": "batch_item_failed",
            "index": "not-an-index",
            "total": None,
        }
    )

    assert line == "warn: Item ? of ? failed"
