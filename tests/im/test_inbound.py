import json

import pytest

from feishu.im import card_text, card_title, interactive_card_text, is_mentioned, message_text, message_transcript


class TestMessageText:
    def test_resolves_mention_to_name(self):
        message = {
            "message_type": "text",
            "content": json.dumps({"text": "@_user_1 你好"}),
            "mentions": [{"key": "@_user_1", "name": "小明"}],
        }
        assert message_text(message) == "@小明 你好"

    def test_keeps_placeholder_without_mentions(self):
        message = {"message_type": "text", "content": json.dumps({"text": "@_user_1 hi"})}
        assert message_text(message) == "@_user_1 hi"

    def test_post_prepends_title_heading(self):
        message = {
            "message_type": "post",
            "content": json.dumps(
                {"title": "标题", "content": [[{"tag": "text", "text": "第一行"}], [{"tag": "text", "text": "第二行"}]]}
            ),
        }
        assert message_text(message) == "## 标题\n\n第一行\n\n第二行"

    def test_post_without_title_joins_segments(self):
        message = {
            "message_type": "post",
            "content": json.dumps({"content": [[{"tag": "text", "text": "only"}]]}),
        }
        assert message_text(message) == "only"

    def test_empty_content(self):
        assert message_text({"message_type": "text", "content": ""}) == ""


class TestMessageTranscript:
    def test_renders_sender_prefixed_lines(self):
        messages = [
            {
                "sender": {"name": "Alice"},
                "message_type": "text",
                "content": json.dumps({"text": "hello"}),
            },
            {"sender": {"open_id": "ou_1"}, "message_type": "image", "content": "{}"},
        ]

        assert message_transcript(messages, id_formatter=lambda value: f"redacted:{value}") == (
            "Alice: hello\nredacted:ou_1: [image]"
        )


class TestCardText:
    def test_extracts_title_and_nested_text(self):
        card = {
            "header": {"title": {"content": "Status"}},
            "body": {
                "elements": [
                    {"tag": "markdown", "content": "**Done**"},
                    {"tag": "div", "text": {"content": "Details"}},
                    {"tag": "column_set", "columns": [{"elements": [{"tag": "markdown", "content": "Nested"}]}]},
                ]
            },
        }

        assert card_title(card) == "Status"
        assert card_text(card) == "**Done**\n\nDetails\n\nNested"

    def test_interactive_card_text_parses_message_content(self):
        card = {"elements": [{"tag": "markdown", "content": "Card body"}]}
        message = {"message_type": "interactive", "content": json.dumps(card)}

        assert interactive_card_text(message) == "Card body"


class TestIsMentioned:
    @pytest.fixture
    def message(self):
        return {
            "mentions": [
                {"key": "@_user_1", "id": {"open_id": "ou_bot", "union_id": "on_bot"}, "name": "Bot"},
            ]
        }

    @pytest.mark.parametrize("kwargs", [{"open_id": "ou_bot"}, {"union_id": "on_bot"}])
    def test_matches_id(self, message, kwargs):
        assert is_mentioned(message, **kwargs) is True

    def test_no_match(self, message):
        assert is_mentioned(message, open_id="ou_other") is False

    def test_no_mentions(self):
        assert is_mentioned({"mentions": []}, open_id="ou_bot") is False
