"""
Progress monitoring system that intercepts tqdm progress from bilingual_book_maker
"""
import threading
import time
import logging
from typing import Callable, Optional, Dict, Any
from datetime import datetime, timedelta
import uuid
from dataclasses import dataclass
from contextlib import contextmanager

from tqdm import tqdm as original_tqdm

logger = logging.getLogger(__name__)


@dataclass
class ProgressUpdate:
    """Progress update event"""
    job_id: str
    current: int
    total: int
    percentage: float
    timestamp: datetime
    description: Optional[str] = None


class ProgressMonitor:
    """
    Progress monitoring system that intercepts tqdm progress updates
    and converts them to API-friendly progress events
    """

    def __init__(self):
        self._callbacks: Dict[str, Callable[[ProgressUpdate], None]] = {}
        self._lock = threading.Lock()
        self._active_jobs: Dict[str, Dict[str, Any]] = {}

    def register_callback(self, job_id: str, callback: Callable[[ProgressUpdate], None]) -> None:
        """Register a progress callback for a specific job"""
        with self._lock:
            self._callbacks[job_id] = callback
            self._active_jobs[job_id] = {
                "start_time": datetime.now(),
                "last_update": datetime.now(),
                "total": 0,
                "current": 0
            }
        logger.warning(f"DEBUG: Registered callback for job {job_id}. Total callbacks: {len(self._callbacks)}")

    def unregister_callback(self, job_id: str) -> None:
        """Unregister progress callback for a job"""
        with self._lock:
            self._callbacks.pop(job_id, None)
            self._active_jobs.pop(job_id, None)

    def update_progress(self, job_id: str, current: int, total: int, description: Optional[str] = None) -> None:
        """Update progress for a specific job"""
        logger.warning(f"DEBUG: update_progress called for job {job_id}: {current}/{total} ({description})")

        if job_id not in self._callbacks:
            logger.warning(f"DEBUG: No callback registered for job {job_id}. Registered jobs: {list(self._callbacks.keys())}")
            return

        percentage = (current / total * 100) if total > 0 else 0

        # Update job tracking
        with self._lock:
            if job_id in self._active_jobs:
                self._active_jobs[job_id].update({
                    "current": current,
                    "total": total,
                    "last_update": datetime.now()
                })

        # Create progress update
        update = ProgressUpdate(
            job_id=job_id,
            current=current,
            total=total,
            percentage=percentage,
            timestamp=datetime.now(),
            description=description
        )

        # Call registered callback
        callback = self._callbacks.get(job_id)
        if callback:
            try:
                logger.warning(f"DEBUG: Calling progress callback for job {job_id}")
                callback(update)
            except Exception as e:
                logger.error(f"Error in progress callback for job {job_id}: {e}")
        else:
            logger.warning(f"DEBUG: No callback found for job {job_id}")

    def get_job_progress(self, job_id: str) -> Optional[Dict[str, Any]]:
        """Get current progress information for a job"""
        with self._lock:
            return self._active_jobs.get(job_id)

    @contextmanager
    def monitor_job(self, job_id: str, callback: Callable[[ProgressUpdate], None]):
        """Context manager for monitoring a job's progress"""
        self.register_callback(job_id, callback)
        try:
            yield self
        finally:
            self.unregister_callback(job_id)


