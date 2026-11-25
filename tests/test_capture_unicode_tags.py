"""Capture tags use the shared Unicode alphabet with their existing flat shape."""
import pytest

from omd.capture import _normalize_tag


@pytest.mark.parametrize(('raw', 'expected'), [
    ('بحث/دلالي', 'بحث-دلالي'),
    ('한국어_검색', '한국어-검색'),
    ('CAFE\u0301', 'café'),
    ('#بَحْث', 'بَحْث'),
    ('语义检索/日本語', '语义检索-日本語'),
    ('a///b', 'a-b'),
    ('a/?!b', 'a-b'),
    ('one___two', 'one---two'),
    ('safe\u202eunsafe', 'safe-unsafe'),
])
def test_capture_tags_preserve_unicode_and_flat_separator_behavior(raw, expected):
    assert _normalize_tag(raw) == expected
