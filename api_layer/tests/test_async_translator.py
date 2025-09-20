"""
Unit tests for AsyncEPUBTranslator
"""
import pytest
import os
import tempfile
import shutil
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from api.async_translator import AsyncEPUBTranslator
from api.models import TranslationModel, JobStatus
from api.job_manager import job_manager


class TestAsyncEPUBTranslator:
    """Test suite for AsyncEPUBTranslator"""

    @pytest.fixture
    def translator(self):
        """Create an AsyncEPUBTranslator instance for testing"""
        return AsyncEPUBTranslator(timeout_minutes=1, max_retries=1)

    @pytest.fixture
    def temp_epub_file(self):
        """Create a temporary EPUB file for testing"""
        with tempfile.NamedTemporaryFile(suffix='.epub', delete=False) as f:
            f.write(b'mock epub content')
            temp_path = f.name

        yield temp_path

        # Cleanup
        if os.path.exists(temp_path):
            os.unlink(temp_path)

    @pytest.fixture
    def cleanup_dirs(self):
        """Clean up test directories after test"""
        yield
        # Cleanup
        for directory in ["uploads", "outputs", "temp"]:
            path = Path(directory)
            if path.exists():
                shutil.rmtree(path, ignore_errors=True)

    def test_start_translation_success(self, translator, temp_epub_file, cleanup_dirs):
        """Test successful translation start"""
        with patch.object(job_manager, 'create_job') as mock_create, \
             patch.object(job_manager, 'start_job') as mock_start, \
             patch('shutil.copy2') as mock_copy:

            # Mock job creation
            mock_job = Mock()
            mock_job.job_id = "test-job-123"
            mock_create.return_value = mock_job
            mock_start.return_value = True

            job_id = translator.start_translation(
                file_path=temp_epub_file,
                model=TranslationModel.CHATGPT,
                key="test-key",
                language="zh-cn"
            )

            assert job_id == "test-job-123"
            mock_create.assert_called_once()
            mock_start.assert_called_once()
            mock_copy.assert_called_once()

    def test_start_translation_file_not_found(self, translator):
        """Test translation start with non-existent file"""
        with pytest.raises(FileNotFoundError):
            translator.start_translation(
                file_path="/non/existent/file.epub",
                model=TranslationModel.CHATGPT,
                key="test-key"
            )

    def test_start_translation_invalid_file_type(self, translator):
        """Test translation start with invalid file type"""
        with tempfile.NamedTemporaryFile(suffix='.txt') as f:
            with pytest.raises(ValueError, match="Only EPUB files are supported"):
                translator.start_translation(
                    file_path=f.name,
                    model=TranslationModel.CHATGPT,
                    key="test-key"
                )

    def test_start_translation_unsupported_model(self, translator, temp_epub_file):
        """Test translation start with unsupported model"""
        # Create a mock unsupported model
        class UnsupportedModel:
            pass

        with pytest.raises(ValueError, match="Unsupported model"):
            translator.start_translation(
                file_path=temp_epub_file,
                model=UnsupportedModel(),
                key="test-key"
            )

    def test_get_job_status(self, translator):
        """Test getting job status"""
        with patch.object(job_manager, 'get_job') as mock_get:
            mock_job = Mock()
            mock_get.return_value = mock_job

            result = translator.get_job_status("test-job-123")

            assert result == mock_job
            mock_get.assert_called_once_with("test-job-123")

    def test_cancel_translation(self, translator):
        """Test translation cancellation"""
        with patch.object(job_manager, 'cancel_job') as mock_cancel:
            mock_cancel.return_value = True

            result = translator.cancel_translation("test-job-123")

            assert result is True
            mock_cancel.assert_called_once_with("test-job-123")

    def test_get_download_path_success(self, translator):
        """Test getting download path for completed job"""
        with patch.object(job_manager, 'get_job') as mock_get, \
             patch('os.path.exists') as mock_exists:

            mock_job = Mock()
            mock_job.status = JobStatus.COMPLETED
            mock_job.output_path = "/path/to/output.epub"
            mock_get.return_value = mock_job
            mock_exists.return_value = True

            result = translator.get_download_path("test-job-123")

            assert result == "/path/to/output.epub"

    def test_get_download_path_job_not_completed(self, translator):
        """Test getting download path for non-completed job"""
        with patch.object(job_manager, 'get_job') as mock_get:
            mock_job = Mock()
            mock_job.status = JobStatus.PROCESSING
            mock_get.return_value = mock_job

            result = translator.get_download_path("test-job-123")

            assert result is None

    def test_get_download_path_file_not_exists(self, translator):
        """Test getting download path when file doesn't exist"""
        with patch.object(job_manager, 'get_job') as mock_get, \
             patch('os.path.exists') as mock_exists:

            mock_job = Mock()
            mock_job.status = JobStatus.COMPLETED
            mock_job.output_path = "/path/to/output.epub"
            mock_get.return_value = mock_job
            mock_exists.return_value = False

            result = translator.get_download_path("test-job-123")

            assert result is None

    def test_list_jobs(self, translator):
        """Test listing jobs"""
        with patch.object(job_manager, 'get_all_jobs') as mock_get_all:
            mock_jobs = [Mock(), Mock(), Mock()]
            mock_jobs[0].status = JobStatus.PENDING
            mock_jobs[1].status = JobStatus.COMPLETED
            mock_jobs[2].status = JobStatus.FAILED
            mock_get_all.return_value = mock_jobs

            # Test without filter
            result = translator.list_jobs()
            assert len(result) == 3

            # Test with filter
            result = translator.list_jobs(status_filter=JobStatus.COMPLETED)
            assert len(result) == 1
            assert result[0].status == JobStatus.COMPLETED

    def test_get_system_stats(self, translator):
        """Test getting system statistics"""
        with patch.object(job_manager, 'get_job_stats') as mock_stats:
            mock_stats.return_value = {"total": 5, "active": 2}

            result = translator.get_system_stats()

            assert "job_stats" in result
            assert result["job_stats"]["total"] == 5
            assert result["job_stats"]["active"] == 2

    @patch('api.async_translator.EPUBBookLoader')
    @patch('shutil.move')
    @patch('os.path.exists')
    def test_translate_with_loader_success(self, mock_exists, mock_move, mock_loader_class, translator):
        """Test successful translation execution with loader"""
        # Mock the loader and its methods
        mock_loader = Mock()
        mock_loader_class.return_value = mock_loader
        mock_exists.side_effect = lambda path: "bilingual.epub" in path

        # Mock job
        mock_job = Mock()
        mock_job.target_language = "zh-cn"
        mock_job.model_api_base = None
        mock_job.is_test = False
        mock_job.test_num = 5
        mock_job.single_translate = False
        mock_job.context_flag = False
        mock_job.context_paragraph_limit = 0
        mock_job.temperature = 1.0
        mock_job.source_language = "auto"

        # Call the method
        translator._translate_with_loader(
            epub_path="/input/test.epub",
            output_path="/output/test_bilingual.epub",
            model=TranslationModel.CHATGPT,
            key="test-key",
            job=mock_job
        )

        # Verify loader was created and called
        mock_loader_class.assert_called_once()
        mock_loader.make_bilingual_book.assert_called_once()

    @patch('api.async_translator.EPUBBookLoader')
    def test_translate_with_loader_error(self, mock_loader_class, translator):
        """Test translation execution with loader error"""
        # Mock the loader to raise an exception
        mock_loader = Mock()
        mock_loader.make_bilingual_book.side_effect = ValueError("Translation error")
        mock_loader_class.return_value = mock_loader

        # Mock job
        mock_job = Mock()
        mock_job.target_language = "zh-cn"
        mock_job.model_api_base = None
        mock_job.is_test = False
        mock_job.test_num = 5
        mock_job.single_translate = False
        mock_job.context_flag = False
        mock_job.context_paragraph_limit = 0
        mock_job.temperature = 1.0
        mock_job.source_language = "auto"

        # Should raise the error
        with pytest.raises(ValueError, match="Translation error"):
            translator._translate_with_loader(
                epub_path="/input/test.epub",
                output_path="/output/test_bilingual.epub",
                model=TranslationModel.CHATGPT,
                key="test-key",
                job=mock_job
            )

    def test_execute_translation_with_retry(self, translator):
        """Test translation execution with retry logic"""
        # Mock job
        mock_job = Mock()
        mock_job.job_id = "test-job"
        mock_job.filename = "test.epub"
        mock_job.retry_count = 0

        with patch.object(translator, '_translate_with_loader') as mock_translate, \
             patch.object(job_manager, 'get_upload_path') as mock_upload_path, \
             patch.object(job_manager, 'get_output_path') as mock_output_path, \
             patch('os.path.exists') as mock_exists:

            mock_upload_path.return_value = "/uploads/test.epub"
            mock_output_path.return_value = "/outputs/test_bilingual.epub"
            mock_exists.return_value = True

            # First call fails, second succeeds
            mock_translate.side_effect = [ValueError("API error"), None]

            result = translator._execute_translation(
                job=mock_job,
                model=TranslationModel.CHATGPT,
                key="test-key"
            )

            # Should have retried and succeeded
            assert mock_translate.call_count == 2
            assert result == "/outputs/test_bilingual.epub"
            assert mock_job.retry_count == 1

    def test_execute_translation_max_retries_exceeded(self, translator):
        """Test translation execution with max retries exceeded"""
        # Mock job
        mock_job = Mock()
        mock_job.job_id = "test-job"
        mock_job.filename = "test.epub"
        mock_job.retry_count = 0

        with patch.object(translator, '_translate_with_loader') as mock_translate, \
             patch.object(job_manager, 'get_upload_path') as mock_upload_path, \
             patch.object(job_manager, 'get_output_path') as mock_output_path:

            mock_upload_path.return_value = "/uploads/test.epub"
            mock_output_path.return_value = "/outputs/test_bilingual.epub"

            # Always fails
            mock_translate.side_effect = ValueError("Persistent error")

            with pytest.raises(ValueError, match="Persistent error"):
                translator._execute_translation(
                    job=mock_job,
                    model=TranslationModel.CHATGPT,
                    key="test-key"
                )

            # Should have attempted max retries + 1
            assert mock_translate.call_count == translator.max_retries + 1

    def test_model_classes_mapping(self, translator):
        """Test that all model types have corresponding classes"""
        for model in TranslationModel:
            assert model in translator.MODEL_CLASSES, f"Model {model} not in MODEL_CLASSES"


if __name__ == "__main__":
    pytest.main([__file__])