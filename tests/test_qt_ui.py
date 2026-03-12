import os
import shutil
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import omniclip_rag  # noqa: F401
from PySide6 import QtWidgets

from omniclip_rag.app_entry.desktop import launch_desktop, main as desktop_main
from omniclip_rag.ui_next_qt import app as qt_app
from omniclip_rag.config import AppConfig, ensure_data_paths, load_config, save_config
from omniclip_rag.errors import BuildCancelledError
from omniclip_rag.models import SearchHit, SpaceEstimate
from omniclip_rag.ui_i18n import text
from omniclip_rag.preflight import estimate_storage_for_vault
from omniclip_rag.ui_next_qt.config_workspace import ConfigWorkspace
from omniclip_rag.ui_next_qt.filter_models import PageBlocklistTableModel
from omniclip_rag.ui_next_qt.main_window import MainWindow
from omniclip_rag.ui_next_qt.query_table_model import QueryResultsTableModel
from omniclip_rag.ui_next_qt.query_workspace import QueryWorkspace
from omniclip_rag.ui_next_qt.theme import build_stylesheet, build_theme

ROOT = Path(__file__).resolve().parents[1]
TEST_ROOT = ROOT / '.tmp' / 'test_qt_ui'
SAMPLE_ROOT = ROOT / 'logseq笔记样本'


def get_app() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication([])
    return app


