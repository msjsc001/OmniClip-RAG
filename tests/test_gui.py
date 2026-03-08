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
            app.main_pane.sashpos(0, 430)
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
