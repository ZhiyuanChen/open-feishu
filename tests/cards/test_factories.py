"""High-level card factories (``text_card``, ``alert_card``, ``table_card``)."""

import pytest

from feishu.cards.elements import button
from feishu.cards.factories import alert_card, table_card, text_card


class TestTextCard:
    def test_with_title(self):
        d = text_card("hello", title="Greeting", template="green")
        assert d["schema"] == "2.0"
        assert d["header"]["title"] == {"tag": "plain_text", "content": "Greeting"}
        assert d["header"]["template"] == "green"
        assert d["body"]["elements"] == [{"tag": "markdown", "content": "hello"}]


class TestAlertCard:
    def test_default_red_with_buttons(self):
        btns = [button("Approve", value={"d": "y"}), button("Reject", value={"d": "n"})]
        d = alert_card("Heads up", title="Alert", buttons=btns)
        assert d["header"]["template"] == "red"
        els = d["body"]["elements"]
        assert els[0] == {"tag": "markdown", "content": "Heads up"}
        assert els[1]["tag"] == "button" and els[1]["text"]["content"] == "Approve"
        assert els[2]["tag"] == "button" and els[2]["text"]["content"] == "Reject"

    def test_without_buttons(self):
        d = alert_card("msg", title="A")
        assert len(d["body"]["elements"]) == 1


class TestTableCard:
    def test_renders_gfm_table(self):
        d = table_card(["Name", "Age"], [["Alice", 30], ["Bob", 25]], title="People")
        assert d["header"]["title"]["content"] == "People"
        els = d["body"]["elements"]
        assert len(els) == 1 and els[0]["tag"] == "markdown"
        assert els[0]["content"] == ("| Name | Age |\n| --- | --- |\n| Alice | 30 |\n| Bob | 25 |")

    @pytest.mark.parametrize(
        "headers, rows, expected",
        [
            # pipes escaped so they cannot add phantom columns
            (["Col A|B", "Score"], [["foo|bar", 42]], "| Col A\\|B | Score |\n| --- | --- |\n| foo\\|bar | 42 |"),
            # newlines / carriage returns collapsed to a single space
            (
                ["Header"],
                [["line1\nline2"], ["cr\rlf\r\nboth"]],
                "| Header |\n| --- |\n| line1 line2 |\n| cr lf both |",
            ),
        ],
    )
    def test_sanitizes_cells(self, headers, rows, expected):
        assert table_card(headers, rows)["body"]["elements"][0]["content"] == expected

    def test_ragged_row_raises(self):
        with pytest.raises(ValueError, match=r"row 1 has 3 cells, expected 2"):
            table_card(["A", "B"], [["ok", "ok"], ["too", "many", "cells"]])


@pytest.mark.parametrize(
    "card, content",
    [
        (text_card("hello"), "hello"),
        (table_card(["A"], [["1"]]), "| A |\n| --- |\n| 1 |"),
    ],
)
def test_omits_header_without_title(card, content):
    assert "header" not in card
    assert card["body"]["elements"][0]["content"] == content
