import json
import os
import shutil
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import omniclip_rag  # noqa: F401
import omniclip_rag.ui_next_qt.config_workspace as config_workspace_module
from PySide6 import QtCore, QtWidgets

from omniclip_rag.app_entry.desktop import launch_desktop, main as desktop_main
from omniclip_rag.ui_next_qt import app as qt_app
from omniclip_rag.ui_next_qt.app import StartupProgressDialog
from omniclip_rag.config import AppConfig, ensure_data_paths, load_config, save_config
from omniclip_rag.data_root_bootstrap import write_bootstrap_pointer
from omniclip_rag.errors import BuildCancelledError
from omniclip_rag.extensions.models import ExtensionDirectoryState, ExtensionSourceDirectory
from omniclip_rag.models import QueryInsights, QueryResult, SearchHit, SpaceEstimate
from omniclip_rag.ui_i18n import data_root_reason_text, text, tooltip
from omniclip_rag.preflight import estimate_storage_for_vault
from omniclip_rag.vector_index import get_local_model_dir, hf_repo_cache_dir
from omniclip_rag.reranker import get_local_reranker_dir, get_local_reranker_repo_cache_dir
from omniclip_rag.ui_next_qt.config_workspace import ConfigWorkspace
from omniclip_rag.ui_next_qt.filter_models import PageBlocklistTableModel
from omniclip_rag.ui_next_qt.main_window import MainWindow
from omniclip_rag.ui_next_qt.query_table_model import QueryResultsTableModel
from omniclip_rag.ui_next_qt.query_workspace import QueryWorkspace
from omniclip_rag.ui_next_qt.theme import build_stylesheet, build_theme
from omniclip_rag.ui_next_qt.workers import QueryTaskResult

ROOT = Path(__file__).resolve().parents[1]
BASE_TEST_SANDBOX = ROOT / '.tmp' / 'test_qt_ui_sandbox'
TEST_SANDBOX = BASE_TEST_SANDBOX
TEST_ROOT = TEST_SANDBOX / 'data'
ENV_ROOT = TEST_SANDBOX / 'system'
SAMPLE_ROOT = ROOT / '笔记样本'


def get_app() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication([])
    return app


