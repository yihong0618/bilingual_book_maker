import os
import sys
import asyncio
import logging
import tempfile
import shutil
from pathlib import Path
from typing import Optional, Dict, Any
from datetime import datetime

# Add parent directory to path to import book_maker
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from book_maker.loader import BOOK_LOADER_DICT
from book_maker.translator import MODEL_DICT
from book_maker.utils import LANGUAGES

from .models import TranslationJob, TranslationStatus, TranslationModel
from .storage import storage
from .config import settings

logger = logging.getLogger(__name__)


class TranslationService:
    def __init__(self):
        self.jobs: Dict[str, TranslationJob] = {}  # In-memory job tracking for MVP

    async def translate_epub(
        self,
        job_id: str,
        input_path: str,
        output_dir: str,
        target_language: str,
        model: TranslationModel,
        api_keys: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> str:
        """
        Translate an EPUB file using the bilingual_book_maker package.
        Returns the path to the translated file.
        """
        try:
            # Map model enum to book_maker model name
            model_name = model.value

            # Get the translator class
            translator_class = MODEL_DICT.get(model_name)
            if not translator_class:
                raise ValueError(f"Unsupported model: {model_name}")

            # Prepare API key based on model type
            api_key = self._get_api_key(model, api_keys)

            # Map language code to full name if needed
            if target_language in LANGUAGES:
                language = LANGUAGES[target_language]
            else:
                language = target_language

            # Get book loader (epub)
            book_loader = BOOK_LOADER_DICT.get("epub")
            if not book_loader:
                raise ValueError("EPUB loader not found")

            # Create output directory if it doesn't exist
            Path(output_dir).mkdir(parents=True, exist_ok=True)

            # Initialize the book loader with translator
            loader = book_loader(
                input_path,
                translator_class,
                api_key,
                resume=False,
                language=language,
                model_api_base=kwargs.get('api_base'),
                is_test=kwargs.get('test_mode', False),
                test_num=kwargs.get('test_num', 10),
                prompt_config=kwargs.get('prompt_config'),
                single_translate=kwargs.get('single_translate', False),
                context_flag=kwargs.get('use_context', False),
                context_paragraph_limit=kwargs.get('context_paragraph_limit', 0),
                temperature=kwargs.get('temperature', 1.0),
                source_lang=kwargs.get('source_lang', 'auto')
            )

            # Set additional options
            if kwargs.get('accumulated_num'):
                loader.accumulated_num = kwargs['accumulated_num']
            if kwargs.get('translation_style'):
                loader.translation_style = kwargs['translation_style']

            # Run the translation
            logger.info(f"Starting translation for job {job_id}")
            loader.make_bilingual_book()

            # Find the output file
            base_name = Path(input_path).stem
            if kwargs.get('single_translate'):
                output_filename = f"{base_name}_{language}.epub"
            else:
                output_filename = f"{base_name}_bilingual.epub"

            # The bilingual_book_maker saves files in the same directory as input
            source_output = Path(input_path).parent / output_filename
            dest_output = Path(output_dir) / output_filename

            # Move the output file to our output directory
            if source_output.exists():
                shutil.move(str(source_output), str(dest_output))
                logger.info(f"Translation completed: {dest_output}")
                return str(dest_output)
            else:
                raise FileNotFoundError(f"Translation output not found: {source_output}")

        except Exception as e:
            logger.error(f"Translation failed for job {job_id}: {str(e)}")
            raise

    def _get_api_key(self, model: TranslationModel, api_keys: Optional[Dict[str, str]]) -> str:
        """Extract the appropriate API key for the given model."""
        if not api_keys:
            api_keys = {}

        key_mapping = {
            TranslationModel.CHATGPT: 'openai_key',
            TranslationModel.GPT4: 'openai_key',
            TranslationModel.GPT4OMINI: 'openai_key',
            TranslationModel.GPT4O: 'openai_key',
            TranslationModel.CLAUDE: 'claude_key',
            TranslationModel.CLAUDE_3_OPUS: 'claude_key',
            TranslationModel.CLAUDE_3_SONNET: 'claude_key',
            TranslationModel.CLAUDE_3_HAIKU: 'claude_key',
            TranslationModel.GEMINI: 'gemini_key',
            TranslationModel.GEMINIPRO: 'gemini_key',
            TranslationModel.DEEPL: 'deepl_key',
            TranslationModel.CAIYUN: 'caiyun_key',
            TranslationModel.GROQ: 'groq_key',
            TranslationModel.XAI: 'xai_key',
            TranslationModel.QWEN: 'qwen_key',
            TranslationModel.CUSTOM: 'custom_api',
        }

        key_name = key_mapping.get(model)
        if key_name:
            return api_keys.get(key_name, "")

        # For Google Translate, no key is required
        return ""

    async def process_translation(self, job: TranslationJob, file_content: bytes, request_data: Dict[str, Any]):
        """Process a translation job."""
        temp_dir = None
        try:
            # Update job status
            job.status = TranslationStatus.PROCESSING
            self.jobs[job.job_id] = job

            # Create temp directory for processing
            temp_dir = tempfile.mkdtemp(prefix=f"epub_trans_{job.job_id}_")

            # Save uploaded file to temp
            input_path = os.path.join(temp_dir, "input.epub")
            with open(input_path, 'wb') as f:
                f.write(file_content)

            # Create output directory
            output_dir = os.path.join(temp_dir, "output")
            os.makedirs(output_dir, exist_ok=True)

            # Run translation
            output_path = await self.translate_epub(
                job.job_id,
                input_path,
                output_dir,
                request_data['target_language'],
                request_data['model'],
                request_data.get('api_keys'),
                **request_data
            )

            # Save result to storage
            output_filename = os.path.basename(output_path)
            storage_path = await storage.save_result(job.job_id, output_path, output_filename)

            # Get download URL
            download_url = await storage.get_download_url(job.job_id, output_filename)

            # Update job
            job.status = TranslationStatus.COMPLETED
            job.completed_at = datetime.utcnow()
            job.duration_seconds = int((job.completed_at - job.created_at).total_seconds())
            job.storage_path = storage_path
            job.download_url = download_url
            self.jobs[job.job_id] = job

            logger.info(f"Job {job.job_id} completed successfully")

        except Exception as e:
            logger.error(f"Job {job.job_id} failed: {str(e)}")
            job.status = TranslationStatus.FAILED
            job.error_message = str(e)
            job.completed_at = datetime.utcnow()
            job.duration_seconds = int((job.completed_at - job.created_at).total_seconds())
            self.jobs[job.job_id] = job

        finally:
            # Cleanup temp directory
            if temp_dir and os.path.exists(temp_dir):
                shutil.rmtree(temp_dir)

    def get_job(self, job_id: str) -> Optional[TranslationJob]:
        """Get job status."""
        return self.jobs.get(job_id)

    def list_jobs(self) -> list:
        """List all jobs."""
        return list(self.jobs.values())


# Global translator service instance
translator_service = TranslationService()