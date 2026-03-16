import json
import os
import shutil
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import omniclip_rag  # noqa: F401
from PySide6 import QtCore, QtWidgets

from omniclip_rag.app_entry.desktop import launch_desktop, main as desktop_main
from omniclip_rag.ui_next_qt import app as qt_app
from omniclip_rag.ui_next_qt.app import StartupProgressDialog
from omniclip_rag.config import AppConfig, ensure_data_paths, load_config, save_config
from omniclip_rag.errors import BuildCancelledError
from omniclip_rag.models import QueryInsights, QueryResult, SearchHit, SpaceEstimate
from omniclip_rag.ui_i18n import text
from omniclip_rag.preflight import estimate_storage_for_vault
from omniclip_rag.vector_index import get_local_model_dir
from omniclip_rag.ui_next_qt.config_workspace import ConfigWorkspace
from omniclip_rag.ui_next_qt.filter_models import PageBlocklistTableModel
from omniclip_rag.ui_next_qt.main_window import MainWindow
from omniclip_rag.ui_next_qt.query_table_model import QueryResultsTableModel
from omniclip_rag.ui_next_qt.query_workspace import QueryWorkspace
from omniclip_rag.ui_next_qt.theme import build_stylesheet, build_theme
from omniclip_rag.ui_next_qt.workers import QueryTaskResult

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

    def test_desktop_entry_retires_legacy_flag_and_still_uses_qt(self) -> None:
        with patch('omniclip_rag.app_entry.desktop.launch_desktop', return_value=0) as launch_mock:
            self.assertEqual(desktop_main(['--ui', 'legacy']), 0)
        launch_mock.assert_called_once_with('legacy')

    def test_qt_app_resets_offscreen_env_for_interactive_launch(self) -> None:
        with patch.dict(os.environ, {'QT_QPA_PLATFORM': 'offscreen'}, clear=False):
            os.environ.pop('OMNICLIP_ALLOW_OFFSCREEN', None)
            qt_app._normalize_qpa_platform()
            self.assertNotEqual(os.environ.get('QT_QPA_PLATFORM'), 'offscreen')

    def test_startup_progress_dialog_marks_source_mode_in_title(self) -> None:
        app = get_app()
        dialog = StartupProgressDialog()
        try:
            self.assertIn('开发态', dialog.windowTitle())
            self.assertIn('开发态', dialog._title_label.text())
        finally:
            dialog.close()
            dialog.deleteLater()
            app.processEvents()

    def test_startup_progress_dialog_is_minimizable_and_updates_status(self) -> None:
        app = get_app()
        dialog = StartupProgressDialog()
        try:
            self.assertTrue(bool(dialog.windowFlags() & QtCore.Qt.WindowType.WindowMinimizeButtonHint))
            dialog.set_status('正在准备主界面...', detail='测试细节')
            self.assertIn('正在准备主界面', dialog._status_label.text())
            self.assertIn('测试细节', dialog._detail_label.text())
        finally:
            dialog.close()
            dialog.deleteLater()
            app.processEvents()

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
        self.assertEqual(model.data(model.index(0, QueryResultsTableModel.COLUMN_INDEX)), '1')
        self.assertEqual(model.headerData(QueryResultsTableModel.COLUMN_INDEX, QtCore.Qt.Orientation.Horizontal), text('zh-CN', 'col_index'))
        model.sort_by_column(QueryResultsTableModel.COLUMN_SCORE)
        self.assertEqual(model.hit_at(0).chunk_id, 'a2')
        self.assertEqual(model.data(model.index(0, QueryResultsTableModel.COLUMN_INDEX)), '1')
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

    def test_query_workspace_hides_copy_buttons_from_query_toolbar(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        paths = ensure_data_paths(str(TEST_ROOT), str(SAMPLE_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root))
        workspace = QueryWorkspace(config=config, paths=paths, language_code='zh-CN', theme=theme)
        try:
            self.assertTrue(workspace.search_copy_button.isHidden())
            self.assertTrue(workspace.copy_context_button.isHidden())
        finally:
            workspace.deleteLater()
            app.processEvents()

    def test_query_workspace_filters_return_allowed_families(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        paths = ensure_data_paths(str(TEST_ROOT), str(SAMPLE_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root), vector_backend='disabled')
        workspace = QueryWorkspace(config=config, paths=paths, language_code='zh-CN', theme=theme)
        try:
            workspace.query_edit.setText('filter smoke test')
            workspace.source_pdf_check.setChecked(False)
            workspace.source_tika_check.setChecked(False)
            prepared = workspace._validate_query_request(copy_result=False)
            self.assertIsNotNone(prepared)
            assert prepared is not None
            self.assertEqual(prepared[-1], ('markdown',))
        finally:
            workspace.deleteLater()
            app.processEvents()

    def test_query_workspace_prefers_live_runtime_snapshot_over_cached_config(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        vault_a = TEST_ROOT / 'vault_a'
        vault_b = TEST_ROOT / 'vault_b'
        vault_a.mkdir(parents=True, exist_ok=True)
        vault_b.mkdir(parents=True, exist_ok=True)
        paths_a = ensure_data_paths(str(TEST_ROOT / 'data_a'), str(vault_a))
        paths_b = ensure_data_paths(str(TEST_ROOT / 'data_b'), str(vault_b))
        cached_config = AppConfig(vault_path=str(vault_a), data_root=str(paths_a.global_root), vector_backend='disabled')
        live_config = AppConfig(vault_path=str(vault_b), data_root=str(paths_b.global_root), vector_backend='disabled')
        workspace = QueryWorkspace(
            config=cached_config,
            paths=paths_a,
            language_code='zh-CN',
            theme=theme,
            runtime_snapshot_provider=lambda: (live_config, paths_b),
        )
        try:
            workspace.query_edit.setText('我的思维')
            workspace.source_pdf_check.setChecked(False)
            workspace.source_tika_check.setChecked(False)
            prepared = workspace._validate_query_request(copy_result=False)
            self.assertIsNotNone(prepared)
            assert prepared is not None
            _query_text, _threshold, prepared_config, prepared_paths, _copy_result, allowed_families = prepared
            self.assertEqual(prepared_config.vault_path, str(vault_b))
            self.assertEqual(str(prepared_paths.root), str(paths_b.root))
            self.assertEqual(allowed_families, ('markdown',))
        finally:
            workspace.deleteLater()
            app.processEvents()

    def test_query_workspace_requires_at_least_one_source_family(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        paths = ensure_data_paths(str(TEST_ROOT), str(SAMPLE_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root), vector_backend='disabled')
        workspace = QueryWorkspace(config=config, paths=paths, language_code='zh-CN', theme=theme)
        try:
            workspace.query_edit.setText('filter smoke test')
            workspace.source_markdown_check.setChecked(False)
            workspace.source_pdf_check.setChecked(False)
            workspace.source_tika_check.setChecked(False)
            with patch('PySide6.QtWidgets.QMessageBox.information') as info_mock:
                prepared = workspace._validate_query_request(copy_result=False)
            self.assertIsNone(prepared)
            info_mock.assert_called_once()
            self.assertIn('请选择查询来源', info_mock.call_args.args[1])
        finally:
            workspace.deleteLater()
            app.processEvents()

    def test_query_workspace_shows_runtime_warning_hint(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        paths = ensure_data_paths(str(TEST_ROOT), str(SAMPLE_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root), vector_backend='disabled')
        workspace = QueryWorkspace(config=config, paths=paths, language_code='zh-CN', theme=theme)
        try:
            workspace._query_runtime_warnings = ('markdown_vector_runtime_unavailable',)
            workspace._refresh_query_runtime_hint()
            self.assertFalse(workspace.query_runtime_hint_label.isHidden())
            self.assertIn('纯字面检索', workspace.query_runtime_hint_label.text())
            self.assertIn('点击修复', workspace.query_runtime_hint_label.text())
            requested: list[bool] = []
            workspace.runtimeRepairRequested.connect(lambda: requested.append(True))
            workspace._handle_runtime_hint_link('runtime-repair')
            self.assertEqual(requested, [True])
        finally:
            workspace.deleteLater()
            app.processEvents()

    def test_query_workspace_snapshot_restores_source_filters(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        paths = ensure_data_paths(str(TEST_ROOT), str(SAMPLE_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root), vector_backend='disabled')
        workspace = QueryWorkspace(config=config, paths=paths, language_code='zh-CN', theme=theme)
        try:
            workspace.source_markdown_check.setChecked(True)
            workspace.source_pdf_check.setChecked(False)
            workspace.source_tika_check.setChecked(True)
            snapshot = workspace.snapshot_view_state()
        finally:
            workspace.deleteLater()
            app.processEvents()

        replacement = QueryWorkspace(config=config, paths=paths, language_code='zh-CN', theme=theme)
        try:
            replacement.restore_view_state(snapshot)
            self.assertTrue(replacement.source_markdown_check.isChecked())
            self.assertFalse(replacement.source_pdf_check.isChecked())
            self.assertTrue(replacement.source_tika_check.isChecked())
        finally:
            replacement.deleteLater()
            app.processEvents()

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

    def test_query_runtime_warning_text_points_to_runtime_repair(self) -> None:
        warning = text('zh-CN', 'query_runtime_warning_markdown_vector_runtime_unavailable')
        self.assertIn('纯字面检索', warning)
        self.assertNotIn('Tika 扩展运行时无关', warning)

    def test_config_workspace_exposes_runtime_management_ui(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        paths = ensure_data_paths(str(TEST_ROOT), str(SAMPLE_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root))
        workspace = ConfigWorkspace(config=config, paths=paths, language_code='zh-CN', theme=theme)
        try:
            self.assertEqual(workspace.sub_tabs.tabText(workspace.sub_tabs.indexOf(workspace.runtime_page)), text('zh-CN', 'left_tab_runtime'))
            self.assertEqual(workspace.runtime_refresh_button.text(), text('zh-CN', 'runtime_refresh'))
            self.assertEqual(workspace.runtime_open_dir_button.text(), text('zh-CN', 'runtime_open_dir'))
            self.assertEqual(workspace.runtime_components_table.rowCount(), 3)
            self.assertTrue(hasattr(workspace, 'runtime_chip'))
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

    def test_config_workspace_startup_background_tasks_serialize_heavy_probes(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        paths = ensure_data_paths(str(TEST_ROOT), str(SAMPLE_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root))
        workspace = ConfigWorkspace(config=config, paths=paths, language_code='zh-CN', theme=theme)
        scheduled: list[tuple[str, object]] = []
        try:
            with patch.object(workspace, '_start_device_probe', side_effect=lambda *, safe_mode=False: scheduled.append(('probe', safe_mode))), \
                 patch.object(workspace, 'schedule_initial_status_load', side_effect=lambda delay_ms=0: scheduled.append(('status', delay_ms))):
                workspace.schedule_startup_background_tasks(safe_mode=True, initial_status_delay_ms=120)
                self.assertEqual(scheduled, [('probe', True)])
                workspace._on_device_probe_finished()
            self.assertEqual(scheduled, [('probe', True), ('status', 120)])
            workspace._on_device_probe_finished()
            self.assertEqual(scheduled, [('probe', True), ('status', 120)])
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

    def test_config_workspace_quick_start_steps_include_runtime_guidance(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        paths = ensure_data_paths(str(TEST_ROOT), str(SAMPLE_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root))
        workspace = ConfigWorkspace(config=config, paths=paths, language_code='zh-CN', theme=theme)
        try:
            labels = [label.text().strip() for label in workspace.quick_steps_widget.findChildren(QtWidgets.QLabel)]
            self.assertEqual(len(labels), 3)
            self.assertTrue(all(labels))
            self.assertIn('runtime', labels[1].lower())
            self.assertIn('下载当前模型', labels[1])
            self.assertIn('预检查空间时间', labels[2])
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

    def test_config_workspace_index_chip_reads_markdown_index_state_from_disk(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        paths = ensure_data_paths(str(TEST_ROOT), str(SAMPLE_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root))
        workspace = ConfigWorkspace(config=config, paths=paths, language_code='zh-CN', theme=theme)
        states: list[tuple[bool, str, str]] = []
        workspace.queryBlockStateChanged.connect(lambda blocked, title, detail: states.append((blocked, title, detail)))
        try:
            (paths.state_dir / 'index_state.json').write_text(json.dumps({
                'version': 1,
                'vault_path': str(SAMPLE_ROOT),
                'completed_at': '2026-03-14T00:00:00Z',
            }, ensure_ascii=False), encoding='utf-8')
            workspace._refresh_status_summary(None)
            self.assertEqual(workspace.index_chip.text(), text('zh-CN', 'index_ready'))
            self.assertFalse(states[-1][0])
            self.assertNotIn('检测当前笔记库的索引状态', workspace.watch_button.toolTip())
        finally:
            workspace.deleteLater()
            app.processEvents()


    def test_config_workspace_checking_snapshot_falls_back_to_markdown_disk_state(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        paths = ensure_data_paths(str(TEST_ROOT), str(SAMPLE_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root))
        workspace = ConfigWorkspace(config=config, paths=paths, language_code='zh-CN', theme=theme)
        try:
            (paths.state_dir / 'index_state.json').write_text(json.dumps({
                'version': 1,
                'vault_path': str(SAMPLE_ROOT),
                'completed_at': '2026-03-14T00:00:00Z',
            }, ensure_ascii=False), encoding='utf-8')
            workspace._refresh_status_summary({'index_state': 'checking', 'index_ready': False, 'stats': {'files': 0, 'chunks': 0, 'refs': 0}})
            self.assertEqual(workspace.index_chip.text(), text('zh-CN', 'index_ready'))
            self.assertNotIn('检测中', workspace.index_chip.text())
        finally:
            workspace.deleteLater()
            app.processEvents()

    def test_extension_overview_refresh_does_not_change_markdown_index_chip(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        paths = ensure_data_paths(str(TEST_ROOT), str(SAMPLE_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root))
        workspace = ConfigWorkspace(config=config, paths=paths, language_code='zh-CN', theme=theme)
        try:
            workspace._refresh_status_summary({
                'stats': {'files': 3, 'chunks': 7, 'refs': 1},
                'index_state': 'ready',
                'index_ready': True,
                'watch_allowed': True,
                'query_allowed': True,
            })
            workspace._refresh_extension_overview()
            self.assertEqual(workspace.index_chip.text(), text('zh-CN', 'index_ready'))
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


    def test_config_workspace_runtime_refresh_uses_background_function_worker(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        paths = ensure_data_paths(str(TEST_ROOT), str(SAMPLE_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root))
        workspace = ConfigWorkspace(config=config, paths=paths, language_code='zh-CN', theme=theme)
        class _Signal:
            def __init__(self) -> None:
                self._callbacks = []
            def connect(self, callback):
                self._callbacks.append(callback)
        class _FakeWorker:
            def __init__(self, *, fn) -> None:
                self.fn = fn
                self.succeeded = _Signal()
                self.failed = _Signal()
                self.finished = _Signal()
                self.started = False
            def start(self) -> None:
                self.started = True
        try:
            with patch('omniclip_rag.ui_next_qt.config_workspace.FunctionWorker', _FakeWorker):
                workspace._request_runtime_management_refresh()
            self.assertIsNotNone(workspace._runtime_refresh_worker)
            self.assertTrue(workspace._runtime_refresh_worker.started)
            self.assertFalse(workspace.runtime_refresh_button.isEnabled())
        finally:
            workspace.deleteLater()
            app.processEvents()

    def test_config_workspace_runtime_chip_uses_cpu_ready_text_when_gpu_is_not_needed(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        paths = ensure_data_paths(str(TEST_ROOT), str(SAMPLE_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root))
        workspace = ConfigWorkspace(config=config, paths=paths, language_code='zh-CN', theme=theme)
        try:
            with patch.object(workspace, '_current_runtime_repair_context', return_value={
                'runtime_complete': True,
                'sentence_transformers_available': True,
                'gpu_present': False,
                'runtime_dir': str(paths.shared_root / 'runtime'),
                'runtime_exists': True,
                'runtime_missing_items': [],
            }), patch('omniclip_rag.ui_next_qt.config_workspace.runtime_component_status', side_effect=lambda component_id: {
                'component_id': component_id,
                'status': 'ready',
                'ready': True,
                'missing_items': [],
                'installed_count': 1,
                'total_count': 1,
                'cleanup_patterns': tuple(),
            }), patch('omniclip_rag.ui_next_qt.config_workspace.runtime_component_usage', return_value={'disk_usage': '0 GB', 'download_usage': '0 GB'}):
                workspace._refresh_runtime_management_ui(force_refresh=True)
            self.assertEqual(workspace.runtime_chip.text(), text('zh-CN', 'runtime_chip_cpu_ready'))
            self.assertIn('GPU 加速这一项不需要安装', workspace.runtime_status_summary_label.text())
            self.assertEqual(workspace.runtime_components_table.rowCount(), 3)
            self.assertEqual(workspace.runtime_components_table.item(2, 2).text(), text('zh-CN', 'runtime_status_not_needed'))
        finally:
            workspace.deleteLater()
            app.processEvents()

    def test_config_workspace_runtime_auto_repair_launches_expected_powershell_command(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        paths = ensure_data_paths(str(TEST_ROOT), str(SAMPLE_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root))
        workspace = ConfigWorkspace(config=config, paths=paths, language_code='zh-CN', theme=theme)
        install_script = TEST_ROOT / 'runtime_auto_repair_smoke' / 'InstallRuntime.ps1'
        install_script.parent.mkdir(parents=True, exist_ok=True)
        install_script.write_text('Write-Host runtime smoke', encoding='utf-8')
        try:
            with patch.object(workspace, '_current_runtime_repair_context', return_value={
                'install_script': str(install_script),
                'recommended_profile': 'cuda',
                'app_dir': str(install_script.parent),
            }), patch.object(workspace, '_powershell_executable', return_value='powershell.exe'), patch('omniclip_rag.ui_next_qt.config_workspace.subprocess.Popen') as popen_mock:
                workspace._run_runtime_auto_repair(source='mirror', component='semantic-core')
            popen_mock.assert_called_once()
            command = popen_mock.call_args.args[0]
            self.assertEqual(command[0], 'powershell.exe')
            self.assertIn('-File', command)
            self.assertIn(str(install_script), command)
            self.assertIn('-Profile', command)
            self.assertIn('cpu', command)
            self.assertIn('-Source', command)
            self.assertIn('mirror', command)
            self.assertIn('-WaitForProcessName', command)
            self.assertIn('OmniClipRAG', command)
            self.assertIn('-Component', command)
            self.assertIn('semantic-core', command)
            self.assertEqual(popen_mock.call_args.kwargs['cwd'], str(install_script.parent))
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

    def test_config_workspace_runtime_missing_for_cuda_detects_incomplete_runtime(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        paths = ensure_data_paths(str(TEST_ROOT), str(SAMPLE_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root))
        workspace = ConfigWorkspace(config=config, paths=paths, language_code='zh-CN', theme=theme)
        try:
            workspace.backend_combo.setCurrentText('lancedb')
            with patch('omniclip_rag.ui_next_qt.config_workspace.runtime_guidance_context', return_value={
                'gpu_present': True,
                'cuda_available': True,
            }), patch('omniclip_rag.ui_next_qt.config_workspace.runtime_dependency_issue', return_value='runtime missing'):
                self.assertTrue(workspace._runtime_missing_for_cuda())
        finally:
            workspace.deleteLater()
            app.processEvents()

    def test_config_workspace_vector_progress_uses_written_counts_and_global_percent(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        paths = ensure_data_paths(str(TEST_ROOT), str(SAMPLE_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root))
        workspace = ConfigWorkspace(config=config, paths=paths, language_code='zh-CN', theme=theme)
        try:
            workspace._update_task_progress({
                'stage': 'vectorizing',
                'current': 1,
                'total': 5,
                'overall_percent': 6.0,
                'encoded_count': 3,
                'written_count': 1,
            })
            self.assertEqual(workspace.task_progress.format(), '1/5 · 6%')
            self.assertIn('已编码 3/5', workspace.task_detail_label.text())
            self.assertIn('已写入 1/5', workspace.task_detail_label.text())
            self.assertIn('6%', workspace.task_percent_label.text())
        finally:
            workspace.deleteLater()
            app.processEvents()

    def test_config_workspace_model_download_ui_uses_selected_model_name(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        paths = ensure_data_paths(str(TEST_ROOT), str(SAMPLE_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root), vector_model='BAAI/bge-m3')
        workspace = ConfigWorkspace(config=config, paths=paths, language_code='zh-CN', theme=theme)
        try:
            self.assertEqual(workspace.bootstrap_button.text(), text('zh-CN', 'bootstrap_button_named', model='BAAI/bge-m3'))
            self.assertEqual(workspace.model_chip.text(), text('zh-CN', 'model_missing_named', model='BAAI/bge-m3'))
        finally:
            workspace.deleteLater()
            app.processEvents()

    def test_config_workspace_manual_model_download_opens_copyable_dialog(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        paths = ensure_data_paths(str(TEST_ROOT), str(SAMPLE_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root), vector_model='BAAI/bge-m3')
        workspace = ConfigWorkspace(config=config, paths=paths, language_code='zh-CN', theme=theme)
        try:
            with patch.object(workspace, '_ask_yes_no_cancel', return_value=QtWidgets.QMessageBox.StandardButton.No), \
                 patch('omniclip_rag.ui_next_qt.config_workspace.ModelDownloadDialog') as dialog_cls:
                dialog_instance = dialog_cls.return_value
                result = workspace._choose_model_download_mode('下载模型', config, paths)
            self.assertEqual(result, 'manual')
            dialog_cls.assert_called_once()
            kwargs = dialog_cls.call_args.kwargs
            self.assertIn('hf download', kwargs['context']['official_download_command'])
            self.assertIn('https://huggingface.co/BAAI/bge-m3', kwargs['context']['official_url'])
            self.assertIn('https://hf-mirror.com/BAAI/bge-m3', kwargs['context']['mirror_url'])
            self.assertIn(str(get_local_model_dir(config, paths)), kwargs['context']['plain_text'])
            dialog_instance.exec.assert_called_once()
        finally:
            workspace.deleteLater()
            app.processEvents()

    def test_config_workspace_device_runtime_status_lists_gpu_cuda_runtime_and_mode(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        paths = ensure_data_paths(str(TEST_ROOT), str(SAMPLE_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root), vector_backend='lancedb', vector_device='cuda')
        workspace = ConfigWorkspace(config=config, paths=paths, language_code='zh-CN', theme=theme)
        payload = {
            'gpu_present': True,
            'gpu_name': 'NVIDIA RTX 3060',
            'torch_available': True,
            'torch_version': '2.10.0+cu128',
            'sentence_transformers_available': True,
            'cuda_available': True,
            'nvcc_available': True,
            'nvcc_version': '12.3',
            'runtime_exists': True,
            'runtime_complete': False,
            'runtime_missing_items': ['lancedb', 'pyarrow'],
            'device_options': ['auto', 'cpu', 'cuda'],
        }
        try:
            with patch.object(workspace, '_runtime_available_for_device', side_effect=lambda device_name: device_name in {'cpu', 'cuda'}):
                with patch('omniclip_rag.ui_next_qt.config_workspace.resolve_vector_device', return_value='cuda'):
                    workspace._refresh_device_options(payload)
            status_text = workspace.device_runtime_status_label.text()
            self.assertIn('N卡支持', status_text)
            self.assertIn('CUDA环境', status_text)
            self.assertIn('runtime 文件夹', status_text)
            self.assertIn('CPU模式', status_text)
            self.assertIn('当前实际模式', status_text)
            self.assertRegex(status_text, r'当前实际模式：(CPU|GPU)')
            self.assertIn('lancedb, pyarrow', status_text)
        finally:
            workspace.deleteLater()
            app.processEvents()

    def test_config_workspace_vector_progress_recovering_warns_not_to_close(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        paths = ensure_data_paths(str(TEST_ROOT), str(SAMPLE_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root))
        workspace = ConfigWorkspace(config=config, paths=paths, language_code='zh-CN', theme=theme)
        try:
            workspace._update_task_progress({
                'stage': 'vectorizing',
                'stage_status': 'recovering',
                'current': 12,
                'total': 100,
                'overall_percent': 12.0,
                'encoded_count': 18,
                'written_count': 12,
                'encode_batch_size': 4,
                'write_batch_size': 128,
                'write_queue_depth': 2,
                'write_queue_capacity': 6,
                'write_flush_count': 3,
                'tuning_action': 'hold',
                'tuning_reason': 'memory_guard',
            })
            self.assertIn('请不要关闭程序', workspace.task_detail_label.text())
            self.assertIn('等待', workspace.task_detail_label.text())
        finally:
            workspace.deleteLater()
            app.processEvents()

    def test_config_workspace_saves_log_size_preferences(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        paths = ensure_data_paths(str(TEST_ROOT), str(SAMPLE_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root))
        workspace = ConfigWorkspace(config=config, paths=paths, language_code='zh-CN', theme=theme)
        try:
            workspace.log_size_spin.setValue(32)
            workspace.query_trace_logging_check.setChecked(True)
            workspace._save_log_preferences()
            loaded = load_config(paths)
            self.assertIsNotNone(loaded)
            assert loaded is not None
            self.assertEqual(loaded.log_file_size_mb, 32)
            self.assertTrue(loaded.query_trace_logging_enabled)
            self.assertIn('32 MB', workspace.log_storage_summary_label.text())
        finally:
            workspace.deleteLater()
            app.processEvents()


    def test_query_workspace_trace_lines_only_persist_when_enabled(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        paths = ensure_data_paths(str(TEST_ROOT / 'query_trace_toggle'), str(SAMPLE_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root), query_trace_logging_enabled=False)
        workspace = QueryWorkspace(config=config, paths=paths, language_code='zh-CN', theme=theme)
        calls: list[tuple[str, bool]] = []

        def capture(message: str, *, persist: bool = True) -> None:
            calls.append((str(message), bool(persist)))

        workspace._append_log = capture  # type: ignore[method-assign]
        try:
            insights = QueryInsights(trace_lines=('查询预期：字面检索 -> 语义检索 -> Reranker',))
            result = QueryResult(hits=[], context_text='', insights=insights)
            payload = QueryTaskResult(query_text='我的思维', copied=False, result=result)
            workspace._on_query_success(payload)
            assert ('查询预期：字面检索 -> 语义检索 -> Reranker', False) in calls
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







