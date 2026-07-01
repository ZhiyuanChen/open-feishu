"""Fluent ``Card``/``ColumnSet`` builder and end-to-end card assembly."""

import json

import pytest

import feishu.cards as cards
from feishu.cards import Card as CardExport
from feishu.cards.builder import Card, ColumnSet


class TestCardSkeleton:
    def test_empty_skeleton(self):
        assert Card().to_dict() == {"schema": "2.0", "body": {"elements": []}}

    def test_build_aliases_to_dict(self):
        c = Card().text("hi")
        assert c.build() == c.to_dict()

    def test_omits_header_and_config_when_unset(self):
        d = Card().markdown("x").to_dict()
        assert "header" not in d
        assert "config" not in d

    def test_json_serializable(self):
        json.dumps(Card().header("T").markdown("hi").to_dict())  # must not raise


class TestCardHeaderAndConfig:
    def test_header_emitted(self):
        d = Card().header("Title", subtitle="sub", template="green").to_dict()
        assert d["header"] == {
            "title": {"tag": "plain_text", "content": "Title"},
            "subtitle": {"tag": "plain_text", "content": "sub"},
            "template": "green",
        }

    def test_header_rejects_bad_template(self):
        with pytest.raises(ValueError):
            Card().header("T", template="rainbow")

    def test_config_emitted(self):
        d = Card().config(streaming_mode=True, width_mode="fill").to_dict()
        assert d["config"] == {"streaming_mode": True, "width_mode": "fill"}


class TestCardElements:
    def test_element_tags_and_options(self):
        els = (
            Card()
            .markdown("**bold**", text_align="center", element_id="md")
            .text("plain")
            .divider()
            .image("img_1", "alt")
            .button("Go", value={"x": 1})
            .to_dict()["body"]["elements"]
        )
        assert els[0] == {"tag": "markdown", "content": "**bold**", "text_align": "center", "element_id": "md"}
        assert els[1] == {"tag": "markdown", "content": "plain"}
        assert els[2] == {"tag": "hr"}
        assert els[3]["tag"] == "img"
        assert els[4]["behaviors"] == [{"type": "callback", "value": {"x": 1}}]

    def test_add_escape_hatch(self):
        els = Card().add({"tag": "custom_thing", "k": 1}).to_dict()["body"]["elements"]
        assert els == [{"tag": "custom_thing", "k": 1}]


class TestColumns:
    def test_columnset_object(self):
        cs = (
            ColumnSet(flex_mode="stretch")
            .column({"tag": "markdown", "content": "left"}, width="weighted", weight=1)
            .column({"tag": "markdown", "content": "right"}, weight=2)
        )
        col_set = Card().columns(cs).to_dict()["body"]["elements"][0]
        assert col_set["tag"] == "column_set"
        assert col_set["flex_mode"] == "stretch"
        assert col_set["columns"][0] == {
            "tag": "column",
            "width": "weighted",
            "weight": 1,
            "elements": [{"tag": "markdown", "content": "left"}],
        }
        assert col_set["columns"][1]["weight"] == 2

    def test_raw_dict_columns_with_flex_mode(self):
        col_a = {"tag": "column", "width": "auto", "elements": []}
        col_b = {"tag": "column", "width": "weighted", "weight": 2, "elements": []}
        col_set = Card().columns(col_a, col_b, flex_mode="stretch").to_dict()["body"]["elements"][0]
        assert col_set["tag"] == "column_set"
        assert col_set["flex_mode"] == "stretch"
        assert col_set["columns"] == [col_a, col_b]

    def test_two_columnsets_raises(self):
        with pytest.raises(TypeError, match="column_set"):
            Card().columns(ColumnSet(flex_mode="stretch"), ColumnSet(flex_mode="none"))

    def test_sub_builder_end_returns_parent(self):
        card = Card()
        returned = card.column_set().column({"tag": "markdown", "content": "a"}).end()
        assert returned is card
        assert card.to_dict()["body"]["elements"][0]["tag"] == "column_set"

    def test_end_without_parent_raises(self):
        with pytest.raises(ValueError):
            ColumnSet().end()


class TestPublicExports:
    @pytest.mark.parametrize(
        "name",
        [
            "Card",
            "ColumnSet",
            "escape_markdown",
            "text_card",
            "alert_card",
            "table_card",
            "parse_action",
            "CardAction",
            "markdown",
            "divider",
            "image",
            "button",
            "column_set",
        ],
    )
    def test_export_present(self, name):
        assert hasattr(cards, name)


class TestEndToEndAssembly:
    def test_representative_card(self):
        card = (
            CardExport()
            .header("Report", subtitle="daily", template="blue")
            .config(width_mode="fill")
            .markdown("**Summary**")
            .divider()
            .button("Confirm", value={"action": "confirm"})
            .button("Docs", url="https://example.com/docs", type="primary")
        )
        assert card.to_dict() == {
            "schema": "2.0",
            "config": {"width_mode": "fill"},
            "header": {
                "title": {"tag": "plain_text", "content": "Report"},
                "subtitle": {"tag": "plain_text", "content": "daily"},
                "template": "blue",
            },
            "body": {
                "elements": [
                    {"tag": "markdown", "content": "**Summary**"},
                    {"tag": "hr"},
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "Confirm"},
                        "type": "default",
                        "behaviors": [{"type": "callback", "value": {"action": "confirm"}}],
                    },
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "Docs"},
                        "type": "primary",
                        "behaviors": [{"type": "open_url", "default_url": "https://example.com/docs"}],
                    },
                ]
            },
        }
