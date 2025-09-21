#!/usr/bin/env python3
"""
Simplified test runner that mocks book_maker dependencies
"""
import sys
from pathlib import Path
from unittest.mock import Mock, patch
import pytest

# Add api_layer to path
sys.path.insert(0, str(Path(__file__).parent))

# Mock book_maker modules before importing
sys.modules['book_maker'] = Mock()
sys.modules['book_maker.loader'] = Mock()
sys.modules['book_maker.translator'] = Mock()
sys.modules['book_maker.utils'] = Mock()

# Mock the specific imports
book_loader_dict = {
    'epub': Mock(),
    'txt': Mock(),
    'srt': Mock(),
    'md': Mock()
}

translator_classes = {
    'ChatGPTAPI': Mock(),
    'Claude': Mock(),
    'Gemini': Mock(),
    'DeepL': Mock(),
    'Google': Mock(),
    'GroqClient': Mock(),
    'QwenTranslator': Mock(),
    'XAIClient': Mock(),
    'Caiyun': Mock(),
    'TencentTranSmart': Mock(),
    'DeepLFree': Mock(),
    'CustomAPI': Mock()
}

sys.modules['book_maker.loader'].BOOK_LOADER_DICT = book_loader_dict
for name, cls in translator_classes.items():
    setattr(sys.modules['book_maker.translator'], name, cls)

def test_api_imports():
    """Test that API modules can be imported"""
    try:
        from api.models import TranslationModel, JobStatus, TranslationJob
        from api.job_manager import JobManager
        from api.progress_monitor import AsyncProgressTracker
        print("✅ All API modules imported successfully")
        return True
    except Exception as e:
        print(f"❌ Import failed: {e}")
        return False

def test_models():
    """Test data models"""
    from api.models import TranslationModel, JobStatus, TranslationJob
    from datetime import datetime

    # Test TranslationModel enum
    assert TranslationModel.GOOGLE == "google"
    assert TranslationModel.DEEPL_FREE == "deepl_free"

    # Test JobStatus enum
    assert JobStatus.PENDING == "pending"
    assert JobStatus.COMPLETED == "completed"

    # Test TranslationJob creation
    job = TranslationJob(
        job_id="test-123",
        status=JobStatus.PENDING,
        filename="test.epub",
        created_at=datetime.now()
    )

    assert job.job_id == "test-123"
    assert job.status == JobStatus.PENDING
    assert job.progress == 0

    # Test progress update
    job.update_progress(50, 100)
    assert job.progress == 50
    assert job.processed_paragraphs == 50
    assert job.total_paragraphs == 100

    print("✅ Models tests passed")

def test_job_manager():
    """Test job manager functionality"""
    from api.job_manager import JobManager
    from api.models import JobStatus
    from datetime import datetime

    # Create job manager
    manager = JobManager()

    # Test job creation
    job = manager.create_job(
        filename="test.epub",
        model="google",
        target_language="zh-cn"
    )

    assert job.filename == "test.epub"
    assert job.model == "google"
    assert job.target_language == "zh-cn"
    assert job.status == JobStatus.PENDING

    # Test job retrieval
    retrieved = manager.get_job(job.job_id)
    assert retrieved.job_id == job.job_id

    # Test job stats
    stats = manager.get_job_stats()
    assert "total" in stats
    assert stats["total"] >= 1

    print("✅ Job manager tests passed")

def test_progress_monitor():
    """Test progress monitoring"""
    from api.progress_monitor import AsyncProgressTracker, ProgressUpdate
    from datetime import datetime

    tracker = AsyncProgressTracker()

    # Test progress update
    update = ProgressUpdate(
        job_id="test-123",
        current=25,
        total=100,
        percentage=25.0,
        timestamp=datetime.now(),
        description="Processing..."
    )

    assert update.job_id == "test-123"
    assert update.current == 25
    assert update.total == 100
    assert update.percentage == 25.0

    # Test duration estimation
    duration = tracker.estimate_duration("test-job", 50)
    assert "minutes" in duration or "seconds" in duration

    print("✅ Progress monitor tests passed")

if __name__ == "__main__":
    print("🧪 Running simplified API tests...\n")

    success = True

    try:
        success &= test_api_imports()
        test_models()
        test_job_manager()
        test_progress_monitor()

        print(f"\n{'✅ All tests passed!' if success else '❌ Some tests failed!'}")

    except Exception as e:
        print(f"❌ Test execution failed: {e}")
        import traceback
        traceback.print_exc()
        success = False

    sys.exit(0 if success else 1)