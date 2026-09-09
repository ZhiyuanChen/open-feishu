from feishu.directory import custom_field_text, custom_fields_by_key


class TestCustomFieldText:
    def test_plain_string(self):
        assert custom_field_text("  alice  ") == "alice"

    def test_default_value_wins(self):
        assert custom_field_text({"default_value": "alice", "i18n_value": {"en_us": "bob"}}) == "alice"

    def test_i18n_locale_order(self):
        assert custom_field_text({"i18n_value": {"zh_cn": "张三", "en_us": "alice"}}) == "alice"
        assert custom_field_text({"i18n_value": {"zh_cn": "张三"}}) == "张三"

    def test_empty_and_unknown(self):
        assert custom_field_text(None) == ""
        assert custom_field_text(1) == ""
        assert custom_field_text({}) == ""


class TestCustomFieldsByKey:
    def test_last_value_wins(self):
        values = custom_fields_by_key(
            {
                "custom_field_values": [
                    {"field_key": "C-alias", "text_value": "first"},
                    {"field_key": "C-alias", "text_value": {"default_value": "second"}},
                    {"field_key": "C-name", "text_value": "bob"},
                    {"field_key": "", "text_value": "ignored"},
                ]
            }
        )
        assert values == {"C-alias": "second", "C-name": "bob"}
