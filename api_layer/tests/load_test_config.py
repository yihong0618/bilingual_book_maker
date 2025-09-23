#!/usr/bin/env python3
"""
Load testing script for configuration values validation
Tests the production-ready settings under realistic load conditions
"""

import asyncio
import aiohttp
import time
import tempfile
import os
from concurrent.futures import ThreadPoolExecutor
from typing import List, Dict, Any
import json
import statistics

# Test configuration
API_BASE_URL = "http://localhost:8000"
CONCURRENT_REQUESTS = 8  # Test beyond DEFAULT_MAX_WORKERS (4)
TEST_DURATION_SECONDS = 60
TEST_FILE_SIZES = [
    (50, "50KB"),    # Small file
    (200, "200KB"),  # Medium file
    (450, "450KB"),  # Near limit file
]

class LoadTester:
    def __init__(self, base_url: str):
        self.base_url = base_url
        self.results = []
        self.test_files = {}

    def create_test_files(self):
        """Create test files of various sizes"""
        for size_kb, label in TEST_FILE_SIZES:
            content = "Test content for load testing. " * (size_kb * 1024 // 50)
            content = content[:size_kb * 1024]  # Exact size

            temp_file = tempfile.NamedTemporaryFile(suffix=".txt", delete=False)
            temp_file.write(content.encode())
            temp_file.close()

            self.test_files[label] = temp_file.name
            print(f"Created {label} test file: {temp_file.name}")

    def cleanup_test_files(self):
        """Clean up test files"""
        for label, filepath in self.test_files.items():
            try:
                os.unlink(filepath)
                print(f"Cleaned up {label} test file")
            except:
                pass

    async def health_check(self, session: aiohttp.ClientSession) -> Dict[str, Any]:
        """Check API health"""
        try:
            async with session.get(f"{self.base_url}/health") as response:
                return {
                    "status_code": response.status,
                    "data": await response.json() if response.status == 200 else None,
                    "response_time": 0  # Not measuring for health check
                }
        except Exception as e:
            return {"status_code": 0, "error": str(e), "data": None}

    async def upload_file(self, session: aiohttp.ClientSession, file_path: str, file_label: str) -> Dict[str, Any]:
        """Upload a file and measure response time"""
        start_time = time.time()

        try:
            with open(file_path, 'rb') as f:
                data = aiohttp.FormData()
                data.add_field('file', f, filename=f'test_{file_label}.txt')
                data.add_field('model', 'google')
                data.add_field('key', 'no-key-required')
                data.add_field('language', 'zh-cn')

                async with session.post(f"{self.base_url}/translate", data=data) as response:
                    response_time = time.time() - start_time

                    result = {
                        "file_label": file_label,
                        "status_code": response.status,
                        "response_time": response_time,
                        "timestamp": start_time
                    }

                    if response.status == 200:
                        result["data"] = await response.json()
                    else:
                        result["error"] = await response.text()

                    return result

        except Exception as e:
            return {
                "file_label": file_label,
                "status_code": 0,
                "response_time": time.time() - start_time,
                "error": str(e),
                "timestamp": start_time
            }

    async def concurrent_upload_test(self, concurrent_level: int, duration_seconds: int):
        """Run concurrent upload tests"""
        print(f"\n🚀 Starting concurrent upload test: {concurrent_level} concurrent requests for {duration_seconds}s")

        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=300),  # 5 minute timeout
            connector=aiohttp.TCPConnector(limit=concurrent_level * 2)
        ) as session:

            # Initial health check
            health = await self.health_check(session)
            if health["status_code"] != 200:
                print(f"❌ API not healthy: {health}")
                return

            print(f"✅ API healthy. Active jobs: {health['data'].get('active_jobs', 0)}")

            start_time = time.time()
            tasks = []

            # Create tasks for the test duration
            task_count = 0
            while time.time() - start_time < duration_seconds:
                # Cycle through different file sizes
                for file_label, file_path in self.test_files.items():
                    if len(tasks) >= concurrent_level:
                        # Wait for some tasks to complete
                        done_tasks = await asyncio.gather(*tasks[:concurrent_level//2], return_exceptions=True)
                        self.results.extend([t for t in done_tasks if isinstance(t, dict)])
                        tasks = tasks[concurrent_level//2:]

                    task = asyncio.create_task(self.upload_file(session, file_path, file_label))
                    tasks.append(task)
                    task_count += 1

                    # Small delay to prevent overwhelming
                    await asyncio.sleep(0.1)

                    if time.time() - start_time >= duration_seconds:
                        break

            # Wait for remaining tasks
            if tasks:
                remaining_results = await asyncio.gather(*tasks, return_exceptions=True)
                self.results.extend([r for r in remaining_results if isinstance(r, dict)])

            print(f"✅ Completed {task_count} total requests in {time.time() - start_time:.1f}s")

    def analyze_results(self):
        """Analyze load test results"""
        if not self.results:
            print("❌ No results to analyze")
            return

        print(f"\n📊 LOAD TEST ANALYSIS ({len(self.results)} requests)")
        print("=" * 60)

        # Success rate
        successful = [r for r in self.results if r["status_code"] == 200]
        success_rate = len(successful) / len(self.results) * 100
        print(f"Success Rate: {success_rate:.1f}% ({len(successful)}/{len(self.results)})")

        # Response times
        response_times = [r["response_time"] for r in self.results]
        print(f"\nResponse Times:")
        print(f"  Average: {statistics.mean(response_times):.2f}s")
        print(f"  Median: {statistics.median(response_times):.2f}s")
        print(f"  Min: {min(response_times):.2f}s")
        print(f"  Max: {max(response_times):.2f}s")

        if len(response_times) > 1:
            print(f"  Std Dev: {statistics.stdev(response_times):.2f}s")

        # Percentiles
        sorted_times = sorted(response_times)
        p95_idx = int(0.95 * len(sorted_times))
        p99_idx = int(0.99 * len(sorted_times))
        print(f"  95th percentile: {sorted_times[p95_idx]:.2f}s")
        print(f"  99th percentile: {sorted_times[p99_idx]:.2f}s")

        # Errors
        errors = [r for r in self.results if r["status_code"] != 200]
        if errors:
            print(f"\n❌ Errors ({len(errors)}):")
            error_counts = {}
            for error in errors:
                key = f"{error['status_code']}: {error.get('error', 'Unknown')[:50]}"
                error_counts[key] = error_counts.get(key, 0) + 1

            for error_type, count in error_counts.items():
                print(f"  {count}x {error_type}")

        # File size performance
        print(f"\n📁 Performance by File Size:")
        for file_label in set(r["file_label"] for r in self.results):
            file_results = [r for r in self.results if r["file_label"] == file_label]
            file_times = [r["response_time"] for r in file_results]
            file_success = len([r for r in file_results if r["status_code"] == 200])

            print(f"  {file_label}: {file_success}/{len(file_results)} success, "
                  f"avg: {statistics.mean(file_times):.2f}s")

        # Configuration validation
        print(f"\n⚙️  CONFIGURATION VALIDATION:")
        print("=" * 40)

        # Test if DEFAULT_MAX_WORKERS=4 is sufficient
        max_concurrent = max(len([r for r in self.results
                                if abs(r["timestamp"] - timestamp) < 1.0])
                           for timestamp in set(r["timestamp"] for r in self.results))

        print(f"Max concurrent requests handled: {max_concurrent}")
        print(f"DEFAULT_MAX_WORKERS=4: {'✅ Sufficient' if max_concurrent <= 6 else '⚠️  May need increase'}")

        # File size limit validation
        max_response_time = max(response_times)
        print(f"Max response time: {max_response_time:.2f}s")
        print(f"DEFAULT_JOB_TTL_HOURS=3: {'✅ Sufficient' if max_response_time < 60 else '⚠️  Monitor closely'}")

        # File size validation
        print(f"File size limit (500KB): ✅ All test files within limit")

        # Overall assessment
        if success_rate >= 95 and statistics.mean(response_times) < 5.0:
            print(f"\n🎉 OVERALL: Production configuration looks good!")
        elif success_rate >= 90:
            print(f"\n⚠️  OVERALL: Configuration needs minor adjustments")
        else:
            print(f"\n❌ OVERALL: Configuration needs significant improvements")

async def main():
    """Main load testing function"""
    print("🔧 Configuration Load Testing Tool")
    print("=" * 50)

    tester = LoadTester(API_BASE_URL)

    try:
        # Create test files
        tester.create_test_files()

        # Run load tests with different concurrency levels
        await tester.concurrent_upload_test(
            concurrent_level=CONCURRENT_REQUESTS,
            duration_seconds=TEST_DURATION_SECONDS
        )

        # Analyze results
        tester.analyze_results()

    finally:
        # Cleanup
        tester.cleanup_test_files()

if __name__ == "__main__":
    asyncio.run(main())