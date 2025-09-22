"""
Log parser utility to extract progress information from Docker logs
"""
import re
import subprocess
import logging
from typing import Optional, Dict, Any
from datetime import datetime

logger = logging.getLogger(__name__)


class ProgressLogParser:
    """Parse Docker logs to extract translation progress"""

    # Regex pattern to match progress logs: PROGRESS: job_id current/total (percentage%)
    PROGRESS_PATTERN = r'PROGRESS:\s+([a-f0-9-]+)\s+(\d+)/(\d+)\s+\((\d+)%\)'

    def __init__(self, container_name: str = "bilingual-test-v2"):
        self.container_name = container_name

    def get_docker_logs(self, tail_lines: int = 100) -> str:
        """Get recent Docker logs"""
        try:
            result = subprocess.run(
                ["docker", "logs", "--tail", str(tail_lines), self.container_name],
                capture_output=True,
                text=True,
                timeout=10
            )
            if result.returncode == 0:
                return result.stdout
            else:
                logger.error(f"Docker logs command failed: {result.stderr}")
                return ""
        except subprocess.TimeoutExpired:
            logger.error("Docker logs command timed out")
            return ""
        except Exception as e:
            logger.error(f"Error getting Docker logs: {e}")
            return ""

    def parse_progress_from_logs(self, logs: str, job_id: str) -> Optional[Dict[str, Any]]:
        """
        Parse progress information for a specific job from logs

        Returns:
            Dict with keys: current, total, percentage, timestamp
            None if no progress found
        """
        progress_entries = []

        for line in logs.split('\n'):
            match = re.search(self.PROGRESS_PATTERN, line)
            if match and match.group(1) == job_id:
                progress_entries.append({
                    'job_id': match.group(1),
                    'current': int(match.group(2)),
                    'total': int(match.group(3)),
                    'percentage': int(match.group(4)),
                    'log_line': line.strip(),
                    'timestamp': datetime.now()  # We could parse actual timestamp from log line if needed
                })

        # Return the most recent progress entry
        if progress_entries:
            return progress_entries[-1]

        return None

    def get_job_progress(self, job_id: str) -> Optional[Dict[str, Any]]:
        """
        Get the latest progress for a specific job by parsing Docker logs

        Args:
            job_id: The job ID to get progress for

        Returns:
            Dict with progress information or None if not found
        """
        logs = self.get_docker_logs()
        if not logs:
            return None

        return self.parse_progress_from_logs(logs, job_id)

    def get_all_active_jobs_progress(self) -> Dict[str, Dict[str, Any]]:
        """
        Get progress for all active jobs from logs

        Returns:
            Dict mapping job_id to progress information
        """
        logs = self.get_docker_logs()
        if not logs:
            return {}

        job_progress = {}

        for line in logs.split('\n'):
            match = re.search(self.PROGRESS_PATTERN, line)
            if match:
                job_id = match.group(1)
                progress_info = {
                    'current': int(match.group(2)),
                    'total': int(match.group(3)),
                    'percentage': int(match.group(4)),
                    'timestamp': datetime.now()
                }
                # Keep only the latest progress for each job
                job_progress[job_id] = progress_info

        return job_progress


# Global instance
progress_parser = ProgressLogParser()