class QtUiTests(unittest.TestCase):
    def setUp(self) -> None:
        global TEST_SANDBOX, TEST_ROOT, ENV_ROOT
        TEST_SANDBOX = BASE_TEST_SANDBOX / self._testMethodName
        TEST_ROOT = TEST_SANDBOX / 'data'
        ENV_ROOT = TEST_SANDBOX / 'system'
        if TEST_SANDBOX.exists():
            shutil.rmtree(TEST_SANDBOX, ignore_errors=True)
        self._env = patch.dict(
            os.environ,
            {
                'APPDATA': str((ENV_ROOT / 'appdata').resolve()),
                'LOCALAPPDATA': str((ENV_ROOT / 'localappdata').resolve()),
                'TEMP': str((ENV_ROOT / 'temp').resolve()),
                'TMP': str((ENV_ROOT / 'temp').resolve()),
                'USERPROFILE': str((ENV_ROOT / 'profile').resolve()),
                'HOME': str((ENV_ROOT / 'profile').resolve()),
                'OMNICLIP_STRICT_TEST_ROOT': str(TEST_SANDBOX.resolve()),
                'OMNICLIP_BOOTSTRAP_PATH': str((ENV_ROOT / 'roaming' / 'bootstrap.json').resolve()),
            },
            clear=False,
        )
        self._env.start()

    def tearDown(self) -> None:
        global TEST_SANDBOX, TEST_ROOT, ENV_ROOT
        self._env.stop()
        if TEST_SANDBOX.exists():
            shutil.rmtree(TEST_SANDBOX, ignore_errors=True)
        TEST_SANDBOX = BASE_TEST_SANDBOX
        TEST_ROOT = TEST_SANDBOX / 'data'
        ENV_ROOT = TEST_SANDBOX / 'system'

    def _make_broken_environment_root(self, root: Path) -> Path:
        root.mkdir(parents=True, exist_ok=True)
        (root / 'shared').mkdir(parents=True, exist_ok=True)
        (root / 'config.json').write_text('{}', encoding='utf-8')
        return root

    def _make_legacy_environment_root(self, root: Path, vault_path: Path | None = None) -> Path:
        root.mkdir(parents=True, exist_ok=True)
        (root / 'shared').mkdir(parents=True, exist_ok=True)
        (root / 'workspaces').mkdir(parents=True, exist_ok=True)
        payload = {
            'vault_path': str(vault_path or SAMPLE_ROOT),
            'data_root': str(root.resolve()),
            'vault_paths': [str(vault_path or SAMPLE_ROOT)],
        }
        (root / 'config.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
        return root

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
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root), vector_backend='lancedb')
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

    def test_query_workspace_shows_scope_label_for_multiple_md_vaults(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        vault_a = TEST_ROOT / 'query_scope_a'
        vault_b = TEST_ROOT / 'query_scope_b'
        vault_a.mkdir(parents=True, exist_ok=True)
        vault_b.mkdir(parents=True, exist_ok=True)
        paths = ensure_data_paths(str(TEST_ROOT / 'query_scope_root'), str(vault_a))
        config = AppConfig(
            vault_path=str(vault_a),
            data_root=str(paths.global_root),
            vault_paths=[str(vault_a), str(vault_b)],
            md_selected_vault_paths=[str(vault_a), str(vault_b)],
            vector_backend='disabled',
        )
        workspace = QueryWorkspace(config=config, paths=paths, language_code='zh-CN', theme=theme)
        try:
            self.assertFalse(workspace.query_scope_label.isHidden())
            self.assertIn('2', workspace.query_scope_label.text())
            self.assertIn('搜索范围', workspace.query_scope_label.text())
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

    def test_query_status_button_sits_after_query_action_and_tracks_state(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        paths = ensure_data_paths(str(TEST_ROOT), str(SAMPLE_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root))
        workspace = QueryWorkspace(config=config, paths=paths, language_code='zh-CN', theme=theme)
        try:
            search_index = workspace.query_row_layout.indexOf(workspace.search_button)
            self.assertIs(
                workspace.query_row_layout.itemAt(search_index + 1).widget(),
                workspace.query_status_button,
            )
            threshold_index = workspace.meta_row_layout.indexOf(workspace.threshold_label)
            self.assertIs(
                workspace.meta_row_layout.itemAt(threshold_index + 1).widget(),
                workspace.threshold_edit,
            )
            limit_index = workspace.meta_row_layout.indexOf(workspace.limit_label)
            self.assertIs(
                workspace.meta_row_layout.itemAt(limit_index + 1).widget(),
                workspace.limit_edit,
            )
            self.assertEqual(workspace.query_status_button.text(), '待查询')

            workspace.set_external_block_state(blocked=True, title='Runtime 检测中', detail='请稍候')
            self.assertEqual(workspace.query_status_button.text(), '暂不可用')
            self.assertIn('Runtime 检测中', workspace.query_status_button.toolTip())

            workspace._external_blocked = False
            workspace._busy = True
            workspace._query_progress_payload = {'overall_percent': 42.0, 'stage_status': 'vector'}
            workspace._refresh_query_status_banner()
            self.assertEqual(workspace.query_status_button.text(), '语义召回 · 42%')

            workspace._query_progress_payload = {'overall_percent': 68.0, 'stage_status': 'rerank'}
            workspace._refresh_query_status_banner()
            self.assertEqual(workspace.query_status_button.text(), '重排 · 68%')

            workspace._busy = False
            workspace._query_last_completed_at = time.time()
            workspace._query_last_result_count = 15
            workspace._refresh_query_status_banner()
            self.assertEqual(workspace.query_status_button.text(), '完成 · 15')

            workspace._query_last_error = '子进程异常退出'
            workspace._refresh_query_status_banner()
            self.assertEqual(workspace.query_status_button.text(), '查询失败')
            self.assertIn('子进程异常退出', workspace.query_status_button.toolTip())
        finally:
            workspace.deleteLater()
            app.processEvents()

    def test_query_workspace_explains_cuda_and_reranker_requirements_in_hint_and_log(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        paths = ensure_data_paths(str(TEST_ROOT), str(SAMPLE_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root))
        workspace = QueryWorkspace(config=config, paths=paths, language_code='zh-CN', theme=theme)
        calls: list[str] = []

        def capture(message: str, *, persist: bool = True) -> None:
            del persist
            calls.append(str(message))

        workspace._append_log = capture  # type: ignore[method-assign]
        try:
            insights = QueryInsights(
                runtime_warnings=(
                    'markdown_vector_system_memory_to_cpu',
                    'markdown_reranker_unavailable',
                ),
                runtime_requirements=(
                    {
                        'kind': 'cuda_memory',
                        'reason': 'system_memory_to_cpu',
                        'vault_path': str(SAMPLE_ROOT),
                        'current': {
                            'commit_headroom_bytes': 4 * 1024**3,
                            'commit_usage_percent': 95.46,
                            'available_physical_bytes': 9 * 1024**3,
                        },
                        'required': {
                            'min_commit_headroom_bytes': 2 * 1024**3,
                            'max_commit_usage_percent': 98.0,
                            'min_physical_available_bytes': 2 * 1024**3,
                        },
                    },
                    {
                        'kind': 'reranker',
                        'reason': 'reranker_execution_failed',
                        'vault_path': str(SAMPLE_ROOT),
                        'error_class': 'OSError',
                        'error_message': 'model shard cannot be opened',
                    },
                ),
            )
            result = QueryResult(hits=[], context_text='', insights=insights)
            workspace._on_query_success(
                QueryTaskResult(query_text='查询', copied=False, result=result)
            )

            hint = workspace.query_runtime_hint_label.text()
            self.assertIn('Commit 余量为 4.00 GiB', hint)
            self.assertIn('至少 2.00 GiB', hint)
            self.assertIn('低于 98%', hint)
            self.assertIn('OSError: model shard cannot be opened', hint)
            self.assertTrue(any('Commit 余量为 4.00 GiB' in message for message in calls))
            self.assertTrue(any('Reranker 正常运行需满足' in message for message in calls))
        finally:
            workspace.deleteLater()
            app.processEvents()

    def test_query_workspace_explains_how_to_resolve_reranker_commit_guard(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        paths = ensure_data_paths(str(TEST_ROOT), str(SAMPLE_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root))
        workspace = QueryWorkspace(config=config, paths=paths, language_code='zh-CN', theme=theme)
        try:
            workspace._query_runtime_warnings = (
                'markdown_reranker_unavailable',
                'markdown_reranker_resource_guard',
            )
            workspace._query_runtime_requirements = ({
                'kind': 'reranker',
                'reason': 'reranker_resource_guard',
                'vault_path': str(SAMPLE_ROOT),
                'error_class': 'LowCommitHeadroom',
                'error_message': 'Windows commit headroom is 3.59 GiB; this model is estimated to need 4.25 GiB.',
                'current': {
                    'commit_headroom_bytes': 3.59 * 1024**3,
                    'available_physical_bytes': 11.25 * 1024**3,
                    'cuda_free_bytes': 7.50 * 1024**3,
                },
                'required': {
                    'min_commit_headroom_bytes': 4.25 * 1024**3,
                    'min_physical_available_bytes': 3.10 * 1024**3,
                    'min_cuda_free_bytes': 3.55 * 1024**3,
                },
            },)
            workspace._refresh_query_runtime_hint()

            hint = workspace.query_runtime_hint_label.text()
            self.assertIn('Commit 余量 3.59 GiB', hint)
            self.assertIn('CUDA 可用显存 7.50 GiB', hint)
            self.assertIn('4.25 / 3.10 / 3.55 GiB', hint)
            self.assertIn('查询已完成，只跳过二次排序', hint)
            self.assertIn('不再使用统一的 10 GiB 门槛', hint)
            self.assertIn('无需重装 Runtime 或模型', hint)
            self.assertNotIn('修复或重装重排模型', hint)
        finally:
            workspace.deleteLater()
            app.processEvents()

    def test_query_workspace_update_runtime_clears_stale_runtime_warning_hint(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        paths = ensure_data_paths(str(TEST_ROOT), str(SAMPLE_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root), vector_backend='disabled')
        workspace = QueryWorkspace(config=config, paths=paths, language_code='zh-CN', theme=theme)
        try:
            workspace._query_runtime_warnings = ('markdown_vector_index_missing',)
            workspace._refresh_query_runtime_hint()
            self.assertFalse(workspace.query_runtime_hint_label.isHidden())
            workspace.update_runtime(config=config, paths=paths)
            self.assertEqual(workspace._query_runtime_warnings, ())
            self.assertTrue(workspace.query_runtime_hint_label.isHidden())
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

    def test_query_workspace_snapshot_restores_search_controls_collapsed(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        paths = ensure_data_paths(str(TEST_ROOT), str(SAMPLE_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root), vector_backend='disabled')
        workspace = QueryWorkspace(config=config, paths=paths, language_code='zh-CN', theme=theme)
        try:
            workspace._toggle_search_controls_collapsed()
            self.assertTrue(workspace.search_controls_collapsed())
            self.assertFalse(workspace.query_edit.isHidden())
            self.assertFalse(workspace.search_button.isHidden())
            self.assertFalse(workspace.search_controls_toggle_button.isHidden())
            self.assertTrue(workspace.search_header_widget.isHidden())
            self.assertTrue(workspace.query_hint_label.isHidden())
            self.assertTrue(workspace.search_details_widget.isHidden())
            self.assertEqual(workspace.search_controls_toggle_button.text(), '展开')
            snapshot = workspace.snapshot_view_state()
        finally:
            workspace.deleteLater()
            app.processEvents()

        replacement = QueryWorkspace(config=config, paths=paths, language_code='zh-CN', theme=theme)
        try:
            replacement.restore_view_state(snapshot)
            self.assertTrue(replacement.search_controls_collapsed())
            self.assertFalse(replacement.query_edit.isHidden())
            self.assertFalse(replacement.search_button.isHidden())
            self.assertFalse(replacement.search_controls_toggle_button.isHidden())
            self.assertTrue(replacement.search_header_widget.isHidden())
            self.assertTrue(replacement.query_hint_label.isHidden())
            self.assertTrue(replacement.search_details_widget.isHidden())
            self.assertEqual(replacement.search_controls_toggle_button.text(), '展开')
        finally:
            replacement.deleteLater()
            app.processEvents()

    def test_query_workspace_collapsed_mode_compacts_query_console(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        paths = ensure_data_paths(str(TEST_ROOT / 'collapsed_splitter_root'), str(SAMPLE_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root), vector_backend='disabled')
        workspace = QueryWorkspace(config=config, paths=paths, language_code='zh-CN', theme=theme)
        try:
            workspace.resize(1200, 900)
            workspace.show()
            app.processEvents()
            before_height = workspace.search_card.height()
            workspace._toggle_search_controls_collapsed()
            app.processEvents()
            after_height = workspace.search_card.height()
            self.assertTrue(workspace.search_controls_collapsed())
            self.assertEqual(workspace.search_controls_toggle_button.text(), '展开')
            self.assertEqual(workspace.query_splitter.count(), 1)
            self.assertLess(after_height, before_height)
            self.assertTrue(workspace.search_details_widget.isHidden())
        finally:
            workspace.close()
            workspace.deleteLater()
            app.processEvents()

    def test_query_workspace_rebuilds_context_and_summary(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        paths = ensure_data_paths(str(TEST_ROOT), str(SAMPLE_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root), vector_backend='lancedb')
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
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root), vector_backend='lancedb')
        workspace = QueryWorkspace(config=config, paths=paths, language_code='zh-CN', theme=theme)
        try:
            workspace.resize(1400, 900)
            workspace.show()
            app.processEvents()
            workspace.query_splitter.setSizes([900])
            workspace.results_splitter.setSizes([90, 620])
            app.processEvents()
            query_sizes = workspace.query_splitter.sizes()
            results_sizes = workspace.results_splitter.sizes()
            self.assertGreater(query_sizes[0], 0)
            self.assertEqual(len(query_sizes), 1)
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
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root), vector_backend='lancedb')
        workspace = ConfigWorkspace(config=config, paths=paths, language_code='zh-CN', theme=theme)
        try:
            self.assertEqual(workspace.sub_tabs.tabText(workspace.sub_tabs.indexOf(workspace.runtime_page)), text('zh-CN', 'left_tab_runtime'))
            ordered_tabs = [workspace.sub_tabs.tabText(index) for index in range(workspace.sub_tabs.count())]
            self.assertEqual(
                ordered_tabs,
                [
                    text('zh-CN', 'left_tab_start'),
                    text('zh-CN', 'left_tab_settings'),
                    text('zh-CN', 'left_tab_runtime'),
                    text('zh-CN', 'left_tab_extensions'),
                    text('zh-CN', 'left_tab_retrieval'),
                    text('zh-CN', 'left_tab_ui'),
                    text('zh-CN', 'left_tab_data'),
                ],
            )
            self.assertEqual(workspace.runtime_refresh_button.text(), text('zh-CN', 'runtime_refresh'))
            self.assertEqual(workspace.runtime_open_dir_button.text(), text('zh-CN', 'runtime_open_dir'))
            self.assertEqual(workspace.runtime_components_table.rowCount(), 3)
            self.assertTrue(hasattr(workspace, 'runtime_chip'))
            self.assertTrue(hasattr(workspace, 'runtime_install_target_label'))
        finally:
            workspace.deleteLater()
            app.processEvents()

    def test_config_workspace_init_does_not_probe_acceleration_synchronously(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        paths = ensure_data_paths(str(TEST_ROOT), str(SAMPLE_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root), vector_backend='lancedb')
        with patch('omniclip_rag.ui_next_qt.config_workspace.detect_acceleration', side_effect=AssertionError('should not be called during init')):
            workspace = ConfigWorkspace(config=config, paths=paths, language_code='zh-CN', theme=theme)
        try:
            self.assertIn(workspace.device_combo.currentText(), {text('zh-CN', 'device_option_auto'), text('zh-CN', 'device_option_cpu')})
        finally:
            workspace.deleteLater()
            app.processEvents()

    def test_config_workspace_startup_background_tasks_begin_runtime_detection_immediately(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        paths = ensure_data_paths(str(TEST_ROOT), str(SAMPLE_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root), vector_backend='lancedb')
        workspace = ConfigWorkspace(config=config, paths=paths, language_code='zh-CN', theme=theme)
        scheduled: list[tuple[int, object]] = []
        query_blocks: list[tuple[bool, str, str]] = []
        workspace.queryBlockStateChanged.connect(
            lambda blocked, title, detail: query_blocks.append((blocked, title, detail))
        )
        try:
            with patch(
                'omniclip_rag.ui_next_qt.config_workspace.QtCore.QTimer.singleShot',
                side_effect=lambda delay_ms, callback: scheduled.append((delay_ms, callback)),
            ):
                workspace.schedule_startup_background_tasks(safe_mode=True, initial_status_delay_ms=120)
            self.assertEqual(len(scheduled), 1)
            self.assertEqual(scheduled[0][0], 0)
            self.assertEqual(scheduled[0][1], workspace._start_startup_runtime_refresh)
            self.assertEqual(workspace.runtime_chip.text(), text('zh-CN', 'runtime_chip_detecting'))
            self.assertEqual(workspace.runtime_status_summary_label.text(), text('zh-CN', 'runtime_refresh_running'))
            self.assertTrue(workspace._startup_prewarm_safe_mode)
            self.assertTrue(query_blocks[-1][0])
            self.assertEqual(
                query_blocks[-1][2],
                text('zh-CN', 'query_status_blocked_detail_runtime_detecting'),
            )
        finally:
            workspace.deleteLater()
            app.processEvents()

    def test_config_workspace_automatically_refreshes_runtime_status_when_full_prewarm_is_skipped(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        paths = ensure_data_paths(str(TEST_ROOT), str(SAMPLE_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root), vector_backend='disabled')
        workspace = ConfigWorkspace(config=config, paths=paths, language_code='zh-CN', theme=theme)
        connected: list[object] = []
        started: list[bool] = []
        cancelled: list[bool] = []

        class _Signal:
            def connect(self, callback) -> None:
                connected.append(callback)

        worker = SimpleNamespace(
            succeeded=_Signal(),
            failed=_Signal(),
            finished=_Signal(),
            start=lambda: started.append(True),
            cancel=lambda: cancelled.append(True),
        )
        try:
            with patch('omniclip_rag.ui_next_qt.config_workspace.inspect_runtime_environment', return_value={'runtime_complete': True}), \
                 patch('omniclip_rag.ui_next_qt.config_workspace.read_memory_status', return_value=object()), \
                 patch('omniclip_rag.ui_next_qt.config_workspace.evaluate_startup_status_refresh', return_value=SimpleNamespace(allowed=True, reason='allowed')), \
                 patch('omniclip_rag.ui_next_qt.config_workspace.decision_log_payload', return_value={}), \
                 patch('omniclip_rag.ui_next_qt.config_workspace.RuntimeProbeWorker', return_value=worker) as worker_type:
                workspace._start_startup_runtime_refresh()

            worker_type.assert_called_once_with(probe_kind='refresh', data_root=str(paths.global_root))
            self.assertEqual(started, [True])
            self.assertTrue(workspace._startup_runtime_refresh_pending)
            self.assertIs(workspace._startup_prewarm_worker, worker)

            workspace._on_startup_activity()
            self.assertEqual(cancelled, [])
        finally:
            with patch.object(workspace, '_schedule_startup_initial_status'):
                workspace._on_startup_prewarm_finished()
            workspace.deleteLater()
            app.processEvents()

    def test_config_workspace_applies_automatic_runtime_status_result_to_start_page(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        paths = ensure_data_paths(str(TEST_ROOT), str(SAMPLE_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root), vector_backend='disabled')
        workspace = ConfigWorkspace(config=config, paths=paths, language_code='zh-CN', theme=theme)
        payload = {
            'runtime_complete': True,
            'runtime_status': 'cpu',
            'torch_available': True,
            'sentence_transformers_available': True,
        }
        try:
            workspace._startup_runtime_refresh_pending = True
            with patch.object(workspace, '_refresh_device_options') as refresh_device, \
                 patch.object(workspace, '_refresh_runtime_management_ui') as refresh_runtime, \
                 patch.object(workspace, '_append_log') as append_log:
                workspace._on_startup_prewarm_success(payload)

            self.assertEqual(workspace._acceleration_payload, payload)
            refresh_device.assert_called_once_with(payload)
            refresh_runtime.assert_called_once_with(force_refresh=False, context=payload)
            append_log.assert_called_once_with(text('zh-CN', 'runtime_refresh_done'))
        finally:
            workspace.deleteLater()
            app.processEvents()

    def test_config_workspace_overview_chips_do_not_force_runtime_management_refresh(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        paths = ensure_data_paths(str(TEST_ROOT), str(SAMPLE_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root), vector_backend='lancedb')
        workspace = ConfigWorkspace(config=config, paths=paths, language_code='zh-CN', theme=theme)
        try:
            with patch.object(workspace, '_refresh_runtime_management_ui', side_effect=AssertionError('overview chips should stay lightweight')):
                workspace._refresh_overview_chips()
        finally:
            workspace.deleteLater()
            app.processEvents()

    def test_config_workspace_suppresses_runtime_refresh_during_programmatic_model_sync(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        paths = ensure_data_paths(str(TEST_ROOT), str(SAMPLE_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root), vector_backend='lancedb')
        workspace = ConfigWorkspace(config=config, paths=paths, language_code='zh-CN', theme=theme)
        try:
            workspace._live_runtime_sync_suppressed = True
            with patch.object(workspace, '_refresh_overview_chips', side_effect=AssertionError('model sync should not refresh overview chips while suppressed')), \
                 patch.object(workspace, '_on_live_runtime_preferences_changed', side_effect=AssertionError('model sync should not emit live runtime changes while suppressed')), \
                 patch.object(workspace, '_refresh_reranker_state', side_effect=AssertionError('reranker sync should not probe readiness while suppressed')):
                workspace._on_model_text_changed('BAAI/bge-m3')
                workspace._on_reranker_model_text_changed('BAAI/bge-reranker-v2-m3')
        finally:
            workspace.deleteLater()
            app.processEvents()

    def test_config_workspace_suppresses_runtime_management_refresh_while_syncing_device_options(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        paths = ensure_data_paths(str(TEST_ROOT), str(SAMPLE_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root), vector_backend='lancedb')
        workspace = ConfigWorkspace(config=config, paths=paths, language_code='zh-CN', theme=theme)
        try:
            with patch.object(workspace, '_refresh_runtime_management_ui', side_effect=AssertionError('device option replay should not synchronously refresh runtime management UI')):
                workspace._refresh_device_options({'device_options': ['auto', 'cpu'], 'gpu_present': False})
        finally:
            workspace.deleteLater()
            app.processEvents()

    def test_config_workspace_initial_status_finish_does_not_queue_runtime_probe(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        paths = ensure_data_paths(str(TEST_ROOT), str(SAMPLE_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root), vector_backend='lancedb')
        workspace = ConfigWorkspace(config=config, paths=paths, language_code='zh-CN', theme=theme)
        try:
            with patch.object(workspace, '_request_runtime_management_refresh', side_effect=AssertionError('startup status must not probe runtime')):
                workspace._on_initial_status_finished()
            self.assertIsNone(workspace._initial_status_worker)
        finally:
            workspace.deleteLater()
            app.processEvents()

    def test_config_workspace_startup_status_uses_isolated_child_process(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        paths = ensure_data_paths(str(TEST_ROOT), str(SAMPLE_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root), vector_backend='lancedb')
        workspace = ConfigWorkspace(config=config, paths=paths, language_code='zh-CN', theme=theme)
        connected: list[object] = []
        started: list[bool] = []

        class _Signal:
            def connect(self, callback) -> None:
                connected.append(callback)

        worker = SimpleNamespace(
            succeeded=_Signal(),
            failed=_Signal(),
            finished=_Signal(),
            start=lambda: started.append(True),
        )
        try:
            with patch('omniclip_rag.ui_next_qt.config_workspace.RuntimeProbeWorker', return_value=worker) as worker_type, \
                 patch('omniclip_rag.ui_next_qt.config_workspace.ServiceTaskWorker', side_effect=AssertionError('startup status must not use an in-process thread')):
                workspace._load_initial_status(isolated=True)
            worker_type.assert_called_once_with(probe_kind='workspace-status', data_root=str(paths.global_root))
            self.assertEqual(started, [True])
            self.assertIs(workspace._initial_status_worker, worker)
        finally:
            workspace._initial_status_worker = None
            workspace.deleteLater()
            app.processEvents()

    def test_config_workspace_staggers_auxiliary_startup_tasks_after_status(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        paths = ensure_data_paths(str(TEST_ROOT), str(SAMPLE_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root), vector_backend='lancedb')
        workspace = ConfigWorkspace(config=config, paths=paths, language_code='zh-CN', theme=theme)
        scheduled: list[int] = []
        try:
            workspace._startup_initial_status_pending = True
            with patch(
                'omniclip_rag.ui_next_qt.config_workspace.QtCore.QTimer.singleShot',
                side_effect=lambda delay_ms, callback: scheduled.append(delay_ms),
            ):
                workspace._on_initial_status_finished()
            self.assertEqual(scheduled, [500, 1_500, 3_000])
        finally:
            workspace.deleteLater()
            app.processEvents()

    def test_config_workspace_defers_full_tika_catalog_until_dialog_is_opened(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        paths = ensure_data_paths(str(TEST_ROOT), str(SAMPLE_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root))
        workspace = ConfigWorkspace(config=config, paths=paths, language_code='zh-CN', theme=theme)
        try:
            with patch('omniclip_rag.ui_next_qt.config_workspace.build_tika_format_catalog', return_value=[]) as catalog:
                workspace._merge_tika_format_catalog([])
                workspace._merge_tika_format_catalog([], full_catalog=True)
            self.assertEqual(catalog.call_args_list[0].args, (None,))
            self.assertEqual(catalog.call_args_list[1].args, (paths,))
        finally:
            workspace.deleteLater()
            app.processEvents()

    def test_config_workspace_apply_config_to_controls_defers_extension_source_summary_refresh(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        paths = ensure_data_paths(str(TEST_ROOT), str(SAMPLE_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root), vector_backend='lancedb')
        workspace = ConfigWorkspace(config=config, paths=paths, language_code='zh-CN', theme=theme)
        try:
            with patch.object(workspace, '_refresh_extension_source_summaries', side_effect=AssertionError('startup config replay should not synchronously rebuild extension summaries')), \
                 patch.object(workspace, '_request_extension_source_summaries_refresh') as request_mock:
                workspace._apply_config_to_controls(config, paths, activate=False)
            request_mock.assert_not_called()
        finally:
            workspace.deleteLater()
            app.processEvents()

    def test_extension_config_persist_never_scans_source_directories_on_ui_thread(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        paths = ensure_data_paths(str(TEST_ROOT / 'extension_persist_async_summary'), str(SAMPLE_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root), vector_backend='disabled')
        workspace = ConfigWorkspace(config=config, paths=paths, language_code='zh-CN', theme=theme)
        try:
            workspace._extension_state.pdf_config.enabled = True
            with patch.object(
                workspace,
                '_normalize_extension_registry_state',
                side_effect=AssertionError('directory normalization must not run in the button callback'),
            ), patch.object(
                workspace,
                '_refresh_extension_source_summaries',
                side_effect=AssertionError('source summaries must not run in the button callback'),
            ), patch.object(workspace, '_request_extension_source_summaries_refresh') as request_refresh:
                workspace._persist_extension_state(sync_tika_sidecar=False)
            request_refresh.assert_called_once_with(workspace._config, workspace._paths)
            self.assertTrue(workspace._extension_registry.load(paths).pdf_config.enabled)
        finally:
            workspace.deleteLater()
            app.processEvents()

    def test_extension_source_summary_refresh_runs_outside_ui_thread(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        paths = ensure_data_paths(str(TEST_ROOT / 'extension_summary_background'), str(SAMPLE_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root), vector_backend='disabled')
        workspace = ConfigWorkspace(config=config, paths=paths, language_code='zh-CN', theme=theme)
        caller_thread_id = threading.get_ident()
        worker_thread_ids: list[int] = []
        try:
            def build_summaries(*_args, **_kwargs):
                worker_thread_ids.append(threading.get_ident())
                return {}

            with patch(
                'omniclip_rag.ui_next_qt.config_workspace._build_extension_source_summaries_payload',
                side_effect=build_summaries,
            ):
                workspace._request_extension_source_summaries_refresh(config, paths)
                deadline = time.monotonic() + 3.0
                while workspace._extension_summary_worker is not None and time.monotonic() < deadline:
                    app.processEvents()
                    time.sleep(0.01)
            self.assertTrue(worker_thread_ids)
            self.assertTrue(all(thread_id != caller_thread_id for thread_id in worker_thread_ids))
            self.assertIsNone(workspace._extension_summary_worker)
        finally:
            workspace.deleteLater()
            app.processEvents()

    def test_extension_source_summary_refresh_discards_stale_results(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        paths = ensure_data_paths(str(TEST_ROOT / 'extension_summary_stale'), str(SAMPLE_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root), vector_backend='disabled')
        workspace = ConfigWorkspace(config=config, paths=paths, language_code='zh-CN', theme=theme)
        existing = {('pdf', 'current'): {'has_indexed_data': True}}
        try:
            workspace._extension_summary_request_id = 2
            workspace._extension_source_summaries = existing
            workspace._handle_extension_source_summaries_success({
                'request_id': 1,
                'state': None,
                'summaries': {('pdf', 'stale'): {'has_indexed_data': False}},
            })
            self.assertEqual(workspace._extension_source_summaries, existing)
        finally:
            workspace.deleteLater()
            app.processEvents()

    def test_extension_source_summary_refresh_preserves_pending_state_reload(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        paths = ensure_data_paths(str(TEST_ROOT / 'extension_summary_pending_reload'), str(SAMPLE_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root), vector_backend='disabled')
        workspace = ConfigWorkspace(config=config, paths=paths, language_code='zh-CN', theme=theme)
        try:
            workspace._extension_summary_worker = object()
            workspace._extension_summary_active_reload_state = True
            workspace._request_extension_source_summaries_refresh(config, paths, reload_state=False)
            self.assertTrue(workspace._extension_summary_refresh_pending)
            self.assertIsNotNone(workspace._extension_summary_pending_context)
            assert workspace._extension_summary_pending_context is not None
            self.assertTrue(workspace._extension_summary_pending_context[2])
        finally:
            workspace._extension_summary_worker = None
            workspace.deleteLater()
            app.processEvents()

    def test_extension_task_completion_requests_background_state_reload(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        paths = ensure_data_paths(str(TEST_ROOT / 'extension_task_reload'), str(SAMPLE_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root), vector_backend='disabled')
        workspace = ConfigWorkspace(config=config, paths=paths, language_code='zh-CN', theme=theme)
        try:
            with patch.object(
                workspace,
                '_load_extension_state',
                side_effect=AssertionError('task completion must not reload extension state on the UI thread'),
            ), patch.object(workspace, '_request_extension_state_reload') as request_reload:
                workspace._after_extension_source_task({
                    'pipeline': 'pdf',
                    'source_path': str(SAMPLE_ROOT),
                    'action': 'preflight',
                    'report': SimpleNamespace(cancelled=False, total_files=1, total_pages=1, recent_issues=()),
                })
            request_reload.assert_called_once_with()
        finally:
            workspace.deleteLater()
            app.processEvents()

    def test_config_workspace_saves_filters_and_ui_preferences(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        paths = ensure_data_paths(str(TEST_ROOT), str(SAMPLE_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root), vector_backend='lancedb')
        workspace = ConfigWorkspace(config=config, paths=paths, language_code='zh-CN', theme=theme)
        seen_preferences: list[tuple[str, int]] = []
        seen_tooltip_preferences: list[bool] = []
        workspace.uiPreferencesChanged.connect(lambda code, scale: seen_preferences.append((code, scale)))
        workspace.tooltipPreferencesChanged.connect(seen_tooltip_preferences.append)
        try:
            workspace._on_page_blocklist_saved('1\t^foo$')
            self.assertEqual(workspace._config.page_blocklist_rules, '1\t^foo$')
            workspace._on_sensitive_filters_saved(False, True, 'custom-rule')
            self.assertFalse(workspace._config.rag_filter_core_enabled)
            self.assertTrue(workspace._config.rag_filter_extended_enabled)
            self.assertEqual(workspace._config.rag_filter_custom_rules, 'custom-rule')
            workspace.ui_scale_spin.setValue(120)
            workspace.ui_theme_combo.setCurrentText(workspace._ui_theme_label('dark'))
            workspace.ui_tooltips_check.setChecked(False)
            workspace._apply_ui_preferences()
            self.assertEqual(seen_preferences[-1], ('dark', 120))
            self.assertEqual(seen_tooltip_preferences[-1], False)
            loaded = load_config(paths)
            self.assertIsNotNone(loaded)
            assert loaded is not None
            self.assertEqual(loaded.page_blocklist_rules, '1\t^foo$')
            self.assertFalse(loaded.rag_filter_core_enabled)
            self.assertTrue(loaded.rag_filter_extended_enabled)
            self.assertEqual(loaded.rag_filter_custom_rules, 'custom-rule')
            self.assertEqual(loaded.ui_theme, 'dark')
            self.assertEqual(loaded.ui_scale_percent, 120)
            self.assertFalse(loaded.ui_tooltips_enabled)
        finally:
            workspace.deleteLater()
            app.processEvents()

    def test_main_window_close_persists_qt_splitter_state(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        paths = ensure_data_paths(str(TEST_ROOT), str(SAMPLE_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root), vector_backend='lancedb')
        window = MainWindow(config=config, paths=paths, language_code='zh-CN', theme=theme, version='0.2.0')
        try:
            self.assertIsInstance(window.config_workspace, ConfigWorkspace)
            window.show()
            app.processEvents()
            window.query_workspace.query_splitter.setSizes([260, 700])
            window.query_workspace.results_splitter.setSizes([320, 380])
            window.query_workspace._toggle_search_controls_collapsed()
            window._toggle_header_collapsed()
            window.close()
            app.processEvents()
            loaded = load_config(paths)
            self.assertIsNotNone(loaded)
            assert loaded is not None
            self.assertTrue(loaded.qt_window_geometry)
            self.assertTrue(loaded.qt_query_splitter_state)
            self.assertTrue(loaded.qt_results_splitter_state)
            self.assertTrue(loaded.qt_query_controls_collapsed)
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
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root), vector_backend='lancedb')
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
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root), vector_backend='lancedb')
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
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root), vector_backend='lancedb')
        workspace = ConfigWorkspace(config=config, paths=paths, language_code='zh-CN', theme=theme)
        try:
            labels = [label.text().strip() for label in workspace.quick_steps_widget.findChildren(QtWidgets.QLabel)]
            self.assertEqual(len(labels), 4)
            self.assertTrue(all(labels))
            self.assertIn('数据目录', labels[0])
            self.assertIn('MD文件来源目录', labels[1])
            self.assertIn('主库', labels[1])
            self.assertIn('Markdown', labels[1])
            self.assertIn('Runtime', labels[2])
            self.assertIn('BGE', labels[2])
            self.assertIn('语义查询', labels[2])
            self.assertIn('活动日志', labels[3])
        finally:
            workspace.deleteLater()
            app.processEvents()

    def test_config_workspace_md_vault_table_uses_primary_and_scope_labels(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        paths = ensure_data_paths(str(TEST_ROOT), str(SAMPLE_ROOT))
        second_vault = TEST_ROOT / 'second-vault'
        second_vault.mkdir(parents=True, exist_ok=True)
        config = AppConfig(
            vault_path=str(SAMPLE_ROOT),
            data_root=str(paths.global_root),
            vault_paths=[str(SAMPLE_ROOT), str(second_vault)],
            md_selected_vault_paths=[str(SAMPLE_ROOT)],
        )
        workspace = ConfigWorkspace(config=config, paths=paths, language_code='zh-CN', theme=theme)
        try:
            self.assertEqual(workspace.md_vault_table.horizontalHeaderItem(0).text(), text('zh-CN', 'md_vault_col_current'))
            self.assertEqual(workspace.md_vault_table.horizontalHeaderItem(1).text(), text('zh-CN', 'md_vault_col_enabled'))
            self.assertTrue(workspace.md_vault_table.horizontalHeaderItem(0).toolTip())
            self.assertTrue(workspace.md_vault_table.horizontalHeaderItem(1).toolTip())
            self.assertEqual(workspace.md_vault_table.item(0, 0).toolTip(), tooltip('zh-CN', 'md_primary_vault'))
            self.assertEqual(workspace.md_vault_table.item(0, 1).toolTip(), tooltip('zh-CN', 'md_selected_scope'))
        finally:
            workspace.deleteLater()
            app.processEvents()

    def test_apply_application_style_updates_global_tooltip_property(self) -> None:
        app = get_app()
        theme = build_theme('dark', 110)
        from omniclip_rag.ui_next_qt.theme import apply_application_style

        apply_application_style(app, theme, tooltips_enabled=False)
        self.assertFalse(bool(app.property('_omniclip_tooltips_enabled')))
        self.assertIn('QToolTip', app.styleSheet())
        apply_application_style(app, theme, tooltips_enabled=True)
        self.assertTrue(bool(app.property('_omniclip_tooltips_enabled')))

    def test_query_workspace_migrates_key_tooltips(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        paths = ensure_data_paths(str(TEST_ROOT), str(SAMPLE_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root), vector_backend='lancedb')
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
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root), vector_backend='lancedb')
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
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root), vector_backend='lancedb')
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
                 patch.object(workspace, '_append_log') as log_mock:
                service_cls.return_value.status_snapshot.return_value = snapshot
                workspace._toggle_watch()
            log_mock.assert_any_call(
                text('zh-CN', 'md_watch_skip_not_ready', vault=Path(config.vault_path).name or config.vault_path)
            )
        finally:
            workspace.deleteLater()
            app.processEvents()

    def test_global_watch_stop_targets_every_actual_worker_and_is_idempotent(self) -> None:
        class FakeWatchWorker:
            def __init__(self) -> None:
                self.stop_calls = 0

            def stop(self) -> None:
                self.stop_calls += 1

        app = get_app()
        theme = build_theme('light', 100)
        vault_a = TEST_ROOT / 'watch_vault_a'
        vault_b = TEST_ROOT / 'watch_vault_b'
        vault_a.mkdir(parents=True, exist_ok=True)
        vault_b.mkdir(parents=True, exist_ok=True)
        paths = ensure_data_paths(str(TEST_ROOT / 'watch_data'), str(vault_a))
        config = AppConfig(
            vault_path=str(vault_a),
            vault_paths=[str(vault_a), str(vault_b)],
            md_selected_vault_paths=[str(vault_a)],
            data_root=str(paths.global_root),
        )
        workspace = ConfigWorkspace(config=config, paths=paths, language_code='zh-CN', theme=theme)
        normalized_a = str(vault_a.resolve())
        normalized_b = str(vault_b.resolve())
        worker_a = FakeWatchWorker()
        worker_b = FakeWatchWorker()
        try:
            workspace._md_watch_workers = {
                normalized_a: worker_a,
                normalized_b: worker_b,
            }
            workspace._refresh_markdown_watch_state()

            workspace._toggle_watch()
            workspace._toggle_watch()

            self.assertEqual(worker_a.stop_calls, 1)
            self.assertEqual(worker_b.stop_calls, 1)
            self.assertEqual(workspace._md_watch_stopping, {normalized_a, normalized_b})
            self.assertFalse(workspace.watch_button.isEnabled())
            self.assertEqual(workspace.watch_button.text(), text('zh-CN', 'status_watch_stopping'))
            with patch.object(workspace, '_append_log') as append_log:
                workspace._on_watch_updated_for_vault(
                    normalized_b,
                    {'stats': {}, 'changed': ['late.md'], 'deleted': []},
                )
            append_log.assert_not_called()

            actions = workspace._build_md_vault_actions_widget(normalized_b, workspace)
            stopping_buttons = [
                button
                for button in actions.findChildren(QtWidgets.QPushButton)
                if button.text() == text('zh-CN', 'md_vault_row_watch_stopping')
            ]
            self.assertEqual(len(stopping_buttons), 1)
            self.assertFalse(stopping_buttons[0].isEnabled())
        finally:
            workspace._md_watch_workers.clear()
            workspace._md_watch_stopping.clear()
            workspace.deleteLater()
            app.processEvents()

    def test_config_workspace_index_chip_reads_markdown_index_state_from_disk(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        paths = ensure_data_paths(str(TEST_ROOT), str(SAMPLE_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root), vector_backend='lancedb')
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
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root), vector_backend='lancedb')
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
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root), vector_backend='lancedb')
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

    def test_extension_global_progress_uses_busy_bar_for_single_file_pdf_inspection(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        paths = ensure_data_paths(str(TEST_ROOT), str(SAMPLE_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root), vector_backend='disabled')
        workspace = ConfigWorkspace(config=config, paths=paths, language_code='zh-CN', theme=theme)
        try:
            workspace._extension_progress_timer.stop()
            workspace._extension_task_worker = object()
            workspace._extension_global_progress = {
                'pipeline': 'pdf',
                'stage_status': 'inspect_pdf',
                'total': 1,
                'current': 0,
                'current_file': 'guide.pdf',
                'close_safe': True,
            }
            workspace._extension_progress_activity_key = 'inspect_pdf|guide.pdf|0|1'
            workspace._extension_progress_activity_started_at = time.monotonic() - 3.2
            workspace._refresh_extension_global_progress_ui()
            self.assertEqual(workspace.ext_global_progress_bar.maximum(), 0)
            self.assertIn(text('zh-CN', 'extensions_progress_stage_inspecting_pdf'), workspace.ext_global_progress_label.text())
            self.assertIn('正在处理', workspace.ext_global_progress_label.text())
            self.assertIn('guide.pdf', workspace.ext_global_progress_detail_label.text())
            self.assertIn('已用时', workspace.ext_global_progress_detail_label.text())
        finally:
            workspace.deleteLater()
            app.processEvents()

    def test_extension_global_progress_restores_determinate_bar_after_finalizing(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        paths = ensure_data_paths(str(TEST_ROOT), str(SAMPLE_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root), vector_backend='disabled')
        workspace = ConfigWorkspace(config=config, paths=paths, language_code='zh-CN', theme=theme)
        try:
            workspace._extension_progress_timer.stop()
            workspace._extension_task_worker = object()
            workspace._extension_global_progress = {
                'pipeline': 'pdf',
                'stage_status': 'inspect_pdf',
                'total': 1,
                'current': 0,
                'current_file': 'guide.pdf',
                'close_safe': True,
            }
            workspace._extension_progress_activity_key = 'inspect_pdf|guide.pdf|0|1'
            workspace._extension_progress_activity_started_at = time.monotonic() - 2.0
            workspace._refresh_extension_global_progress_ui()
            workspace._extension_global_progress = {
                'pipeline': 'pdf',
                'stage_status': 'finalizing',
                'overall_percent': 100.0,
                'total': 1,
                'current': 1,
                'close_safe': True,
            }
            workspace._refresh_extension_global_progress_ui()
            self.assertEqual(workspace.ext_global_progress_bar.maximum(), 100)
            self.assertEqual(workspace.ext_global_progress_bar.value(), 100)
        finally:
            workspace.deleteLater()
            app.processEvents()

    def test_extension_source_delete_removes_directory_from_registry_after_index_cleanup(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        paths = ensure_data_paths(str(TEST_ROOT / 'extension_delete_remove_source'), str(SAMPLE_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root), vector_backend='disabled')
        workspace = ConfigWorkspace(config=config, paths=paths, language_code='zh-CN', theme=theme)
        source_a = TEST_ROOT / 'extension_delete_remove_source' / 'pdf_a'
        source_b = TEST_ROOT / 'extension_delete_remove_source' / 'pdf_b'
        normalized_a = config_workspace_module.normalize_vault_path(str(source_a))
        normalized_b = config_workspace_module.normalize_vault_path(str(source_b))
        status_messages: list[str] = []
        workspace.statusMessageChanged.connect(status_messages.append)
        try:
            source_a.mkdir(parents=True, exist_ok=True)
            source_b.mkdir(parents=True, exist_ok=True)
            workspace._extension_state.pdf_config.enabled = True
            workspace._extension_state.pdf_config.source_directories = [
                ExtensionSourceDirectory(path=normalized_a, state=ExtensionDirectoryState.ENABLED, selected=True, source_label='pdf_a'),
                ExtensionSourceDirectory(path=normalized_b, state=ExtensionDirectoryState.ENABLED, selected=True, source_label='pdf_b'),
            ]
            workspace._extension_registry.save(paths, workspace._extension_state)
            workspace._extension_source_progress[('pdf', normalized_a)] = '正在执行…'
            workspace._extension_source_summaries[('pdf', normalized_a)] = {'has_indexed_data': True}
            workspace._extension_source_buttons[('pdf', normalized_a)] = []
            with patch.object(workspace, '_refresh_extension_source_summaries'):
                workspace._after_extension_source_task({
                    'pipeline': 'pdf',
                    'source_path': normalized_a,
                    'action': 'delete',
                    'report': SimpleNamespace(cancelled=False, deleted_files=4, indexed_files=2, recent_issues=()),
                })
            remaining = [config_workspace_module.normalize_vault_path(item.path) for item in workspace._extension_state.pdf_config.source_directories]
            saved_state = workspace._extension_registry.load(paths)
            saved_remaining = [config_workspace_module.normalize_vault_path(item.path) for item in saved_state.pdf_config.source_directories]
            self.assertNotIn(normalized_a, remaining)
            self.assertIn(normalized_b, remaining)
            self.assertNotIn(normalized_a, saved_remaining)
            self.assertIn(normalized_b, saved_remaining)
            self.assertNotIn(('pdf', normalized_a), workspace._extension_source_progress)
            self.assertNotIn(('pdf', normalized_a), workspace._extension_source_summaries)
            self.assertNotIn(('pdf', normalized_a), workspace._extension_source_buttons)
            self.assertTrue(any('来源目录已移除' in message for message in status_messages))
        finally:
            workspace.deleteLater()
            app.processEvents()

    def test_extension_source_delete_cancelled_keeps_directory_registered(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        paths = ensure_data_paths(str(TEST_ROOT / 'extension_delete_cancelled'), str(SAMPLE_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root), vector_backend='disabled')
        workspace = ConfigWorkspace(config=config, paths=paths, language_code='zh-CN', theme=theme)
        source_dir = TEST_ROOT / 'extension_delete_cancelled' / 'pdf_a'
        normalized = config_workspace_module.normalize_vault_path(str(source_dir))
        try:
            source_dir.mkdir(parents=True, exist_ok=True)
            workspace._extension_state.pdf_config.enabled = True
            workspace._extension_state.pdf_config.source_directories = [
                ExtensionSourceDirectory(path=normalized, state=ExtensionDirectoryState.ENABLED, selected=True, source_label='pdf_a'),
            ]
            workspace._extension_registry.save(paths, workspace._extension_state)
            with patch.object(workspace, '_refresh_extension_source_summaries'):
                workspace._after_extension_source_task({
                    'pipeline': 'pdf',
                    'source_path': normalized,
                    'action': 'delete',
                    'report': SimpleNamespace(cancelled=True, resume_available=False),
                })
            remaining = [config_workspace_module.normalize_vault_path(item.path) for item in workspace._extension_state.pdf_config.source_directories]
            self.assertIn(normalized, remaining)
        finally:
            workspace.deleteLater()
            app.processEvents()

    def test_config_workspace_auto_enables_semantic_backend_when_local_model_is_ready(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        paths = ensure_data_paths(str(TEST_ROOT / 'auto_enable_semantic_backend'), str(SAMPLE_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root), vector_backend='disabled', vector_model='BAAI/bge-m3')
        save_config(config, paths)
        workspace = ConfigWorkspace(config=config, paths=paths, language_code='zh-CN', theme=theme)
        status_messages: list[str] = []
        workspace.statusMessageChanged.connect(status_messages.append)
        try:
            workspace._acceleration_payload = {
                'runtime_complete': True,
                'torch_available': True,
                'sentence_transformers_available': True,
            }
            snapshot = {
                'stats': {'files': 3, 'chunks': 7, 'refs': 1},
                'index_state': 'ready',
                'index_ready': True,
                'watch_allowed': True,
                'query_allowed': True,
                'vector_table_ready': False,
            }
            with patch('omniclip_rag.ui_next_qt.config_workspace.is_local_model_ready', return_value=True), \
                 patch('omniclip_rag.ui_next_qt.config_workspace.runtime_dependency_issue', return_value=None):
                updated_config, updated_snapshot, auto_enabled = workspace._maybe_auto_enable_semantic_backend(config, paths, snapshot)
            self.assertTrue(auto_enabled)
            self.assertEqual(updated_config.vector_backend, 'lancedb')
            loaded = load_config(paths)
            self.assertIsNotNone(loaded)
            assert loaded is not None
            self.assertEqual(loaded.vector_backend, 'lancedb')
            self.assertEqual(workspace.backend_combo.currentText(), 'lancedb')
            self.assertIsNotNone(updated_snapshot)
            assert updated_snapshot is not None
            self.assertEqual(updated_snapshot.get('vector_backend'), 'lancedb')
            workspace._refresh_status_summary(updated_snapshot)
            self.assertEqual(workspace.index_chip.text(), text('zh-CN', 'index_ready_semantic_missing'))
            self.assertEqual(status_messages[-1], text('zh-CN', 'status_semantic_backend_auto_enabled_rebuild', model='BAAI/bge-m3'))
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

    def test_config_workspace_saves_watch_quiet_period_separately_from_polling(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        paths = ensure_data_paths(str(TEST_ROOT), str(SAMPLE_ROOT))
        config = AppConfig(
            vault_path=str(SAMPLE_ROOT),
            data_root=str(paths.global_root),
            poll_interval_seconds=2.0,
            watch_debounce_seconds=10.0,
        )
        workspace = ConfigWorkspace(config=config, paths=paths, language_code='zh-CN', theme=theme)
        try:
            workspace.interval_edit.setText('30')
            workspace._save_only()
            loaded = load_config(paths)
            self.assertIsNotNone(loaded)
            assert loaded is not None
            self.assertEqual(loaded.watch_debounce_seconds, 30.0)
            self.assertEqual(loaded.poll_interval_seconds, 2.0)
            self.assertEqual(text('zh-CN', 'interval_label'), '停止修改后等待（秒）')
        finally:
            workspace.deleteLater()
            app.processEvents()

    def test_config_workspace_ui_theme_choices_include_classic_themes(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        paths = ensure_data_paths(str(TEST_ROOT), str(SAMPLE_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root), ui_language='zh-CN')
        workspace = ConfigWorkspace(config=config, paths=paths, language_code='zh-CN', theme=theme)
        seen_preferences: list[tuple[str, int]] = []
        workspace.uiPreferencesChanged.connect(lambda code, scale: seen_preferences.append((code, scale)))
        try:
            labels = [workspace.ui_theme_combo.itemText(index) for index in range(workspace.ui_theme_combo.count())]
            for expected in ('暖纸色', '北境蓝灰', 'Solarized 浅色', 'Solarized 深色', '深石墨'):
                self.assertIn(expected, labels)
            workspace.ui_theme_combo.setCurrentText('暖纸色')
            workspace._apply_ui_preferences()
            self.assertEqual(seen_preferences[-1][0], 'sepia')
            loaded = load_config(paths)
            self.assertIsNotNone(loaded)
            assert loaded is not None
            self.assertEqual(loaded.ui_theme, 'sepia')
        finally:
            workspace.deleteLater()
            app.processEvents()

    def test_config_workspace_current_runtime_snapshot_ignores_unsaved_data_root_change(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        vault = SAMPLE_ROOT
        paths_a = ensure_data_paths(str(TEST_ROOT / 'data_a'), str(vault))
        ensure_data_paths(str(TEST_ROOT / 'data_b'), str(vault))
        config = AppConfig(vault_path=str(vault), data_root=str(paths_a.global_root))
        workspace = ConfigWorkspace(config=config, paths=paths_a, language_code='zh-CN', theme=theme)
        try:
            workspace.data_dir_edit.setText(str((TEST_ROOT / 'data_b').resolve()))
            live_config, live_paths = workspace.current_runtime_snapshot()
            self.assertEqual(str(live_paths.global_root), str(paths_a.global_root))
            self.assertEqual(live_config.data_root, str(paths_a.global_root))
        finally:
            workspace.deleteLater()
            app.processEvents()

    def test_config_workspace_current_runtime_snapshot_keeps_md_selected_vault_scope(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        vault_a = TEST_ROOT / 'runtime_scope_a'
        vault_b = TEST_ROOT / 'runtime_scope_b'
        vault_a.mkdir(parents=True, exist_ok=True)
        vault_b.mkdir(parents=True, exist_ok=True)
        paths = ensure_data_paths(str(TEST_ROOT / 'runtime_scope_root'), str(vault_a))
        config = AppConfig(
            vault_path=str(vault_a),
            data_root=str(paths.global_root),
            vault_paths=[str(vault_a), str(vault_b)],
            md_selected_vault_paths=[str(vault_a), str(vault_b)],
            vector_backend='disabled',
        )
        workspace = ConfigWorkspace(config=config, paths=paths, language_code='zh-CN', theme=theme)
        try:
            live_config, live_paths = workspace.current_runtime_snapshot()
            self.assertEqual(
                live_config.md_selected_vault_paths,
                [str(vault_a.resolve()), str(vault_b.resolve())],
            )
            self.assertEqual(str(live_paths.global_root), str(paths.global_root))
            item = workspace.md_vault_table.item(1, 1)
            self.assertIsNotNone(item)
            item.setCheckState(QtCore.Qt.CheckState.Unchecked)
            app.processEvents()
            live_config, _live_paths = workspace.current_runtime_snapshot()
            self.assertEqual(live_config.md_selected_vault_paths, [str(vault_a.resolve())])
        finally:
            workspace.deleteLater()
            app.processEvents()

    def test_config_workspace_pending_data_root_switch_blocks_query_and_saves_target_root_on_save(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        vault = SAMPLE_ROOT
        paths_a = ensure_data_paths(str(TEST_ROOT / 'save_pending_a'), str(vault))
        target_root = (TEST_ROOT / 'save_pending_b').resolve()
        config = AppConfig(vault_path=str(vault), data_root=str(paths_a.global_root))
        workspace = ConfigWorkspace(config=config, paths=paths_a, language_code='zh-CN', theme=theme)
        blocked_events: list[tuple[bool, str, str]] = []
        workspace.queryBlockStateChanged.connect(lambda blocked, title, detail: blocked_events.append((bool(blocked), str(title), str(detail))))
        try:
            workspace.data_dir_edit.setText(str(target_root))
            with patch('omniclip_rag.ui_next_qt.config_workspace.write_bootstrap_pointer') as pointer_mock, \
                 patch('omniclip_rag.ui_next_qt.config_workspace.resolve_and_validate_active_data_root') as resolve_mock, \
                 patch('omniclip_rag.ui_next_qt.config_workspace.ConfigWorkspace._confirm_pending_data_root_switch', return_value='continue'), \
                 patch('omniclip_rag.ui_next_qt.config_workspace.ConfigWorkspace._restart_for_data_root_switch', return_value=False) as restart_mock, \
                 patch('PySide6.QtWidgets.QMessageBox.question', return_value=QtWidgets.QMessageBox.StandardButton.Yes):
                resolve_mock.return_value = type('Resolved', (), {'path': target_root})()
                workspace._save_only()
            pointer_mock.assert_called_once()
            restart_mock.assert_called_once()
            self.assertEqual(str(workspace._paths.global_root), str(paths_a.global_root))
            saved = load_config(paths_a)
            self.assertIsNotNone(saved)
            assert saved is not None
            self.assertEqual(saved.data_root, str(paths_a.global_root))
            self.assertFalse((target_root / 'config.json').exists())
            workspace._emit_query_block_state()
            self.assertTrue(blocked_events)
            self.assertTrue(blocked_events[-1][0])
            self.assertIn(text('zh-CN', 'query_status_blocked_detail_data_root_switch'), blocked_events[-1][2])
        finally:
            workspace.deleteLater()
            app.processEvents()

    def test_config_workspace_loads_selected_data_root_as_preview_without_activating_runtime_paths(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        vault_a = TEST_ROOT / 'preview_vault_a'
        vault_b = TEST_ROOT / 'preview_vault_b'
        vault_a.mkdir(parents=True, exist_ok=True)
        vault_b.mkdir(parents=True, exist_ok=True)
        paths_a = ensure_data_paths(str(TEST_ROOT / 'preview_data_a'), str(vault_a))
        paths_b = ensure_data_paths(str(TEST_ROOT / 'preview_data_b'), str(vault_b))
        config_a = AppConfig(vault_path=str(vault_a), data_root=str(paths_a.global_root), vector_backend='disabled')
        config_b = AppConfig(vault_path=str(vault_b), data_root=str(paths_b.global_root), vector_backend='lancedb')
        save_config(config_a, paths_a)
        save_config(config_b, paths_b)
        workspace = ConfigWorkspace(config=config_a, paths=paths_a, language_code='zh-CN', theme=theme)
        try:
            workspace.data_dir_edit.setText(str(paths_b.global_root))
            workspace._load_config_from_current_dir()
            self.assertEqual(workspace.vault_edit.text(), str(vault_a.resolve()))
            self.assertEqual(str(workspace._paths.global_root), str(paths_a.global_root))
            live_config, live_paths = workspace.current_runtime_snapshot()
            self.assertEqual(str(live_paths.global_root), str(paths_a.global_root))
            self.assertEqual(live_config.data_root, str(paths_a.global_root))
        finally:
            workspace.deleteLater()
            app.processEvents()


    def test_config_workspace_runtime_refresh_uses_isolated_probe_worker(self) -> None:
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
            def __init__(self, *, probe_kind, data_root) -> None:
                self.probe_kind = probe_kind
                self.data_root = data_root
                self.succeeded = _Signal()
                self.failed = _Signal()
                self.cancelled = _Signal()
                self.finished = _Signal()
                self.started = False
            def start(self) -> None:
                self.started = True
        try:
            with patch('omniclip_rag.ui_next_qt.config_workspace.RuntimeProbeWorker', _FakeWorker):
                workspace._request_runtime_management_refresh()
            self.assertIsNotNone(workspace._runtime_refresh_worker)
            self.assertTrue(workspace._runtime_refresh_worker.started)
            self.assertEqual(workspace._runtime_refresh_worker.probe_kind, 'refresh')
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
                'profile': 'cuda',
            }), patch('omniclip_rag.ui_next_qt.config_workspace.runtime_component_usage', return_value={'disk_usage': '0 GB', 'download_usage': '0 GB'}):
                workspace._refresh_runtime_management_ui(force_refresh=True)
            self.assertEqual(workspace.runtime_chip.text(), text('zh-CN', 'runtime_chip_cpu_ready'))
            self.assertIn('GPU 加速这一项不需要安装', workspace.runtime_status_summary_label.text())
            self.assertEqual(workspace.runtime_components_table.rowCount(), 3)
            self.assertEqual(workspace.runtime_components_table.item(2, 2).text(), text('zh-CN', 'runtime_status_installed_unverified'))
        finally:
            workspace.deleteLater()
            app.processEvents()

    def test_config_workspace_runtime_chip_treats_deferred_probe_as_installed(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        paths = ensure_data_paths(str(TEST_ROOT), str(SAMPLE_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root))
        workspace = ConfigWorkspace(config=config, paths=paths, language_code='zh-CN', theme=theme)
        try:
            context = {
                'runtime_complete': True,
                'runtime_status': 'deferred',
                'torch_available': False,
                'torch_error': 'runtime probe deferred',
                'sentence_transformers_available': False,
                'sentence_transformers_error': 'runtime probe deferred',
                'gpu_present': False,
                'runtime_dir': str(paths.shared_root / 'runtime'),
                'runtime_exists': True,
                'runtime_missing_items': [],
            }
            with patch('omniclip_rag.ui_next_qt.config_workspace.runtime_component_status', side_effect=lambda component_id: {
                'component_id': component_id,
                'status': 'ready',
                'ready': True,
                'missing_items': [],
                'installed_count': 1,
                'total_count': 1,
                'cleanup_patterns': tuple(),
                'profile': 'cpu',
            }), patch('omniclip_rag.ui_next_qt.config_workspace.runtime_component_usage', return_value={'disk_usage': '0 GB', 'download_usage': '0 GB'}):
                workspace._refresh_runtime_management_ui(force_refresh=False, context=context)
            self.assertEqual(workspace.runtime_chip.text(), text('zh-CN', 'runtime_chip_installed_unverified'))
            self.assertIn('文件结构完整', workspace.runtime_status_summary_label.text())
            self.assertNotIn('未安装', workspace.runtime_status_summary_label.text())
            self.assertNotIn('runtime probe deferred', workspace.runtime_status_summary_label.text())
            self.assertEqual(
                workspace.runtime_components_table.item(0, 2).text(),
                text('zh-CN', 'runtime_status_installed_unverified'),
            )
            self.assertEqual(
                workspace.runtime_components_table.item(0, 3).text(),
                text('zh-CN', 'runtime_missing_probe_deferred'),
            )
        finally:
            workspace.deleteLater()
            app.processEvents()

    def test_config_workspace_tika_runtime_install_progress_updates_inline_widgets(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        paths = ensure_data_paths(str(TEST_ROOT), str(SAMPLE_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root))
        workspace = ConfigWorkspace(config=config, paths=paths, language_code='zh-CN', theme=theme)
        try:
            workspace._handle_tika_runtime_install_progress(
                {
                    'stage': 'download_jar',
                    'downloaded': 50,
                    'total': 100,
                    'detail': 'Downloading tika-server-standard-3.2.3.jar',
                }
            )
            runtime = workspace._extension_state.snapshot.tika.runtime
            workspace._refresh_tika_runtime_progress_ui(runtime)
            self.assertFalse(workspace.ext_tika_runtime_progress_label.isHidden())
            self.assertIn('下载 Tika Server', workspace.ext_tika_runtime_progress_label.text())
            self.assertEqual(workspace.ext_tika_runtime_progress_bar.maximum(), 100)
            self.assertEqual(workspace.ext_tika_runtime_progress_bar.value(), 50)
            self.assertIn('tika-server-standard-3.2.3.jar', workspace.ext_tika_runtime_progress_detail_label.text())
        finally:
            workspace.deleteLater()
            app.processEvents()

    def test_install_tika_runtime_requested_uses_progress_worker_path(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        paths = ensure_data_paths(str(TEST_ROOT), str(SAMPLE_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root))
        workspace = ConfigWorkspace(config=config, paths=paths, language_code='zh-CN', theme=theme)
        try:
            with patch.object(workspace, '_start_tika_runtime_progress_worker', return_value=True) as progress_mock, \
                 patch.object(workspace, '_start_tika_runtime_worker') as basic_mock:
                workspace._install_tika_runtime_requested()
            progress_mock.assert_called_once()
            basic_mock.assert_not_called()
        finally:
            workspace.deleteLater()
            app.processEvents()

    def test_config_workspace_gpu_runtime_row_requires_execution_verification(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        paths = ensure_data_paths(str(TEST_ROOT), str(SAMPLE_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root))
        workspace = ConfigWorkspace(config=config, paths=paths, language_code='zh-CN', theme=theme)
        try:
            with patch.object(workspace, '_current_runtime_repair_context', return_value={
                'gpu_present': True,
                'torch_available': True,
                'sentence_transformers_available': True,
                'cuda_available': True,
                'gpu_probe_state': 'verified',
                'gpu_probe_verified': True,
                'gpu_probe_reason': '',
                'gpu_probe_error_message': '',
                'gpu_execution_state': 'not-run',
                'gpu_execution_verified': False,
                'gpu_execution_reason': '',
                'gpu_execution_error_message': '',
            }), patch('omniclip_rag.ui_next_qt.config_workspace.runtime_component_status', return_value={
                'component_id': 'semantic-core',
                'status': 'ready',
                'ready': True,
                'missing_items': [],
                'installed_count': 1,
                'total_count': 1,
                'cleanup_patterns': tuple(),
                'profile': 'cuda',
            }), patch('omniclip_rag.ui_next_qt.config_workspace.runtime_component_usage', return_value={'disk_usage': '0 GB', 'download_usage': '0 GB'}):
                state = workspace._runtime_component_state('gpu-acceleration')
            self.assertFalse(state['ready'])
            self.assertEqual(state['install_state'], 'ready')
            self.assertEqual(state['probe_state'], 'ready')
            self.assertEqual(state['execution_state'], 'not-run')
            self.assertIn(text('zh-CN', 'runtime_missing_gpu_execution_unverified'), state['missing_items'])
        finally:
            workspace.deleteLater()
            app.processEvents()

    def test_config_workspace_gpu_runtime_row_turns_ready_only_after_execution_verification(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        paths = ensure_data_paths(str(TEST_ROOT), str(SAMPLE_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root))
        workspace = ConfigWorkspace(config=config, paths=paths, language_code='zh-CN', theme=theme)
        try:
            with patch.object(workspace, '_current_runtime_repair_context', return_value={
                'gpu_present': True,
                'torch_available': True,
                'sentence_transformers_available': True,
                'cuda_available': True,
                'gpu_probe_state': 'verified',
                'gpu_probe_verified': True,
                'gpu_probe_reason': '',
                'gpu_probe_error_message': '',
                'gpu_execution_state': 'verified',
                'gpu_execution_verified': True,
                'gpu_execution_reason': '',
                'gpu_execution_error_message': '',
                'gpu_execution_actual_device': 'cuda:0',
            }), patch('omniclip_rag.ui_next_qt.config_workspace.runtime_component_status', return_value={
                'component_id': 'semantic-core',
                'status': 'ready',
                'ready': True,
                'missing_items': [],
                'installed_count': 1,
                'total_count': 1,
                'cleanup_patterns': tuple(),
                'profile': 'cuda',
            }), patch('omniclip_rag.ui_next_qt.config_workspace.runtime_component_usage', return_value={'disk_usage': '0 GB', 'download_usage': '0 GB'}):
                state = workspace._runtime_component_state('gpu-acceleration')
            self.assertTrue(state['ready'])
            self.assertEqual(state['status'], 'ready')
            self.assertEqual(state['install_state'], 'ready')
            self.assertEqual(state['probe_state'], 'ready')
            self.assertEqual(state['execution_state'], 'verified')
            self.assertEqual(state['missing_items'], [])
        finally:
            workspace.deleteLater()
            app.processEvents()

    def test_config_workspace_gpu_runtime_row_accepts_cuda_build_even_if_registry_profile_cpu(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        paths = ensure_data_paths(str(TEST_ROOT), str(SAMPLE_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root))
        workspace = ConfigWorkspace(config=config, paths=paths, language_code='zh-CN', theme=theme)
        runtime_dir = Path(paths.shared_root) / 'runtime'
        runtime_state = {
            'runtime_dir': runtime_dir,
            'runtime_exists': True,
            'runtime_has_content': True,
            'runtime_pending': False,
            'runtime_pending_components': [],
            'runtime_complete': True,
            'runtime_missing_items': [],
            'preferred_runtime_dir': runtime_dir,
        }
        # Simulate an environment where the actual torch build supports CUDA, but the
        # runtime registry "profile" is stale and still reports cpu.
        workspace._acceleration_payload = {
            'gpu_present': True,
            'gpu_name': 'NVIDIA RTX',
            'cuda_available': True,
            'torch_available': True,
            'torch_version': '2.10.0+cu128',
            'torch_cuda_build': '12.8',
            'sentence_transformers_available': True,
            'gpu_probe_state': 'verified',
            'gpu_probe_verified': True,
            'gpu_probe_reason': '',
            'gpu_probe_error_message': '',
            'gpu_execution_state': 'verified',
            'gpu_execution_verified': True,
            'gpu_execution_reason': '',
            'gpu_execution_error_message': '',
            'gpu_execution_actual_device': 'cuda:0',
            'gpu_execution_reranker_actual_device': 'cuda:0',
        }
        try:
            with patch('omniclip_rag.vector_index.inspect_runtime_environment', return_value=runtime_state), \
                 patch('omniclip_rag.vector_index.resolve_vector_device', return_value='cuda'), \
                 patch('omniclip_rag.ui_next_qt.config_workspace.runtime_component_status', return_value={
                     'component_id': 'semantic-core',
                     'status': 'ready',
                     'ready': True,
                     'missing_items': [],
                     'installed_count': 1,
                     'total_count': 1,
                     'cleanup_patterns': tuple(),
                     'profile': 'cpu',
                 }), patch('omniclip_rag.ui_next_qt.config_workspace.runtime_component_usage', return_value={'disk_usage': '0 GB', 'download_usage': '0 GB'}):
                context = workspace._current_runtime_repair_context(force_refresh=False)
                self.assertEqual(context.get('torch_cuda_build'), '12.8')
                self.assertEqual(context.get('gpu_execution_state'), 'verified')
                state = workspace._runtime_component_state('gpu-acceleration', context=context)
            self.assertTrue(state['ready'])
            self.assertEqual(state['status'], 'ready')
            self.assertEqual(state['missing_items'], [])
        finally:
            workspace.deleteLater()
            app.processEvents()

    def test_config_workspace_gpu_runtime_row_rejects_cpu_profile_semantic_core(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        paths = ensure_data_paths(str(TEST_ROOT), str(SAMPLE_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root))
        workspace = ConfigWorkspace(config=config, paths=paths, language_code='zh-CN', theme=theme)
        try:
            with patch.object(workspace, '_current_runtime_repair_context', return_value={
                'gpu_present': True,
                'torch_available': True,
                'sentence_transformers_available': True,
                'cuda_available': True,
                'gpu_probe_state': 'verified',
                'gpu_probe_verified': True,
                'gpu_probe_reason': '',
                'gpu_probe_error_message': '',
                'gpu_execution_state': 'verified',
                'gpu_execution_verified': True,
                'gpu_execution_reason': '',
                'gpu_execution_error_message': '',
                'gpu_execution_actual_device': 'cuda:0',
                'torch_cuda_build': '',
            }), patch('omniclip_rag.ui_next_qt.config_workspace.runtime_component_status', return_value={
                'component_id': 'semantic-core',
                'status': 'ready',
                'ready': True,
                'missing_items': [],
                'installed_count': 1,
                'total_count': 1,
                'cleanup_patterns': tuple(),
                'profile': 'cpu',
            }), patch('omniclip_rag.ui_next_qt.config_workspace.runtime_component_usage', return_value={'disk_usage': '0 GB', 'download_usage': '0 GB'}):
                state = workspace._runtime_component_state('gpu-acceleration')
            self.assertFalse(state['ready'])
            self.assertIn(text('zh-CN', 'runtime_missing_gpu_semantic_profile_cpu'), state['missing_items'])
        finally:
            workspace.deleteLater()
            app.processEvents()

    def test_config_workspace_runtime_refresh_reuses_one_context_snapshot(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        paths = ensure_data_paths(str(TEST_ROOT), str(SAMPLE_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root))
        workspace = ConfigWorkspace(config=config, paths=paths, language_code='zh-CN', theme=theme)
        context_payload = {
            'runtime_dir': str(paths.shared_root / 'runtime'),
            'runtime_exists': True,
            'runtime_complete': False,
            'runtime_pending_components': [],
            'gpu_present': True,
            'torch_available': True,
            'sentence_transformers_available': True,
            'cuda_available': True,
                'gpu_probe_state': 'verified',
                'gpu_probe_verified': True,
                'gpu_probe_reason': '',
                'gpu_probe_error_message': '',
                'gpu_execution_state': 'not-run',
            'gpu_execution_verified': False,
            'gpu_execution_reason': '',
            'gpu_execution_error_message': '',
        }
        semantic_state = {
            'component_id': 'semantic-core',
            'status': 'ready',
            'ready': True,
            'missing_items': [],
            'installed_count': 1,
            'total_count': 1,
            'cleanup_patterns': tuple(),
            'profile': 'cuda',
        }
        vector_state = {
            'component_id': 'vector-store',
            'status': 'ready',
            'ready': True,
            'missing_items': [],
            'installed_count': 1,
            'total_count': 1,
            'cleanup_patterns': tuple(),
            'profile': 'cuda',
        }
        try:
            with patch.object(workspace, '_current_runtime_repair_context', return_value=context_payload) as context_mock, patch(
                'omniclip_rag.ui_next_qt.config_workspace.runtime_component_status',
                side_effect=lambda component_id: semantic_state if component_id == 'semantic-core' else vector_state,
            ), patch(
                'omniclip_rag.ui_next_qt.config_workspace.runtime_component_usage',
                return_value={'disk_usage': '0 GB', 'download_usage': '0 GB'},
            ):
                workspace._refresh_runtime_management_ui(force_refresh=True)
            self.assertEqual(context_mock.call_count, 1)
        finally:
            workspace.deleteLater()
            app.processEvents()

    def test_config_workspace_gpu_runtime_actions_show_verify_button(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        paths = ensure_data_paths(str(TEST_ROOT), str(SAMPLE_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root))
        workspace = ConfigWorkspace(config=config, paths=paths, language_code='zh-CN', theme=theme)
        try:
            state = {
                'component_id': 'gpu-acceleration',
                'status': 'incomplete',
                'ready': False,
                'missing_items': [text('zh-CN', 'runtime_missing_gpu_execution_unverified')],
            }
            widget = workspace._build_runtime_component_actions_widget(
                {
                    'component_id': 'gpu-acceleration',
                    'name': text('zh-CN', 'runtime_component_gpu_acceleration'),
                    'description': text('zh-CN', 'runtime_component_gpu_acceleration_desc'),
                },
                state,
                workspace,
            )
            labels = {button.text() for button in widget.findChildren(QtWidgets.QPushButton)}
            self.assertIn(text('zh-CN', 'runtime_row_verify'), labels)
            self.assertNotIn(text('zh-CN', 'runtime_row_refresh'), labels)
        finally:
            workspace.deleteLater()
            app.processEvents()

    def test_config_workspace_gpu_runtime_actions_allow_manual_repair_without_gpu(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        paths = ensure_data_paths(str(TEST_ROOT), str(SAMPLE_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root))
        workspace = ConfigWorkspace(config=config, paths=paths, language_code='zh-CN', theme=theme)
        try:
            state = {
                'component_id': 'gpu-acceleration',
                'status': 'not-needed',
                'ready': False,
                'missing_items': [text('zh-CN', 'runtime_missing_not_needed_gpu')],
                'installed_count': 0,
            }
            widget = workspace._build_runtime_component_actions_widget(
                {
                    'component_id': 'gpu-acceleration',
                    'name': text('zh-CN', 'runtime_component_gpu_acceleration'),
                    'description': text('zh-CN', 'runtime_component_gpu_acceleration_desc'),
                },
                state,
                workspace,
            )
            buttons = {button.text(): button for button in widget.findChildren(QtWidgets.QPushButton)}
            self.assertIn(text('zh-CN', 'runtime_row_repair'), buttons)
            self.assertTrue(buttons[text('zh-CN', 'runtime_row_repair')].isEnabled())
            self.assertIn(text('zh-CN', 'runtime_row_verify'), buttons)
            self.assertTrue(buttons[text('zh-CN', 'runtime_row_verify')].isEnabled())
            self.assertIn(text('zh-CN', 'runtime_row_clear'), buttons)
            self.assertFalse(buttons[text('zh-CN', 'runtime_row_clear')].isEnabled())
        finally:
            workspace.deleteLater()
            app.processEvents()

    def test_config_workspace_no_gpu_cuda_payload_reports_installed_unverified(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        paths = ensure_data_paths(str(TEST_ROOT), str(SAMPLE_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root))
        workspace = ConfigWorkspace(config=config, paths=paths, language_code='zh-CN', theme=theme)
        try:
            with patch.object(workspace, '_current_runtime_repair_context', return_value={
                'gpu_present': False,
                'runtime_dir': str(paths.shared_root / 'runtime'),
                'runtime_exists': True,
            }), patch(
                'omniclip_rag.ui_next_qt.config_workspace.runtime_component_status',
                return_value={
                    'component_id': 'semantic-core',
                    'status': 'ready',
                    'ready': True,
                    'missing_items': [],
                    'installed_count': 1,
                    'total_count': 1,
                    'cleanup_patterns': ('torch',),
                    'profile': 'cuda',
                },
            ), patch(
                'omniclip_rag.ui_next_qt.config_workspace.runtime_component_usage',
                return_value={'disk_usage': '1.2 GB', 'download_usage': '900 MB'},
            ):
                state = workspace._runtime_component_state('gpu-acceleration')
            self.assertEqual(state['status'], 'installed-unverified')
            self.assertFalse(state['ready'])
            self.assertEqual(state['installed_count'], 1)
            self.assertIn(text('zh-CN', 'runtime_missing_gpu_no_hardware_installed'), state['missing_items'])
        finally:
            workspace.deleteLater()
            app.processEvents()

    def test_config_workspace_runtime_gpu_verify_without_gpu_shows_info_message(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        paths = ensure_data_paths(str(TEST_ROOT), str(SAMPLE_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root))
        workspace = ConfigWorkspace(config=config, paths=paths, language_code='zh-CN', theme=theme)
        try:
            with patch.object(workspace, '_current_runtime_repair_context', return_value={'gpu_present': False}), patch(
                'omniclip_rag.ui_next_qt.config_workspace.QtWidgets.QMessageBox.information'
            ) as info_mock:
                workspace._request_runtime_gpu_verification()
            info_mock.assert_called_once()
            self.assertIsNone(workspace._runtime_verify_worker)
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
        runtime_root = TEST_ROOT / 'runtime_auto_repair_smoke' / 'custom_runtime'
        install_script.parent.mkdir(parents=True, exist_ok=True)
        install_script.write_text('Write-Host runtime smoke', encoding='utf-8')
        try:
            with patch.object(workspace, '_current_runtime_repair_context', return_value={
                'install_script': str(install_script),
                'recommended_profile': 'cuda',
                'app_dir': str(install_script.parent),
                'preferred_runtime_dir': str(runtime_root),
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
            self.assertIn('Caelune', command)
            self.assertIn('-Component', command)
            self.assertIn('semantic-core', command)
            self.assertIn('-DiagnosticsPath', command)
            self.assertIn('-ResultPath', command)
            self.assertEqual(popen_mock.call_args.kwargs['cwd'], str(install_script.parent))
            self.assertEqual(popen_mock.call_args.kwargs['env']['CAELUNE_RUNTIME_ROOT'], str(runtime_root))
            self.assertFalse(workspace.runtime_install_progress_label.isHidden())
            self.assertIn(text('zh-CN', 'runtime_component_semantic_core'), workspace.runtime_install_progress_label.text())
        finally:
            workspace.deleteLater()
            app.processEvents()

    def test_config_workspace_runtime_install_progress_monitor_updates_labels(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        paths = ensure_data_paths(str(TEST_ROOT), str(SAMPLE_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root))
        workspace = ConfigWorkspace(config=config, paths=paths, language_code='zh-CN', theme=theme)
        try:
            diagnostics_path = TEST_ROOT / 'runtime_progress' / 'diag.json'
            result_path = TEST_ROOT / 'runtime_progress' / 'result.json'
            diagnostics_path.parent.mkdir(parents=True, exist_ok=True)
            diagnostics_path.write_text(json.dumps({
                'current_stage': 'download',
                'current_artifact': 'torch-2.10.0+cpu-cp313-cp313-win_amd64.whl',
                'artifacts_total': 4,
                'artifacts_downloaded': 2,
                'artifacts_verified': 1,
            }, ensure_ascii=False), encoding='utf-8')
            workspace._runtime_install_component_label = text('zh-CN', 'runtime_component_semantic_core')
            workspace._runtime_install_started_at = time.time() - 3
            workspace._runtime_install_diagnostics_path = diagnostics_path
            workspace._runtime_install_result_path = result_path
            workspace._poll_runtime_install_progress()
            self.assertFalse(workspace.runtime_install_progress_label.isHidden())
            self.assertIn('已下载 2/4', workspace.runtime_install_progress_label.text())
            self.assertIn('torch-2.10.0+cpu', workspace.runtime_install_progress_detail_label.text())
        finally:
            workspace.deleteLater()
            app.processEvents()

    def test_config_workspace_rebuild_merges_returned_stats_into_status_summary(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        paths = ensure_data_paths(str(TEST_ROOT), str(SAMPLE_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root), vector_backend='lancedb')
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
            self.assertEqual(result, ('manual', None))
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

    def test_config_workspace_auto_model_download_prompts_for_source_and_returns_selection(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        paths = ensure_data_paths(str(TEST_ROOT), str(SAMPLE_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root), vector_model='BAAI/bge-m3')
        workspace = ConfigWorkspace(config=config, paths=paths, language_code='zh-CN', theme=theme)
        try:
            with patch.object(workspace, '_ask_yes_no_cancel', return_value=QtWidgets.QMessageBox.StandardButton.Yes), \
                 patch.object(workspace, '_ask_model_download_source', return_value='mirror'):
                result = workspace._choose_model_download_mode('下载模型', config, paths)
            self.assertEqual(result, ('auto', 'mirror'))
        finally:
            workspace.deleteLater()
            app.processEvents()

    def test_config_workspace_manual_reranker_download_opens_copyable_dialog(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        paths = ensure_data_paths(str(TEST_ROOT / 'manual_reranker_dialog'), str(SAMPLE_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root), reranker_enabled=True)
        workspace = ConfigWorkspace(config=config, paths=paths, language_code='zh-CN', theme=theme)
        try:
            with patch.object(workspace, '_ask_yes_no_cancel', return_value=QtWidgets.QMessageBox.StandardButton.No), \
                 patch('omniclip_rag.ui_next_qt.config_workspace.ModelDownloadDialog') as dialog_cls:
                dialog_instance = dialog_cls.return_value
                result = workspace._choose_reranker_download_mode('下载重排模型', config, paths)
            self.assertEqual(result, ('manual', None))
            dialog_cls.assert_called_once()
            kwargs = dialog_cls.call_args.kwargs
            self.assertIn('hf download', kwargs['context']['official_download_command'])
            self.assertIn('https://huggingface.co/', kwargs['context']['official_url'])
            self.assertIn('https://hf-mirror.com/', kwargs['context']['mirror_url'])
            self.assertIn(str(get_local_reranker_dir(config, paths)), kwargs['context']['plain_text'])
            dialog_instance.exec.assert_called_once()
        finally:
            workspace.deleteLater()
            app.processEvents()

    def test_config_workspace_auto_reranker_download_prompts_for_source_and_returns_selection(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        paths = ensure_data_paths(str(TEST_ROOT / 'auto_reranker_source'), str(SAMPLE_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root), reranker_enabled=True)
        workspace = ConfigWorkspace(config=config, paths=paths, language_code='zh-CN', theme=theme)
        try:
            with patch.object(workspace, '_ask_yes_no_cancel', return_value=QtWidgets.QMessageBox.StandardButton.Yes), \
                 patch.object(workspace, '_ask_reranker_download_source', return_value='mirror'):
                result = workspace._choose_reranker_download_mode('下载重排模型', config, paths)
            self.assertEqual(result, ('auto', 'mirror'))
        finally:
            workspace.deleteLater()
            app.processEvents()

    def test_config_workspace_runtime_manual_context_uses_explicit_runtime_root(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        paths = ensure_data_paths(str(TEST_ROOT / 'runtime_manual_context_data'), str(SAMPLE_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root))
        workspace = ConfigWorkspace(config=config, paths=paths, language_code='zh-CN', theme=theme)
        runtime_root = Path(paths.shared_root) / 'runtime'
        try:
            with patch.object(workspace, '_current_runtime_repair_context', return_value={
                'preferred_runtime_dir': str(runtime_root),
                'runtime_dir': str(runtime_root),
                'disk_usage': '1 GB',
                'download_usage': '512 MB',
                'recommended_profile': 'cpu',
            }):
                context = workspace._runtime_manual_context('semantic-core')
            self.assertEqual(context['runtime_dir'], str(runtime_root))
            self.assertIn('CAELUNE_RUNTIME_ROOT', context['official_install_command'])
            self.assertIn(str(runtime_root), context['official_install_command'])
            self.assertIn('CAELUNE_RUNTIME_ROOT', context['mirror_install_command'])
        finally:
            workspace.deleteLater()
            app.processEvents()

    def test_config_workspace_launch_download_log_terminal_runs_worker_in_powershell(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        paths = ensure_data_paths(str(TEST_ROOT / 'download_terminal_launch'), str(SAMPLE_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root))
        workspace = ConfigWorkspace(config=config, paths=paths, language_code='zh-CN', theme=theme)
        log_path = paths.logs_dir / 'downloads' / 'model.log'
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text('', encoding='utf-8')
        try:
            worker_command = ['python.exe', '-m', 'omniclip_rag.app_entry.desktop', '--download-worker']
            with patch.object(workspace, '_powershell_executable', return_value='powershell.exe'), \
                 patch('omniclip_rag.ui_next_qt.config_workspace.subprocess.Popen') as popen_mock:
                workspace._launch_download_log_terminal(
                    log_path=log_path,
                    title='Caelune - test',
                    worker_command=worker_command,
                )
            popen_mock.assert_called_once()
            command = popen_mock.call_args.args[0]
            self.assertEqual(command[0], 'powershell.exe')
            self.assertIn('-NoExit', command)
            self.assertIn('-Command', command)
            self.assertIn('omniclip_rag.app_entry.desktop', command[-1])
            self.assertIn('--download-worker', command[-1])
        finally:
            workspace.deleteLater()
            app.processEvents()

    def test_config_workspace_run_bootstrap_model_passes_worker_download_metadata(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        paths = ensure_data_paths(str(TEST_ROOT / 'bootstrap_model_source'), str(SAMPLE_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root), vector_model='BAAI/bge-m3')
        workspace = ConfigWorkspace(config=config, paths=paths, language_code='zh-CN', theme=theme)
        try:
            with patch.object(workspace, '_collect_config', return_value=(config, paths)), \
                 patch('omniclip_rag.ui_next_qt.config_workspace.is_local_model_ready', return_value=False), \
                 patch.object(workspace, '_create_download_log_file', return_value=paths.logs_dir / 'downloads' / 'model.log'), \
                 patch.object(workspace, '_start_download_task') as start_mock:
                workspace._run_bootstrap_model(download_source='mirror', followup=lambda: None)
            kwargs = start_mock.call_args.kwargs
            self.assertEqual(kwargs['download_kind'], 'vector')
            self.assertEqual(kwargs['repo_id'], config.vector_model)
            self.assertEqual(kwargs['download_source'], 'mirror')
            self.assertEqual(kwargs['target_dir'], get_local_model_dir(config, paths))
            self.assertEqual(kwargs['hf_home_dir'], paths.cache_dir / 'models' / '_hf_home')
        finally:
            workspace.deleteLater()
            app.processEvents()

    def test_config_workspace_run_bootstrap_reranker_passes_worker_download_metadata(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        paths = ensure_data_paths(str(TEST_ROOT / 'bootstrap_reranker_source'), str(SAMPLE_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root), reranker_enabled=True)
        workspace = ConfigWorkspace(config=config, paths=paths, language_code='zh-CN', theme=theme)
        try:
            with patch.object(workspace, '_collect_config', return_value=(config, paths)), \
                 patch('omniclip_rag.ui_next_qt.config_workspace.is_local_reranker_ready', return_value=False), \
                 patch.object(workspace, '_choose_reranker_download_mode', return_value=('auto', 'mirror')), \
                 patch.object(workspace, '_create_download_log_file', return_value=paths.logs_dir / 'downloads' / 'reranker.log'), \
                 patch.object(workspace, '_start_download_task') as start_mock:
                workspace._run_bootstrap_reranker()
            kwargs = start_mock.call_args.kwargs
            self.assertEqual(kwargs['download_kind'], 'reranker')
            self.assertEqual(kwargs['repo_id'], config.reranker_model)
            self.assertEqual(kwargs['download_source'], 'mirror')
            self.assertEqual(kwargs['target_dir'], get_local_reranker_dir(config, paths))
            self.assertEqual(kwargs['hf_home_dir'], paths.cache_dir / 'models' / '_hf_home')
        finally:
            workspace.deleteLater()
            app.processEvents()

    def test_config_workspace_download_supervisor_switches_to_official_after_stall(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        paths = ensure_data_paths(str(TEST_ROOT / 'download_supervisor_stall'), str(SAMPLE_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root), vector_model='BAAI/bge-m3')
        workspace = ConfigWorkspace(config=config, paths=paths, language_code='zh-CN', theme=theme)
        log_path = paths.logs_dir / 'downloads' / 'model.log'
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text('', encoding='utf-8')
        target_dir = get_local_model_dir(config, paths)
        repo_cache_dir = hf_repo_cache_dir(paths.cache_dir / 'models' / '_hf_home', config.vector_model)
        now = time.monotonic()
        try:
            log_path.write_text('[2026-03-23 15:40:00] 下载心跳：已用时 01:35；最近 5 秒暂无新增文件或字节。\n', encoding='utf-8')
            workspace._download_task_state = config_workspace_module._DownloadTaskState(
                label_key='bootstrap_button',
                kind='vector',
                repo_id=config.vector_model,
                config=config,
                paths=paths,
                target_dir=target_dir,
                hf_home_dir=paths.cache_dir / 'models' / '_hf_home',
                repo_cache_dir=repo_cache_dir,
                requested_source='mirror',
                active_source='mirror',
                log_path=log_path,
                pid_path=log_path.with_suffix('.pid'),
                result_path=log_path.with_suffix('.result.json'),
                terminal_title='Caelune - test',
                start_message='start',
                local_files_only=False,
                on_success=lambda _payload: None,
                on_failure=lambda *_args: None,
                success_payload_builder=lambda: {},
                worker_pid=123,
                log_offset=0,
                last_log_size=0,
                last_progress_at=now - 95.0,
                last_material_progress_at=now - 95.0,
                last_sample_at=now - 6.0,
                started_at=now - 95.0,
            )
            with patch.object(workspace, '_is_download_worker_alive', return_value=True), \
                 patch.object(workspace, '_terminate_download_worker') as terminate_mock, \
                 patch.object(workspace, '_launch_download_worker_for_state', return_value=True) as launch_mock:
                workspace._poll_download_task()
            state = workspace._download_task_state
            self.assertIsNotNone(state)
            self.assertEqual(state.active_source, 'official')
            self.assertTrue(state.source_switched)
            terminate_mock.assert_called_once()
            launch_mock.assert_called_once_with(state)
        finally:
            workspace.deleteLater()
            app.processEvents()

    def test_config_workspace_delete_buttons_remove_only_selected_model_directories(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        paths = ensure_data_paths(str(TEST_ROOT / 'delete_model_dirs'), str(SAMPLE_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root), reranker_enabled=True)
        workspace = ConfigWorkspace(config=config, paths=paths, language_code='zh-CN', theme=theme)
        model_dir = get_local_model_dir(config, paths)
        reranker_dir = get_local_reranker_dir(config, paths)
        model_cache_dir = hf_repo_cache_dir(paths.cache_dir / 'models' / '_hf_home', config.vector_model)
        reranker_cache_dir = get_local_reranker_repo_cache_dir(config, paths)
        model_dir.mkdir(parents=True, exist_ok=True)
        reranker_dir.mkdir(parents=True, exist_ok=True)
        model_cache_dir.mkdir(parents=True, exist_ok=True)
        reranker_cache_dir.mkdir(parents=True, exist_ok=True)
        try:
            with patch('PySide6.QtWidgets.QMessageBox.question', return_value=QtWidgets.QMessageBox.StandardButton.Yes), \
                 patch('omniclip_rag.ui_next_qt.config_workspace.release_process_vector_resources'), \
                 patch('omniclip_rag.ui_next_qt.config_workspace.release_process_reranker_resources'):
                workspace._delete_local_model()
                workspace._delete_local_reranker()
            self.assertFalse(model_dir.exists())
            self.assertFalse(reranker_dir.exists())
            self.assertFalse(model_cache_dir.exists())
            self.assertFalse(reranker_cache_dir.exists())
            self.assertIn(config.vector_model, workspace.delete_model_button.text())
            self.assertIn(config.reranker_model, workspace.delete_reranker_button.text())
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

    def test_main_window_uses_independent_query_results_config_and_log_tabs(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        paths = ensure_data_paths(str(TEST_ROOT), str(SAMPLE_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root))
        window = MainWindow(config=config, paths=paths, language_code='zh-CN', theme=theme, version='0.2.0')
        try:
            labels = [window.main_tabs.tabText(index) for index in range(window.main_tabs.count())]
            self.assertEqual(labels, ['查询台', '结果与详情', '配置', '活动日志'])
            self.assertEqual(window.query_workspace.detail_tabs.count(), 2)
            self.assertIs(window.query_workspace.log_panel.parentWidget(), window.activity_log_workspace)

            window.query_workspace.resultsRequested.emit()
            self.assertIs(window.main_tabs.currentWidget(), window.results_workspace)
            window.query_workspace.activityLogRequested.emit()
            self.assertIs(window.main_tabs.currentWidget(), window.activity_log_workspace)
        finally:
            window.deleteLater()
            app.processEvents()

    def test_main_window_header_fully_hides_and_toggle_stays_in_tab_row(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        paths = ensure_data_paths(str(TEST_ROOT), str(SAMPLE_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root))
        window = MainWindow(config=config, paths=paths, language_code='zh-CN', theme=theme, version='0.2.0')
        try:
            window.show()
            app.processEvents()
            self.assertTrue(window.header_card.isVisible())
            self.assertEqual(window.header_toggle_button.text(), '折叠顶部')
            self.assertIs(
                window.main_tabs.cornerWidget(QtCore.Qt.Corner.TopRightCorner),
                window.header_toggle_button,
            )

            window._toggle_header_collapsed()
            app.processEvents()
            self.assertTrue(window.header_card.isHidden())
            self.assertTrue(window.header_toggle_button.isVisible())
            self.assertEqual(window.header_toggle_button.text(), '展开顶部')

            window._toggle_header_collapsed()
            app.processEvents()
            self.assertTrue(window.header_card.isVisible())
            self.assertEqual(window.header_toggle_button.text(), '折叠顶部')
        finally:
            window.close()
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
            self.assertEqual(replacement.main_tabs.tabText(1), text('en', 'main_tab_results'))
            self.assertEqual(replacement.main_tabs.tabText(2), text('en', 'main_tab_config'))
            self.assertEqual(replacement.main_tabs.tabText(3), text('en', 'main_tab_activity_log'))
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
            self.assertIs(window.main_tabs.currentWidget(), window.activity_log_workspace)
        finally:
            window.deleteLater()
            app.processEvents()

    def test_main_window_recovery_mode_only_keeps_config_shell(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        recovery_root = TEST_ROOT / 'missing_root'
        reason_text = data_root_reason_text('zh-CN', 'active_data_root_missing', 'path-missing')
        paths = ensure_data_paths(str(TEST_ROOT / 'recovery_placeholder'), str(SAMPLE_ROOT))
        config = AppConfig(vault_path='', data_root=str(paths.global_root), ui_language='zh-CN')
        window = MainWindow(
            config=config,
            paths=paths,
            language_code='zh-CN',
            theme=theme,
            version='0.2.0',
            recovery_mode=True,
            recovery_context={
                'path': str(recovery_root),
                'reason_code': 'active_data_root_missing',
                'reason_text': reason_text,
            },
        )
        try:
            window.show()
            app.processEvents()
            self.assertEqual(window.main_tabs.count(), 1)
            self.assertIs(window.main_tabs.currentWidget(), window.config_workspace)
            self.assertIsNone(window.query_workspace)
            self.assertTrue(window.recovery_banner.isVisible())
            self.assertEqual(window.recovery_banner.property('role'), 'warning')
            self.assertIn(str(recovery_root), window.recovery_banner.text())
            self.assertNotIn('startup pointer', window.recovery_banner.text().lower())
            self.assertEqual(window.status_label.text(), text('zh-CN', 'data_root_recovery_status', path=str(recovery_root), reason=reason_text))
        finally:
            window.deleteLater()
            app.processEvents()

    def test_config_workspace_recovery_switches_existing_environment_and_requests_restart(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        placeholder_paths = ensure_data_paths(str(TEST_ROOT / 'recovery_placeholder'), str(SAMPLE_ROOT))
        target_paths = ensure_data_paths(str(TEST_ROOT / 'valid_env'), str(SAMPLE_ROOT))
        save_config(AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(target_paths.global_root)), target_paths)
        workspace = ConfigWorkspace(
            config=AppConfig(vault_path='', data_root=str(placeholder_paths.global_root)),
            paths=placeholder_paths,
            language_code='zh-CN',
            theme=theme,
            recovery_mode=True,
            recovery_path=str(TEST_ROOT / 'missing_root'),
            recovery_reason_code='active_data_root_missing',
            recovery_reason_text=data_root_reason_text('zh-CN', 'active_data_root_missing', 'path-missing'),
        )
        try:
            workspace.data_root_combo.setCurrentText(str(target_paths.global_root))
            with patch.object(workspace, '_confirm_pending_data_root_switch', return_value='continue'), \
                 patch('PySide6.QtCore.QProcess.startDetached', return_value=True) as start_detached:
                workspace._prompt_pending_data_root_switch()
            start_detached.assert_called_once()
            bootstrap = json.loads((ENV_ROOT / 'roaming' / 'bootstrap.json').read_text(encoding='utf-8'))
            self.assertEqual(bootstrap['active_data_root'], str(target_paths.global_root))
            self.assertIn(str(target_paths.global_root), bootstrap['known_data_roots'])
        finally:
            workspace.deleteLater()
            app.processEvents()

    def test_config_workspace_switch_dialog_can_remove_selected_saved_root(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        active_paths = ensure_data_paths(str(TEST_ROOT / 'switch_forget_active'), str(SAMPLE_ROOT))
        target_paths = ensure_data_paths(str(TEST_ROOT / 'switch_forget_target'), str(SAMPLE_ROOT))
        write_bootstrap_pointer(active_paths.global_root, known_data_roots=[str(active_paths.global_root), str(target_paths.global_root)])
        workspace = ConfigWorkspace(
            config=AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(active_paths.global_root)),
            paths=active_paths,
            language_code='zh-CN',
            theme=theme,
        )
        try:
            workspace.data_root_combo.setCurrentText(str(target_paths.global_root))
            with patch.object(workspace, '_confirm_pending_data_root_switch', return_value='remove'), \
                 patch('PySide6.QtCore.QProcess.startDetached', return_value=True):
                workspace._prompt_pending_data_root_switch(explicit=True)
            bootstrap = json.loads((ENV_ROOT / 'roaming' / 'bootstrap.json').read_text(encoding='utf-8'))
            self.assertEqual(bootstrap['active_data_root'], str(active_paths.global_root))
            self.assertNotIn(str(target_paths.global_root), bootstrap['known_data_roots'])
            self.assertIn(str(active_paths.global_root), bootstrap['known_data_roots'])
        finally:
            workspace.deleteLater()
            app.processEvents()

    def test_config_workspace_recovery_retry_restores_original_root_and_requests_restart(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        placeholder_paths = ensure_data_paths(str(TEST_ROOT / 'recovery_placeholder_retry'), str(SAMPLE_ROOT))
        restored_root = TEST_ROOT / 'restored_env'
        workspace = ConfigWorkspace(
            config=AppConfig(vault_path='', data_root=str(placeholder_paths.global_root)),
            paths=placeholder_paths,
            language_code='zh-CN',
            theme=theme,
            recovery_mode=True,
            recovery_path=str(restored_root),
            recovery_reason_code='active_data_root_missing',
            recovery_reason_text=data_root_reason_text('zh-CN', 'active_data_root_missing', 'path-missing'),
        )
        try:
            restored_paths = ensure_data_paths(str(restored_root), str(SAMPLE_ROOT))
            save_config(AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(restored_paths.global_root)), restored_paths)
            with patch('PySide6.QtCore.QProcess.startDetached', return_value=True) as start_detached:
                workspace._retry_recovery_data_root()
            start_detached.assert_called_once()
            bootstrap = json.loads((ENV_ROOT / 'roaming' / 'bootstrap.json').read_text(encoding='utf-8'))
            self.assertEqual(bootstrap['active_data_root'], str(restored_root.resolve()))
        finally:
            workspace.deleteLater()
            app.processEvents()

    def test_config_workspace_recovery_preview_distinguishes_new_and_broken_environment(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        placeholder_paths = ensure_data_paths(str(TEST_ROOT / 'recovery_preview_placeholder'), str(SAMPLE_ROOT))
        broken_root = self._make_broken_environment_root(TEST_ROOT / 'broken_env')
        empty_root = TEST_ROOT / 'empty_env'
        empty_root.mkdir(parents=True, exist_ok=True)
        workspace = ConfigWorkspace(
            config=AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(placeholder_paths.global_root)),
            paths=placeholder_paths,
            language_code='zh-CN',
            theme=theme,
            recovery_mode=True,
            recovery_path=str(TEST_ROOT / 'missing_root'),
            recovery_reason_code='active_data_root_missing',
            recovery_reason_text=data_root_reason_text('zh-CN', 'active_data_root_missing', 'path-missing'),
        )
        try:
            workspace.data_root_combo.setCurrentText(str(empty_root))
            app.processEvents()
            self.assertIn('新环境', workspace.workspace_summary_label.text())
            workspace.data_root_combo.setCurrentText(str(broken_root))
            app.processEvents()
            self.assertIn('环境结构不完整', workspace.workspace_summary_label.text())
            self.assertNotIn('将作为一个新环境使用', workspace.workspace_summary_label.text())
        finally:
            workspace.deleteLater()
            app.processEvents()

    def test_config_workspace_recovery_preview_identifies_existing_legacy_environment(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        placeholder_paths = ensure_data_paths(str(TEST_ROOT / 'recovery_legacy_placeholder'), str(SAMPLE_ROOT))
        legacy_root = self._make_legacy_environment_root(TEST_ROOT / 'legacy_env', SAMPLE_ROOT)
        workspace = ConfigWorkspace(
            config=AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(placeholder_paths.global_root)),
            paths=placeholder_paths,
            language_code='zh-CN',
            theme=theme,
            recovery_mode=True,
            recovery_path=str(TEST_ROOT / 'missing_root'),
            recovery_reason_code='active_data_root_missing',
            recovery_reason_text=data_root_reason_text('zh-CN', 'active_data_root_missing', 'path-missing'),
        )
        try:
            workspace.data_root_combo.setCurrentText(str(legacy_root))
            app.processEvents()
            self.assertIn('已识别现有 Caelune 环境', workspace.workspace_summary_label.text())
            self.assertIn('legacy 结构', workspace.workspace_summary_label.text())
            self.assertIn('legacy', workspace.workspace_summary_label.text())
            self.assertIn(str(legacy_root), workspace.workspace_summary_label.text())
        finally:
            workspace.deleteLater()
            app.processEvents()

    def test_config_workspace_recovery_notice_uses_warning_roles(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        placeholder_paths = ensure_data_paths(str(TEST_ROOT / 'recovery_notice_placeholder'), str(SAMPLE_ROOT))
        workspace = ConfigWorkspace(
            config=AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(placeholder_paths.global_root)),
            paths=placeholder_paths,
            language_code='zh-CN',
            theme=theme,
            recovery_mode=True,
            recovery_path=str(TEST_ROOT / 'missing_root'),
            recovery_reason_code='active_data_root_missing',
            recovery_reason_text=data_root_reason_text('zh-CN', 'active_data_root_missing', 'path-missing'),
        )
        try:
            self.assertEqual(workspace.recovery_notice_title.property('role'), 'warningTitle')
            self.assertEqual(workspace.recovery_notice_body.property('role'), 'warning')
        finally:
            workspace.deleteLater()
            app.processEvents()

    def test_config_workspace_invalid_saved_data_root_can_be_removed_from_prompt(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        active_paths = ensure_data_paths(str(TEST_ROOT / 'active_prompt_env'), str(SAMPLE_ROOT))
        broken_root = self._make_broken_environment_root(TEST_ROOT / 'broken_prompt_env').resolve()
        write_bootstrap_pointer(active_paths.global_root, known_data_roots=[str(active_paths.global_root), str(broken_root)])
        workspace = ConfigWorkspace(
            config=AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(active_paths.global_root)),
            paths=active_paths,
            language_code='zh-CN',
            theme=theme,
        )
        try:
            workspace.data_root_combo.setCurrentText(str(broken_root))
            with patch.object(workspace, '_prompt_invalid_data_root_action', return_value='remove'):
                workspace._prompt_pending_data_root_switch(explicit=True)
            bootstrap = json.loads((ENV_ROOT / 'roaming' / 'bootstrap.json').read_text(encoding='utf-8'))
            self.assertNotIn(str(broken_root), bootstrap['known_data_roots'])
            self.assertTrue(broken_root.exists())
        finally:
            workspace.deleteLater()
            app.processEvents()

    def test_config_workspace_browse_data_root_prompts_once(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        active_paths = ensure_data_paths(str(TEST_ROOT / 'browse_active_env'), str(SAMPLE_ROOT))
        empty_root = TEST_ROOT / 'browse_empty_env'
        empty_root.mkdir(parents=True, exist_ok=True)
        workspace = ConfigWorkspace(
            config=AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(active_paths.global_root)),
            paths=active_paths,
            language_code='zh-CN',
            theme=theme,
        )
        try:
            with patch('PySide6.QtWidgets.QFileDialog.getExistingDirectory', return_value=str(empty_root)), \
                 patch.object(workspace, '_confirm_pending_data_root_switch', return_value='cancel') as confirm_mock:
                workspace._browse_data_root()
                app.processEvents()
            self.assertEqual(confirm_mock.call_count, 1)
        finally:
            workspace.deleteLater()
            app.processEvents()

    def test_config_workspace_can_remove_non_active_saved_data_root(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        active_paths = ensure_data_paths(str(TEST_ROOT / 'active_env'), str(SAMPLE_ROOT))
        extra_root = (TEST_ROOT / 'extra_env').resolve()
        extra_root.mkdir(parents=True, exist_ok=True)
        write_bootstrap_pointer(active_paths.global_root, known_data_roots=[str(active_paths.global_root), str(extra_root)])
        workspace = ConfigWorkspace(
            config=AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(active_paths.global_root)),
            paths=active_paths,
            language_code='zh-CN',
            theme=theme,
        )
        try:
            workspace.data_root_combo.setCurrentText(str(extra_root))
            workspace._remove_selected_data_root()
            bootstrap = json.loads((ENV_ROOT / 'roaming' / 'bootstrap.json').read_text(encoding='utf-8'))
            self.assertNotIn(str(extra_root), bootstrap['known_data_roots'])
            self.assertEqual(bootstrap['active_data_root'], str(active_paths.global_root))
        finally:
            workspace.deleteLater()
            app.processEvents()

    def test_config_workspace_cannot_remove_active_saved_data_root(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        active_paths = ensure_data_paths(str(TEST_ROOT / 'active_env_forbid'), str(SAMPLE_ROOT))
        write_bootstrap_pointer(active_paths.global_root, known_data_roots=[str(active_paths.global_root)])
        workspace = ConfigWorkspace(
            config=AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(active_paths.global_root)),
            paths=active_paths,
            language_code='zh-CN',
            theme=theme,
        )
        try:
            with patch('PySide6.QtWidgets.QMessageBox.information') as info_mock:
                workspace._remove_selected_data_root()
            info_mock.assert_called_once()
            bootstrap = json.loads((ENV_ROOT / 'roaming' / 'bootstrap.json').read_text(encoding='utf-8'))
            self.assertEqual(bootstrap['known_data_roots'], [str(active_paths.global_root)])
        finally:
            workspace.deleteLater()
            app.processEvents()

    def test_stylesheet_defines_combobox_hover_state(self) -> None:
        sheet = build_stylesheet(build_theme('light', 100))
        self.assertIn('QComboBox QAbstractItemView::item:hover', sheet)
        self.assertIn('selection-background-color', sheet)

    def test_runtime_app_icon_loads_from_resources(self) -> None:
        icon = qt_app._load_app_icon()
        self.assertFalse(icon.isNull())


if __name__ == '__main__':
    unittest.main()
