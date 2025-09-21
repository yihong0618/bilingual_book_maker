"""
Async wrapper around bilingual_book_maker for non-blocking translation processing
"""
import os
import sys
import shutil
import traceback
import logging
from pathlib import Path
from typing import Optional, Dict, Any, Callable
from datetime import datetime, timedelta
import signal
import threading

# Add book_maker to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from book_maker.loader import BOOK_LOADER_DICT
from book_maker.translator import (
    ChatGPTAPI, Claude, Gemini, DeepL, Google,
    GroqClient, QwenTranslator, XAIClient, Caiyun, TencentTranSmart,
    DeepLFree, CustomAPI
)

from .models import TranslationJob, JobStatus, TranslationModel
from .job_manager import job_manager
from .progress_monitor import global_progress_tracker, ProgressUpdate


logger = logging.getLogger(__name__)


class TranslationTimeoutError(Exception):
    """Raised when translation exceeds timeout"""
    pass


class AsyncEPUBTranslator:
    """
    Async wrapper around bilingual_book_maker EPUBBookLoader
    Provides non-blocking translation with job tracking and progress monitoring
    """

    # Model mapping for easy access
    MODEL_CLASSES = {
        TranslationModel.CHATGPT: ChatGPTAPI,
        TranslationModel.CLAUDE: Claude,
        TranslationModel.GEMINI: Gemini,
        TranslationModel.DEEPL: DeepL,
        TranslationModel.DEEPL_FREE: DeepLFree,
        TranslationModel.GOOGLE: Google,
        TranslationModel.GROQ: GroqClient,
        TranslationModel.QWEN: QwenTranslator,
        TranslationModel.XAI: XAIClient,
    }

    def __init__(self, timeout_minutes: int = 30, max_retries: int = 1):
        """
        Initialize async translator

        Args:
            timeout_minutes: Maximum translation time in minutes
            max_retries: Maximum number of retry attempts
        """
        self.timeout = timedelta(minutes=timeout_minutes)
        self.max_retries = max_retries

    def start_translation(
        self,
        file_path: str,
        model: TranslationModel,
        key: str,
        language: str = "zh-cn",
        **kwargs
    ) -> str:
        """
        Start a non-blocking translation job

        Args:
            file_path: Path to the EPUB file to translate
            model: Translation model to use
            key: API key for the translation service
            language: Target language code
            **kwargs: Additional translation parameters

        Returns:
            Job ID for tracking the translation

        Raises:
            FileNotFoundError: If the input file doesn't exist
            ValueError: If invalid parameters are provided
        """
        # Validate input file
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"Input file not found: {file_path}")

        # Check supported file formats
        supported_formats = ['.epub', '.txt', '.srt', '.md']
        file_ext = '.' + file_path.lower().split('.')[-1] if '.' in file_path else ''

        if file_ext not in supported_formats:
            raise ValueError(f"Unsupported file format. Supported formats: {', '.join(supported_formats)}")

        # Validate model
        if model not in self.MODEL_CLASSES:
            raise ValueError(f"Unsupported model: {model}")

        # Create job
        filename = os.path.basename(file_path)
        job = job_manager.create_job(
            filename=filename,
            model=model.value,
            target_language=language,
            source_language=kwargs.get('source_lang', 'auto'),
            model_api_base=kwargs.get('model_api_base'),
            temperature=kwargs.get('temperature', 1.0),
            context_flag=kwargs.get('context_flag', False),
            context_paragraph_limit=kwargs.get('context_paragraph_limit', 0),
            single_translate=kwargs.get('single_translate', False),
            is_test=kwargs.get('is_test', False),
            test_num=kwargs.get('test_num', 5),
        )

        # File is already saved at the correct path, no need to copy again
        upload_path = file_path

        # Set up progress callback
        def progress_callback(update: ProgressUpdate):
            job_manager.update_job_progress(
                job_id=update.job_id,
                processed=update.current,
                total=update.total
            )

        # Start the translation job
        success = job_manager.start_job(
            job_id=job.job_id,
            translation_func=lambda j: self._execute_translation(j, model, key, upload_path, **kwargs),
            progress_callback=progress_callback
        )

        if not success:
            raise RuntimeError(f"Failed to start translation job {job.job_id}")

        logger.info(f"Started translation job {job.job_id} for file {filename}")
        return job.job_id

    def get_job_status(self, job_id: str) -> Optional[TranslationJob]:
        """
        Get the current status of a translation job

        Args:
            job_id: Job ID to check

        Returns:
            TranslationJob object or None if not found
        """
        return job_manager.get_job(job_id)

    def cancel_translation(self, job_id: str) -> bool:
        """
        Cancel a running translation job

        Args:
            job_id: Job ID to cancel

        Returns:
            True if successfully cancelled, False otherwise
        """
        return job_manager.cancel_job(job_id)

    def get_download_path(self, job_id: str) -> Optional[str]:
        """
        Get the download path for a completed translation

        Args:
            job_id: Job ID to get download path for

        Returns:
            Path to the translated file or None if not available
        """
        job = job_manager.get_job(job_id)
        if job and job.status == JobStatus.COMPLETED and job.output_path:
            if os.path.exists(job.output_path):
                return job.output_path
        return None

    def list_jobs(self, status_filter: Optional[JobStatus] = None) -> list[TranslationJob]:
        """
        List all jobs, optionally filtered by status

        Args:
            status_filter: Optional status to filter by

        Returns:
            List of TranslationJob objects
        """
        jobs = job_manager.get_all_jobs()
        if status_filter:
            jobs = [job for job in jobs if job.status == status_filter]
        return jobs

    def get_system_stats(self) -> Dict[str, Any]:
        """Get system statistics"""
        return {
            "job_stats": job_manager.get_job_stats(),
            "uptime": "N/A",  # Could track this if needed
            "memory_usage": "N/A",  # Could add psutil if needed
        }

    def _execute_translation(
        self,
        job: TranslationJob,
        model: TranslationModel,
        key: str,
        file_path: str,
        **kwargs
    ) -> str:
        """
        Execute the actual translation in a separate thread

        Args:
            job: Translation job to execute
            model: Translation model to use
            key: API key
            file_path: Path to the uploaded file
            **kwargs: Additional parameters

        Returns:
            Path to the translated file

        Raises:
            TranslationTimeoutError: If translation times out
            Exception: For other translation errors
        """
        job_id = job.job_id
        upload_path = file_path

        # Set up timeout handling
        timeout_occurred = threading.Event()

        def timeout_handler():
            timeout_occurred.wait(self.timeout.total_seconds())
            if not timeout_occurred.is_set():
                logger.error(f"Translation timeout for job {job_id}")
                # The job will be marked as failed by the exception

        timeout_thread = threading.Thread(target=timeout_handler, daemon=True)
        timeout_thread.start()

        try:
            # Create output path
            output_path = job_manager.get_output_path(job_id, job.filename)

            # Execute translation with progress monitoring
            with global_progress_tracker.create_tqdm_patch(job_id):
                self._translate_with_loader(
                    input_path=str(upload_path),
                    output_path=str(output_path),
                    model=model,
                    key=key,
                    job=job,
                    **kwargs
                )

            # Signal successful completion
            timeout_occurred.set()

            if not os.path.exists(output_path):
                raise RuntimeError("Translation completed but output file was not created")

            logger.info(f"Translation completed for job {job_id}: {output_path}")
            return str(output_path)

        except Exception as e:
            # Signal timeout thread to stop
            timeout_occurred.set()

            # Handle retries
            if job.retry_count < self.max_retries:
                job.retry_count += 1
                logger.info(f"Retrying job {job_id} (attempt {job.retry_count + 1})")

                # Exponential backoff
                import time
                time.sleep(2 ** job.retry_count)

                return self._execute_translation(job, model, key, file_path, **kwargs)
            else:
                logger.error(f"Translation failed for job {job_id} after {job.retry_count + 1} attempts: {e}")
                raise

    def _translate_with_loader(
        self,
        input_path: str,
        output_path: str,
        model: TranslationModel,
        key: str,
        job: TranslationJob,
        **kwargs
    ) -> None:
        """
        Execute translation using appropriate loader

        Args:
            input_path: Path to input file (EPUB, TXT, SRT, MD)
            output_path: Path for output file
            model: Translation model
            key: API key
            job: Translation job for parameter access
            **kwargs: Additional parameters
        """
        # Get model class
        model_class = self.MODEL_CLASSES.get(model)
        if not model_class:
            raise ValueError(f"Unsupported model: {model}")

        # Determine file type and get appropriate loader
        file_ext = '.' + input_path.lower().split('.')[-1] if '.' in input_path else ''
        file_type = file_ext[1:]  # Remove the dot

        loader_class = BOOK_LOADER_DICT.get(file_type)
        if not loader_class:
            raise ValueError(f"No loader found for file type: {file_type}")

        # Prepare parameters for the loader
        loader_kwargs = {
            'model': model_class,
            'key': key,
            'resume': kwargs.get('resume', False),
            'language': job.target_language,
            'model_api_base': job.model_api_base,
            'is_test': job.is_test,
            'test_num': job.test_num,
            'single_translate': job.single_translate,
            'context_flag': job.context_flag,
            'context_paragraph_limit': job.context_paragraph_limit,
            'temperature': job.temperature,
            'source_lang': job.source_language,
        }

        # Add file-specific parameter based on loader type
        if file_type == 'epub':
            loader_kwargs['epub_name'] = input_path
        elif file_type == 'txt':
            loader_kwargs['txt_name'] = input_path
        elif file_type == 'srt':
            loader_kwargs['srt_name'] = input_path
        elif file_type == 'md':
            loader_kwargs['md_name'] = input_path

        # Add any additional prompt config
        if 'prompt_config' in kwargs:
            loader_kwargs['prompt_config'] = kwargs['prompt_config']

        try:
            # Create loader instance
            loader = loader_class(**loader_kwargs)

            # Execute translation with format-specific handling
            if file_type == 'epub':
                # Monkey patch the loader to use our output path
                original_make_bilingual_book = loader.make_bilingual_book

                def patched_make_bilingual_book():
                    # Call original method
                    original_make_bilingual_book()

                    # Move the generated file to our desired output path
                    name, _ = os.path.splitext(input_path)
                    generated_file = f"{name}_bilingual.epub"

                    if os.path.exists(generated_file):
                        shutil.move(generated_file, output_path)
                    else:
                        raise RuntimeError("Translation completed but bilingual file was not generated")

                loader.make_bilingual_book = patched_make_bilingual_book
                loader.make_bilingual_book()
            else:
                # For TXT, SRT, MD files, they typically generate files directly
                # Call the make_bilingual_book method
                loader.make_bilingual_book()

                # Find the generated file and move it to output path
                name, ext = os.path.splitext(input_path)
                generated_file = f"{name}_bilingual{ext}"

                if os.path.exists(generated_file):
                    shutil.move(generated_file, output_path)
                else:
                    # Fallback: try different naming conventions
                    possible_files = [
                        f"{name}.txt",
                        f"{name}_translated{ext}",
                        f"{name}_bilingual{ext}"
                    ]
                    for possible_file in possible_files:
                        if os.path.exists(possible_file):
                            shutil.move(possible_file, output_path)
                            break
                    else:
                        raise RuntimeError(f"Translation completed but output file was not found. Expected: {generated_file}")

        except KeyboardInterrupt:
            raise RuntimeError("Translation was interrupted")
        except Exception as e:
            logger.error(f"Translation error: {e}")
            logger.error(traceback.format_exc())
            raise


# Global translator instance
async_translator = AsyncEPUBTranslator()