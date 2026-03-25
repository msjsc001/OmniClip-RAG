import os
import shutil
import threading
import unittest
from dataclasses import fields
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import omniclip_rag  # noqa: F401
from PySide6 import QtCore, QtWidgets

from omniclip_rag.config import AppConfig, ensure_data_paths
from omniclip_rag.extensions.models import (
    ExtensionDirectoryState,
    ExtensionIndexState,
    ExtensionSourceDirectory,
    TikaFormatSelection,
    TikaFormatSupportTier,
    default_tika_format_selections,
)
from omniclip_rag.extensions.build_state import write_extension_build_state
from omniclip_rag.extensions.paths import build_extension_data_paths
from omniclip_rag.extensions.registry import ExtensionRegistry, ExtensionRegistryState
from omniclip_rag.extensions.service import (
    ExtensionTaskCoordinator,
    ExtensionTaskKind,
    ExtensionTaskRequest,
    TikaExtensionService,
)
from omniclip_rag.extensions.tika_catalog import build_tika_format_catalog
from omniclip_rag.ui_i18n import text
from omniclip_rag.ui_next_qt.config_workspace import ConfigWorkspace
from omniclip_rag.ui_next_qt.theme import build_theme
from omniclip_rag.ui_next_qt.tika_format_dialog import TikaFormatDialog

ROOT = Path(__file__).resolve().parents[1]
TEST_ROOT = ROOT / '.tmp' / 'test_extensions'
SAMPLE_ROOT = ROOT / '笔记样本'


def get_app() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication([])
    return app


