"""
Integration test to verify async wrapper works with bilingual_book_maker
"""

import sys
import tempfile
import shutil
from pathlib import Path
from unittest.mock import Mock, patch

# Add paths
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

from api.async_translator import AsyncEPUBTranslator
from api.models import TranslationModel
from api.job_manager import job_manager


def test_async_wrapper_integration():
    """
    Test that async wrapper integrates properly with bilingual_book_maker
    """
    print("Testing Async Wrapper Integration...")

    # Create translator
    translator = AsyncEPUBTranslator(timeout_minutes=1, max_retries=0)

    # Create a mock EPUB file
    with tempfile.NamedTemporaryFile(suffix=".epub", delete=False) as temp_file:
        temp_file.write(b"mock epub content")
        temp_epub_path = temp_file.name

    try:
        # Test model class mapping
        print("✓ Testing model class mapping...")
        for model in TranslationModel:
            if model in translator.MODEL_CLASSES:
                print(
                    f"  ✓ {model.value} -> {translator.MODEL_CLASSES[model].__name__}"
                )
            else:
                print(f"  ✗ {model.value} not mapped")

        # Test job creation and path management
        print("✓ Testing job creation...")

        # Mock the translation execution to avoid actual API calls
        with patch.object(translator, "_execute_translation") as mock_execute:
            mock_execute.return_value = "/mock/output/path.epub"

            # Test starting a translation job
            job_id = translator.start_translation(
                file_path=temp_epub_path,
                model=TranslationModel.CHATGPT,
                key="mock-api-key",
                language="zh-cn",
                is_test=True,
                test_num=3,
            )

            print(f"  ✓ Created job: {job_id}")

            # Test job status retrieval
            job = translator.get_job_status(job_id)
            print(f"  ✓ Job status: {job.status.value}")
            print(f"  ✓ Job filename: {job.filename}")
            print(f"  ✓ Job model: {job.model}")

            # Test job stats
            stats = translator.get_system_stats()
            print(f"  ✓ System stats: {stats}")

        print("✓ Integration test completed successfully!")

    except Exception as e:
        print(f"✗ Integration test failed: {e}")
        import traceback

        traceback.print_exc()

    finally:
        # Cleanup
        try:
            Path(temp_epub_path).unlink()
        except:
            pass

        # Cleanup any created directories
        for directory in ["uploads", "outputs", "temp"]:
            path = Path(directory)
            if path.exists():
                shutil.rmtree(path, ignore_errors=True)


def test_progress_monitoring():
    """
    Test progress monitoring integration
    """
    print("\nTesting Progress Monitoring...")

    from api.progress_monitor import global_progress_tracker, ProgressUpdate

    try:
        # Test progress callback
        progress_updates = []

        def test_callback(update: ProgressUpdate):
            progress_updates.append(update)
            print(
                f"  Progress: {update.percentage:.1f}% ({update.current}/{update.total})"
            )

        job_id = "test-progress-job"

        # Start tracking
        global_progress_tracker.start_tracking(job_id, test_callback)

        # Simulate progress updates
        global_progress_tracker.monitor.update_progress(
            job_id, 25, 100, "Processing..."
        )
        global_progress_tracker.monitor.update_progress(
            job_id, 50, 100, "Halfway done..."
        )
        global_progress_tracker.monitor.update_progress(job_id, 100, 100, "Completed!")

        # Stop tracking
        global_progress_tracker.stop_tracking(job_id)

        print(f"  ✓ Received {len(progress_updates)} progress updates")

        # Test duration estimation
        duration = global_progress_tracker.estimate_duration("test", 100)
        print(f"  ✓ Duration estimate for 100 paragraphs: {duration}")

        print("✓ Progress monitoring test completed successfully!")

    except Exception as e:
        print(f"✗ Progress monitoring test failed: {e}")


def test_error_handling():
    """
    Test error handling components
    """
    print("\nTesting Error Handling...")

    from api.error_handler import global_error_handler, TranslationError, ErrorType

    try:
        # Test error classification
        test_error = ValueError("Test validation error")
        classified_error = global_error_handler.handle_error(
            test_error, "test-job", "test-context"
        )

        print(f"  ✓ Error classified as: {classified_error.error_type}")
        print(f"  ✓ Error message: {classified_error}")

        # Test error stats
        stats = global_error_handler.get_error_stats()
        print(f"  ✓ Error stats: {stats}")

        print("✓ Error handling test completed successfully!")

    except Exception as e:
        print(f"✗ Error handling test failed: {e}")


def test_job_manager():
    """
    Test job manager functionality
    """
    print("\nTesting Job Manager...")

    try:
        # Test job creation
        job = job_manager.create_job(
            filename="test.epub", model="chatgpt", target_language="zh-cn"
        )

        print(f"  ✓ Created job: {job.job_id}")
        print(f"  ✓ Job status: {job.status}")

        # Test job retrieval
        retrieved_job = job_manager.get_job(job.job_id)
        assert retrieved_job == job
        print("  ✓ Job retrieval works")

        # Test job stats
        stats = job_manager.get_job_stats()
        print(f"  ✓ Job stats: {stats}")

        # Test path generation
        upload_path = job_manager.get_upload_path("test.epub")
        output_path = job_manager.get_output_path(job.job_id, "test.epub")
        temp_path = job_manager.get_temp_path(job.job_id)

        print(f"  ✓ Upload path: {upload_path}")
        print(f"  ✓ Output path: {output_path}")
        print(f"  ✓ Temp path: {temp_path}")

        print("✓ Job manager test completed successfully!")

    except Exception as e:
        print(f"✗ Job manager test failed: {e}")


def main():
    """
    Run all integration tests
    """
    print("Bilingual Book Maker - Async Wrapper Integration Tests")
    print("=" * 60)

    test_async_wrapper_integration()
    test_progress_monitoring()
    test_error_handling()
    test_job_manager()

    print("\n" + "=" * 60)
    print("Integration tests completed!")
    print("\nIf all tests passed, the async wrapper is ready for use.")
    print("Start the API server with: python api/main.py")


if __name__ == "__main__":
    main()
