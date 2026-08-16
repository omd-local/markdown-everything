def test_process_peak_memory_normalises_linux_kib(monkeypatch):
    from omd import runtime_metrics

    monkeypatch.setattr(runtime_metrics.sys, "platform", "linux")
    monkeypatch.setattr(runtime_metrics, "_raw_peak_rss", lambda: 2048)

    assert runtime_metrics.process_peak_memory_bytes() == 2 * 1024 * 1024


def test_process_peak_memory_keeps_macos_bytes(monkeypatch):
    from omd import runtime_metrics

    monkeypatch.setattr(runtime_metrics.sys, "platform", "darwin")
    monkeypatch.setattr(runtime_metrics, "_raw_peak_rss", lambda: 2048)

    assert runtime_metrics.process_peak_memory_bytes() == 2048


def test_process_peak_memory_is_optional_when_unavailable(monkeypatch):
    from omd import runtime_metrics

    monkeypatch.setattr(runtime_metrics, "_raw_peak_rss", lambda: None)

    assert runtime_metrics.process_peak_memory_bytes() is None