class QtUiTests(unittest.TestCase):
    def tearDown(self) -> None:
        if TEST_ROOT.exists():
            shutil.rmtree(TEST_ROOT, ignore_errors=True)

    def test_desktop_entry_defaults_to_next_ui(self) -> None:
        with patch('omniclip_rag.app_entry.desktop.launch_desktop', return_value=0) as launch_mock:
            self.assertEqual(desktop_main([]), 0)
        launch_mock.assert_called_once_with('next')

    def test_desktop_entry_can_open_legacy_ui(self) -> None:
        with patch('omniclip_rag.ui_legacy_tk.app.main', return_value=7) as legacy_mock:
            self.assertEqual(launch_desktop('legacy'), 7)
        legacy_mock.assert_called_once_with()

    def test_qt_app_resets_offscreen_env_for_interactive_launch(self) -> None:
        with patch.dict(os.environ, {'QT_QPA_PLATFORM': 'offscreen'}, clear=False):
            os.environ.pop('OMNICLIP_ALLOW_OFFSCREEN', None)
            qt_app._normalize_qpa_platform()
            self.assertNotEqual(os.environ.get('QT_QPA_PLATFORM'), 'offscreen')

    def test_config_roundtrip_keeps_qt_layout_fields(self) -> None:
        vault = SAMPLE_ROOT
        paths = ensure_data_paths(str(TEST_ROOT), str(vault))
        config = AppConfig(
            vault_path=str(vault),
            data_root=str(paths.global_root),
            qt_window_geometry='abc',
            qt_query_splitter_state='query-state',
            qt_results_splitter_state='results-state',
            qt_header_collapsed=True,
        )
        save_config(config, paths)
        loaded = load_config(paths)
        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual(loaded.qt_window_geometry, 'abc')
        self.assertEqual(loaded.qt_query_splitter_state, 'query-state')
        self.assertEqual(loaded.qt_results_splitter_state, 'results-state')
        self.assertTrue(loaded.qt_header_collapsed)

    def test_query_results_model_supports_sort_and_page_sort(self) -> None:
        model = QueryResultsTableModel(lambda key, **kwargs: text('zh-CN', key, **kwargs))
        model.set_results([
            SearchHit(score=88.0, title='Page A', anchor='A-1', source_path='a.md', rendered_text='A1', chunk_id='a1', reason='x'),
            SearchHit(score=72.0, title='Page B', anchor='B-1', source_path='b.md', rendered_text='B1', chunk_id='b1', reason='x'),
            SearchHit(score=92.0, title='Page A', anchor='A-2', source_path='a.md', rendered_text='A2', chunk_id='a2', reason='x'),
        ])
        model.sort_by_column(QueryResultsTableModel.COLUMN_SCORE)
        self.assertEqual(model.hit_at(0).chunk_id, 'a2')
        model.toggle_page_sort()
        self.assertEqual(model.hit_at(0).title, 'Page A')
        self.assertTrue(model.page_sort_active)
        model.toggle_all_selection()
        self.assertEqual(model.selected_count(), 0)
        model.toggle_all_selection()
        self.assertEqual(model.selected_count(), 3)

    def test_page_blocklist_model_serializes_rules(self) -> None:
        model = PageBlocklistTableModel(lambda key, **kwargs: text('zh-CN', key, **kwargs))
        model.set_rules_from_serialized('1\t^foo$\n0\t^bar$')
        self.assertEqual(model.rowCount(), 2)
        self.assertEqual(model.serialized_rules(), '1\t^foo$\n0\t^bar$')
        model.add_rule(enabled=True, pattern='^baz$')
        self.assertIn('1\t^baz$', model.serialized_rules())
        model.remove_rule(1)
        self.assertNotIn('^bar$', model.serialized_rules())

    def test_query_workspace_rebuilds_context_and_summary(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        paths = ensure_data_paths(str(TEST_ROOT), str(SAMPLE_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root))
        workspace = QueryWorkspace(config=config, paths=paths, language_code='zh-CN', theme=theme)
        try:
            workspace._current_query_text = '测试问题'
            workspace.results_model.set_results([
                SearchHit(score=88.0, title='Page A', anchor='A-1', source_path='a.md', rendered_text='B1', chunk_id='a1', display_text='Alpha body', preview_text='Alpha', reason='x'),
                SearchHit(score=72.0, title='Page B', anchor='B-1', source_path='b.md', rendered_text='B2', chunk_id='b1', display_text='Beta body', preview_text='Beta', reason='x'),
            ])
            workspace._rebuild_context_view()
            self.assertIn('# 笔记名：Page A', workspace.context_panel.plain_text())
            self.assertIn('共 2 个笔记', workspace.context_jump_summary_label.text())
            self.assertIn('已勾选 2/2', workspace.context_selection_label.text())
        finally:
            workspace.deleteLater()
            app.processEvents()

    def test_query_workspace_splitters_are_draggable_and_persistent_while_visible(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        paths = ensure_data_paths(str(TEST_ROOT), str(SAMPLE_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root))
        workspace = QueryWorkspace(config=config, paths=paths, language_code='zh-CN', theme=theme)
        try:
            workspace.resize(1400, 900)
            workspace.show()
            app.processEvents()
            workspace.query_splitter.setSizes([80, 820])
            workspace.results_splitter.setSizes([90, 620])
            app.processEvents()
            query_sizes = workspace.query_splitter.sizes()
            results_sizes = workspace.results_splitter.sizes()
            self.assertGreater(query_sizes[0], 0)
            self.assertLessEqual(query_sizes[0], 100)
            self.assertGreater(results_sizes[0], 0)
            self.assertLessEqual(results_sizes[0], 110)
            workspace.hide()
            app.processEvents()
            workspace.show()
            app.processEvents()
            self.assertEqual(workspace.query_splitter.sizes(), query_sizes)
            self.assertEqual(workspace.results_splitter.sizes(), results_sizes)
        finally:
            workspace.deleteLater()
            app.processEvents()

    def test_config_workspace_init_does_not_probe_acceleration_synchronously(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        paths = ensure_data_paths(str(TEST_ROOT), str(SAMPLE_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root))
        with patch('omniclip_rag.ui_next_qt.config_workspace.detect_acceleration', side_effect=AssertionError('should not be called during init')):
            workspace = ConfigWorkspace(config=config, paths=paths, language_code='zh-CN', theme=theme)
        try:
            self.assertIn(workspace.device_combo.currentText(), {text('zh-CN', 'device_option_auto'), text('zh-CN', 'device_option_cpu')})
        finally:
            workspace.deleteLater()
            app.processEvents()

    def test_config_workspace_saves_filters_and_ui_preferences(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        paths = ensure_data_paths(str(TEST_ROOT), str(SAMPLE_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root))
        workspace = ConfigWorkspace(config=config, paths=paths, language_code='zh-CN', theme=theme)
        seen_preferences: list[tuple[str, int]] = []
        workspace.uiPreferencesChanged.connect(lambda code, scale: seen_preferences.append((code, scale)))
        try:
            workspace._on_page_blocklist_saved('1\t^foo$')
            self.assertEqual(workspace._config.page_blocklist_rules, '1\t^foo$')
            workspace._on_sensitive_filters_saved(False, True, 'custom-rule')
            self.assertFalse(workspace._config.rag_filter_core_enabled)
            self.assertTrue(workspace._config.rag_filter_extended_enabled)
            self.assertEqual(workspace._config.rag_filter_custom_rules, 'custom-rule')
            workspace.ui_scale_spin.setValue(120)
            workspace.ui_theme_combo.setCurrentText(workspace._ui_theme_label('dark'))
            workspace._apply_ui_preferences()
            self.assertEqual(seen_preferences[-1], ('dark', 120))
            loaded = load_config(paths)
            self.assertIsNotNone(loaded)
            assert loaded is not None
            self.assertEqual(loaded.page_blocklist_rules, '1\t^foo$')
            self.assertFalse(loaded.rag_filter_core_enabled)
            self.assertTrue(loaded.rag_filter_extended_enabled)
            self.assertEqual(loaded.rag_filter_custom_rules, 'custom-rule')
            self.assertEqual(loaded.ui_theme, 'dark')
            self.assertEqual(loaded.ui_scale_percent, 120)
        finally:
            workspace.deleteLater()
            app.processEvents()

    def test_main_window_close_persists_qt_splitter_state(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        paths = ensure_data_paths(str(TEST_ROOT), str(SAMPLE_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root))
        window = MainWindow(config=config, paths=paths, language_code='zh-CN', theme=theme, version='0.2.0')
        try:
            self.assertIsInstance(window.config_workspace, ConfigWorkspace)
            window.show()
            app.processEvents()
            window.query_workspace.query_splitter.setSizes([260, 700])
            window.query_workspace.results_splitter.setSizes([320, 380])
            window._toggle_header_collapsed()
            window.close()
            app.processEvents()
            loaded = load_config(paths)
            self.assertIsNotNone(loaded)
            assert loaded is not None
            self.assertTrue(loaded.qt_window_geometry)
            self.assertTrue(loaded.qt_query_splitter_state)
            self.assertTrue(loaded.qt_results_splitter_state)
            self.assertTrue(loaded.qt_header_collapsed)
        finally:
            window.deleteLater()
            app.processEvents()

    def test_preflight_estimate_reports_progress_and_obeys_cancel(self) -> None:
        vault = TEST_ROOT / 'cancel_vault'
        vault.mkdir(parents=True, exist_ok=True)
        for index in range(4):
            (vault / f'note-{index}.md').write_text(f'# Note {index}\n- item {index}\n', encoding='utf-8')
        paths = ensure_data_paths(str(TEST_ROOT / 'cancel_data'), str(vault))
        config = AppConfig(vault_path=str(vault), data_root=str(paths.global_root))
        cancel_event = threading.Event()
        seen_stages: list[str] = []

        def on_progress(payload: dict[str, object]) -> None:
            stage = str(payload.get('stage') or '')
            seen_stages.append(stage)
            if stage == 'preflight' and int(payload.get('current', 0) or 0) >= 1:
                cancel_event.set()

        with self.assertRaises(BuildCancelledError):
            estimate_storage_for_vault(config, paths, on_progress=on_progress, cancel_event=cancel_event)
        self.assertIn('preflight', seen_stages)

    def test_task_success_callback_runs_after_busy_is_cleared(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        paths = ensure_data_paths(str(TEST_ROOT), str(SAMPLE_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root))
        workspace = ConfigWorkspace(config=config, paths=paths, language_code='zh-CN', theme=theme)
        seen_busy: list[bool] = []
        try:
            workspace._busy = True
            workspace._active_task_key = 'bootstrap_button'
            workspace._task_success_handler = lambda payload: seen_busy.append(workspace._busy)
            workspace._on_task_success({'blocked': False})
            workspace._on_task_finished()
            self.assertEqual(seen_busy, [False])
        finally:
            workspace.deleteLater()
            app.processEvents()

    def test_config_workspace_pause_and_cancel_freeze_progress_visual(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        paths = ensure_data_paths(str(TEST_ROOT), str(SAMPLE_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root))
        workspace = ConfigWorkspace(config=config, paths=paths, language_code='zh-CN', theme=theme)
        try:
            workspace._busy = True
            workspace._active_task_key = 'rebuild_button'
            workspace._latest_task_progress = {'stage': 'preflight_scan', 'current': 5, 'total': 0, 'overall_percent': 0.0}
            workspace.task_progress.setRange(0, 0)
            workspace._toggle_rebuild_pause()
            self.assertEqual(workspace.task_progress.maximum(), 100)
            self.assertIn('已暂停', workspace.task_state_label.text())
            with patch('PySide6.QtWidgets.QMessageBox.question', return_value=QtWidgets.QMessageBox.StandardButton.Yes):
                workspace._cancel_rebuild()
            self.assertTrue(workspace._rebuild_cancel_event.is_set())
            self.assertEqual(workspace.task_progress.maximum(), 100)
        finally:
            workspace.deleteLater()
            app.processEvents()

    def test_config_workspace_quick_start_steps_are_not_empty(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        paths = ensure_data_paths(str(TEST_ROOT), str(SAMPLE_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root))
        workspace = ConfigWorkspace(config=config, paths=paths, language_code='zh-CN', theme=theme)
        try:
            labels = [label.text().strip() for label in workspace.quick_steps_widget.findChildren(QtWidgets.QLabel)]
            self.assertEqual(len(labels), 3)
            self.assertTrue(all(labels))
        finally:
            workspace.deleteLater()
            app.processEvents()

    def test_query_workspace_migrates_key_tooltips(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        paths = ensure_data_paths(str(TEST_ROOT), str(SAMPLE_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root))
        workspace = QueryWorkspace(config=config, paths=paths, language_code='zh-CN', theme=theme)
        try:
            self.assertTrue(workspace.query_edit.toolTip())
            self.assertTrue(workspace.search_button.toolTip())
            self.assertTrue(workspace.page_blocklist_button.toolTip())
            self.assertTrue(workspace.context_jump_combo.toolTip())
            self.assertTrue(workspace.preview_panel.search_edit.toolTip())
            self.assertTrue(workspace.context_panel.next_button.toolTip())
        finally:
            workspace.deleteLater()
            app.processEvents()

    def test_config_workspace_preflight_success_notice_is_clickable(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        paths = ensure_data_paths(str(TEST_ROOT), str(SAMPLE_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root))
        workspace = ConfigWorkspace(config=config, paths=paths, language_code='zh-CN', theme=theme)
        opened_log: list[bool] = []
        workspace.showQueryLogRequested.connect(lambda: opened_log.append(True))
        report = SpaceEstimate(
            run_at='2026-03-11T08:00:00+08:00',
            vault_file_count=3,
            vault_total_bytes=2048,
            parsed_chunk_count=7,
            ref_count=2,
            logseq_file_count=1,
            markdown_file_count=2,
            estimated_sqlite_bytes=512,
            estimated_fts_bytes=256,
            estimated_vector_bytes=768,
            estimated_model_bytes=1024,
            estimated_peak_temp_bytes=128,
            safety_margin_bytes=64,
            current_state_bytes=0,
            current_model_cache_bytes=0,
            required_free_bytes=4096,
            available_free_bytes=8192,
            vector_backend='lancedb',
            vector_model='BAAI/bge-m3',
            can_proceed=True,
            risk_level='ok',
            estimated_build_seconds=75,
            estimated_download_seconds=0,
            notes=[],
        )
        try:
            workspace.show()
            app.processEvents()
            workspace._after_preflight({'report': report, 'status': {'stats': {'files': 0, 'chunks': 0, 'refs': 0}, 'index_state': 'missing', 'watch_allowed': False, 'query_allowed': False}})
            self.assertEqual(workspace.preflight_notice_label.text(), text('zh-CN', 'preflight_success_notice'))
            self.assertEqual(opened_log, [])
            workspace._on_preflight_notice_link('query-log')
            self.assertEqual(opened_log, [True])
        finally:
            workspace.deleteLater()
            app.processEvents()

    def test_config_workspace_pending_index_keeps_watch_clickable_and_blocks_query(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        paths = ensure_data_paths(str(TEST_ROOT), str(SAMPLE_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root))
        workspace = ConfigWorkspace(config=config, paths=paths, language_code='zh-CN', theme=theme)
        states: list[tuple[bool, str, str]] = []
        workspace.queryBlockStateChanged.connect(lambda blocked, title, detail: states.append((blocked, title, detail)))
        try:
            workspace._refresh_status_summary({
                'stats': {'files': 1, 'chunks': 3, 'refs': 0},
                'pending_rebuild': {'phase': 'indexing', 'completed': 1, 'total': 2},
                'index_state': 'pending',
                'index_ready': False,
                'watch_allowed': False,
                'query_allowed': False,
            })
            self.assertEqual(workspace.index_chip.text(), text('zh-CN', 'index_pending'))
            self.assertTrue(workspace.watch_button.isEnabled())
            self.assertTrue(states[-1][0])
            self.assertEqual(states[-1][2], text('zh-CN', 'query_status_blocked_detail_index'))
        finally:
            workspace.deleteLater()
            app.processEvents()

    def test_config_workspace_missing_index_watch_click_shows_feedback(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        paths = ensure_data_paths(str(TEST_ROOT), str(SAMPLE_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root))
        workspace = ConfigWorkspace(config=config, paths=paths, language_code='zh-CN', theme=theme)
        snapshot = {'stats': {'files': 0, 'chunks': 0, 'refs': 0}, 'index_state': 'missing', 'index_ready': False, 'watch_allowed': False, 'query_allowed': False}
        try:
            workspace._refresh_status_summary(snapshot)
            self.assertTrue(workspace.watch_button.isEnabled())
            with patch('omniclip_rag.ui_next_qt.config_workspace.OmniClipService') as service_cls, \
                 patch('omniclip_rag.ui_next_qt.config_workspace.save_config'), \
                 patch('PySide6.QtWidgets.QMessageBox.information') as info_mock:
                service_cls.return_value.status_snapshot.return_value = snapshot
                workspace._toggle_watch()
            info_mock.assert_called_once()
            self.assertIn('无索引不可监听', info_mock.call_args.args[2])
        finally:
            workspace.deleteLater()
            app.processEvents()

    def test_config_workspace_cuda_selection_uses_guidance_dialog_and_preserves_cuda_value(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        paths = ensure_data_paths(str(TEST_ROOT), str(SAMPLE_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root))
        workspace = ConfigWorkspace(config=config, paths=paths, language_code='zh-CN', theme=theme)
        payload = {
            'gpu_present': True,
            'gpu_name': 'NVIDIA RTX',
            'torch_available': False,
            'sentence_transformers_available': False,
            'cuda_available': False,
            'device_options': ['auto', 'cpu'],
        }
        try:
            workspace.backend_combo.setCurrentText('lancedb')
            workspace._refresh_device_options(payload)
            labels = [workspace.device_combo.itemText(i) for i in range(workspace.device_combo.count())]
            self.assertIn(text('zh-CN', 'device_option_cuda'), labels)
            with patch('omniclip_rag.ui_next_qt.config_workspace.runtime_guidance_context', return_value={
                'plain_text': 'install runtime',
                'gpu_present': True,
                'torch_available': False,
                'sentence_transformers_available': False,
                'cuda_available': False,
                'cuda_step_status': '未检测到可用 CUDA 条件',
                'runtime_step_status': '还没有检测到 runtime 文件夹',
                'runtime_complete': False,
                'current_status_lines': ['- runtime 文件夹：未检测到'],
                'install_command': 'PowerShell ...',
                'cuda_guide_url': 'https://pytorch.org/get-started/locally/',
                'app_dir': str(ROOT),
                'runtime_dir': str(ROOT / 'runtime'),
                'disk_usage': '约 4.3 GB - 4.6 GB',
                'download_usage': '约 3 GB - 5 GB',
                'requested_device': 'cuda',
                'extra_detail': '',
            }) as context_mock, patch('omniclip_rag.ui_next_qt.config_workspace.RuntimeGuidanceDialog') as dialog_cls:
                dialog_cls.return_value.exec.return_value = 0
                workspace._set_device_value('cuda')
            context_mock.assert_called()
            dialog_cls.assert_called_once()
            dialog_context = dialog_cls.call_args.kwargs['context']
            self.assertEqual(dialog_context['requested_device'], 'cuda')
            self.assertEqual(workspace.device_combo.currentText(), text('zh-CN', 'device_option_cuda'))
            self.assertEqual(workspace._current_device_value(), 'cuda')
            saved_config, _ = workspace._collect_config(False)
            self.assertEqual(saved_config.vector_device, 'cuda')
        finally:
            workspace.deleteLater()
            app.processEvents()

    def test_config_workspace_saves_watch_peak_setting(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        paths = ensure_data_paths(str(TEST_ROOT), str(SAMPLE_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root))
        workspace = ConfigWorkspace(config=config, paths=paths, language_code='zh-CN', theme=theme)
        try:
            workspace.watch_peak_combo.setCurrentText(workspace._watch_peak_label(60))
            workspace._save_only()
            loaded = load_config(paths)
            self.assertIsNotNone(loaded)
            assert loaded is not None
            self.assertEqual(loaded.watch_resource_peak_percent, 60)
        finally:
            workspace.deleteLater()
            app.processEvents()

    def test_config_workspace_rebuild_merges_returned_stats_into_status_summary(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        paths = ensure_data_paths(str(TEST_ROOT), str(SAMPLE_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root))
        workspace = ConfigWorkspace(config=config, paths=paths, language_code='zh-CN', theme=theme)
        try:
            workspace._after_rebuild({'status': {'stats': {'files': 0, 'chunks': 0, 'refs': 0}}, 'stats': {'files': 3, 'chunks': 7, 'refs': 11, 'duplicate_block_ids': 0}, 'blocked': False})
            self.assertEqual(workspace.files_value.text(), '3')
            self.assertEqual(workspace.chunks_value.text(), '7')
            self.assertEqual(workspace.refs_value.text(), '11')
            self.assertEqual(workspace.index_chip.text(), text('zh-CN', 'index_ready'))
        finally:
            workspace.deleteLater()
            app.processEvents()

    def test_config_workspace_progress_text_tracks_current_and_total(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        paths = ensure_data_paths(str(TEST_ROOT), str(SAMPLE_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root))
        workspace = ConfigWorkspace(config=config, paths=paths, language_code='zh-CN', theme=theme)
        try:
            workspace._update_task_progress({'stage': 'indexing', 'current': 2, 'total': 5, 'overall_percent': 40.0, 'current_path': 'a.md'})
            self.assertEqual(workspace.task_progress.format(), '2/5 · 40%')
            self.assertTrue(workspace.task_progress.isTextVisible())
            workspace._update_task_progress({'stage': 'indexing', 'current': 4, 'total': 5, 'overall_percent': 80.0, 'current_path': 'b.md'})
            self.assertEqual(workspace.task_progress.format(), '4/5 · 80%')
        finally:
            workspace.deleteLater()
            app.processEvents()

    def test_main_window_header_removes_legacy_controls(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        paths = ensure_data_paths(str(TEST_ROOT), str(SAMPLE_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root))
        window = MainWindow(config=config, paths=paths, language_code='zh-CN', theme=theme, version='0.2.0')
        try:
            self.assertFalse(hasattr(window, 'open_legacy_button'))
            header_texts = {widget.text().strip() for widget in window.header_card.findChildren(QtWidgets.QLabel)}
            header_texts.update(widget.text().strip() for widget in window.header_card.findChildren(QtWidgets.QPushButton))
            header_texts.update(widget.text().strip() for widget in window.header_card.findChildren(QtWidgets.QToolButton))
            self.assertNotIn('打开旧版界面', header_texts)
            self.assertNotIn('Qt新界面', header_texts)
        finally:
            window.deleteLater()
            app.processEvents()

    def test_main_window_language_switch_rebuilds_shell_and_preserves_state(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        paths = ensure_data_paths(str(TEST_ROOT), str(SAMPLE_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root), ui_language='zh-CN')
        window = MainWindow(config=config, paths=paths, language_code='zh-CN', theme=theme, version='0.2.0')
        replacement: MainWindow | None = None
        try:
            window.show()
            app.processEvents()
            window.query_workspace.query_edit.setText('language switch smoke test')
            window.query_workspace.threshold_edit.setText('35')
            window.query_workspace.limit_edit.setText('21')
            window.main_tabs.setCurrentWidget(window.config_workspace)
            window.config_workspace.sub_tabs.setCurrentIndex(3)
            window.language_combo.setCurrentText('English')
            app.processEvents()
            replacement = window._replacement_window
            self.assertIsNotNone(replacement)
            assert replacement is not None
            app.processEvents()
            self.assertEqual(replacement._language_code, 'en')
            self.assertEqual(replacement.language_caption.text(), text('en', 'language'))
            self.assertEqual(replacement.main_tabs.tabText(0), text('en', 'main_tab_query'))
            self.assertEqual(replacement.main_tabs.tabText(1), text('en', 'main_tab_config'))
            self.assertEqual(replacement.main_tabs.currentWidget(), replacement.config_workspace)
            self.assertEqual(replacement.config_workspace.sub_tabs.currentIndex(), 3)
            self.assertEqual(replacement.query_workspace.query_edit.text(), 'language switch smoke test')
            self.assertEqual(replacement.query_workspace.limit_edit.text(), '21')
            loaded = load_config(paths)
            self.assertIsNotNone(loaded)
            assert loaded is not None
            self.assertEqual(loaded.ui_language, 'en')
        finally:
            if replacement is not None:
                replacement.close()
                replacement.deleteLater()
            window.deleteLater()
            app.processEvents()

    def test_main_window_show_query_log_signal_switches_page_and_tab(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        paths = ensure_data_paths(str(TEST_ROOT), str(SAMPLE_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root))
        window = MainWindow(config=config, paths=paths, language_code='zh-CN', theme=theme, version='0.2.0')
        try:
            window.main_tabs.setCurrentWidget(window.config_workspace)
            window.query_workspace.detail_tabs.setCurrentIndex(0)
            window.config_workspace.showQueryLogRequested.emit()
            app.processEvents()
            self.assertIs(window.main_tabs.currentWidget(), window.query_workspace)
            self.assertEqual(window.query_workspace.detail_tabs.currentIndex(), 2)
        finally:
            window.deleteLater()
            app.processEvents()

    def test_stylesheet_defines_combobox_hover_state(self) -> None:
        sheet = build_stylesheet(build_theme('light', 100))
        self.assertIn('QComboBox QAbstractItemView::item:hover', sheet)
        self.assertIn('selection-background-color', sheet)


if __name__ == '__main__':
    unittest.main()
