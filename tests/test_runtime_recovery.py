import builtins
from pathlib import Path
import os
import shutil
import unittest
from unittest.mock import patch

from omniclip_rag.config import ensure_data_paths
from omniclip_rag.runtime_recovery import mark_session_started, prepare_startup_recovery, record_runtime_incident
from omniclip_rag.vector_index import detect_acceleration

ROOT = Path(__file__).resolve().parents[1]
TEST_DATA_ROOT = ROOT / '.tmp' / 'test_runtime_recovery'


class RuntimeRecoveryTests(unittest.TestCase):
    def tearDown(self) -> None:
        os.environ.pop('OMNICLIP_SAFE_STARTUP', None)
        if TEST_DATA_ROOT.exists():
            shutil.rmtree(TEST_DATA_ROOT)

    def test_prepare_startup_recovery_enables_safe_mode_after_memory_incident(self) -> None:
        data_paths = ensure_data_paths(str(TEST_DATA_ROOT / 'safe_startup'))
        mark_session_started(data_paths, version='test')
        record_runtime_incident(data_paths, kind='vector_oom', detail='oom', phase='vectorizing')
        with patch('omniclip_rag.vector_index.release_process_vector_resources') as release_mock:
            recovery = prepare_startup_recovery(data_paths)
        self.assertTrue(recovery['safe_startup'])
        self.assertEqual(recovery['incident_kind'], 'vector_oom')
        self.assertEqual(os.environ.get('OMNICLIP_SAFE_STARTUP'), '1')
        release_mock.assert_called_once()

    def test_detect_acceleration_safe_mode_skips_torch_probe(self) -> None:
        original_import = builtins.__import__

        def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
            if name in {'torch', 'sentence_transformers'}:
                raise AssertionError(f'{name} should not be imported in safe mode')
            return original_import(name, globals, locals, fromlist, level)

        with patch('omniclip_rag.vector_index._ACCELERATION_CACHE', None),              patch('omniclip_rag.vector_index.inspect_runtime_environment', return_value={'runtime_exists': True, 'runtime_complete': True, 'runtime_missing_items': []}),              patch('omniclip_rag.vector_index._detect_nvidia_gpus', return_value=['NVIDIA RTX']),              patch('omniclip_rag.vector_index._detect_nvcc_version', return_value='12.3'),              patch('builtins.__import__', side_effect=guarded_import):
            payload = detect_acceleration(safe_mode=True)

        self.assertTrue(payload['safe_mode'])
        self.assertTrue(payload['gpu_present'])
        self.assertFalse(payload['cuda_available'])
        self.assertEqual(payload['torch_error'], 'safe startup deferred torch probe')


if __name__ == '__main__':
    unittest.main()
