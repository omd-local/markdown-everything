"""Generated tags must retain users' script, without accepting unsafe symbols."""
import pytest

from omd.tag_normalization import normalize_generated_tag, normalize_generated_tags


@pytest.mark.parametrize(('raw', 'expected'), [
    (' #بحث_دلالي ', 'بحث-دلالي'),
    ('#بَحْث/دَلَالِي', 'بَحْث/دَلَالِي'),
    ('한국어_검색', '한국어-검색'),
    ('CAFE\u0301/ÉTUDES', 'café/études'),
    ('语义检索_日本語', '语义检索-日本語'),
    ('हिन्दी_खोज', 'हिन्दी-खोज'),
    ('#ＭＬ/１２٣', 'ｍｌ/１２٣'),
    ('safe\u202eunsafe🧪tag', 'safe-unsafe-tag'),
    ('##topic___name / sub...tag', 'topic---name-/-sub-tag'),
])
def test_normalization_retains_unicode_letters_marks_and_numbers(raw, expected):
    assert normalize_generated_tag(raw) == expected


def test_canonically_equivalent_tags_deduplicate_without_losing_accents():
    assert normalize_generated_tags(['café', 'cafe\u0301', 'بحث', '한국어'], limit=3) == [
        'café', 'بحث', '한국어',
    ]
