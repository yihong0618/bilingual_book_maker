"""
Example usage of the Async Translation API
Demonstrates how to use the async wrapper for translation jobs
"""
import asyncio
import time
import requests
from pathlib import Path


class AsyncTranslationClient:
    """
    Example client for the Async Translation API
    """

    def __init__(self, base_url: str = "http://localhost:8000"):
        self.base_url = base_url

    def start_translation(self, file_path: str, model: str, api_key: str, **kwargs) -> str:
        """
        Start a translation job

        Args:
            file_path: Path to EPUB file
            model: Translation model to use
            api_key: API key for the service
            **kwargs: Additional translation parameters

        Returns:
            Job ID for tracking
        """
        with open(file_path, 'rb') as f:
            files = {'file': f}
            data = {
                'model': model,
                'key': api_key,
                **kwargs
            }

            response = requests.post(f"{self.base_url}/translate", files=files, data=data)
            response.raise_for_status()

            result = response.json()
            return result['job_id']

    def get_job_status(self, job_id: str) -> dict:
        """Get job status and progress"""
        response = requests.get(f"{self.base_url}/status/{job_id}")
        response.raise_for_status()
        return response.json()

    def wait_for_completion(self, job_id: str, poll_interval: int = 5) -> dict:
        """
        Wait for job completion with progress monitoring

        Args:
            job_id: Job ID to monitor
            poll_interval: Polling interval in seconds

        Returns:
            Final job status
        """
        print(f"Monitoring job {job_id}...")

        while True:
            status = self.get_job_status(job_id)

            print(f"Status: {status['status']}, Progress: {status['progress']}% "
                  f"({status['processed_paragraphs']}/{status['total_paragraphs']} paragraphs)")

            if status['status'] in ['completed', 'failed', 'cancelled']:
                return status

            time.sleep(poll_interval)

    def download_result(self, job_id: str, output_path: str) -> None:
        """Download the translated file"""
        response = requests.get(f"{self.base_url}/download/{job_id}")
        response.raise_for_status()

        with open(output_path, 'wb') as f:
            f.write(response.content)

        print(f"Downloaded translated file to: {output_path}")

    def cancel_job(self, job_id: str) -> bool:
        """Cancel a running job"""
        response = requests.post(f"{self.base_url}/cancel/{job_id}")
        if response.status_code == 200:
            print(f"Job {job_id} cancelled successfully")
            return True
        else:
            print(f"Failed to cancel job {job_id}: {response.text}")
            return False

    def list_jobs(self, status_filter: str = None) -> list:
        """List all jobs"""
        params = {}
        if status_filter:
            params['status'] = status_filter

        response = requests.get(f"{self.base_url}/jobs", params=params)
        response.raise_for_status()
        return response.json()

    def get_health(self) -> dict:
        """Get API health status"""
        response = requests.get(f"{self.base_url}/health")
        response.raise_for_status()
        return response.json()


def example_basic_translation():
    """
    Example: Basic translation workflow
    """
    print("=== Basic Translation Example ===")

    client = AsyncTranslationClient()

    # Check API health
    try:
        health = client.get_health()
        print(f"API Status: {health['status']}")
        print(f"Active Jobs: {health['active_jobs']}")
    except requests.exceptions.ConnectionError:
        print("ERROR: API server is not running. Please start it with: python api/main.py")
        return

    # Example file path (you would use your actual EPUB file)
    epub_file = "example_book.epub"

    if not Path(epub_file).exists():
        print(f"WARNING: Example file {epub_file} not found.")
        print("Please provide an actual EPUB file path to test translation.")
        return

    try:
        # Start translation job
        job_id = client.start_translation(
            file_path=epub_file,
            model="chatgpt",  # or "claude", "gemini", etc.
            api_key="your-api-key-here",
            language="zh-cn",
            is_test=True,  # Enable test mode for faster testing
            test_num=5
        )

        print(f"Started translation job: {job_id}")

        # Monitor progress
        final_status = client.wait_for_completion(job_id)

        if final_status['status'] == 'completed':
            # Download result
            output_file = f"translated_{epub_file}"
            client.download_result(job_id, output_file)
            print(f"Translation completed! Output saved as: {output_file}")

        elif final_status['status'] == 'failed':
            print(f"Translation failed: {final_status.get('error_message', 'Unknown error')}")

    except requests.exceptions.HTTPError as e:
        print(f"API Error: {e}")
    except Exception as e:
        print(f"Error: {e}")


