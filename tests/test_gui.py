from pathlib import Path
import tkinter as tk
import shutil
import time
import unittest
from unittest.mock import patch

from omniclip_rag.gui import OmniClipDesktopApp
from omniclip_rag.models import SearchHit
from omniclip_rag.reranker import get_local_reranker_dir
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
        with patch('omniclip_rag.gui.ensure_data_paths', side_effect=lambda custom_root=None, vault_path=None: __import__('omniclip_rag.config', fromlist=['ensure_data_paths']).ensure_data_paths(str(TEST_DATA_ROOT.resolve()), vault_path)), \
             patch.object(OmniClipDesktopApp, '_load_initial_status', autospec=True, return_value=None):
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

    def test_on_close_ignores_destroyed_task_progress(self) -> None:
        app = self._build_app()
        try:
            app.task_progress.destroy()
            app._on_close()
        finally:
            try:
                if app.root.winfo_exists():
                    app.root.destroy()
            except tk.TclError:
                pass

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

    def test_choose_reranker_download_mode_manual_creates_target_folder(self) -> None:
        app = self._build_app()
        try:
            config, paths = app._config(True)
            with patch('omniclip_rag.gui.messagebox.askyesnocancel', return_value=False), \
                 patch('omniclip_rag.gui.messagebox.showinfo') as showinfo_mock:
                result = app._choose_reranker_download_mode('下载重排模型', config, paths)
            self.assertEqual(result, 'manual')
            self.assertTrue(get_local_reranker_dir(config, paths).exists())
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

    def test_config_carries_reranker_and_export_mode(self) -> None:
        app = self._build_app()
        try:
            app.reranker_enabled_var.set(True)
            app.reranker_model_var.set('BAAI/bge-reranker-v2-m3')
            app.reranker_batch_cpu_var.set('3')
            app.reranker_batch_cuda_var.set('6')
            app.context_export_ai_collab_var.set(True)
            app.build_resource_profile_var.set(app._build_profile_label('peak'))
            config, _paths = app._config(True)
            self.assertTrue(config.reranker_enabled)
            self.assertEqual(config.reranker_batch_size_cpu, 3)
            self.assertEqual(config.reranker_batch_size_cuda, 6)
            self.assertEqual(config.context_export_mode, 'ai-collab')
            self.assertEqual(config.build_resource_profile, 'peak')
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
            self.assertEqual(app.score_threshold_var.get(), '35')
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
            self.assertEqual(app.score_threshold_var.get(), '35')
        finally:
            app._on_close()


    def test_config_persists_watch_resource_peak(self) -> None:
        app = self._build_app()
        try:
            app.watch_resource_peak_var.set(app._watch_peak_label(60))
            config, _paths = app._config(True)
            self.assertEqual(config.watch_resource_peak_percent, 60)
        finally:
            app._on_close()

    def test_rebuild_prompts_before_rebuilding_existing_index(self) -> None:
        app = self._build_app()
        try:
            app.status_snapshot = {'index_state': 'ready', 'stats': {'files': 1, 'chunks': 12, 'refs': 0}}
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

    def test_bootstrap_reranker_runs_even_when_disabled(self) -> None:
        app = self._build_app()
        try:
            app.reranker_enabled_var.set(False)
            with patch('omniclip_rag.gui.is_local_reranker_ready', return_value=False), \
                 patch.object(app, '_choose_reranker_download_mode', return_value='auto') as choose_mock, \
                 patch.object(app, '_run_task') as run_task_mock:
                app._bootstrap_reranker()
            choose_mock.assert_called_once()
            run_task_mock.assert_called_once()
        finally:
            app._on_close()

    def test_apply_status_updates_query_limit_guidance(self) -> None:
        app = self._build_app()
        try:
            app._apply_status({
                'stats': {'files': 1, 'chunks': 2, 'refs': 0},
                'query_limit_recommendation': {
                    'device': 'cpu',
                    'preferred': 15,
                    'minimum': 8,
                    'maximum': 24,
                    'reason_code': 'steady',
                    'samples': 3,
                    'elapsed_ms': 520,
                },
            })
            self.assertIn('8-24', app.query_limit_hint_var.get())
            self.assertIn('15', app.query_limit_hint_var.get())
            self.assertIsNotNone(app.limit_entry_tooltip)
            self.assertIn('8-24', app.limit_entry_tooltip.text)
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

    def test_page_sort_groups_hits_by_page_average_and_restores(self) -> None:
        app = self._build_app()
        try:
            app.current_query_text = 'test query'
            app.current_hits = [
                SearchHit(score=62.0, title='Page A', anchor='A-1', source_path='pages/a.md', rendered_text='Alpha 1', chunk_id='a1', preview_text='Alpha 1', reason='正文命中'),
                SearchHit(score=40.0, title='Page C', anchor='C-1', source_path='pages/c.md', rendered_text='Gamma 1', chunk_id='c1', preview_text='Gamma 1', reason='正文命中'),
                SearchHit(score=81.0, title='Page B', anchor='B-1', source_path='pages/b.md', rendered_text='Beta 1', chunk_id='b1', preview_text='Beta 1', reason='正文命中'),
                SearchHit(score=78.0, title='Page A', anchor='A-2', source_path='pages/a.md', rendered_text='Alpha 2', chunk_id='a2', preview_text='Alpha 2', reason='正文命中'),
            ]
            app.selected_chunk_ids = {'a1', 'a2', 'b1', 'c1'}
            app._render_hits(selected_chunk_id='a1')

            app._toggle_page_sort()
            self.assertTrue(app.result_page_sort_active)
            self.assertEqual([hit.chunk_id for hit in app.current_hits], ['b1', 'a1', 'a2', 'c1'])
            self.assertEqual(app.page_sort_var.get(), app._tr('page_sort_restore_button'))

            app._toggle_page_sort()
            self.assertFalse(app.result_page_sort_active)
            self.assertEqual([hit.chunk_id for hit in app.current_hits], ['a1', 'c1', 'b1', 'a2'])
            self.assertEqual(app.page_sort_var.get(), app._tr('page_sort_button'))
        finally:
            app._on_close()

    def test_sort_header_exits_page_sort_mode(self) -> None:
        app = self._build_app()
        try:
            app.current_query_text = 'test query'
            app.current_hits = [
                SearchHit(score=62.0, title='Page A', anchor='A-1', source_path='pages/a.md', rendered_text='Alpha 1', chunk_id='a1', preview_text='Alpha 1', reason='正文命中'),
                SearchHit(score=40.0, title='Page C', anchor='C-1', source_path='pages/c.md', rendered_text='Gamma 1', chunk_id='c1', preview_text='Gamma 1', reason='正文命中'),
                SearchHit(score=81.0, title='Page B', anchor='B-1', source_path='pages/b.md', rendered_text='Beta 1', chunk_id='b1', preview_text='Beta 1', reason='正文命中'),
            ]
            app.selected_chunk_ids = {'a1', 'b1', 'c1'}
            app._render_hits()
            app._toggle_page_sort()

            app._sort_hits_by('title')
            self.assertFalse(app.result_page_sort_active)
            self.assertEqual([hit.title for hit in app.current_hits], ['Page A', 'Page B', 'Page C'])
            self.assertEqual(app.page_sort_var.get(), app._tr('page_sort_button'))
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


    def test_config_left_tabs_include_retrieval_boost(self) -> None:
        app = self._build_app()
        try:
            tabs = app.left_tabs.tabs()
            labels = [app.left_tabs.tab(tab, 'text') for tab in tabs]
            self.assertIn(app._tr('left_tab_retrieval'), labels)
        finally:
            app._on_close()

    def test_apply_status_updates_reranker_state_summary(self) -> None:
        app = self._build_app()
        try:
            app._apply_status({
                'stats': {'files': 1, 'chunks': 2, 'refs': 0},
                'reranker_ready': True,
                'reranker_model': 'BAAI/bge-reranker-v2-m3',
            })
            self.assertEqual(app.reranker_state_var.get(), app._tr('reranker_ready'))
            self.assertIsNotNone(app.reranker_state_label)
        finally:
            app._on_close()

    def test_config_left_tabs_include_ui(self) -> None:
        app = self._build_app()
        try:
            tabs = app.left_tabs.tabs()
            labels = [app.left_tabs.tab(tab, 'text') for tab in tabs]
            self.assertIn(app._tr('left_tab_ui'), labels)
        finally:
            app._on_close()

    def test_toggle_quick_start_is_local_refresh(self) -> None:
        app = self._build_app()
        try:
            app.root.update_idletasks()
            with patch.object(app, '_render_ui') as render_mock:
                self.assertTrue(app.quick_start_steps.winfo_ismapped())
                app._toggle_quick_start()
                app.root.update_idletasks()
                render_mock.assert_not_called()
                self.assertFalse(app.quick_start_steps.winfo_ismapped())
                self.assertEqual(app.quick_start_button_var.get(), app._tr('quick_start_show'))
                app._toggle_quick_start()
                app.root.update_idletasks()
                self.assertTrue(app.quick_start_steps.winfo_ismapped())
                self.assertEqual(app.quick_start_button_var.get(), app._tr('quick_start_hide'))
        finally:
            app._on_close()

    def test_toggle_advanced_is_local_refresh(self) -> None:
        app = self._build_app()
        try:
            app.root.update_idletasks()
            with patch.object(app, '_render_ui') as render_mock:
                self.assertTrue(app.advanced_panel.winfo_ismapped())
                app._toggle_advanced()
                app.root.update_idletasks()
                render_mock.assert_not_called()
                self.assertFalse(app.advanced_panel.winfo_ismapped())
                self.assertEqual(app.advanced_button_var.get(), app._tr('advanced_show'))
                app._toggle_advanced()
                app.root.update_idletasks()
                self.assertTrue(app.advanced_panel.winfo_ismapped())
                self.assertEqual(app.advanced_button_var.get(), app._tr('advanced_hide'))
        finally:
            app._on_close()

    def test_query_status_banner_is_placed_in_search_header_right(self) -> None:
        app = self._build_app()
        try:
            info = app.query_status_shell.grid_info()
            self.assertEqual(int(info['row']), 0)
            self.assertEqual(int(info['column']), 1)
        finally:
            app._on_close()

    def test_query_layout_restores_custom_split_positions(self) -> None:
        app = self._build_app()
        try:
            app.root.update()
            app.right_pane.sashpos(0, 360)
            app.results_pane.sashpos(0, 170)
            for _ in range(4):
                app.root.update()
                time.sleep(0.05)
            expected_right = app.right_pane.sashpos(0)
            expected_results = app.results_pane.sashpos(0)
            app._capture_layout_state()
        finally:
            app._on_close()

        app = self._build_app()
        try:
            for _ in range(18):
                app.root.update()
                time.sleep(0.05)
            self.assertLessEqual(abs(app.right_pane.sashpos(0) - expected_right), 8)
            self.assertLessEqual(abs(app.results_pane.sashpos(0) - expected_results), 8)
        finally:
            app._on_close()

    def test_query_status_banner_transitions(self) -> None:
        app = self._build_app()
        try:
            class _AliveWatch:
                def is_alive(self) -> bool:
                    return True

            app.status_snapshot = {'index_state': 'ready', 'stats': {'files': 1, 'chunks': 2, 'refs': 0}}
            app._refresh_query_status_banner()
            self.assertEqual(app.query_status_mode, 'idle')
            self.assertEqual(app.query_status_title_var.get(), app._tr('query_status_idle_title'))

            app.busy = True
            app.active_task_key = 'search_button'
            app.latest_task_progress = {
                'stage': 'query',
                'stage_status': 'rank',
                'overall_percent': 52.0,
                'candidates': 8,
                'limit': 15,
                'current': 52,
                'total': 100,
            }
            app._refresh_query_status_banner()
            self.assertEqual(app.query_status_mode, 'running')
            self.assertIn('52', app.query_status_title_var.get())
            self.assertIn(app._tr('query_stage_rank'), app.query_status_detail_var.get())

            app.busy = False
            app.active_task_key = None
            app.query_last_completed_at = time.time()
            app.query_last_result_count = 3
            app.query_last_copied = True
            app._refresh_query_status_banner()
            self.assertEqual(app.query_status_mode, 'done')
            self.assertIn('3', app.query_status_detail_var.get())

            app.query_last_completed_at = 0.0
            app.watch_thread = _AliveWatch()
            app._refresh_query_status_banner()
            self.assertEqual(app.query_status_mode, 'blocked')
            self.assertEqual(app.query_status_title_var.get(), app._tr('query_status_blocked_title'))
        finally:
            app._on_close()

    def test_apply_ui_preferences_from_controls_updates_theme_and_scale(self) -> None:
        app = self._build_app()
        try:
            app.ui_theme_var.set(app._tr('ui_theme_dark'))
            app.ui_scale_var.set('125')
            app._apply_ui_preferences_from_controls(rebuild_ui=False, persist=False)
            self.assertEqual(app.ui_theme, 'dark')
            self.assertEqual(app.effective_ui_theme, 'dark')
            self.assertEqual(app.ui_scale_percent, 125)
        finally:
            app._on_close()

    def test_root_configure_only_starts_interaction_on_size_change(self) -> None:
        app = self._build_app()
        try:
            app.last_root_size = (1560, 1000)

            class _Event:
                def __init__(self, widget, width: int, height: int) -> None:
                    self.widget = widget
                    self.width = width
                    self.height = height

            with patch.object(app, '_begin_ui_interaction') as begin_mock, \
                 patch.object(app, '_queue_window_geometry_capture') as capture_mock:
                app._on_root_configure(_Event(app.root, 1560, 1000))
                begin_mock.assert_not_called()
                capture_mock.assert_called_once()

                capture_mock.reset_mock()
                app._on_root_configure(_Event(app.root, 1640, 1040))
                begin_mock.assert_called_once()
                capture_mock.assert_called_once()
        finally:
            app._on_close()

    def test_responsive_wrap_reuses_parent_group(self) -> None:
        app = self._build_app()
        try:
            parent = tk.Frame(app.root, bg=app.colors['card'])
            first = tk.Label(parent, text='Alpha', bg=app.colors['card'])
            second = tk.Label(parent, text='Beta', bg=app.colors['card'])
            app._configure_responsive_wrap(first, padding=18, min_wrap=180, max_wrap=420)
            app._configure_responsive_wrap(second, padding=22, min_wrap=200, max_wrap=440)

            group = app.responsive_wrap_groups[str(parent)]
            self.assertEqual(len(group['widgets']), 2)
            self.assertTrue(group['bound'])
        finally:
            app._on_close()

    def test_notebook_tab_change_schedules_local_layout_refresh(self) -> None:
        app = self._build_app()
        try:
            with patch.object(app, '_begin_ui_interaction') as begin_mock, \
                 patch.object(app, '_schedule_notebook_layout_refresh') as refresh_mock:
                app._on_notebook_tab_changed(app.left_tabs)
            begin_mock.assert_called_once()
            refresh_mock.assert_called_once_with(app.left_tabs, delay_ms=0, force=True)
        finally:
            app._on_close()

if __name__ == '__main__':
    unittest.main()
