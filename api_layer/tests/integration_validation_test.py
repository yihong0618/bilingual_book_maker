#!/usr/bin/env python3
"""
Integration test script for file validation and sanitization
Tests the live Docker container API endpoints with various file upload scenarios
Run this script while the Docker container is running on localhost:8000
"""

import requests
import os
import tempfile
import time
import json
from typing import Dict, Any
import sys

# Colors for output
RED = '\033[91m'
GREEN = '\033[92m'
YELLOW = '\033[93m'
BLUE = '\033[94m'
ENDC = '\033[0m'

class APIValidationTester:
    def __init__(self, base_url: str = "http://localhost:8000"):
        self.base_url = base_url
        self.test_dir = tempfile.mkdtemp()
        self.passed = 0
        self.failed = 0
        self.test_results = []

    def log(self, message: str, color: str = ""):
        """Log message with optional color"""
        print(f"{color}{message}{ENDC}")

    def test_result(self, test_name: str, expected: str, actual: str, passed: bool):
        """Record and display test result"""
        status = f"{GREEN}PASS{ENDC}" if passed else f"{RED}FAIL{ENDC}"
        self.log(f"  {status} {test_name}")
        if not passed:
            self.log(f"    Expected: {expected}", YELLOW)
            self.log(f"    Actual: {actual}", YELLOW)

        self.test_results.append({
            "test": test_name,
            "expected": expected,
            "actual": actual,
            "passed": passed
        })

        if passed:
            self.passed += 1
        else:
            self.failed += 1

    def create_test_files(self):
        """Create test files for validation"""
        files = {}

        # Valid files
        files['valid_txt'] = os.path.join(self.test_dir, "valid.txt")
        with open(files['valid_txt'], "w") as f:
            f.write("This is a valid text file for testing")

        files['valid_epub'] = os.path.join(self.test_dir, "valid.epub")
        with open(files['valid_epub'], "wb") as f:
            f.write(b"PK\x03\x04")  # ZIP magic bytes for EPUB
            f.write(b"fake epub content for testing" * 20)

        files['valid_srt'] = os.path.join(self.test_dir, "valid.srt")
        with open(files['valid_srt'], "w") as f:
            f.write("1\n00:00:01,000 --> 00:00:03,000\nTest subtitle")

        files['valid_md'] = os.path.join(self.test_dir, "valid.md")
        with open(files['valid_md'], "w") as f:
            f.write("# Test Markdown\nThis is test content")

        # Malicious content files
        files['malicious_script'] = os.path.join(self.test_dir, "malicious.txt")
        with open(files['malicious_script'], "w") as f:
            f.write('<script>alert("xss")</script>Some content')

        files['malicious_php'] = os.path.join(self.test_dir, "php_code.txt")
        with open(files['malicious_php'], "w") as f:
            f.write('<?php system($_GET["cmd"]); ?>Some text')

        files['malicious_shell'] = os.path.join(self.test_dir, "shell_script.txt")
        with open(files['malicious_shell'], "w") as f:
            f.write('#!/bin/bash\nrm -rf /\nSome content')

        # Large file (over 500KB)
        files['large_file'] = os.path.join(self.test_dir, "large.txt")
        with open(files['large_file'], "wb") as f:
            f.write(b"x" * (600 * 1024))  # 600KB

        # Invalid extension
        files['invalid_ext'] = os.path.join(self.test_dir, "malware.exe")
        with open(files['invalid_ext'], "w") as f:
            f.write("This should be rejected")

        # Fake EPUB (wrong magic bytes)
        files['fake_epub'] = os.path.join(self.test_dir, "fake.epub")
        with open(files['fake_epub'], "w") as f:
            f.write("This is not a real EPUB file")

        return files

    def upload_file(self, file_path: str, filename_override: str = None, **kwargs) -> requests.Response:
        """Upload file to API"""
        filename = filename_override or os.path.basename(file_path)

        with open(file_path, "rb") as f:
            files = {"file": (filename, f)}
            data = {
                "model": "google",
                "key": "no-key-required",
                "language": "zh-cn",
                **kwargs
            }
            response = requests.post(f"{self.base_url}/translate", files=files, data=data)

        return response

    def test_health_check(self):
        """Test API health check"""
        self.log(f"\n{BLUE}=== Testing Health Check ==={ENDC}")

        try:
            response = requests.get(f"{self.base_url}/health")
            if response.status_code == 200 and response.json().get("status") == "healthy":
                self.test_result("Health check", "healthy", "healthy", True)
                return True
            else:
                self.test_result("Health check", "healthy", f"status: {response.status_code}", False)
                return False
        except Exception as e:
            self.test_result("Health check", "healthy", f"error: {e}", False)
            return False

    def test_valid_file_extensions(self, files: Dict[str, str]):
        """Test valid file extension acceptance"""
        self.log(f"\n{BLUE}=== Testing Valid File Extensions ==={ENDC}")

        test_cases = [
            ('valid_txt', '.txt'),
            ('valid_epub', '.epub'),
            ('valid_srt', '.srt'),
            ('valid_md', '.md')
        ]

        for file_key, ext in test_cases:
            response = self.upload_file(files[file_key])
            expected = "job_id returned"

            if response.status_code == 200 and "job_id" in response.json():
                actual = "job_id returned"
                passed = True
            else:
                actual = f"status: {response.status_code}, response: {response.text[:100]}"
                passed = False

            self.test_result(f"Valid {ext} file acceptance", expected, actual, passed)

    def test_invalid_file_extensions(self, files: Dict[str, str]):
        """Test invalid file extension rejection"""
        self.log(f"\n{BLUE}=== Testing Invalid File Extensions ==={ENDC}")

        response = self.upload_file(files['invalid_ext'])
        expected = "400 error with unsupported format message"

        if response.status_code == 400 and "Unsupported file format" in response.text:
            actual = "400 error with unsupported format message"
            passed = True
        else:
            actual = f"status: {response.status_code}, response: {response.text[:100]}"
            passed = False

        self.test_result("Invalid .exe extension rejection", expected, actual, passed)

    def test_file_size_limits(self, files: Dict[str, str]):
        """Test file size validation"""
        self.log(f"\n{BLUE}=== Testing File Size Limits ==={ENDC}")

        response = self.upload_file(files['large_file'])
        expected = "413 error with size limit message"

        if response.status_code == 413 and "File too large" in response.text:
            actual = "413 error with size limit message"
            passed = True
        else:
            actual = f"status: {response.status_code}, response: {response.text[:100]}"
            passed = False

        self.test_result("Large file (600KB) rejection", expected, actual, passed)

    def test_malicious_content_detection(self, files: Dict[str, str]):
        """Test malicious content detection"""
        self.log(f"\n{BLUE}=== Testing Malicious Content Detection ==={ENDC}")

        test_cases = [
            ('malicious_script', 'JavaScript script tag'),
            ('malicious_php', 'PHP code'),
            ('malicious_shell', 'Shell script')
        ]

        for file_key, content_type in test_cases:
            response = self.upload_file(files[file_key])
            expected = "400 error with malicious content message"

            if response.status_code == 400 and "potentially malicious content" in response.text:
                actual = "400 error with malicious content message"
                passed = True
            else:
                actual = f"status: {response.status_code}, response: {response.text[:100]}"
                passed = False

            self.test_result(f"Malicious {content_type} detection", expected, actual, passed)

    def test_path_traversal_prevention(self, files: Dict[str, str]):
        """Test path traversal prevention"""
        self.log(f"\n{BLUE}=== Testing Path Traversal Prevention ==={ENDC}")

        test_cases = [
            "../../../etc/passwd.txt",
            "..\\\\..\\\\windows\\\\system32\\\\config.txt",
            "/etc/passwd.txt",
            "file../traversal.txt"
        ]

        for malicious_filename in test_cases:
            response = self.upload_file(files['valid_txt'], filename_override=malicious_filename)
            expected = "400 error with path pattern message"

            if response.status_code == 400 and ("invalid path pattern" in response.text or "reserved" in response.text):
                actual = "400 error with path pattern message"
                passed = True
            else:
                actual = f"status: {response.status_code}, response: {response.text[:100]}"
                passed = False

            self.test_result(f"Path traversal '{malicious_filename[:20]}...' prevention", expected, actual, passed)

    def test_reserved_filename_blocking(self, files: Dict[str, str]):
        """Test reserved filename blocking"""
        self.log(f"\n{BLUE}=== Testing Reserved Filename Blocking ==={ENDC}")

        test_cases = [
            "CON.txt",
            "PRN.txt",
            "AUX.txt",
            "COM1.txt",
            "desktop.ini"
        ]

        for reserved_name in test_cases:
            response = self.upload_file(files['valid_txt'], filename_override=reserved_name)
            expected = "400 error with reserved filename message"

            if response.status_code == 400 and "reserved" in response.text:
                actual = "400 error with reserved filename message"
                passed = True
            else:
                actual = f"status: {response.status_code}, response: {response.text[:100]}"
                passed = False

            self.test_result(f"Reserved filename '{reserved_name}' blocking", expected, actual, passed)

    def test_file_magic_bytes(self, files: Dict[str, str]):
        """Test file magic bytes validation"""
        self.log(f"\n{BLUE}=== Testing File Magic Bytes Validation ==={ENDC}")

        response = self.upload_file(files['fake_epub'])
        expected = "400 error with invalid file header message"

        if response.status_code == 400 and ("doesn't match" in response.text or "invalid file header" in response.text):
            actual = "400 error with invalid file header message"
            passed = True
        else:
            actual = f"status: {response.status_code}, response: {response.text[:100]}"
            passed = False

        self.test_result("Fake EPUB magic bytes rejection", expected, actual, passed)

    def test_parameter_validation(self, files: Dict[str, str]):
        """Test API parameter validation"""
        self.log(f"\n{BLUE}=== Testing Parameter Validation ==={ENDC}")

        # Test invalid temperature
        response = self.upload_file(files['valid_txt'], temperature="5.0")
        expected = "400 error with temperature range message"

        if response.status_code == 400 and "Temperature must be between" in response.text:
            actual = "400 error with temperature range message"
            passed = True
        else:
            actual = f"status: {response.status_code}, response: {response.text[:100]}"
            passed = False

        self.test_result("Invalid temperature rejection", expected, actual, passed)

    def test_filename_sanitization(self, files: Dict[str, str]):
        """Test filename sanitization (should accept after cleaning)"""
        self.log(f"\n{BLUE}=== Testing Filename Sanitization ==={ENDC}")

        # These should be accepted after sanitization
        test_cases = [
            "file with spaces.txt",
            "file-with-dashes.txt",
            "file_with_underscores.txt",
            "file(with)parentheses.txt"
        ]

        for filename in test_cases:
            response = self.upload_file(files['valid_txt'], filename_override=filename)
            expected = "job_id returned after sanitization"

            if response.status_code == 200 and "job_id" in response.json():
                actual = "job_id returned after sanitization"
                passed = True
            else:
                actual = f"status: {response.status_code}, response: {response.text[:100]}"
                passed = False

            self.test_result(f"Filename sanitization '{filename}'", expected, actual, passed)

    def run_all_tests(self):
        """Run all validation tests"""
        self.log(f"{GREEN}Starting API Validation Integration Tests{ENDC}")
        self.log(f"Target: {self.base_url}")

        # Check if API is healthy first
        if not self.test_health_check():
            self.log(f"{RED}API is not healthy, aborting tests{ENDC}")
            return False

        # Create test files
        self.log(f"\n{YELLOW}Creating test files...{ENDC}")
        files = self.create_test_files()

        # Run all test suites
        self.test_valid_file_extensions(files)
        self.test_invalid_file_extensions(files)
        self.test_file_size_limits(files)
        self.test_malicious_content_detection(files)
        self.test_path_traversal_prevention(files)
        self.test_reserved_filename_blocking(files)
        self.test_file_magic_bytes(files)
        self.test_parameter_validation(files)
        self.test_filename_sanitization(files)

        # Print summary
        self.print_summary()

        # Cleanup
        import shutil
        shutil.rmtree(self.test_dir, ignore_errors=True)

        return self.failed == 0

    def print_summary(self):
        """Print test summary"""
        total = self.passed + self.failed
        success_rate = (self.passed / total * 100) if total > 0 else 0

        self.log(f"\n{BLUE}=== Test Summary ==={ENDC}")
        self.log(f"Total Tests: {total}")
        self.log(f"Passed: {GREEN}{self.passed}{ENDC}")
        self.log(f"Failed: {RED}{self.failed}{ENDC}")
        self.log(f"Success Rate: {GREEN if success_rate == 100 else YELLOW}{success_rate:.1f}%{ENDC}")

        if self.failed > 0:
            self.log(f"\n{RED}Failed Tests:{ENDC}")
            for result in self.test_results:
                if not result["passed"]:
                    self.log(f"  - {result['test']}")

def main():
    """Main function"""
    import argparse

    parser = argparse.ArgumentParser(description="API Validation Integration Tests")
    parser.add_argument("--url", default="http://localhost:8000", help="API base URL")
    parser.add_argument("--wait", type=int, default=0, help="Wait seconds before starting tests")
    args = parser.parse_args()

    if args.wait > 0:
        print(f"Waiting {args.wait} seconds for container to start...")
        time.sleep(args.wait)

    tester = APIValidationTester(args.url)
    success = tester.run_all_tests()

    sys.exit(0 if success else 1)

if __name__ == "__main__":
    main()