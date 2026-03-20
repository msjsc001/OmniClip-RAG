from __future__ import annotations


class BuildCancelledError(RuntimeError):
    """Raised when the user cancels a long-running full rebuild."""


class RuntimeDependencyError(RuntimeError):
    """Raised when the packaged app is missing an optional heavy AI runtime."""


class ActiveDataRootUnavailableError(RuntimeError):
    """Raised when the active data root cannot be used safely."""

    def __init__(
        self,
        path: str,
        reason: str,
        *,
        source: str = 'bootstrap',
        detail: str = '',
    ) -> None:
        self.path = str(path or '').strip()
        self.reason = str(reason or '').strip() or 'unknown'
        self.source = str(source or '').strip() or 'bootstrap'
        self.detail = str(detail or '').strip()
        if self.detail:
            message = f'目录不可用：{self.path}（{self.reason}: {self.detail}）'
        else:
            message = f'目录不可用：{self.path}（{self.reason}）'
        super().__init__(message)

    @property
    def display_reason(self) -> str:
        return f'{self.reason}: {self.detail}' if self.detail else self.reason