class ExtensionSkeletonTests(unittest.TestCase):
    def tearDown(self) -> None:
        if TEST_ROOT.exists():
            shutil.rmtree(TEST_ROOT, ignore_errors=True)

    def test_extension_directory_states_are_isolated_from_app_config(self) -> None:
        self.assertEqual(ExtensionDirectoryState.MISSING_TEMPORARILY.value, 'missing_temporarily')
        self.assertEqual(ExtensionDirectoryState.REMOVED_CONFIRMED.value, 'removed_confirmed')
        self.assertNotIn('pdf_extension', {field.name for field in fields(AppConfig)})
        self.assertNotIn('tika_extension', {field.name for field in fields(AppConfig)})

    def test_extension_task_coordinator_blocks_heavy_tasks_during_markdown_rebuild(self) -> None:
        coordinator = ExtensionTaskCoordinator()
        request = ExtensionTaskRequest(pipeline='pdf', kind=ExtensionTaskKind.FULL_REBUILD)
        decision = coordinator.can_start(request, markdown_rebuild_active=True)
        self.assertFalse(decision.allowed)
        self.assertTrue(decision.queued)
        self.assertEqual(decision.reason, 'markdown_rebuild_active')

    def test_extension_registry_roundtrip_persists_independent_config(self) -> None:
        paths = ensure_data_paths(str(TEST_ROOT / 'data_registry_roundtrip'), str(SAMPLE_ROOT))
        registry = ExtensionRegistry()
        state = ExtensionRegistryState()
        state.pdf_config.enabled = True
        state.pdf_config.source_directories = [
            ExtensionSourceDirectory(path=str(SAMPLE_ROOT), selected=True, state=ExtensionDirectoryState.ENABLED)
        ]
        state.tika_config.enabled = True
        state.tika_config.selected_formats = default_tika_format_selections()
        state.tika_config.selected_formats[0].enabled = True
        registry.save(paths, state)
        reloaded = registry.load(paths)
        self.assertTrue(reloaded.pdf_config.enabled)
        self.assertEqual(reloaded.pdf_config.source_directories[0].path, str(SAMPLE_ROOT))
        self.assertTrue(reloaded.tika_config.enabled)
        self.assertTrue(reloaded.tika_config.selected_formats[0].enabled)
        self.assertEqual(reloaded.snapshot.pdf.build_id, '')
        self.assertFalse(reloaded.snapshot.pdf.resume_available)

    def test_config_workspace_exposes_extensions_tabs_and_persists_extension_config(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        paths = ensure_data_paths(str(TEST_ROOT / 'data_workspace_roundtrip'), str(SAMPLE_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root))
        workspace = ConfigWorkspace(config=config, paths=paths, language_code='zh-CN', theme=theme)
        try:
            tab_labels = [workspace.sub_tabs.tabText(index) for index in range(workspace.sub_tabs.count())]
            self.assertIn(text('zh-CN', 'left_tab_extensions'), tab_labels)
            self.assertEqual(workspace.ext_tabs.count(), 2)
            workspace.ext_pdf_enabled_check.setChecked(True)
            app.processEvents()
            item = workspace.ext_pdf_source_table.item(0, 0)
            self.assertIsNotNone(item)
            item.setCheckState(QtCore.Qt.CheckState.Checked)
            workspace.ext_tika_enabled_check.setChecked(True)
            workspace._extension_state.tika_config.selected_formats[0].enabled = True
            workspace._persist_extension_state()
        finally:
            workspace.deleteLater()
            app.processEvents()
        replacement = ConfigWorkspace(config=config, paths=paths, language_code='zh-CN', theme=theme)
        try:
            self.assertTrue(replacement.ext_pdf_enabled_check.isChecked())
            self.assertEqual(replacement.ext_pdf_source_table.item(0, 0).checkState(), QtCore.Qt.CheckState.Checked)
            self.assertTrue(replacement.ext_tika_enabled_check.isChecked())
            self.assertIn('1', replacement.ext_tika_formats_summary_label.text())
        finally:
            replacement.deleteLater()
            app.processEvents()

    def test_unchecking_extension_directory_requires_confirmation(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        paths = ensure_data_paths(str(TEST_ROOT / 'data_uncheck_confirm'), str(SAMPLE_ROOT))
        registry = ExtensionRegistry()
        state = registry.load(paths)
        state.pdf_config.enabled = True
        state.pdf_config.source_directories = [
            ExtensionSourceDirectory(path=str(SAMPLE_ROOT), selected=True, state=ExtensionDirectoryState.ENABLED, managed_by_workspace=True)
        ]
        registry.save(paths, state)
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root))
        workspace = ConfigWorkspace(config=config, paths=paths, language_code='zh-CN', theme=theme)
        try:
            item = workspace.ext_pdf_source_table.item(0, 0)
            with patch('omniclip_rag.ui_next_qt.config_workspace.QtWidgets.QMessageBox.question', return_value=QtWidgets.QMessageBox.StandardButton.No):
                item.setCheckState(QtCore.Qt.CheckState.Unchecked)
                app.processEvents()
            refreshed_item = workspace.ext_pdf_source_table.item(0, 0)
            self.assertIsNotNone(refreshed_item)
            self.assertEqual(refreshed_item.checkState(), QtCore.Qt.CheckState.Checked)
        finally:
            workspace.deleteLater()
            app.processEvents()

    def test_tika_dialog_excludes_pdf_and_disables_poor_formats(self) -> None:
        app = get_app()
        selections = default_tika_format_selections() + [
            TikaFormatSelection(format_id='pdf', display_name='PDF (.pdf)', tier=TikaFormatSupportTier.POOR)
        ]
        dialog = TikaFormatDialog(selections=selections, language_code='zh-CN')
        try:
            seen_ids: list[str] = []
            poor_zip_checkable = False
            for parent_index in range(dialog.tree.topLevelItemCount()):
                parent = dialog.tree.topLevelItem(parent_index)
                for child_index in range(parent.childCount()):
                    child = parent.child(child_index)
                    format_id = str(child.data(0, QtCore.Qt.ItemDataRole.UserRole) or '')
                    seen_ids.append(format_id)
                    if format_id == 'zip':
                        poor_zip_checkable = bool(child.flags() & QtCore.Qt.ItemFlag.ItemIsUserCheckable)
            self.assertNotIn('pdf', seen_ids)
            self.assertTrue(poor_zip_checkable)
        finally:
            dialog.deleteLater()
            app.processEvents()

    def test_extension_registry_roundtrip_preserves_non_default_tika_formats(self) -> None:
        paths = ensure_data_paths(str(TEST_ROOT / 'data_registry_custom_formats'), str(SAMPLE_ROOT))
        registry = ExtensionRegistry()
        state = ExtensionRegistryState()
        state.tika_config.enabled = True
        state.tika_config.selected_formats = default_tika_format_selections() + [
            TikaFormatSelection(
                format_id='tar.gz',
                display_name='TAR.GZ (.tar.gz)',
                tier=TikaFormatSupportTier.UNTESTED,
                enabled=True,
            )
        ]
        registry.save(paths, state)
        reloaded = registry.load(paths)
        by_id = {item.format_id: item for item in reloaded.tika_config.selected_formats}
        self.assertIn('tar.gz', by_id)
        self.assertTrue(by_id['tar.gz'].enabled)
        self.assertEqual(by_id['tar.gz'].tier, TikaFormatSupportTier.UNTESTED)

    def test_tika_iter_files_matches_composite_suffixes(self) -> None:
        source_root = TEST_ROOT / 'tika_source'
        source_root.mkdir(parents=True, exist_ok=True)
        (source_root / 'a.tar.gz').write_text('hello', encoding='utf-8')

        paths = ensure_data_paths(str(TEST_ROOT / 'data_tika_suffixes'), str(SAMPLE_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root))
        service = TikaExtensionService(config=config, paths=paths)
        try:
            matches = list(service._iter_tika_files([source_root.resolve()], ['tar.gz']))
            self.assertEqual(len(matches), 1)
            self.assertEqual(matches[0][2], 'tar.gz')
        finally:
            service.close()

    def test_tika_catalog_falls_back_to_packaged_suffix_list_without_jar(self) -> None:
        catalog = build_tika_format_catalog(jar_path=TEST_ROOT / 'missing-tika-server.jar')
        format_ids = {item.format_id for item in catalog}
        self.assertGreater(len(catalog), len(default_tika_format_selections()))
        self.assertIn('7z', format_ids)
        self.assertIn('warc.gz', format_ids)
        self.assertNotIn('pdf', format_ids)

    def test_extension_sources_include_all_saved_vaults(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        second_vault = TEST_ROOT / 'saved_vault_2'
        second_vault.mkdir(parents=True, exist_ok=True)
        paths = ensure_data_paths(str(TEST_ROOT / 'data_saved_vaults'), str(SAMPLE_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), vault_paths=[str(SAMPLE_ROOT), str(second_vault)], data_root=str(paths.global_root))
        workspace = ConfigWorkspace(config=config, paths=paths, language_code='zh-CN', theme=theme)
        try:
            pdf_paths = {workspace.ext_pdf_source_table.item(i, 0).data(QtCore.Qt.ItemDataRole.UserRole) for i in range(workspace.ext_pdf_source_table.rowCount())}
            tika_paths = {workspace.ext_tika_source_table.item(i, 0).data(QtCore.Qt.ItemDataRole.UserRole) for i in range(workspace.ext_tika_source_table.rowCount())}
            self.assertIn(str(SAMPLE_ROOT), pdf_paths)
            self.assertIn(str(second_vault), pdf_paths)
            self.assertEqual(pdf_paths, tika_paths)
        finally:
            workspace.deleteLater()
            app.processEvents()

    def test_extension_source_progress_uses_index_summary_when_idle(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        paths = ensure_data_paths(str(TEST_ROOT / 'data_idle_progress'), str(SAMPLE_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root))
        workspace = ConfigWorkspace(config=config, paths=paths, language_code='zh-CN', theme=theme)
        try:
            workspace._extension_source_summaries[('pdf', str(SAMPLE_ROOT))] = {
                'has_indexed_data': True,
                'indexed_files': 2,
                'indexed_chunks': 5,
            }
            source = ExtensionSourceDirectory(path=str(SAMPLE_ROOT), selected=True, state=ExtensionDirectoryState.ENABLED)
            message = workspace._extension_source_progress_text('pdf', source)
            self.assertIn('2', message)
            self.assertIn('5', message)
        finally:
            workspace.deleteLater()
            app.processEvents()

    def test_extension_source_progress_includes_stage_file_and_close_hint(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        paths = ensure_data_paths(str(TEST_ROOT / 'data_progress_file_hint'), str(SAMPLE_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root))
        workspace = ConfigWorkspace(config=config, paths=paths, language_code='zh-CN', theme=theme)
        try:
            source_path = str(SAMPLE_ROOT)
            workspace._handle_extension_source_progress(
                'pdf',
                source_path,
                {
                    'stage_status': 'parse_pdf',
                    'current': 2,
                    'total': 5,
                    'overall_percent': 40.0,
                    'current_path': str(Path(source_path) / 'chapter-a.pdf'),
                    'processed_files': 1,
                    'skipped_files': 0,
                    'error_count': 0,
                    'close_safe': False,
                },
            )
            row = workspace._find_extension_source_row('pdf', source_path)
            self.assertIsNotNone(row)
            text_value = workspace.ext_pdf_source_table.item(int(row), 3).text()
            self.assertIn(text('zh-CN', 'extensions_progress_stage_parsing_pdf'), text_value)
            self.assertIn('chapter-a.pdf', text_value)
            self.assertIn(text('zh-CN', 'extensions_progress_close_busy'), text_value)
        finally:
            workspace.deleteLater()
            app.processEvents()

    def test_extension_issue_summary_is_appended_when_report_has_issue_log(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        paths = ensure_data_paths(str(TEST_ROOT / 'data_issue_summary'), str(SAMPLE_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root))
        workspace = ConfigWorkspace(config=config, paths=paths, language_code='zh-CN', theme=theme)
        try:
            report = SimpleNamespace(
                regrouped_files=2,
                oversized_skipped_files=3,
                issue_log_path=r'D:\tmp\issues.jsonl',
            )
            message = workspace._append_extension_issue_summary('建库完成', report)
            self.assertIn('2', message)
            self.assertIn('3', message)
            self.assertIn(r'D:\tmp\issues.jsonl', message)
        finally:
            workspace.deleteLater()
            app.processEvents()

    def test_extension_source_progress_includes_recent_issue_hint(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        paths = ensure_data_paths(str(TEST_ROOT / 'data_progress_issue_hint'), str(SAMPLE_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root))
        workspace = ConfigWorkspace(config=config, paths=paths, language_code='zh-CN', theme=theme)
        try:
            source_path = str(SAMPLE_ROOT)
            workspace._handle_extension_source_progress(
                'tika',
                source_path,
                {
                    'stage_status': 'parse_tika',
                    'current': 1,
                    'total': 1,
                    'overall_percent': 86.0,
                    'current_path': str(Path(source_path) / 'book.epub'),
                    'processed_files': 1,
                    'skipped_files': 1,
                    'error_count': 1,
                    'close_safe': False,
                    'recent_issue': 'HTTP 406 · book.epub',
                },
            )
            row = workspace._find_extension_source_row('tika', source_path)
            self.assertIsNotNone(row)
            text_value = workspace.ext_tika_source_table.item(int(row), 3).text()
            self.assertIn(text('zh-CN', 'extensions_progress_recent_issue').split('：', 1)[0], text_value)
            self.assertIn('HTTP 406 · book.epub', text_value)
        finally:
            workspace.deleteLater()
            app.processEvents()

    def test_extension_source_rebuild_can_route_to_scan_once_when_index_exists(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        paths = ensure_data_paths(str(TEST_ROOT / 'data_source_rebuild_mode'), str(SAMPLE_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root))
        workspace = ConfigWorkspace(config=config, paths=paths, language_code='zh-CN', theme=theme)
        try:
            with patch.object(workspace, '_choose_extension_source_build_mode', return_value='scan_once'), \
                 patch.object(workspace, '_run_extension_source_scan_once') as scan_mock:
                workspace._run_extension_source_rebuild('pdf', str(SAMPLE_ROOT))
            scan_mock.assert_called_once_with('pdf', str(SAMPLE_ROOT))
        finally:
            workspace.deleteLater()
            app.processEvents()

    def test_extensions_subtitle_no_longer_mentions_ui_only_phase(self) -> None:
        self.assertNotIn('本阶段只接入 UI 与配置管理', text('zh-CN', 'extensions_subtitle'))

    def test_extension_clear_build_state_button_has_plain_tooltip(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        paths = ensure_data_paths(str(TEST_ROOT / 'data_clear_build_state_tooltip'), str(SAMPLE_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root))
        workspace = ConfigWorkspace(config=config, paths=paths, language_code='zh-CN', theme=theme)
        try:
            tooltip_text = workspace.ext_clear_resume_button.toolTip()
            self.assertIn('不会删除原始文件', tooltip_text)
            self.assertIn('从头重来', tooltip_text)
        finally:
            workspace.deleteLater()
            app.processEvents()

    def test_extension_global_preflight_dispatches_selected_pipeline(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        paths = ensure_data_paths(str(TEST_ROOT / 'data_global_preflight_dispatch'), str(SAMPLE_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root))
        workspace = ConfigWorkspace(config=config, paths=paths, language_code='zh-CN', theme=theme)
        try:
            with patch.object(workspace, '_collect_config', return_value=(config, paths)), \
                 patch.object(workspace, '_current_extension_pipeline', return_value='pdf'), \
                 patch.object(workspace, '_start_extension_task', return_value=True) as start_mock:
                workspace._run_extension_preflight()
            self.assertEqual(start_mock.call_args.kwargs['task_key'], 'preflight:pdf')
        finally:
            workspace.deleteLater()
            app.processEvents()

    def test_extension_resume_state_is_loaded_from_build_state_file(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        paths = ensure_data_paths(str(TEST_ROOT / 'data_resume'), str(SAMPLE_ROOT))
        registry = ExtensionRegistry()
        state = registry.load(paths)
        state.pdf_config.enabled = True
        state.pdf_config.source_directories = [
            ExtensionSourceDirectory(path=str(SAMPLE_ROOT), selected=True, state=ExtensionDirectoryState.ENABLED)
        ]
        registry.save(paths, state)
        build_paths = build_extension_data_paths(paths, 'pdf')
        write_extension_build_state(
            build_paths,
            {
                'build_id': 'pdf-build-1',
                'pipeline': 'pdf',
                'status': 'resumable',
                'resume_available': True,
                'phase': 'parse_files',
                'total': 7,
                'completed_files': {'a.pdf': {'size': 1, 'mtime': 1.0}},
            },
        )
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root))
        with patch('omniclip_rag.ui_next_qt.config_workspace.QtCore.QTimer.singleShot', side_effect=lambda *_args, **_kwargs: None):
            workspace = ConfigWorkspace(config=config, paths=paths, language_code='zh-CN', theme=theme)
        try:
            self.assertEqual(workspace._extension_state.snapshot.pdf.index_state, ExtensionIndexState.RESUMABLE)
            self.assertTrue(workspace._extension_state.snapshot.pdf.resume_available)
            self.assertEqual(workspace._extension_state.snapshot.pdf.build_id, 'pdf-build-1')
        finally:
            workspace.deleteLater()
            app.processEvents()

    def test_extension_stop_action_requests_cancel_for_active_task(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        paths = ensure_data_paths(str(TEST_ROOT / 'data_cancel'), str(SAMPLE_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(paths.global_root))
        workspace = ConfigWorkspace(config=config, paths=paths, language_code='zh-CN', theme=theme)
        messages: list[str] = []
        try:
            workspace.statusMessageChanged.connect(messages.append)
            workspace._extension_task_worker = object()
            workspace._extension_task_key = 'rebuild:pdf'
            workspace._extension_cancel_event = threading.Event()
            workspace._handle_extension_stop_action()
            self.assertTrue(workspace._extension_cancel_event.is_set())
            self.assertEqual(workspace._extension_state.snapshot.pdf.index_state, ExtensionIndexState.CANCELLING)
            self.assertTrue(any('停止当前扩展任务' in item or '停止' in item for item in messages))
        finally:
            workspace.deleteLater()
            app.processEvents()
