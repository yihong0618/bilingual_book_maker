"""
Unit tests for ProgressMonitor
"""

import pytest
import time
import threading
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from api.progress_monitor import (
    ProgressMonitor,
    ProgressUpdate,
    TqdmInterceptor,
    AsyncProgressTracker,
    patch_tqdm_for_job,
)


class TestProgressMonitor:
    """Test suite for ProgressMonitor"""

    @pytest.fixture
    def progress_monitor(self):
        """Create a ProgressMonitor instance for testing"""
        return ProgressMonitor()

    def test_register_callback(self, progress_monitor):
        """Test callback registration"""
        callback = Mock()
        job_id = "test-job-123"

        progress_monitor.register_callback(job_id, callback)

        assert job_id in progress_monitor._callbacks
        assert job_id in progress_monitor._active_jobs
        assert progress_monitor._callbacks[job_id] == callback

    def test_unregister_callback(self, progress_monitor):
        """Test callback unregistration"""
        callback = Mock()
        job_id = "test-job-123"

        progress_monitor.register_callback(job_id, callback)
        progress_monitor.unregister_callback(job_id)

        assert job_id not in progress_monitor._callbacks
        assert job_id not in progress_monitor._active_jobs

    def test_update_progress(self, progress_monitor):
        """Test progress update"""
        callback = Mock()
        job_id = "test-job-123"

        progress_monitor.register_callback(job_id, callback)
        progress_monitor.update_progress(
            job_id, current=50, total=100, description="Processing"
        )

        # Verify callback was called
        callback.assert_called_once()
        update = callback.call_args[0][0]

        assert isinstance(update, ProgressUpdate)
        assert update.job_id == job_id
        assert update.current == 50
        assert update.total == 100
        assert update.percentage == 50.0
        assert update.description == "Processing"

    def test_update_progress_no_callback(self, progress_monitor):
        """Test progress update without registered callback"""
        # Should not raise an error
        progress_monitor.update_progress("non-existent-job", 50, 100)

    def test_get_job_progress(self, progress_monitor):
        """Test getting job progress information"""
        callback = Mock()
        job_id = "test-job-123"

        progress_monitor.register_callback(job_id, callback)
        progress_monitor.update_progress(job_id, 75, 100)

        progress_info = progress_monitor.get_job_progress(job_id)

        assert progress_info is not None
        assert progress_info["current"] == 75
        assert progress_info["total"] == 100
        assert "start_time" in progress_info
        assert "last_update" in progress_info

    def test_monitor_job_context_manager(self, progress_monitor):
        """Test monitor_job context manager"""
        callback = Mock()
        job_id = "test-job-123"

        with progress_monitor.monitor_job(job_id, callback) as monitor:
            assert job_id in progress_monitor._callbacks
            monitor.update_progress(job_id, 25, 100)

        # After context, callback should be unregistered
        assert job_id not in progress_monitor._callbacks

    def test_callback_error_handling(self, progress_monitor):
        """Test error handling in callback execution"""

        def failing_callback(update):
            raise ValueError("Callback error")

        job_id = "test-job-123"
        progress_monitor.register_callback(job_id, failing_callback)

        # Should not raise an error
        progress_monitor.update_progress(job_id, 50, 100)

    def test_thread_safety(self, progress_monitor):
        """Test thread safety of progress monitor"""
        callbacks_called = []
        lock = threading.Lock()

        def thread_callback(update):
            with lock:
                callbacks_called.append(update.job_id)

        # Register callbacks for multiple jobs
        job_ids = [f"job-{i}" for i in range(10)]
        for job_id in job_ids:
            progress_monitor.register_callback(job_id, thread_callback)

        # Update progress concurrently
        def update_worker(job_id):
            progress_monitor.update_progress(job_id, 100, 100)

        threads = []
        for job_id in job_ids:
            thread = threading.Thread(target=update_worker, args=(job_id,))
            threads.append(thread)
            thread.start()

        # Wait for all threads
        for thread in threads:
            thread.join()

        # All callbacks should have been called
        assert len(callbacks_called) == 10
        assert set(callbacks_called) == set(job_ids)


