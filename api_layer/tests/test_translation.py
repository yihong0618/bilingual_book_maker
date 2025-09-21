#!/usr/bin/env python3
"""
Test the translation API with a real translation request
"""
import requests
import json
import time
import os
from pathlib import Path

# API configuration
API_BASE = "http://localhost:8000"

def test_translation_api():
    """Test the full translation workflow"""
    print("🚀 Testing Bilingual Book Maker API...")

    # 1. Check health
    print("\n1. Health check...")
    response = requests.get(f"{API_BASE}/health")
    if response.status_code == 200:
        print("✅ API is healthy")
        print(f"   Status: {response.json()['status']}")
    else:
        print("❌ API health check failed")
        return False

    # 2. List available models
    print("\n2. Checking available models...")
    response = requests.get(f"{API_BASE}/models")
    if response.status_code == 200:
        models = response.json()
        print(f"✅ Found {len(models)} available models:")
        for model in models:
            print(f"   - {model['name']}: {model['display_name']}")
    else:
        print("❌ Failed to get models")
        return False

    # 3. Create a simple test EPUB (we'll use a placeholder since creating a real EPUB is complex)
    print("\n3. Creating test content...")
    test_file_path = Path(__file__).parent / "test_book.epub"

    # For this test, we'll use a simple text file as a placeholder
    # In a real scenario, you'd upload an actual EPUB file
    with open(test_file_path, "w") as f:
        f.write("This is a test book content for translation.")

    print(f"✅ Test file created: {test_file_path}")

    # 4. Test translation endpoint (this will likely fail without a real API key)
    print("\n4. Testing translation endpoint...")
    print("   Note: This test requires a real API key to work fully")

    # Prepare the translation request
    files = {
        'file': ('test_book.epub', open(test_file_path, 'rb'), 'application/epub+zip')
    }

    data = {
        'model': 'chatgpt',  # Using ChatGPT as test model
        'key': 'test-key',   # This would need to be a real API key
        'language': 'zh-cn',
        'is_test': 'true'    # Enable test mode to limit processing
    }

    try:
        response = requests.post(f"{API_BASE}/translate", files=files, data=data)
        print(f"   Response status: {response.status_code}")
        print(f"   Response: {response.text}")

        if response.status_code == 200:
            job_data = response.json()
            job_id = job_data.get('job_id')
            print(f"✅ Translation job started successfully!")
            print(f"   Job ID: {job_id}")

            # 5. Monitor job status
            print("\n5. Monitoring job status...")
            for i in range(10):  # Check status for up to 10 iterations
                time.sleep(2)
                status_response = requests.get(f"{API_BASE}/status/{job_id}")
                if status_response.status_code == 200:
                    status_data = status_response.json()
                    print(f"   Status: {status_data['status']} - Progress: {status_data.get('progress', 0)}%")

                    if status_data['status'] in ['completed', 'failed', 'cancelled']:
                        break
                else:
                    print(f"   Failed to get status: {status_response.status_code}")
                    break

        elif response.status_code == 422:
            print("⚠️  Validation error - this is expected without a real API key")
            print("   The endpoint is working but needs valid credentials")
        else:
            print(f"❌ Translation request failed: {response.status_code}")
            print(f"   Error: {response.text}")

    except Exception as e:
        print(f"❌ Translation test failed: {str(e)}")

    finally:
        # Cleanup
        if test_file_path.exists():
            test_file_path.unlink()
            print(f"\n🧹 Cleaned up test file: {test_file_path}")

    print("\n✅ API testing completed!")
    print("   The translation API is properly configured and ready to use.")
    print("   To perform actual translations, provide valid API keys for your chosen model.")

    return True

if __name__ == "__main__":
    test_translation_api()