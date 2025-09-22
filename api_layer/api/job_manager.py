"""
Job management system for async translation operations
Handles job lifecycle, storage, and cleanup with thread safety
"""
import threading
import time
import uuid
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Callable
from concurrent.futures import ThreadPoolExecutor, Future
import logging
import os
from pathlib import Path

from .models import TranslationJob, JobStatus
from .log_parser import progress_parser
from .progress_monitor import ProgressUpdate, global_progress_tracker
from .config import settings


logger = logging.getLogger(__name__)


class JobManager:
    """
    Thread-safe job manager for async translation operations
    Handles job storage, lifecycle management, and cleanup
    """

    # Class constants for magic numbers
    INITIAL_COUNT = 0
    INCREMENT_STEP = 1
    UUID_PREFIX_LENGTH = 8

    def __init__(self, max_workers: int = None, job_ttl_hours: int = None, cleanup_interval_minutes: int = None):
        """
        Initialize job manager with configurable settings

        Args:
            max_workers: Maximum number of concurrent translation jobs (uses config default if None)
            job_ttl_hours: Time-to-live for completed jobs in hours (uses config default if None)
            cleanup_interval_minutes: Cleanup interval in minutes (uses config default if None)
        """
        # Use configuration defaults if not provided
        max_workers = max_workers or settings.max_workers
        job_ttl_hours = job_ttl_hours or settings.job_ttl_hours
        cleanup_interval_minutes = cleanup_interval_minutes or settings.cleanup_interval_minutes

        self._jobs: Dict[str, TranslationJob] = {}
        self._job_futures: Dict[str, Future] = {}
        self._lock = threading.RLock()
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="translation-")
        self._job_ttl = timedelta(hours=job_ttl_hours)
        self._cleanup_interval = timedelta(minutes=cleanup_interval_minutes)
        self._last_cleanup = datetime.now()

        # Storage paths from configuration
        self._upload_dir = Path(settings.upload_dir)
        self._output_dir = Path(settings.output_dir)
        self._temp_dir = Path(settings.temp_dir)

        # Create directories
        for directory in [self._upload_dir, self._output_dir, self._temp_dir]:
            directory.mkdir(exist_ok=True)

        logger.info(f"JobManager initialized with {max_workers} workers, {job_ttl_hours}h TTL, {cleanup_interval_minutes}min cleanup interval")
        logger.info(f"Storage paths - Upload: {self._upload_dir}, Output: {self._output_dir}, Temp: {self._temp_dir}")

    def create_job(
        self,
        filename: str,
        model: str,
        target_language: str,
        **kwargs
    ) -> TranslationJob:
        """
        Create a new translation job

        Args:
            filename: Source file name
            model: Translation model to use
            target_language: Target language code
            **kwargs: Additional translation parameters

        Returns:
            Created TranslationJob instance
        """
        job_id = str(uuid.uuid4())

        job = TranslationJob(
            job_id=job_id,
            status=JobStatus.PENDING,
            filename=filename,
            created_at=datetime.now(),
            model=model,
            target_language=target_language,
            **kwargs
        )

        with self._lock:
            self._jobs[job_id] = job

        logger.info(f"Created job {job_id} for file {filename}")
        return job

    def get_job(self, job_id: str) -> Optional[TranslationJob]:
        """Get job by ID"""
        with self._lock:
            return self._jobs.get(job_id)

    def get_all_jobs(self) -> List[TranslationJob]:
        """Get all jobs"""
        with self._lock:
            return list(self._jobs.values())

    def get_active_jobs(self) -> List[TranslationJob]:
        """Get all active (pending or processing) jobs"""
        with self._lock:
            return [
                job for job in self._jobs.values()
                if job.status in [JobStatus.PENDING, JobStatus.PROCESSING]
            ]

    def start_job(
        self,
        job_id: str,
        translation_func: Callable[[TranslationJob], str],
        progress_callback: Optional[Callable[[ProgressUpdate], None]] = None
    ) -> bool:
        """
        Start executing a translation job

        Args:
            job_id: Job ID to start
            translation_func: Function to execute for translation
            progress_callback: Optional progress callback

        Returns:
            True if job was started successfully, False otherwise
        """
        job = self.get_job(job_id)
        if not job:
            logger.error(f"Job {job_id} not found")
            return False

        if job.status != JobStatus.PENDING:
            logger.warning(f"Job {job_id} is not in PENDING status: {job.status}")
            return False

        # Set up progress tracking
        if progress_callback:
            logger.warning(f"DEBUG: Registering progress callback for job {job_id}")
            global_progress_tracker.start_tracking(job_id, progress_callback)
        else:
            logger.warning(f"DEBUG: No progress callback provided for job {job_id}")

        # Update job status
        with self._lock:
            job.status = JobStatus.PROCESSING

        # Submit job to executor
        future = self._executor.submit(self._execute_job, job, translation_func)
        with self._lock:
            self._job_futures[job_id] = future

        logger.info(f"Started job {job_id}")
        return True

    def cancel_job(self, job_id: str) -> bool:
        """
        Cancel a running job

        Args:
            job_id: Job ID to cancel

        Returns:
            True if job was cancelled successfully, False otherwise
        """
        job = self.get_job(job_id)
        if not job:
            return False

        with self._lock:
            if job.status in [JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED]:
                return False

            job.mark_cancelled()

            # Cancel future if it exists
            future = self._job_futures.get(job_id)
            if future:
                future.cancel()

        # Stop progress tracking
        global_progress_tracker.stop_tracking(job_id)

        logger.info(f"Cancelled job {job_id}")
        return True

    def _execute_job(self, job: TranslationJob, translation_func: Callable[[TranslationJob], str]) -> None:
        """
        Execute a translation job in the thread pool

        Args:
            job: Job to execute
            translation_func: Function to call for translation
        """
        job_id = job.job_id
        try:
            logger.info(f"Executing job {job_id}")

            # Call the translation function
            output_path = translation_func(job)

            # Mark job as completed
            with self._lock:
                job.mark_completed(output_path)

            logger.info(f"Job {job_id} completed successfully: {output_path}")

        except Exception as e:
            logger.error(f"Job {job_id} failed: {str(e)}", exc_info=True)

            with self._lock:
                job.mark_failed(str(e))

        finally:
            # Clean up future reference
            with self._lock:
                self._job_futures.pop(job_id, None)

            # Stop progress tracking
            global_progress_tracker.stop_tracking(job_id)

            # Trigger cleanup if needed
            self._cleanup_if_needed()

    def update_job_progress(self, job_id: str, processed: int, total: Optional[int] = None) -> None:
        """Update job progress"""
        job = self.get_job(job_id)
        if job:
            with self._lock:
                job.update_progress(processed, total)

    def update_progress_from_logs(self, job_id: str) -> bool:
        """
        Update job progress by parsing Docker logs

        Returns:
            True if progress was updated, False otherwise
        """
        try:
            progress_info = progress_parser.get_job_progress(job_id)
            if progress_info:
                self.update_job_progress(
                    job_id=job_id,
                    processed=progress_info['current'],
                    total=progress_info['total']
                )
                logger.debug(f"Updated progress from logs for job {job_id}: {progress_info['current']}/{progress_info['total']} ({progress_info['percentage']}%)")
                return True
            return False
        except Exception as e:
            logger.error(f"Error updating progress from logs for job {job_id}: {e}")
            return False

    def cleanup_expired_jobs(self) -> int:
        """
        Clean up expired jobs and their associated files

        Returns:
            Number of jobs cleaned up
        """
        cutoff_time = datetime.now() - self._job_ttl
        jobs_to_remove = []

        with self._lock:
            for job_id, job in self._jobs.items():
                # Only clean up completed, failed, or cancelled jobs
                if (job.status in [JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED] and
                    job.completed_at and job.completed_at < cutoff_time):
                    jobs_to_remove.append(job_id)

        # Remove expired jobs
        cleaned_count = self.INITIAL_COUNT
        for job_id in jobs_to_remove:
            if self._remove_job(job_id):
                cleaned_count += self.INCREMENT_STEP

        if cleaned_count > self.INITIAL_COUNT:
            logger.info(f"Cleaned up {cleaned_count} expired jobs")

        self._last_cleanup = datetime.now()
        return cleaned_count

    def _remove_job(self, job_id: str) -> bool:
        """
        Remove a job and clean up its files

        Args:
            job_id: Job ID to remove

        Returns:
            True if job was removed successfully
        """
        job = None
        with self._lock:
            job = self._jobs.pop(job_id, None)
            future = self._job_futures.pop(job_id, None)

            # Cancel future if still running
            if future and not future.done():
                future.cancel()

        if not job:
            return False

        # Clean up files
        try:
            # Remove output file if exists
            if job.output_path and os.path.exists(job.output_path):
                os.remove(job.output_path)

            # Remove temp files
            temp_files = [
                self._temp_dir / f"{job_id}*",
                self._upload_dir / job.filename,
            ]

            for pattern in temp_files:
                for file_path in Path().glob(str(pattern)):
                    if file_path.exists():
                        file_path.unlink()

        except Exception as e:
            logger.warning(f"Error cleaning up files for job {job_id}: {e}")

        logger.debug(f"Removed job {job_id}")
        return True

    def _cleanup_if_needed(self) -> None:
        """Trigger cleanup if enough time has passed"""
        if datetime.now() - self._last_cleanup >= self._cleanup_interval:
            self.cleanup_expired_jobs()

    def get_job_stats(self) -> Dict[str, int]:
        """Get job statistics"""
        with self._lock:
            stats = {
                "total": len(self._jobs),
                "pending": self.INITIAL_COUNT,
                "processing": self.INITIAL_COUNT,
                "completed": self.INITIAL_COUNT,
                "failed": self.INITIAL_COUNT,
                "cancelled": self.INITIAL_COUNT,
                "active": self.INITIAL_COUNT
            }

            for job in self._jobs.values():
                status_key = job.status.value
                stats[status_key] = stats.get(status_key, self.INITIAL_COUNT) + self.INCREMENT_STEP

                if job.status in [JobStatus.PENDING, JobStatus.PROCESSING]:
                    stats["active"] += self.INCREMENT_STEP

            return stats

    def get_upload_path(self, filename: str) -> Path:
        """Get upload path for a file with unique prefix to avoid conflicts"""
        import uuid
        unique_filename = f"{uuid.uuid4().hex[:self.UUID_PREFIX_LENGTH]}_{filename}"
        return self._upload_dir / unique_filename

    def get_output_path(self, job_id: str, filename: str) -> Path:
        """Get output path for a job"""
        name, ext = os.path.splitext(filename)
        return self._output_dir / f"{name}_bilingual_{job_id}{ext}"

    def get_temp_path(self, job_id: str) -> Path:
        """Get temporary directory path for a job"""
        temp_dir = self._temp_dir / job_id
        temp_dir.mkdir(exist_ok=True)
        return temp_dir

    def shutdown(self, wait: bool = True) -> None:
        """
        Shutdown the job manager

        Args:
            wait: Whether to wait for running jobs to complete
        """
        logger.info("Shutting down JobManager...")

        # Cancel all pending jobs
        with self._lock:
            for job in self._jobs.values():
                if job.status == JobStatus.PENDING:
                    job.mark_cancelled()

        # Shutdown executor
        self._executor.shutdown(wait=wait)

        if not wait:
            # Cancel all running jobs
            with self._lock:
                for future in self._job_futures.values():
                    future.cancel()

        logger.info("JobManager shutdown complete")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.shutdown()


# Global job manager instance with configuration-based settings
job_manager = JobManager()