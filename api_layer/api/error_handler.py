"""
Comprehensive error handling and timeout management for async translation
"""
import logging
import traceback
import functools
import signal
import threading
import time
from typing import Any, Callable, Optional, Dict, Type
from datetime import datetime, timedelta
from contextlib import contextmanager
from enum import Enum

from .models import JobStatus


logger = logging.getLogger(__name__)


class ErrorType(str, Enum):
    """Error type classification"""
    TIMEOUT = "timeout"
    NETWORK = "network"
    API_ERROR = "api_error"
    FILE_ERROR = "file_error"
    VALIDATION_ERROR = "validation_error"
    SYSTEM_ERROR = "system_error"
    TRANSLATION_ERROR = "translation_error"
    CANCELLED = "cancelled"


class TranslationError(Exception):
    """Base exception for translation errors"""
    def __init__(self, message: str, error_type: ErrorType, details: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.error_type = error_type
        self.details = details or {}
        self.timestamp = datetime.now()


class TimeoutError(TranslationError):
    """Timeout error"""
    def __init__(self, message: str, timeout_duration: float):
        super().__init__(message, ErrorType.TIMEOUT, {"timeout_duration": timeout_duration})


class NetworkError(TranslationError):
    """Network-related error"""
    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message, ErrorType.NETWORK, {"status_code": status_code})


class APIError(TranslationError):
    """API-related error"""
    def __init__(self, message: str, api_name: str, error_code: Optional[str] = None):
        super().__init__(message, ErrorType.API_ERROR, {"api_name": api_name, "error_code": error_code})


class FileError(TranslationError):
    """File-related error"""
    def __init__(self, message: str, file_path: str):
        super().__init__(message, ErrorType.FILE_ERROR, {"file_path": file_path})


class ValidationError(TranslationError):
    """Validation error"""
    def __init__(self, message: str, field: str, value: Any):
        super().__init__(message, ErrorType.VALIDATION_ERROR, {"field": field, "value": value})


class TimeoutManager:
    """
    Manages timeouts for translation operations
    """

    def __init__(self, default_timeout: float = 1800):  # 30 minutes
        self.default_timeout = default_timeout
        self._active_timeouts: Dict[str, threading.Timer] = {}
        self._lock = threading.Lock()

    @contextmanager
    def timeout_context(self, job_id: str, timeout_seconds: Optional[float] = None, callback: Optional[Callable] = None):
        """
        Context manager for timeout handling

        Args:
            job_id: Job identifier
            timeout_seconds: Timeout duration in seconds
            callback: Callback to execute on timeout
        """
        timeout_duration = timeout_seconds or self.default_timeout

        def timeout_handler():
            logger.warning(f"Timeout occurred for job {job_id} after {timeout_duration} seconds")
            if callback:
                try:
                    callback()
                except Exception as e:
                    logger.error(f"Error in timeout callback for job {job_id}: {e}")

        # Set up timeout
        timer = threading.Timer(timeout_duration, timeout_handler)

        with self._lock:
            self._active_timeouts[job_id] = timer

        timer.start()

        try:
            yield
        finally:
            # Cancel timeout
            timer.cancel()
            with self._lock:
                self._active_timeouts.pop(job_id, None)

    def cancel_timeout(self, job_id: str) -> bool:
        """Cancel timeout for a specific job"""
        with self._lock:
            timer = self._active_timeouts.pop(job_id, None)
            if timer:
                timer.cancel()
                return True
            return False

    def get_active_timeouts(self) -> list[str]:
        """Get list of jobs with active timeouts"""
        with self._lock:
            return list(self._active_timeouts.keys())


class RetryManager:
    """
    Manages retry logic for failed operations
    """

    def __init__(self, max_retries: int = 3, base_delay: float = 1.0, max_delay: float = 60.0):
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.max_delay = max_delay

    def calculate_delay(self, attempt: int) -> float:
        """Calculate delay for exponential backoff"""
        delay = self.base_delay * (2 ** attempt)
        return min(delay, self.max_delay)

    def should_retry(self, error: Exception, attempt: int) -> bool:
        """Determine if operation should be retried"""
        if attempt >= self.max_retries:
            return False

        # Don't retry certain error types
        if isinstance(error, TranslationError):
            non_retryable = [ErrorType.VALIDATION_ERROR, ErrorType.FILE_ERROR, ErrorType.CANCELLED]
            if error.error_type in non_retryable:
                return False

        # Don't retry if it's a permanent API error (401, 403, etc.)
        if isinstance(error, APIError) and error.details.get("status_code") in [401, 403, 404]:
            return False

        return True

    @contextmanager
    def retry_context(self, job_id: str, operation_name: str):
        """
        Context manager for retry logic

        Args:
            job_id: Job identifier
            operation_name: Name of the operation being retried
        """
        attempt = 0
        last_error = None

        while attempt <= self.max_retries:
            try:
                yield attempt
                return  # Success, exit retry loop

            except Exception as e:
                last_error = e
                attempt += 1

                if not self.should_retry(e, attempt):
                    logger.error(f"Operation {operation_name} for job {job_id} failed permanently: {e}")
                    raise

                if attempt <= self.max_retries:
                    delay = self.calculate_delay(attempt - 1)
                    logger.warning(
                        f"Operation {operation_name} for job {job_id} failed (attempt {attempt}), "
                        f"retrying in {delay}s: {e}"
                    )
                    time.sleep(delay)

        # If we get here, all retries failed
        logger.error(f"All retries exhausted for {operation_name} job {job_id}")
        raise last_error


