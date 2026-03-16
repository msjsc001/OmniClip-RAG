import shutil
import unittest
from pathlib import Path
from unittest.mock import patch

from omniclip_rag.config import ensure_data_paths
from omniclip_rag.extensions.runtimes.tika_runtime import (
    TIKA_JAR_NAME,
    TikaSidecarManager,
    detect_tika_runtime,
    install_tika_runtime,
    runtime_layout,
)

ROOT = Path(__file__).resolve().parents[1]
TEST_ROOT = ROOT / '.tmp' / 'test_tika_runtime'
SAMPLE_ROOT = ROOT / 'logseq笔记样本'


class _FakeProcess:
    def __init__(self, pid: int = 4321) -> None:
        self.pid = pid
        self._returncode = None
        self.terminate_called = False
        self.kill_called = False

    def poll(self):
        return self._returncode

    def terminate(self) -> None:
        self.terminate_called = True
        self._returncode = 0

    def kill(self) -> None:
        self.kill_called = True
        self._returncode = 1

    def wait(self, timeout=None):
        if self._returncode is None:
            self._returncode = 0
        return self._returncode




class _FakeTemporaryDirectory:
    def __init__(self, path: Path) -> None:
        self.name = str(path)
        path.mkdir(parents=True, exist_ok=True)

    def __enter__(self) -> str:
        return self.name

    def __exit__(self, exc_type, exc, tb) -> None:
        shutil.rmtree(self.name, ignore_errors=True)

class TikaRuntimeTests(unittest.TestCase):
    def tearDown(self) -> None:
        if TEST_ROOT.exists():
            shutil.rmtree(TEST_ROOT, ignore_errors=True)

    def test_detect_tika_runtime_reports_ready_bundle(self) -> None:
        paths = ensure_data_paths(str(TEST_ROOT), str(SAMPLE_ROOT))
        layout = runtime_layout(paths)
        (layout.jre_root / 'bin').mkdir(parents=True, exist_ok=True)
        (layout.jre_root / 'bin' / 'java.exe').write_text('', encoding='utf-8')
        layout.jar_path.parent.mkdir(parents=True, exist_ok=True)
        layout.jar_path.write_text('', encoding='utf-8')

        status = detect_tika_runtime(paths)

        self.assertTrue(status.installed)
        self.assertTrue(status.java_available)
        self.assertTrue(status.jar_available)
        self.assertEqual(status.install_root, str(layout.root))
        self.assertEqual(status.jar_path, str(layout.jar_path))

    def test_install_tika_runtime_uses_download_and_extract_hooks(self) -> None:
        paths = ensure_data_paths(str(TEST_ROOT), str(SAMPLE_ROOT))

        def fake_download(_url: str, destination: Path, **_kwargs) -> None:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(b'bundle')

        def fake_extract(_archive: Path, destination: Path) -> Path:
            extracted = destination / 'jdk-21'
            (extracted / 'bin').mkdir(parents=True, exist_ok=True)
            (extracted / 'bin' / 'java.exe').write_text('', encoding='utf-8')
            return extracted

        fake_temp_root = TEST_ROOT / 'manual_temp_runtime'
        with patch('omniclip_rag.extensions.runtimes.tika_runtime._download_file', side_effect=fake_download), patch(
            'omniclip_rag.extensions.runtimes.tika_runtime._extract_jre_archive', side_effect=fake_extract
        ), patch(
            'omniclip_rag.extensions.runtimes.tika_runtime.tempfile.TemporaryDirectory', return_value=_FakeTemporaryDirectory(fake_temp_root)
        ):
            status = install_tika_runtime(paths)

        layout = runtime_layout(paths)
        self.assertTrue(status.installed)
        self.assertTrue((layout.jre_root / 'bin' / 'java.exe').exists())
        self.assertTrue(layout.jar_path.exists())

    def test_tika_sidecar_manager_start_and_stop_tracks_process_health(self) -> None:
        paths = ensure_data_paths(str(TEST_ROOT), str(SAMPLE_ROOT))
        layout = runtime_layout(paths)
        (layout.jre_root / 'bin').mkdir(parents=True, exist_ok=True)
        (layout.jre_root / 'bin' / 'java.exe').write_text('', encoding='utf-8')
        layout.jar_path.parent.mkdir(parents=True, exist_ok=True)
        layout.jar_path.write_text('', encoding='utf-8')
        fake_process = _FakeProcess()
        manager = TikaSidecarManager(port=9998, health_timeout=2.0, poll_interval=0.01)

        with patch('omniclip_rag.extensions.runtimes.tika_runtime.subprocess.Popen', return_value=fake_process), patch(
            'omniclip_rag.extensions.runtimes.tika_runtime._assign_process_kill_on_close_job', return_value=None
        ), patch(
            'omniclip_rag.extensions.runtimes.tika_runtime.check_tika_health', side_effect=[False, True, True]
        ), patch('omniclip_rag.extensions.runtimes.tika_runtime.time.sleep', return_value=None):
            status = manager.ensure_started(paths)
            self.assertTrue(status.running)
            self.assertTrue(status.healthy)
            self.assertEqual(status.pid, 4321)
            stopped = manager.stop()

        self.assertTrue(fake_process.terminate_called)
        self.assertEqual(stopped.port, 9998)

    def test_check_tika_health_treats_http_405_as_ready(self) -> None:
        import urllib.error

        with patch('omniclip_rag.extensions.runtimes.tika_runtime.urllib.request.urlopen', side_effect=urllib.error.HTTPError('http://127.0.0.1:9998/tika', 405, 'Method Not Allowed', {}, None)):
            from omniclip_rag.extensions.runtimes.tika_runtime import check_tika_health

            self.assertTrue(check_tika_health())


if __name__ == '__main__':
    unittest.main()
