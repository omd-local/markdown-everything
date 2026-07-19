from __future__ import annotations


def test_to_rmarkdown_adds_front_matter_from_heading():
    from omd._rmarkdown import to_rmarkdown

    out = to_rmarkdown("# Demo Title\n\nBody\n")

    assert out.startswith('---\ntitle: "Demo Title"\noutput: html_document\n---\n\n')
    assert out.endswith("# Demo Title\n\nBody\n")


def test_to_rmarkdown_does_not_duplicate_existing_front_matter():
    from omd._rmarkdown import to_rmarkdown

    body = "---\ntitle: Existing\n---\n\n# Body\n"

    assert to_rmarkdown(body) == body


def test_to_rmarkdown_adds_front_matter_when_body_starts_with_horizontal_rule():
    from omd._rmarkdown import to_rmarkdown

    body = "---\n\n# Body\n\nText\n"

    out = to_rmarkdown(body)

    assert out.startswith('---\ntitle: "Body"\noutput: html_document\n---\n\n')
    assert out.endswith(body.lstrip("\n"))


def test_to_rmarkdown_adds_front_matter_for_unclosed_yaml_marker():
    from omd._rmarkdown import to_rmarkdown

    body = "---\n# Body\n\nText\n"

    out = to_rmarkdown(body)

    assert out.startswith('---\ntitle: "Body"\noutput: html_document\n---\n\n')
    assert out.endswith(body.lstrip("\n"))


def test_convert_file_writes_rmarkdown(tmp_path):
    from omd._rmarkdown import convert_file

    path = tmp_path / "note.Rmd"
    path.write_text("# Note\n\nText\n", encoding="utf-8")

    convert_file(path)

    assert path.read_text(encoding="utf-8").startswith('---\ntitle: "Note"\noutput: html_document\n---')
