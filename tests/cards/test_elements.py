"""Low-level element constructors (``md``, ``hr``, ``img``, ``button``, ``column_set``)."""

import pytest

from feishu.cards.elements import button, column_set, hr, img, md


class TestMarkdownElement:
    def test_basic(self):
        # tag is "markdown" (not the legacy "div") — that is the wire contract.
        assert md("hello") == {"tag": "markdown", "content": "hello"}

    def test_optional_fields(self):
        el = md("h", text_align="center", text_size="heading", element_id="md")
        assert el["text_align"] == "center"
        assert el["text_size"] == "heading"
        assert el["element_id"] == "md"

    def test_escape(self):
        assert md("a*b", escape=True)["content"] == "a&#42;b"

    def test_invalid_element_id_raises(self):
        with pytest.raises(ValueError):
            md("h", element_id="1bad")


class TestDividerElement:
    def test_hr(self):
        assert hr() == {"tag": "hr"}


class TestImageElement:
    def test_img(self):
        el = img("img_v2_abc", "a cat", scale_type="crop_center")
        assert el["tag"] == "img"
        assert el["img_key"] == "img_v2_abc"
        assert el["alt"] == {"tag": "plain_text", "content": "a cat"}
        assert el["scale_type"] == "crop_center"


class TestButtonElement:
    @pytest.mark.parametrize(
        "kwargs, behaviors",
        [
            ({"value": {"k": "v"}}, [{"type": "callback", "value": {"k": "v"}}]),
            ({"url": "https://x.com"}, [{"type": "open_url", "default_url": "https://x.com"}]),
            (
                {"value": {"a": 1}, "url": "https://x.com"},
                [
                    {"type": "callback", "value": {"a": 1}},
                    {"type": "open_url", "default_url": "https://x.com"},
                ],
            ),
        ],
    )
    def test_behaviors(self, kwargs, behaviors):
        el = button("Click", **kwargs)
        assert el["tag"] == "button"
        assert el["text"] == {"tag": "plain_text", "content": "Click"}
        assert el["type"] == "default"
        assert el["behaviors"] == behaviors

    def test_type_and_extras(self):
        el = button("Del", value={}, type="primary", confirm={"title": "Sure?"}, icon={"tag": "standard_icon"})
        assert el["type"] == "primary"
        assert el["confirm"] == {"title": "Sure?"}
        assert el["icon"] == {"tag": "standard_icon"}


class TestColumnSetElement:
    def test_column_set(self):
        cols = [{"tag": "column", "elements": [md("a")]}]
        cs = column_set(cols, flex_mode="stretch", horizontal_spacing=12)
        assert cs["tag"] == "column_set"
        assert cs["flex_mode"] == "stretch"
        assert cs["horizontal_spacing"] == 12
        assert cs["columns"] == cols

    def test_clamps_int_spacing(self):
        assert column_set([], horizontal_spacing=200)["horizontal_spacing"] == 99