class TqdmInterceptor(original_tqdm):
    """
    Custom tqdm class that intercepts progress updates and forwards them
    to the progress monitor for a specific job
    """

    _monitor: Optional[ProgressMonitor] = None
    _job_id: Optional[str] = None
    _update_interval_seconds: int = 5  # Default update interval in seconds

    @classmethod
    def set_monitor(cls, monitor: ProgressMonitor, job_id: str, update_interval_seconds: int = 5):
        """Set the progress monitor and job ID for this tqdm instance"""
        cls._monitor = monitor
        cls._job_id = job_id
        cls._update_interval_seconds = update_interval_seconds

    @classmethod
    def clear_monitor(cls):
        """Clear the progress monitor"""
        cls._monitor = None
        cls._job_id = None
        cls._update_interval_seconds = 5  # Reset to default

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._last_update_time = None  # Start with None to ensure first update is sent
        self._update_interval = timedelta(seconds=self._update_interval_seconds)

    def update(self, n=1):
        """Override update method to intercept progress"""
        result = super().update(n)

        # Send progress update if monitor is set
        if self._monitor and self._job_id:
            logger.debug(f"TqdmInterceptor: update called for job {self._job_id}: {self.n}/{self.total}")
            now = datetime.now()

            # Always send update if it's the first one or if enough time has passed
            # Also send updates at milestone percentages (every 5%)
            should_update = (
                self._last_update_time is None or
                now - self._last_update_time >= self._update_interval or
                self.n == 1 or  # First update
                (self.total and self.n >= self.total) or  # Last update
                (self.total and (self.n * 100 // self.total) % 5 == 0 and
                 (self.n - n) * 100 // self.total != self.n * 100 // self.total)  # Every 5% milestone
            )

            if should_update:
                logger.debug(f"TqdmInterceptor: Sending progress update for job {self._job_id}")
                self._monitor.update_progress(
                    job_id=self._job_id,
                    current=self.n,
                    total=self.total or 0,
                    description=self.desc
                )
                self._last_update_time = now
            else:
                logger.debug(f"TqdmInterceptor: Skipping update for job {self._job_id}")
        else:
            logger.debug(f"TqdmInterceptor: No monitor or job_id set (monitor={self._monitor}, job_id={self._job_id})")

        return result

    def close(self):
        """Override close method to send final progress update"""
        if self._monitor and self._job_id:
            self._monitor.update_progress(
                job_id=self._job_id,
                current=self.n,
                total=self.total or 0,
                description=f"Completed: {self.desc}" if self.desc else "Completed"
            )
        super().close()


def patch_tqdm_for_job(monitor: ProgressMonitor, job_id: str, update_interval_seconds: int = 5):
    """
    Patch tqdm to use our interceptor for a specific job
    Returns a context manager to restore original tqdm
    """
    @contextmanager
    def tqdm_patch():
        # Store original tqdm
        import tqdm as tqdm_module
        original_tqdm_class = tqdm_module.tqdm

        # Set up interceptor
        TqdmInterceptor.set_monitor(monitor, job_id, update_interval_seconds)

        # Patch tqdm module
        tqdm_module.tqdm = TqdmInterceptor

        try:
            yield
        finally:
            # Restore original tqdm
            tqdm_module.tqdm = original_tqdm_class
            TqdmInterceptor.clear_monitor()

    return tqdm_patch()


class AsyncProgressTracker:
    """
    High-level progress tracker for async translation jobs
    Provides convenience methods for tracking translation progress
    """

    def __init__(self):
        self.monitor = ProgressMonitor()
        self._job_estimations: Dict[str, Dict[str, Any]] = {}

    def start_tracking(self, job_id: str, callback: Callable[[ProgressUpdate], None]) -> None:
        """Start tracking progress for a job"""
        self.monitor.register_callback(job_id, callback)

    def stop_tracking(self, job_id: str) -> None:
        """Stop tracking progress for a job"""
        self.monitor.unregister_callback(job_id)
        self._job_estimations.pop(job_id, None)

    def estimate_duration(self, job_id: str, total_paragraphs: int) -> str:
        """Estimate completion time based on paragraph count"""
        # Rough estimation: 1-3 seconds per paragraph depending on model
        estimated_seconds = total_paragraphs * 2  # Average 2 seconds per paragraph

        if estimated_seconds < 60:
            return f"{estimated_seconds} seconds"
        elif estimated_seconds < 3600:
            minutes = int(estimated_seconds / 60)
            return f"{minutes} minutes"
        else:
            hours = int(estimated_seconds / 3600)
            minutes = int((estimated_seconds % 3600) / 60)
            return f"{hours}h {minutes}m"

    def get_progress_percentage(self, job_id: str) -> float:
        """Get current progress percentage for a job"""
        progress = self.monitor.get_job_progress(job_id)
        if not progress or progress["total"] == 0:
            return 0.0

        return (progress["current"] / progress["total"]) * 100

    def create_tqdm_patch(self, job_id: str, update_interval_seconds: int = 5):
        """Create a tqdm patch context manager for a specific job"""
        return patch_tqdm_for_job(self.monitor, job_id, update_interval_seconds)


# Global progress tracker instance
global_progress_tracker = AsyncProgressTracker()