def example_multiple_jobs():
    """
    Example: Managing multiple translation jobs
    """
    print("\n=== Multiple Jobs Example ===")

    client = AsyncTranslationClient()

    # Start multiple jobs
    job_ids = []
    epub_files = ["book1.epub", "book2.epub", "book3.epub"]

    for epub_file in epub_files:
        if Path(epub_file).exists():
            try:
                job_id = client.start_translation(
                    file_path=epub_file,
                    model="chatgpt",
                    api_key="your-api-key-here",
                    language="zh-cn",
                    is_test=True,
                    test_num=3
                )
                job_ids.append(job_id)
                print(f"Started job {job_id} for {epub_file}")
            except Exception as e:
                print(f"Failed to start job for {epub_file}: {e}")

    if not job_ids:
        print("No jobs started. Please ensure EPUB files exist.")
        return

    # Monitor all jobs
    completed_jobs = []
    while len(completed_jobs) < len(job_ids):
        for job_id in job_ids:
            if job_id in completed_jobs:
                continue

            status = client.get_job_status(job_id)
            print(f"Job {job_id}: {status['status']} ({status['progress']}%)")

            if status['status'] in ['completed', 'failed', 'cancelled']:
                completed_jobs.append(job_id)

        if len(completed_jobs) < len(job_ids):
            time.sleep(5)

    print("All jobs completed!")

    # List final status
    jobs_list = client.list_jobs()
    print(f"\nFinal status summary:")
    for job in jobs_list['jobs']:
        if job['job_id'] in job_ids:
            print(f"  {job['filename']}: {job['status']}")


def example_error_handling():
    """
    Example: Error handling and job cancellation
    """
    print("\n=== Error Handling Example ===")

    client = AsyncTranslationClient()

    try:
        # Try to start a job with invalid parameters
        job_id = client.start_translation(
            file_path="nonexistent.epub",
            model="invalid_model",
            api_key="invalid-key"
        )
    except requests.exceptions.HTTPError as e:
        print(f"Expected error for invalid file: {e}")

    # Start a valid job and then cancel it
    epub_file = "example_book.epub"
    if Path(epub_file).exists():
        try:
            job_id = client.start_translation(
                file_path=epub_file,
                model="chatgpt",
                api_key="your-api-key-here",
                language="zh-cn"
            )

            print(f"Started job {job_id}")

            # Wait a moment, then cancel
            time.sleep(2)
            success = client.cancel_job(job_id)

            if success:
                status = client.get_job_status(job_id)
                print(f"Job status after cancellation: {status['status']}")

        except Exception as e:
            print(f"Error in cancellation example: {e}")


async def example_async_workflow():
    """
    Example: Using async/await pattern for concurrent operations
    """
    print("\n=== Async Workflow Example ===")

    client = AsyncTranslationClient()

    async def monitor_job(job_id: str, name: str):
        """Monitor a single job asynchronously"""
        print(f"Monitoring {name} (Job: {job_id})")

        while True:
            # In a real async implementation, you'd use aiohttp
            # For this example, we'll simulate with asyncio.sleep
            await asyncio.sleep(2)

            try:
                status = client.get_job_status(job_id)
                progress = status['progress']
                job_status = status['status']

                print(f"{name}: {job_status} ({progress}%)")

                if job_status in ['completed', 'failed', 'cancelled']:
                    return status

            except Exception as e:
                print(f"Error monitoring {name}: {e}")
                return None

    # This would be more useful with actual async HTTP client
    # For demonstration purposes only
    print("This example shows how you could structure async monitoring")
    print("In practice, you'd use aiohttp for true async HTTP requests")


def main():
    """
    Run all examples
    """
    print("Bilingual Book Maker - Async API Examples")
    print("=" * 50)

    # Basic translation
    example_basic_translation()

    # Multiple jobs
    example_multiple_jobs()

    # Error handling
    example_error_handling()

    # Async workflow (conceptual)
    # asyncio.run(example_async_workflow())

    print("\n" + "=" * 50)
    print("Examples completed!")
    print("\nTo run these examples with real files:")
    print("1. Start the API server: python api/main.py")
    print("2. Place EPUB files in the current directory")
    print("3. Update API keys in the examples")
    print("4. Run: python examples/example_usage.py")


if __name__ == "__main__":
    main()