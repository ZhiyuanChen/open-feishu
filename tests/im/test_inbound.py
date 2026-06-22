import json

import pytest

from feishu.im import is_mentioned, message_text


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
