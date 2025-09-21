"""
Unit tests for JobManager
"""
import pytest
import time
import threading
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from api.job_manager import JobManager
from api.models import TranslationJob, JobStatus


class TestJobManager:
    """Test suite for JobManager"""

    @pytest.fixture
    def job_manager(self):
        """Create a JobManager instance for testing"""
        return JobManager(max_workers=2, job_ttl_hours=1)

    @pytest.fixture
    def cleanup_dirs(self):
        """Clean up test directories after test"""
        yield
        # Cleanup
        for directory in ["uploads", "outputs", "temp"]:
            path = Path(directory)
            if path.exists():
                import shutil
                shutil.rmtree(path, ignore_errors=True)

    def test_create_job(self, job_manager):
        """Test job creation"""
        job = job_manager.create_job(
            filename="test.epub",
            model="chatgpt",
            target_language="zh-cn"
        )

        assert job.filename == "test.epub"
        assert job.model == "chatgpt"
        assert job.target_language == "zh-cn"
        assert job.status == JobStatus.PENDING
        assert job.job_id is not None
        assert isinstance(job.created_at, datetime)

        # Verify job is stored
        retrieved_job = job_manager.get_job(job.job_id)
        assert retrieved_job == job

    def test_get_job_not_found(self, job_manager):
        """Test getting non-existent job"""
        job = job_manager.get_job("non-existent-id")
        assert job is None

    def test_get_all_jobs(self, job_manager):
        """Test getting all jobs"""
        # Initially empty
        jobs = job_manager.get_all_jobs()
        assert len(jobs) == 0

        # Create some jobs
        job1 = job_manager.create_job("test1.epub", "chatgpt", "zh-cn")
        job2 = job_manager.create_job("test2.epub", "claude", "en")

        jobs = job_manager.get_all_jobs()
        assert len(jobs) == 2
        job_ids = [job.job_id for job in jobs]
        assert job1.job_id in job_ids
        assert job2.job_id in job_ids

    def test_get_active_jobs(self, job_manager):
        """Test getting active jobs"""
        # Create jobs with different statuses
        job1 = job_manager.create_job("test1.epub", "chatgpt", "zh-cn")
        job2 = job_manager.create_job("test2.epub", "claude", "en")

        # Mark one as completed
        job2.status = JobStatus.COMPLETED

        active_jobs = job_manager.get_active_jobs()
        assert len(active_jobs) == 1
        assert active_jobs[0].job_id == job1.job_id

    def test_start_job_success(self, job_manager):
        """Test successful job start"""
        job = job_manager.create_job("test.epub", "chatgpt", "zh-cn")

        # Mock translation function that takes some time
        def slow_translation_func(job):
            time.sleep(0.05)  # Small delay to ensure we can check PROCESSING status
            return "/path/to/output.epub"

        success = job_manager.start_job(
            job_id=job.job_id,
            translation_func=slow_translation_func
        )

        assert success is True
        # Job should be PROCESSING immediately after start
        assert job.status == JobStatus.PROCESSING

        # Wait for job to complete
        time.sleep(0.1)
        assert job.status == JobStatus.COMPLETED
        assert job.output_path == "/path/to/output.epub"

    def test_start_job_not_found(self, job_manager):
        """Test starting non-existent job"""
        mock_translation_func = Mock()

        success = job_manager.start_job(
            job_id="non-existent-id",
            translation_func=mock_translation_func
        )

        assert success is False

    def test_start_job_wrong_status(self, job_manager):
        """Test starting job with wrong status"""
        job = job_manager.create_job("test.epub", "chatgpt", "zh-cn")
        job.status = JobStatus.COMPLETED

        mock_translation_func = Mock()

        success = job_manager.start_job(
            job_id=job.job_id,
            translation_func=mock_translation_func
        )

        assert success is False

    def test_cancel_job(self, job_manager):
        """Test job cancellation"""
        job = job_manager.create_job("test.epub", "chatgpt", "zh-cn")

        # Cancel pending job
        success = job_manager.cancel_job(job.job_id)
        assert success is True
        assert job.status == JobStatus.CANCELLED

    def test_cancel_completed_job(self, job_manager):
        """Test cancelling already completed job"""
        job = job_manager.create_job("test.epub", "chatgpt", "zh-cn")
        job.status = JobStatus.COMPLETED

        success = job_manager.cancel_job(job.job_id)
        assert success is False

    def test_update_job_progress(self, job_manager):
        """Test updating job progress"""
        job = job_manager.create_job("test.epub", "chatgpt", "zh-cn")

        job_manager.update_job_progress(job.job_id, processed=50, total=100)

        assert job.processed_paragraphs == 50
        assert job.total_paragraphs == 100
        assert job.progress == 50

    def test_job_stats(self, job_manager):
        """Test job statistics"""
        # Initially empty
        stats = job_manager.get_job_stats()
        assert stats["total"] == 0
        assert stats["active"] == 0

        # Create jobs with different statuses
        job1 = job_manager.create_job("test1.epub", "chatgpt", "zh-cn")
        job2 = job_manager.create_job("test2.epub", "claude", "en")
        job3 = job_manager.create_job("test3.epub", "gemini", "fr")

        job2.status = JobStatus.COMPLETED
        job3.status = JobStatus.FAILED

        stats = job_manager.get_job_stats()
        assert stats["total"] == 3
        assert stats["pending"] == 1
        assert stats["completed"] == 1
        assert stats["failed"] == 1
        assert stats["active"] == 1

    def test_cleanup_expired_jobs(self, job_manager):
        """Test cleanup of expired jobs"""
        # Create job and mark as completed
        job = job_manager.create_job("test.epub", "chatgpt", "zh-cn")
        job.status = JobStatus.COMPLETED
        job.completed_at = datetime.now() - timedelta(hours=2)  # Expired

        # Cleanup should remove the job
        cleaned_count = job_manager.cleanup_expired_jobs()
        assert cleaned_count == 1

        # Job should be gone
        retrieved_job = job_manager.get_job(job.job_id)
        assert retrieved_job is None

    def test_cleanup_non_expired_jobs(self, job_manager):
        """Test cleanup doesn't remove non-expired jobs"""
        # Create recent completed job
        job = job_manager.create_job("test.epub", "chatgpt", "zh-cn")
        job.status = JobStatus.COMPLETED
        job.completed_at = datetime.now() - timedelta(minutes=30)  # Not expired

        # Cleanup should not remove the job
        cleaned_count = job_manager.cleanup_expired_jobs()
        assert cleaned_count == 0

        # Job should still exist
        retrieved_job = job_manager.get_job(job.job_id)
        assert retrieved_job is not None

    def test_cleanup_active_jobs(self, job_manager):
        """Test cleanup doesn't remove active jobs"""
        # Create old pending job
        job = job_manager.create_job("test.epub", "chatgpt", "zh-cn")
        job.created_at = datetime.now() - timedelta(hours=2)

        # Cleanup should not remove active job
        cleaned_count = job_manager.cleanup_expired_jobs()
        assert cleaned_count == 0

        # Job should still exist
        retrieved_job = job_manager.get_job(job.job_id)
        assert retrieved_job is not None

    def test_path_generation(self, job_manager, cleanup_dirs):
        """Test path generation methods"""
        upload_path = job_manager.get_upload_path("test.epub")
        # Upload path now includes unique prefix to avoid conflicts
        assert upload_path.parent == Path("uploads")
        assert upload_path.name.endswith("_test.epub")
        assert len(upload_path.name) == len("12345678_test.epub")  # 8-char prefix + underscore + filename

        output_path = job_manager.get_output_path("job123", "test.epub")
        assert output_path == Path("outputs") / "test_bilingual_job123.epub"

        temp_path = job_manager.get_temp_path("job123")
        assert temp_path == Path("temp") / "job123"

    def test_concurrent_job_creation(self, job_manager):
        """Test thread-safe job creation"""
        job_ids = []
        lock = threading.Lock()

        def create_job_worker(i):
            job = job_manager.create_job(f"test{i}.epub", "chatgpt", "zh-cn")
            with lock:
                job_ids.append(job.job_id)

        # Create jobs concurrently
        threads = []
        for i in range(10):
            thread = threading.Thread(target=create_job_worker, args=(i,))
            threads.append(thread)
            thread.start()

        # Wait for all threads to complete
        for thread in threads:
            thread.join()

        # All jobs should be created with unique IDs
        assert len(job_ids) == 10
        assert len(set(job_ids)) == 10  # All unique

    def test_job_execution_error_handling(self, job_manager):
        """Test error handling in job execution"""
        job = job_manager.create_job("test.epub", "chatgpt", "zh-cn")

        # Mock translation function that raises an error
        def failing_translation_func(job):
            raise ValueError("Translation failed")

        success = job_manager.start_job(
            job_id=job.job_id,
            translation_func=failing_translation_func
        )

        assert success is True
        assert job.status == JobStatus.PROCESSING

        # Wait for job to fail
        time.sleep(0.1)
        assert job.status == JobStatus.FAILED
        assert "Translation failed" in job.error_message

    def test_shutdown(self, job_manager):
        """Test job manager shutdown"""
        # Create and start a long-running job
        job = job_manager.create_job("test.epub", "chatgpt", "zh-cn")

        def long_running_func(job):
            time.sleep(1)
            return "/output/path"

        job_manager.start_job(job.job_id, long_running_func)

        # Shutdown immediately
        job_manager.shutdown(wait=False)

        # Job should be cancelled or processing state maintained
        assert job.status in [JobStatus.PROCESSING, JobStatus.CANCELLED]


if __name__ == "__main__":
    pytest.main([__file__])