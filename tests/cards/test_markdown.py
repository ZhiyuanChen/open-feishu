"""``escape_markdown`` control-character escaping for Card 2.0 markdown."""

import pytest

from feishu.cards.markdown import escape_markdown


class TestEscapeMarkdown:
    @pytest.mark.parametrize(
        ("raw", "escaped"),
        [
            ("*", "&#42;"),
            ("`", "&#96;"),
            ("<", "&#60;"),
            ("[", "&#91;"),
            ("#", "&#35;"),
            ("\\", "&#92;"),
            ("~", "&sim;"),  # the one named (non-numeric) entity
            ("|", "&#124;"),
            ("a*b_c", "a&#42;b&#95;c"),  # mixed string
            ("hello world 123", "hello world 123"),  # plain text untouched
            ("", ""),  # empty
            ("C#", "C&#35;"),  # entity text not re-escaped, only raw '#'
        ],
    )
    def test_escapes(self, raw, escaped):
        assert escape_markdown(raw) == escaped

    def test_output_contains_no_raw_control_chars(self):
        # Every markdown control character must be replaced so the text cannot break
        # Card 2.0 markdown rendering. ('#' is excluded because numeric HTML entities
        # like '&#42;' legitimately reintroduce it in the escaped output.)
        controls = "*_`<>[]()\\~!+-.|"
        escaped = escape_markdown(f"start {controls} end")
        assert not any(ch in escaped for ch in controls)
