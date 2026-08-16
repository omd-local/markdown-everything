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
