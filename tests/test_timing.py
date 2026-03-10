import unittest

from omniclip_rag.config import AppConfig
from omniclip_rag.timing import BuildEtaTracker, estimate_remaining_build_seconds


class TimingTests(unittest.TestCase):
    def test_recent_vector_slowdown_increases_remaining_estimate(self) -> None:
        config = AppConfig(vault_path='.', data_root='.', vector_backend='lancedb', vector_device='cuda', vector_model='BAAI/bge-m3')
        base_remaining, _ = estimate_remaining_build_seconds(
            config,
            stage='vectorizing',
            current=8000,
            total=10000,
            elapsed_total=3600.0,
            stage_elapsed=1600.0,
            parsed_chunks=10000,
            estimated_total_chunks=10000,
            history_entry=None,
            vector_enabled=True,
            model_ready=True,
        )
        slower_remaining, _ = estimate_remaining_build_seconds(
            config,
            stage='vectorizing',
            current=8000,
            total=10000,
            elapsed_total=3600.0,
            stage_elapsed=1600.0,
            parsed_chunks=10000,
            estimated_total_chunks=10000,
            history_entry=None,
            vector_enabled=True,
            model_ready=True,
            recent_observed_rate=0.2,
        )
        self.assertGreater(slower_remaining, base_remaining)

    def test_eta_tracker_prefers_recent_window_for_vector_stage(self) -> None:
        config = AppConfig(vault_path='.', data_root='.', vector_backend='lancedb', vector_device='cuda', vector_model='BAAI/bge-m3')
        fast_tracker = BuildEtaTracker(config, history_entry=None, vector_enabled=True, model_ready=True)
        fast_tracker.estimate(
            stage='vectorizing',
            current=2000,
            total=10000,
            elapsed_total=1200.0,
            stage_elapsed=600.0,
            parsed_chunks=10000,
            estimated_total_chunks=10000,
            timestamp=10.0,
        )
        fast_tracker.estimate(
            stage='vectorizing',
            current=6000,
            total=10000,
            elapsed_total=1800.0,
            stage_elapsed=1200.0,
            parsed_chunks=10000,
            estimated_total_chunks=10000,
            timestamp=30.0,
        )
        fast_remaining, _ = fast_tracker.estimate(
            stage='vectorizing',
            current=7000,
            total=10000,
            elapsed_total=2400.0,
            stage_elapsed=1800.0,
            parsed_chunks=10000,
            estimated_total_chunks=10000,
            timestamp=35.0,
        )

        slow_tracker = BuildEtaTracker(config, history_entry=None, vector_enabled=True, model_ready=True)
        slow_tracker.estimate(
            stage='vectorizing',
            current=2000,
            total=10000,
            elapsed_total=1200.0,
            stage_elapsed=600.0,
            parsed_chunks=10000,
            estimated_total_chunks=10000,
            timestamp=10.0,
        )
        slow_tracker.estimate(
            stage='vectorizing',
            current=6000,
            total=10000,
            elapsed_total=1800.0,
            stage_elapsed=1200.0,
            parsed_chunks=10000,
            estimated_total_chunks=10000,
            timestamp=30.0,
        )
        slow_remaining, _ = slow_tracker.estimate(
            stage='vectorizing',
            current=7000,
            total=10000,
            elapsed_total=2400.0,
            stage_elapsed=1800.0,
            parsed_chunks=10000,
            estimated_total_chunks=10000,
            timestamp=90.0,
        )
        self.assertGreater(slow_remaining, fast_remaining)

    def test_history_tail_rate_biases_future_vector_eta(self) -> None:
        config = AppConfig(vault_path='.', data_root='.', vector_backend='lancedb', vector_device='cuda', vector_model='BAAI/bge-m3')
        baseline, _ = estimate_remaining_build_seconds(
            config,
            stage='vectorizing',
            current=0,
            total=10000,
            elapsed_total=10.0,
            stage_elapsed=0.0,
            parsed_chunks=10000,
            estimated_total_chunks=10000,
            history_entry=None,
            vector_enabled=True,
            model_ready=True,
        )
        slower_history, _ = estimate_remaining_build_seconds(
            config,
            stage='vectorizing',
            current=0,
            total=10000,
            elapsed_total=10.0,
            stage_elapsed=0.0,
            parsed_chunks=10000,
            estimated_total_chunks=10000,
            history_entry={
                'files': 1000,
                'chunks': 10000,
                'indexing_seconds': 120.0,
                'rendering_seconds': 50.0,
                'vectorizing_seconds': 180.0,
                'vector_tail_seconds_per_chunk': 0.12,
            },
            vector_enabled=True,
            model_ready=True,
        )
        self.assertGreater(slower_history, baseline)



if __name__ == '__main__':
    unittest.main()
