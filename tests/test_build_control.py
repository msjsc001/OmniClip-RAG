import unittest

from omniclip_rag.build_control import BuildPerformanceController, ResourceSample
from omniclip_rag.config import AppConfig


class _FakeMonitor:
    def __init__(self, samples):
        self.samples = list(samples)
        self.index = 0
        self.sample_interval_seconds = 0.0

    def sample(self, *, force: bool = False):
        if self.index >= len(self.samples):
            return self.samples[-1]
        value = self.samples[self.index]
        self.index += 1
        return value


class BuildControlTests(unittest.TestCase):
    def test_cuda_headroom_expands_encode_batch(self) -> None:
        config = AppConfig(vault_path='.', data_root='.', build_resource_profile='peak', vector_batch_size=16)
        monitor = _FakeMonitor([ResourceSample(timestamp=10.0, cpu_percent=24.0, memory_percent=45.0, gpu_percent=18.0, gpu_memory_percent=32.0)])
        controller = BuildPerformanceController(config, 'cuda', monitor=monitor)
        before = controller.current_encode_batch_size
        snapshot = controller.observe(encode_elapsed_ms=120.0, write_elapsed_ms=20.0)
        self.assertGreaterEqual(controller.current_encode_batch_size, before)
        self.assertIn(snapshot.action, {'expand', 'steady'})

    def test_oom_recovery_shrinks_batches(self) -> None:
        config = AppConfig(vault_path='.', data_root='.', build_resource_profile='balanced', vector_batch_size=16)
        monitor = _FakeMonitor([ResourceSample(timestamp=20.0, cpu_percent=30.0, memory_percent=50.0, gpu_percent=70.0, gpu_memory_percent=70.0)])
        controller = BuildPerformanceController(config, 'cuda', monitor=monitor)
        encode_before = controller.current_encode_batch_size
        write_before = controller.current_write_batch_size
        snapshot = controller.note_oom()
        self.assertLess(controller.current_encode_batch_size, encode_before)
        self.assertLess(controller.current_write_batch_size, write_before)
        self.assertEqual(snapshot.reason, 'oom_recovery')
        self.assertEqual(snapshot.action, 'shrink')


if __name__ == '__main__':
    unittest.main()
