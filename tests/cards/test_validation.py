"""Card field validation helpers (element ids, header templates, spacing clamps)."""

import pytest

from feishu.cards.validation import (
    HEADER_TEMPLATES,
    clamp_spacing,
    validate_element_id,
    validate_template,
)


class TestValidateElementId:
    @pytest.mark.parametrize("good", ["md", "a", "Title_1", "x" * 20])
    def test_element_id_valid(self, good):
        assert validate_element_id(good) == good

    @pytest.mark.parametrize(
        "bad",
        [
            "",  # empty
            "1abc",  # leading digit
            "_abc",  # leading underscore
            "a-b",  # hyphen not allowed
            "a b",  # space not allowed
            "a.b",  # dot not allowed
            "x" * 21,  # too long
        ],
    )
    def test_element_id_invalid(self, bad):
        with pytest.raises(ValueError):
            validate_element_id(bad)


class TestValidateTemplate:
    @pytest.mark.parametrize("template", list(HEADER_TEMPLATES))
    def test_accepts_known(self, template):
        assert validate_template(template) == template

    def test_rejects_unknown(self):
        with pytest.raises(ValueError):
            validate_template("rainbow")


class TestClampSpacing:
    @pytest.mark.parametrize(
        "value, expected",
        [(0, 0), (50, 50), (99, 99), (-99, -99), (100, 99), (-100, -99)],
    )
    def test_clamps_to_range(self, value, expected):
        assert clamp_spacing(value) == expected

    def test_rejects_non_int(self):
        with pytest.raises(TypeError):
            clamp_spacing("10")
