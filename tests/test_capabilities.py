import json

from omd.capabilities import capabilities_json, capabilities_payload


def test_capabilities_reports_enrich_note_v1_without_runtime_probe(monkeypatch):
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("network probe")),
    )

    assert capabilities_payload() == {
        "enrich_note": {"supported": True, "schema_versions": [1]}
    }
    assert json.loads(capabilities_json()) == capabilities_payload()
    assert capabilities_json().endswith("\n")
