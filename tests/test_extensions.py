import os
import shutil
import unittest
from dataclasses import fields
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import omniclip_rag  # noqa: F401
from PySide6 import QtCore, QtWidgets

from omniclip_rag.config import AppConfig, ensure_data_paths
from omniclip_rag.extensions.models import (
    ExtensionDirectoryState,
    ExtensionSourceDirectory,
    TikaFormatSelection,
    TikaFormatSupportTier,
    default_tika_format_selections,
)
from omniclip_rag.extensions.registry import ExtensionRegistry, ExtensionRegistryState
from omniclip_rag.extensions.service import (
    ExtensionTaskCoordinator,
    ExtensionTaskKind,
    ExtensionTaskRequest,
)
from omniclip_rag.ui_i18n import text
from omniclip_rag.ui_next_qt.config_workspace import ConfigWorkspace
from omniclip_rag.ui_next_qt.theme import build_theme
from omniclip_rag.ui_next_qt.tika_format_dialog import TikaFormatDialog

ROOT = Path(__file__).resolve().parents[1]
TEST_ROOT = ROOT / '.tmp' / 'test_extensions'
SAMPLE_ROOT = ROOT / 'logseq笔记样本'


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
        paths = ensure_data_paths(str(TEST_ROOT), str(SAMPLE_ROOT))
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

    def test_config_workspace_exposes_extensions_tabs_and_persists_extension_config(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        paths = ensure_data_paths(str(TEST_ROOT), str(SAMPLE_ROOT))
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
        paths = ensure_data_paths(str(TEST_ROOT), str(SAMPLE_ROOT))
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
            poor_zip_disabled = False
            for parent_index in range(dialog.tree.topLevelItemCount()):
                parent = dialog.tree.topLevelItem(parent_index)
                for child_index in range(parent.childCount()):
                    child = parent.child(child_index)
                    format_id = str(child.data(0, QtCore.Qt.ItemDataRole.UserRole) or '')
                    seen_ids.append(format_id)
                    if format_id == 'zip':
                        poor_zip_disabled = not bool(child.flags() & QtCore.Qt.ItemFlag.ItemIsUserCheckable)
            self.assertNotIn('pdf', seen_ids)
            self.assertTrue(poor_zip_disabled)
        finally:
            dialog.deleteLater()
            app.processEvents()

    def test_extension_sources_include_all_saved_vaults(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        second_vault = TEST_ROOT / 'saved_vault_2'
        second_vault.mkdir(parents=True, exist_ok=True)
        paths = ensure_data_paths(str(TEST_ROOT), str(SAMPLE_ROOT))
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

    def test_extensions_subtitle_no_longer_mentions_ui_only_phase(self) -> None:
        self.assertNotIn('本阶段只接入 UI 与配置管理', text('zh-CN', 'extensions_subtitle'))

    def test_extension_global_preflight_dispatches_selected_pipeline(self) -> None:
        app = get_app()
        theme = build_theme('light', 100)
        paths = ensure_data_paths(str(TEST_ROOT), str(SAMPLE_ROOT))
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