class TestTqdmInterceptor:
    """Test suite for TqdmInterceptor"""

    def test_set_and_clear_monitor(self):
        """Test setting and clearing monitor"""
        monitor = ProgressMonitor()
        job_id = "test-job"

        TqdmInterceptor.set_monitor(monitor, job_id)
        assert TqdmInterceptor._monitor == monitor
        assert TqdmInterceptor._job_id == job_id

        TqdmInterceptor.clear_monitor()
        assert TqdmInterceptor._monitor is None
        assert TqdmInterceptor._job_id is None

    def test_update_with_monitor(self):
        """Test tqdm update with monitor"""
        monitor = ProgressMonitor()
        callback = Mock()
        job_id = "test-job"

        monitor.register_callback(job_id, callback)
        TqdmInterceptor.set_monitor(monitor, job_id)

        # Create tqdm instance and update
        pbar = TqdmInterceptor(total=100)
        pbar.update(50)

        # Callback should be called (may need to wait due to timing)
        time.sleep(0.1)

        pbar.close()
        TqdmInterceptor.clear_monitor()

    def test_update_without_monitor(self):
        """Test tqdm update without monitor"""
        # Should work like normal tqdm
        pbar = TqdmInterceptor(total=100)
        pbar.update(50)
        assert pbar.n == 50
        pbar.close()

    def test_close_with_monitor(self):
        """Test tqdm close with monitor"""
        monitor = ProgressMonitor()
        callback = Mock()
        job_id = "test-job"

        monitor.register_callback(job_id, callback)
        TqdmInterceptor.set_monitor(monitor, job_id)

        pbar = TqdmInterceptor(total=100)
        pbar.update(100)
        pbar.close()

        # Final progress update should be sent on close
        time.sleep(0.1)

        TqdmInterceptor.clear_monitor()


class TestAsyncProgressTracker:
    """Test suite for AsyncProgressTracker"""

    @pytest.fixture
    def progress_tracker(self):
        """Create an AsyncProgressTracker instance for testing"""
        return AsyncProgressTracker()

    def test_start_stop_tracking(self, progress_tracker):
        """Test starting and stopping progress tracking"""
        callback = Mock()
        job_id = "test-job"

        progress_tracker.start_tracking(job_id, callback)
        assert job_id in progress_tracker.monitor._callbacks

        progress_tracker.stop_tracking(job_id)
        assert job_id not in progress_tracker.monitor._callbacks

    def test_estimate_duration(self, progress_tracker):
        """Test duration estimation"""
        # Test different paragraph counts
        assert "seconds" in progress_tracker.estimate_duration("job1", 10)
        assert "minutes" in progress_tracker.estimate_duration("job2", 100)
        assert "h" in progress_tracker.estimate_duration("job3", 5000)

    def test_get_progress_percentage(self, progress_tracker):
        """Test getting progress percentage"""
        callback = Mock()
        job_id = "test-job"

        # No progress initially
        assert progress_tracker.get_progress_percentage(job_id) == 0.0

        # Register and update progress
        progress_tracker.start_tracking(job_id, callback)
        progress_tracker.monitor.update_progress(job_id, 50, 100)

        percentage = progress_tracker.get_progress_percentage(job_id)
        assert percentage == 50.0

    def test_create_tqdm_patch(self, progress_tracker):
        """Test creating tqdm patch"""
        job_id = "test-job"
        callback = Mock()

        progress_tracker.start_tracking(job_id, callback)

        # Test that patch context manager can be created
        patch_context = progress_tracker.create_tqdm_patch(job_id)
        assert patch_context is not None

        # Test using the patch
        with patch_context:
            # Import tqdm here to get the patched version
            import tqdm

            pbar = tqdm.tqdm(total=100)
            pbar.update(25)
            pbar.close()


def test_patch_tqdm_for_job():
    """Test patch_tqdm_for_job function"""
    monitor = ProgressMonitor()
    callback = Mock()
    job_id = "test-job"

    monitor.register_callback(job_id, callback)

    with patch_tqdm_for_job(monitor, job_id):
        # Import tqdm to get patched version
        import tqdm

        # Verify tqdm is patched
        assert tqdm.tqdm == TqdmInterceptor

        # Use patched tqdm
        pbar = tqdm.tqdm(total=100, desc="Test")
        pbar.update(50)
        pbar.close()

    # After context, tqdm should be restored
    import tqdm

    assert tqdm.tqdm != TqdmInterceptor


if __name__ == "__main__":
    pytest.main([__file__])
