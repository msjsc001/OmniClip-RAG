from pathlib import Path
import shutil
import time
import unittest
from unittest.mock import patch

from omniclip_rag.gui import OmniClipDesktopApp
from omniclip_rag.models import SearchHit
from omniclip_rag.vector_index import get_local_model_dir


ROOT = Path(__file__).resolve().parents[1]
SAMPLE_ROOT = ROOT / 'logseq笔记样本'
TEST_DATA_ROOT = ROOT / '.tmp' / 'test_gui_data'


class GuiTests(unittest.TestCase):
    def tearDown(self) -> None:
        if TEST_DATA_ROOT.exists():
            shutil.rmtree(TEST_DATA_ROOT)

    def _build_app(self) -> OmniClipDesktopApp:
        TEST_DATA_ROOT.mkdir(parents=True, exist_ok=True)
        with patch('omniclip_rag.gui.ensure_data_paths', side_effect=lambda custom_root=None, vault_path=None: __import__('omniclip_rag.config', fromlist=['ensure_data_paths']).ensure_data_paths(str(TEST_DATA_ROOT.resolve()), vault_path)):
            app = OmniClipDesktopApp()
        app.data_dir_var.set(str(TEST_DATA_ROOT.resolve()))
        app.backend_var.set('lancedb')
        app.vault_var.set(str(SAMPLE_ROOT.resolve()))
        return app

    def test_start_task_skips_model_prompt_when_model_ready(self) -> None:
        app = self._build_app()
        try:
            with patch('omniclip_rag.gui.is_local_model_ready', return_value=True), \
                 patch.object(app, '_prepare_model_for_followup') as prepare_mock, \
                 patch.object(app, '_run_task') as run_task_mock:
                app._start_task('search_button', lambda service: None, lambda payload: None, ensure_model=True)
            prepare_mock.assert_not_called()
            run_task_mock.assert_called_once()
        finally:
            app._on_close()

    def test_start_task_uses_model_prompt_when_model_missing(self) -> None:
        app = self._build_app()
        try:
            with patch('omniclip_rag.gui.is_local_model_ready', return_value=False), \
                 patch.object(app, '_prepare_model_for_followup', return_value=False) as prepare_mock, \
                 patch.object(app, '_run_task') as run_task_mock:
                app._start_task('search_button', lambda service: None, lambda payload: None, ensure_model=True)
            prepare_mock.assert_called_once()
            run_task_mock.assert_not_called()
        finally:
            app._on_close()

    def test_choose_model_download_mode_manual_creates_target_folder(self) -> None:
        app = self._build_app()
        try:
            config, paths = app._config(True)
            with patch('omniclip_rag.gui.messagebox.askyesnocancel', return_value=False), \
                 patch('omniclip_rag.gui.messagebox.showinfo') as showinfo_mock:
                result = app._choose_model_download_mode('下载模型', config, paths)
            self.assertEqual(result, 'manual')
            self.assertTrue(get_local_model_dir(config, paths).exists())
            self.assertTrue(showinfo_mock.called)
        finally:
            app._on_close()

    def test_config_carries_ui_layout_state(self) -> None:
        app = self._build_app()
        try:
            app.root.update()
            app.right_pane.sashpos(0, 170)
            app.results_pane.sashpos(0, 210)
            app._capture_layout_state()
            config, _paths = app._config(True)
            self.assertGreater(config.ui_main_sash, 0)
            self.assertGreater(config.ui_right_sash, 0)
            self.assertGreaterEqual(config.ui_results_sash, 0)
            self.assertTrue(config.ui_window_geometry)
        finally:
            app._on_close()


    def test_toggle_all_hits_updates_context_selection(self) -> None:
        app = self._build_app()
        try:
            app.current_query_text = 'test query'
            app.current_hits = [
                SearchHit(score=80.0, title='Page A', anchor='A', source_path='pages/a.md', rendered_text='Alpha body', chunk_id='a', preview_text='Alpha', reason='正文命中'),
                SearchHit(score=70.0, title='Page B', anchor='B', source_path='pages/b.md', rendered_text='Beta body', chunk_id='b', preview_text='Beta', reason='正文命中'),
            ]
            app.selected_chunk_ids = set()
            app._render_hits()
            app._rebuild_context_view()
            app._toggle_all_hit_selection()
            self.assertEqual(app.context_selection_var.get(), app._tr('context_selection_summary', selected=2, total=2))
            self.assertEqual(app.context_toggle_var.get(), app._tr('context_clear_all'))
            app._toggle_all_hit_selection()
            self.assertEqual(app.context_selection_var.get(), app._tr('context_selection_summary', selected=0, total=2))
            self.assertEqual(app.context_toggle_var.get(), app._tr('context_select_all'))
        finally:
            app._on_close()

    def test_config_includes_page_blocklist_rules(self) -> None:
        app = self._build_app()
        try:
            app.page_blocklist_rules_var.set('1\t^日志$\n0\t^草稿$')
            app._update_page_blocklist_summary()
            config, _paths = app._config(True)
            self.assertEqual(config.page_blocklist_rules, '1\t^日志$\n0\t^草稿$')
            self.assertIn('1/2', app.page_blocklist_summary_var.get())
        finally:
            app._on_close()

    def test_defaults_make_quick_start_and_advanced_visible(self) -> None:
        app = self._build_app()
        try:
            self.assertEqual(app.limit_var.get(), '15')
            self.assertTrue(app.quick_start_expanded_var.get())
            self.assertTrue(app.show_advanced_var.get())
            self.assertIn('1\t^2026-.*\\.android$', app.page_blocklist_rules_var.get())
            self.assertIn('^.*\\.sync-conflict-\\d{8}-\\d{6}-[A-Z0-9]+$', app.page_blocklist_rules_var.get())
        finally:
            app._on_close()

    def test_apply_recommended_keeps_auto_device(self) -> None:
        app = self._build_app()
        try:
            app.device_var.set('cpu')
            app._apply_recommended()
            self.assertEqual(app.device_var.get(), 'auto')
        finally:
            app._on_close()

    def test_rebuild_prompts_before_rebuilding_existing_index(self) -> None:
        app = self._build_app()
        try:
            app.chunks_var.set('12')
            with patch('omniclip_rag.gui.OmniClipService') as service_cls, \
                 patch('omniclip_rag.gui.messagebox.askyesno', return_value=False) as ask_mock, \
                 patch.object(app, '_start_rebuild') as start_mock:
                service_cls.return_value.pending_rebuild.return_value = None
                app._rebuild()
            self.assertTrue(ask_mock.called)
            start_mock.assert_not_called()
        finally:
            app._on_close()

    def test_main_tabs_present(self) -> None:
        app = self._build_app()
        try:
            tabs = app.main_tabs.tabs()
            self.assertEqual(app.main_tabs.tab(tabs[0], 'text'), app._tr('main_tab_query'))
            self.assertEqual(app.main_tabs.tab(tabs[1], 'text'), app._tr('main_tab_config'))
        finally:
            app._on_close()

    def test_context_jump_summary_counts_pages_and_fragments(self) -> None:
        app = self._build_app()
        try:
            app.current_context = '# RAG结果\n\n# 笔记名：手机笔记\n笔记片段1：\n- A\n\n笔记片段2：\n- B\n\n---\n\n# 笔记名：鞋子记录\n笔记片段1：\n- C\n'
            app._refresh_context_jump_controls()
            self.assertIn('2', app.context_jump_summary_var.get())
            values = app.context_jump_combo.cget('values')
            self.assertTrue(any('手机笔记' in value for value in values))
        finally:
            app._on_close()

    def test_text_panel_search_moves_between_matches(self) -> None:
        app = self._build_app()
        try:
            app._set_text(app.log_text, 'Alpha Beta Alpha')
            state = app.text_search_state['log']
            state['query_var'].set('Alpha')
            app._find_in_text_panel('log')
            self.assertEqual(state['status_var'].get(), '1/2')
            app._find_in_text_panel('log', advance=True)
            self.assertEqual(state['status_var'].get(), '2/2')
        finally:
            app._on_close()

    def test_sort_hits_by_title_and_score(self) -> None:
        app = self._build_app()
        try:
            app.current_query_text = 'test query'
            app.current_hits = [
                SearchHit(score=50.0, title='手机笔记', anchor='B', source_path='pages/b.md', rendered_text='Beta body', chunk_id='b', preview_text='Beta', reason='语义相似'),
                SearchHit(score=90.0, title='Alpha', anchor='A', source_path='pages/a.md', rendered_text='Alpha body', chunk_id='a', preview_text='Alpha', reason='正文命中'),
            ]
            app.selected_chunk_ids = {'a', 'b'}
            app._render_hits()
            app._sort_hits_by('title')
            self.assertEqual(app.current_hits[0].title, 'Alpha')
            app._sort_hits_by('score')
            self.assertEqual(app.current_hits[0].score, 90.0)
        finally:
            app._on_close()

    def test_pause_rebuild_button_toggles_event_and_label(self) -> None:
        app = self._build_app()
        try:
            config, paths = app._config(True)
            app.busy = True
            app._start_task_feedback('rebuild_button', config, paths)
            app.root.update_idletasks()
            self.assertTrue(app.rebuild_pause_button.winfo_ismapped())
            self.assertEqual(app.rebuild_pause_var.get(), app._tr('pause_rebuild'))

            app._toggle_rebuild_pause()
            self.assertTrue(app.rebuild_pause_event.is_set())
            self.assertEqual(app.rebuild_pause_var.get(), app._tr('resume_rebuild_button'))
            self.assertIn(app._tr('status_rebuild_paused'), app.status_var.get())

            app._toggle_rebuild_pause()
            self.assertFalse(app.rebuild_pause_event.is_set())
            self.assertEqual(app.rebuild_pause_var.get(), app._tr('pause_rebuild'))
        finally:
            app._on_close()

    def test_update_task_progress_sets_detail_and_progressbar(self) -> None:
        app = self._build_app()
        try:
            app.busy = True
            app.task_started_at = 1.0
            app._update_task_progress({'stage': 'indexing', 'current': 2, 'total': 4, 'current_path': 'pages/a.md'})
            self.assertIn('2/4', app.task_detail_var.get())
            self.assertEqual(float(app.task_progress.cget('value')), 2.0)
        finally:
            app._on_close()


    def test_paused_rebuild_freezes_elapsed_display(self) -> None:
        app = self._build_app()
        try:
            config, paths = app._config(True)
            app.busy = True
            app._start_task_feedback('rebuild_button', config, paths)
            app.task_started_at = time.time() - 20
            app._tick_task_feedback()
            before = app.task_elapsed_var.get()
            app._toggle_rebuild_pause()
            time.sleep(1.1)
            app._tick_task_feedback()
            after = app.task_elapsed_var.get()
            self.assertEqual(before, after)
        finally:
            app._on_close()

    def test_device_summary_explains_missing_runtime_even_with_nvcc(self) -> None:
        app = self._build_app()
        try:
            with patch('omniclip_rag.gui.detect_acceleration', return_value={
                'gpu_present': True,
                'gpu_name': 'NVIDIA GeForce RTX 3060',
                'cuda_name': '',
                'cuda_available': False,
                'torch_available': False,
                'sentence_transformers_available': False,
                'nvcc_available': True,
                'nvcc_version': '12.3',
            }):
                summary = app._device_capability_summary()
            self.assertIn('InstallRuntime.ps1', summary)
            self.assertIn('12.3', summary)
        finally:
            app._on_close()

    def test_runtime_error_uses_friendly_message_in_log_and_dialog(self) -> None:
        app = self._build_app()
        try:
            app.queue.put(('runtime-error', 'rebuild_button', '全量建库', '当前还不能开始本地语义建库或向量查询。'))
            with patch('omniclip_rag.gui.messagebox.showerror') as showerror_mock:
                app._drain_queue()
            self.assertIn('当前还不能开始本地语义建库或向量查询。', app.log_lines[-1])
            self.assertEqual(showerror_mock.call_args.args[1], '当前还不能开始本地语义建库或向量查询。')
        finally:
            app._on_close()

    def test_toggle_hit_selection_updates_context_summary(self) -> None:
        app = self._build_app()
        try:
            app.current_query_text = 'test query'
            app.current_hits = [
                SearchHit(score=80.0, title='Page A', anchor='A', source_path='pages/a.md', rendered_text='Alpha body', chunk_id='a', preview_text='Alpha', reason='正文命中'),
                SearchHit(score=70.0, title='Page B', anchor='B', source_path='pages/b.md', rendered_text='Beta body', chunk_id='b', preview_text='Beta', reason='正文命中'),
            ]
            app.selected_chunk_ids = {'a', 'b'}
            app._render_hits()
            app._rebuild_context_view()
            app._toggle_hit_selection(1)
            self.assertEqual(app.context_selection_var.get(), app._tr('context_selection_summary', selected=1, total=2))
            self.assertIn('Page A', app.current_context)
            self.assertNotIn('Page B', app.current_context)
        finally:
            app._on_close()


if __name__ == '__main__':
    unittest.main()
