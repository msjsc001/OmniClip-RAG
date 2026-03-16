from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class ExtensionDirectoryState(str, Enum):
    """Directory-level lifecycle states for extension sources.

    This state machine is intentionally extension-specific and must never be
    folded into the Markdown workspace config/state model.
    """

    ENABLED = 'enabled'
    DISABLED = 'disabled'
    MISSING_TEMPORARILY = 'missing_temporarily'
    REMOVED_CONFIRMED = 'removed_confirmed'
    STALE = 'stale'
    ERROR = 'error'


class ExtensionIndexState(str, Enum):
    """High-level build state snapshot for one isolated extension pipeline."""

    DISABLED = 'disabled'
    NOT_BUILT = 'not_built'
    BUILDING = 'building'
    READY = 'ready'
    STALE = 'stale'
    ERROR = 'error'


class TikaFormatSupportTier(str, Enum):
    """UI exposure tier for Tika-backed formats."""

    RECOMMENDED = 'recommended'
    UNKNOWN = 'unknown'
    POOR = 'poor'


@dataclass(slots=True)
class ExtensionSourceDirectory:
    """One user-managed source directory inside an extension pipeline."""

    path: str
    state: ExtensionDirectoryState = ExtensionDirectoryState.DISABLED
    selected: bool = False
    source_label: str = ''
    last_error: str = ''
    managed_by_workspace: bool = False


@dataclass(slots=True)
class ExtensionWatchState:
    """Persisted watch-loop snapshot owned by the extension subsystem.

    Why: watch lifecycle must stay separate from the Markdown mainline so a
    noisy or blocked extension watch loop never contaminates the primary
    workspace watch state.
    """

    running: bool = False
    last_event_at: str = ''
    last_scan_at: str = ''
    last_error: str = ''
    pending_changes: int = 0


@dataclass(slots=True)
class PdfExtensionConfig:
    """User configuration for the isolated PDF extension pipeline."""

    enabled: bool = False
    include_in_query: bool = False
    watch_enabled: bool = False
    source_directories: list[ExtensionSourceDirectory] = field(default_factory=list)


@dataclass(slots=True)
class PdfExtensionStatus:
    """Runtime/build snapshot for the isolated PDF extension pipeline."""

    index_state: ExtensionIndexState = ExtensionIndexState.DISABLED
    build_in_progress: bool = False
    watch_running: bool = False
    watch_state: ExtensionWatchState = field(default_factory=ExtensionWatchState)
    last_error: str = ''
    indexed_document_count: int = 0


@dataclass(slots=True)
class TikaFormatSelection:
    """One Tika format option exposed to the format picker dialog."""

    format_id: str
    display_name: str
    tier: TikaFormatSupportTier = TikaFormatSupportTier.UNKNOWN
    enabled: bool = False
    visible: bool = True


_DEFAULT_TIKA_FORMAT_CATALOG: tuple[tuple[str, str, TikaFormatSupportTier], ...] = (
    ('docx', 'Word (.docx)', TikaFormatSupportTier.RECOMMENDED),
    ('doc', 'Word (.doc)', TikaFormatSupportTier.RECOMMENDED),
    ('pptx', 'PowerPoint (.pptx)', TikaFormatSupportTier.RECOMMENDED),
    ('ppt', 'PowerPoint (.ppt)', TikaFormatSupportTier.RECOMMENDED),
    ('html', 'HTML (.html/.htm)', TikaFormatSupportTier.RECOMMENDED),
    ('xml', 'XML (.xml)', TikaFormatSupportTier.RECOMMENDED),
    ('txt', 'Plain Text (.txt)', TikaFormatSupportTier.RECOMMENDED),
    ('rtf', 'Rich Text (.rtf)', TikaFormatSupportTier.RECOMMENDED),
    ('epub', 'EPUB (.epub)', TikaFormatSupportTier.RECOMMENDED),
    ('odt', 'OpenDocument Text (.odt)', TikaFormatSupportTier.RECOMMENDED),
    ('mhtml', 'MHTML (.mhtml/.mht)', TikaFormatSupportTier.UNKNOWN),
    ('eml', 'Email (.eml)', TikaFormatSupportTier.UNKNOWN),
    ('msg', 'Outlook Message (.msg)', TikaFormatSupportTier.UNKNOWN),
    ('xlsx', 'Excel (.xlsx)', TikaFormatSupportTier.UNKNOWN),
    ('xls', 'Excel (.xls)', TikaFormatSupportTier.UNKNOWN),
    ('zip', 'Archive (.zip)', TikaFormatSupportTier.POOR),
    ('tar', 'Archive (.tar)', TikaFormatSupportTier.POOR),
    ('rar', 'Archive (.rar)', TikaFormatSupportTier.POOR),
)


def default_tika_format_selections() -> list[TikaFormatSelection]:
    """Return the first-stage Tika catalog exposed by the UI.

    PDF is intentionally absent here because PDF runs through its own isolated
    chain and must never be selectable through Tika.
    """

    return [
        TikaFormatSelection(format_id=format_id, display_name=display_name, tier=tier)
        for format_id, display_name, tier in _DEFAULT_TIKA_FORMAT_CATALOG
    ]


@dataclass(slots=True)
class TikaRuntimeStatus:
    """External Tika runtime availability and sidecar state snapshot."""

    installed: bool = False
    installing: bool = False
    java_available: bool = False
    jar_available: bool = False
    starting: bool = False
    running: bool = False
    healthy: bool = False
    version: str = ''
    install_root: str = ''
    java_path: str = ''
    jar_path: str = ''
    pid: int = 0
    port: int = 9998
    last_error: str = ''


@dataclass(slots=True)
class TikaExtensionConfig:
    """User configuration for the isolated Tika extension pipeline."""

    enabled: bool = False
    include_in_query: bool = False
    watch_enabled: bool = False
    source_directories: list[ExtensionSourceDirectory] = field(default_factory=list)
    selected_formats: list[TikaFormatSelection] = field(default_factory=default_tika_format_selections)


@dataclass(slots=True)
class TikaExtensionStatus:
    """Runtime/build snapshot for the isolated Tika extension pipeline."""

    index_state: ExtensionIndexState = ExtensionIndexState.DISABLED
    build_in_progress: bool = False
    watch_running: bool = False
    watch_state: ExtensionWatchState = field(default_factory=ExtensionWatchState)
    runtime: TikaRuntimeStatus = field(default_factory=TikaRuntimeStatus)
    last_error: str = ''
    indexed_document_count: int = 0


@dataclass(slots=True)
class ExtensionSubsystemSnapshot:
    """Aggregated extension snapshot kept outside the Markdown main config."""

    pdf: PdfExtensionStatus = field(default_factory=PdfExtensionStatus)
    tika: TikaExtensionStatus = field(default_factory=TikaExtensionStatus)
