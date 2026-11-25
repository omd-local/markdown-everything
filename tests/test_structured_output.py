from __future__ import annotations

import pytest


def _schema():
    return {
        "type": "object",
        "properties": {
            "summary": {"type": "string"},
            "tags": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["summary", "tags"],
        "additionalProperties": False,
    }


def test_output_schema_keeps_a_defensive_copy():
    from omd.structured_output import AIOutputSchema

    source = _schema()
    contract = AIOutputSchema(name="note_result", schema=source)
    source["properties"]["summary"]["type"] = "number"

    assert contract.schema["properties"]["summary"]["type"] == "string"


def test_output_schema_rejects_unsupported_schema_keywords():
    from omd.structured_output import AIOutputSchema

    schema = _schema()
    schema["properties"]["summary"]["pattern"] = "private.*"

    with pytest.raises(ValueError, match="unsupported"):
        AIOutputSchema(name="note_result", schema=schema)


def test_parse_structured_output_accepts_matching_json():
    from omd.structured_output import AIOutputSchema, parse_structured_output

    parsed = parse_structured_output(
        '{"summary":"Useful note","tags":["reference"]}',
        AIOutputSchema(name="note_result", schema=_schema()),
    )

    assert parsed == {"summary": "Useful note", "tags": ["reference"]}


def test_parse_structured_output_rejects_missing_required_property():
    from omd.structured_output import (
        AIOutputSchema,
        StructuredOutputError,
        parse_structured_output,
    )

    with pytest.raises(StructuredOutputError, match="required"):
        parse_structured_output(
            '{"summary":"Useful note"}',
            AIOutputSchema(name="note_result", schema=_schema()),
        )


def test_parse_structured_output_rejects_additional_property():
    from omd.structured_output import (
        AIOutputSchema,
        StructuredOutputError,
        parse_structured_output,
    )

    with pytest.raises(StructuredOutputError, match="additional"):
        parse_structured_output(
            '{"summary":"Useful note","tags":[],"source":"private"}',
            AIOutputSchema(name="note_result", schema=_schema()),
        )


def test_parse_structured_output_rejects_wrong_nested_type():
    from omd.structured_output import (
        AIOutputSchema,
        StructuredOutputError,
        parse_structured_output,
    )

    with pytest.raises(StructuredOutputError, match="string"):
        parse_structured_output(
            '{"summary":"Useful note","tags":[3]}',
            AIOutputSchema(name="note_result", schema=_schema()),
        )


def test_parse_structured_output_enforces_one_required_array_item():
    from omd.structured_output import (
        AIOutputSchema,
        StructuredOutputError,
        parse_structured_output,
    )

    schema = _schema()
    schema["properties"]["tags"]["minItems"] = 1
    contract = AIOutputSchema(name="note_result", schema=schema)

    assert parse_structured_output(
        '{"summary":"Useful note","tags":["reference"]}',
        contract,
    )["tags"] == ["reference"]
    with pytest.raises(StructuredOutputError, match="at least 1 item"):
        parse_structured_output(
            '{"summary":"Useful note","tags":[]}',
            contract,
        )


@pytest.mark.parametrize("value", [True, -1, 2])
def test_output_schema_rejects_unsupported_min_items(value):
    from omd.structured_output import AIOutputSchema

    schema = _schema()
    schema["properties"]["tags"]["minItems"] = value

    with pytest.raises(ValueError, match="minItems"):
        AIOutputSchema(name="note_result", schema=schema)


def test_output_schema_rejects_min_items_on_non_array():
    from omd.structured_output import AIOutputSchema

    schema = _schema()
    schema["minItems"] = 1

    with pytest.raises(ValueError, match="only supported for arrays"):
        AIOutputSchema(name="note_result", schema=schema)


def test_grounded_answer_contract_requires_an_allowed_citation():
    from omd.structured_output import (
        AIOutputSchema,
        StructuredOutputError,
        parse_structured_output,
    )

    claim = {
        "type": "object",
        "properties": {
            "claim": {"type": "string"},
            "citations": {
                "type": "array",
                "items": {"type": "string", "enum": ["S1", "S2"]},
                "minItems": 1,
            },
        },
        "required": ["claim", "citations"],
        "additionalProperties": False,
    }
    contract = AIOutputSchema(
        name="grounded_answer",
        schema={
            "type": "object",
            "properties": {
                "source_states": {"type": "array", "items": claim, "minItems": 1},
                "model_inference": {"type": "array", "items": claim},
            },
            "required": ["source_states", "model_inference"],
            "additionalProperties": False,
        },
    )

    valid = '{"source_states":[{"claim":"Blue key on Tuesday.","citations":["S1"]}],"model_inference":[]}'
    assert parse_structured_output(valid, contract)["source_states"][0]["citations"] == ["S1"]
    for invalid in (
        '{"source_states":[{"claim":"Blue key on Tuesday.","citations":[]}],"model_inference":[]}',
        '{"source_states":[{"claim":"Blue key on Tuesday.","citations":["S99"]}],"model_inference":[]}',
    ):
        with pytest.raises(StructuredOutputError):
            parse_structured_output(invalid, contract)