class ErrorHandler:
    """
    Centralized error handling for translation operations
    """

    def __init__(self):
        self.timeout_manager = TimeoutManager()
        self.retry_manager = RetryManager()
        self._error_stats: Dict[str, int] = {}
        self._lock = threading.Lock()

    def handle_error(self, error: Exception, job_id: str, context: str) -> TranslationError:
        """
        Process and classify an error

        Args:
            error: The original exception
            job_id: Job identifier
            context: Context where error occurred

        Returns:
            Classified TranslationError
        """
        # Log the error
        logger.error(f"Error in {context} for job {job_id}: {error}", exc_info=True)

        # Update error statistics
        error_type_name = type(error).__name__
        with self._lock:
            self._error_stats[error_type_name] = self._error_stats.get(error_type_name, 0) + 1

        # Classify and convert error
        if isinstance(error, TranslationError):
            return error

        # Convert common exceptions to TranslationError
        error_str = str(error)
        error_lower = error_str.lower()

        if "timeout" in error_lower or isinstance(error, TimeoutError):
            return TimeoutError(f"Operation timed out in {context}: {error_str}", 1800)

        elif "connection" in error_lower or "network" in error_lower:
            return NetworkError(f"Network error in {context}: {error_str}")

        elif "api" in error_lower or "unauthorized" in error_lower or "forbidden" in error_lower:
            return APIError(f"API error in {context}: {error_str}", "unknown")

        elif "file" in error_lower or "path" in error_lower or isinstance(error, (FileNotFoundError, IOError)):
            return FileError(f"File error in {context}: {error_str}", "unknown")

        elif "validation" in error_lower or isinstance(error, ValueError):
            return ValidationError(f"Validation error in {context}: {error_str}", "unknown", None)

        else:
            return TranslationError(
                f"System error in {context}: {error_str}",
                ErrorType.SYSTEM_ERROR,
                {"original_type": type(error).__name__}
            )

    def get_error_stats(self) -> Dict[str, int]:
        """Get error statistics"""
        with self._lock:
            return self._error_stats.copy()

    def reset_error_stats(self) -> None:
        """Reset error statistics"""
        with self._lock:
            self._error_stats.clear()


def with_error_handling(error_handler: ErrorHandler, context: str):
    """
    Decorator for automatic error handling

    Args:
        error_handler: ErrorHandler instance
        context: Context description for error logging
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            # Try to extract job_id from arguments
            job_id = kwargs.get('job_id', 'unknown')
            if not job_id and args:
                # Check if first argument has job_id attribute
                if hasattr(args[0], 'job_id'):
                    job_id = args[0].job_id

            try:
                return func(*args, **kwargs)
            except Exception as e:
                translation_error = error_handler.handle_error(e, job_id, context)
                raise translation_error

        return wrapper
    return decorator


def with_timeout(timeout_manager: TimeoutManager, timeout_seconds: Optional[float] = None):
    """
    Decorator for automatic timeout handling

    Args:
        timeout_manager: TimeoutManager instance
        timeout_seconds: Timeout duration in seconds
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            # Try to extract job_id from arguments
            job_id = kwargs.get('job_id', 'unknown')
            if not job_id and args:
                if hasattr(args[0], 'job_id'):
                    job_id = args[0].job_id

            def timeout_callback():
                raise TimeoutError(f"Function {func.__name__} timed out", timeout_seconds or 1800)

            with timeout_manager.timeout_context(job_id, timeout_seconds, timeout_callback):
                return func(*args, **kwargs)

        return wrapper
    return decorator


def with_retry(retry_manager: RetryManager, operation_name: str):
    """
    Decorator for automatic retry logic

    Args:
        retry_manager: RetryManager instance
        operation_name: Name of the operation for logging
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            # Try to extract job_id from arguments
            job_id = kwargs.get('job_id', 'unknown')
            if not job_id and args:
                if hasattr(args[0], 'job_id'):
                    job_id = args[0].job_id

            with retry_manager.retry_context(job_id, operation_name) as attempt:
                kwargs['_retry_attempt'] = attempt
                return func(*args, **kwargs)

        return wrapper
    return decorator


# Global error handler instance
global_error_handler = ErrorHandler()