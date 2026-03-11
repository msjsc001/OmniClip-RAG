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
        self.assertEqual(text('zh-CN', 'left_tab_ui'), 'UI')
        self.assertEqual(text('zh-CN', 'page_sort_button'), '页面排序')
        self.assertEqual(text('en', 'page_sort_restore_button'), 'Restore Order')
        self.assertEqual(text('en', 'ui_theme_dark'), 'Dark mode')
        self.assertIn('查询', text('zh-CN', 'query_status_running_title', percent=52))
        self.assertIn('few seconds', text('en', 'task_eta_query'))
        self.assertIn('单字查询', tooltip('zh-CN', 'query'))
        self.assertIn('平均相关性', tooltip('zh-CN', 'page_sort'))
        self.assertIn('theme', tooltip('en', 'ui_theme').lower())
        self.assertIn('建议范围', text('zh-CN', 'query_limit_hint_ready', current=15, minimum=8, maximum=24, preferred=15, device='CPU', elapsed='520 毫秒', samples=3, reason='当前设置基本稳定'))
        self.assertIn('candidate pool', tooltip('en', 'limit'))


if __name__ == '__main__':
    unittest.main()
