import unittest

from omniclip_rag.config import AppConfig
from omniclip_rag.ui_i18n import language_code_from_label, language_label, normalize_language, text, tooltip


class UiI18nTests(unittest.TestCase):
    def test_language_label_roundtrip(self) -> None:
        self.assertEqual(language_code_from_label(language_label('zh-CN')), 'zh-CN')
        self.assertEqual(language_code_from_label(language_label('en')), 'en')

    def test_normalize_language_accepts_common_values(self) -> None:
        self.assertEqual(normalize_language('zh'), 'zh-CN')
        self.assertEqual(normalize_language('English'), 'en')

    def test_text_catalog_returns_translated_strings(self) -> None:
        self.assertIn('OmniClip', text('en', 'title'))
        self.assertIn('方寸引', text('zh-CN', 'title'))

    def test_app_config_has_ui_language_default(self) -> None:
        config = AppConfig(vault_path='.', data_root='.')
        self.assertIn(config.ui_language, {'zh-CN', 'en'})

    def test_page_filter_texts_exist(self) -> None:
        self.assertEqual(text('zh-CN', 'page_blocklist_button'), '过滤页面标题')
        self.assertEqual(text('en', 'page_blocklist_button'), 'Filter page titles')
        self.assertEqual(text('zh-CN', 'help_updates'), '帮助与更新')
        self.assertIn('{enabled}/{total}', text('zh-CN', 'page_blocklist_summary'))
        self.assertEqual(text('zh-CN', 'main_tab_query'), '查询')
        self.assertEqual(text('en', 'main_tab_config'), 'Config')
        self.assertIn('单字查询', tooltip('zh-CN', 'query'))


if __name__ == '__main__':
    unittest.main()
