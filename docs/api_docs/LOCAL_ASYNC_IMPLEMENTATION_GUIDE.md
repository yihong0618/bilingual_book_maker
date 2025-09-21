# Local Async EPUB Translation API Implementation Guide

## Executive Summary

This document provides a comprehensive implementation guide for converting the bilingual_book_maker CLI tool into a local async API service. The implementation focuses on creating a robust foundation for asynchronous EPUB translation processing that will eventually migrate to AWS Fargate. This Phase 1 concentrates on local development with in-memory job tracking, async processing, and a clean architecture that enables seamless cloud migration.

**Key Objectives:**
- Transform synchronous CLI tool to async API service
- Implement non-blocking translation requests with job tracking
- Create foundation for AWS Fargate migration
- Support 10-1000 translations/month volume
- Ensure 99.9% reliability for local development

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Current State Analysis](#current-state-analysis)
3. [Technical Design](#technical-design)
4. [Implementation Strategy](#implementation-strategy)
5. [Code Structure](#code-structure)
6. [Development Workflow](#development-workflow)
7. [Testing Strategy](#testing-strategy)
8. [Migration Path to AWS](#migration-path-to-aws)
9. [Performance Considerations](#performance-considerations)
10. [Security & Error Handling](#security--error-handling)

## Architecture Overview

### High-Level System Design

```
┌─────────────────┐    ┌──────────────────┐    ┌─────────────────┐
│   Client App    │────│   FastAPI App    │────│  Job Manager    │
│                 │    │                  │    │   (In-Memory)   │
└─────────────────┘    └──────────────────┘    └─────────────────┘
                                │                        │
                                │                        │
                       ┌────────▼────────┐    ┌─────────▼─────────┐
                       │ Translation     │    │   Progress        │
                       │ Service         │    │   Reporter        │
                       │ (Async)         │    │   (AsyncIO)       │
                       └─────────────────┘    └───────────────────┘
                                │
                       ┌────────▼────────┐
                       │  File Storage   │
                       │   (Local FS)    │
                       └─────────────────┘
```

### Core Components

1. **FastAPI Application**: Web API framework with async support
2. **Job Manager**: In-memory job tracking with status updates
3. **Translation Service**: Async wrapper around existing translation logic
4. **Progress Reporter**: Real-time progress updates via asyncio
5. **File Storage**: Local filesystem with cleanup policies
6. **Worker Pool**: Async task execution with concurrency control

### Design Principles

- **Async-First**: All I/O operations are asynchronous
- **Non-Blocking**: API responses immediate with job tracking
- **Stateless**: Job state persisted for horizontal scaling preparation
- **Modular**: Clean separation for AWS migration
- **Resilient**: Comprehensive error handling and recovery

## Current State Analysis

### Existing CLI Architecture

The current implementation is a synchronous CLI tool with the following structure:

```python
# Current Flow (Synchronous)
cli.py → EPUBBookLoader → Translator → Output File
```

**Key Components:**
- `/book_maker/cli.py`: Command-line interface with argument parsing
- `/book_maker/loader/epub_loader.py`: EPUB file processing
- `/book_maker/translator/`: Various translation service implementations
- `/book_maker/utils.py`: Shared utilities and language mappings

**Limitations for API Use:**
- Synchronous execution blocks request thread
- No job tracking or progress reporting
- CLI-only interface without HTTP endpoints
- No concurrent processing capabilities
- Limited error handling for API scenarios

### Existing API Layer (Basic)

The current `/api_layer/api/main.py` provides a basic synchronous endpoint:

```python
@app.post("/translate")
async def translate_epub(file, target_language, model, ...):
    # Current implementation is synchronous despite async def
    output_path = await translator.translate_epub(...)  # Blocking call
    return FileResponse(output_path)
```

**Issues with Current Implementation:**
- Marked as `async` but performs blocking operations
- No job management or progress tracking
- Direct file response without proper cleanup
- No timeout handling or cancellation support

## Technical Design

### 1. Async Processing Model

#### Threading vs AsyncIO Decision

**Selected Approach: Hybrid AsyncIO + ThreadPoolExecutor**

```python
import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, Dict, Any

class AsyncTranslationService:
    def __init__(self):
        self.executor = ThreadPoolExecutor(max_workers=4)
        self.active_jobs: Dict[str, asyncio.Task] = {}

    async def translate_epub_async(
        self,
        job_id: str,
        input_path: str,
        **kwargs
    ) -> str:
        """Async wrapper for translation with progress reporting"""
        loop = asyncio.get_event_loop()

        # Run translation in thread pool to avoid blocking
        task = loop.run_in_executor(
            self.executor,
            self._translate_with_progress,
            job_id,
            input_path,
            kwargs
        )

        self.active_jobs[job_id] = task
        try:
            result = await task
            return result
        finally:
            self.active_jobs.pop(job_id, None)
```

**Rationale:**
- **AsyncIO**: For HTTP handling, job management, progress updates
- **ThreadPoolExecutor**: For CPU-intensive translation work
- **Benefits**: Non-blocking API, proper resource management, cancellation support

#### Job Lifecycle Management

```python
from enum import Enum
from datetime import datetime, timedelta
from typing import Dict, Optional
import asyncio

class JobStatus(Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

class JobManager:
    def __init__(self):
        self.jobs: Dict[str, TranslationJob] = {}
        self.cleanup_task: Optional[asyncio.Task] = None

    async def create_job(self, job_data: Dict[str, Any]) -> str:
        """Create new translation job"""
        job = TranslationJob(
            job_id=str(uuid.uuid4()),
            status=JobStatus.PENDING,
            created_at=datetime.utcnow(),
            **job_data
        )
        self.jobs[job.job_id] = job

        # Start cleanup task if not running
        if not self.cleanup_task:
            self.cleanup_task = asyncio.create_task(self._cleanup_loop())

        return job.job_id

    async def _cleanup_loop(self):
        """Background cleanup of expired jobs"""
        while True:
            try:
                await asyncio.sleep(300)  # Check every 5 minutes
                await self._cleanup_expired_jobs()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Cleanup error: {e}")

    async def _cleanup_expired_jobs(self):
        """Remove jobs older than 3 hours"""
        cutoff_time = datetime.utcnow() - timedelta(hours=3)
        expired_jobs = [
            job_id for job_id, job in self.jobs.items()
            if job.completed_at and job.completed_at < cutoff_time
        ]

        for job_id in expired_jobs:
            await self._cleanup_job_files(job_id)
            del self.jobs[job_id]

        if expired_jobs:
            logger.info(f"Cleaned up {len(expired_jobs)} expired jobs")
```

### 2. Progress Reporting System

```python
import asyncio
from typing import Callable, Optional

class ProgressReporter:
    def __init__(self, job_id: str, job_manager: JobManager):
        self.job_id = job_id
        self.job_manager = job_manager
        self.current_progress = 0

    async def update_progress(self, percentage: int, message: str = None):
        """Update job progress (called every 10% completion)"""
        if percentage > self.current_progress:
            self.current_progress = percentage
            await self.job_manager.update_job_progress(
                self.job_id,
                percentage,
                message
            )
            logger.info(f"Job {self.job_id}: {percentage}% - {message}")

    async def set_status(self, status: JobStatus, error_message: str = None):
        """Update job status"""
        await self.job_manager.update_job_status(
            self.job_id,
            status,
            error_message
        )

# Integration with existing translation logic
class AsyncEPUBTranslator:
    def __init__(self, progress_reporter: ProgressReporter):
        self.progress_reporter = progress_reporter

    async def translate_paragraphs(self, paragraphs: List[str]) -> List[str]:
        """Translate paragraphs with progress reporting"""
        total = len(paragraphs)
        translated = []

        for i, paragraph in enumerate(paragraphs):
            result = await self._translate_single(paragraph)
            translated.append(result)

            # Report progress every 10%
            progress = int((i + 1) / total * 100)
            if progress % 10 == 0:
                await self.progress_reporter.update_progress(
                    progress,
                    f"Translated {i + 1}/{total} paragraphs"
                )

        return translated
```

### 3. API Endpoint Design

#### Core Endpoints

```python
from fastapi import FastAPI, UploadFile, Form, HTTPException
from typing import Optional

app = FastAPI(title="EPUB Translator API", version="1.0.0")

@app.post("/translate", response_model=TranslationResponse)
async def start_translation(
    file: UploadFile,
    target_language: str = Form(...),
    model: TranslationModel = Form(TranslationModel.CHATGPT),
    # ... other parameters
) -> TranslationResponse:
    """Start async translation job - returns immediately with job_id"""

    # Validate input
    if not file.filename.endswith('.epub'):
        raise HTTPException(400, "Only EPUB files supported")

    # Create job
    job_id = await job_manager.create_job({
        'filename': file.filename,
        'target_language': target_language,
        'model': model,
        # ... other parameters
    })

    # Save uploaded file
    input_path = await file_storage.save_upload(job_id, file)

    # Start async translation (non-blocking)
    asyncio.create_task(translation_service.process_job(job_id, input_path))

    return TranslationResponse(
        job_id=job_id,
        status=JobStatus.PENDING,
        message="Translation job started",
        estimated_duration_minutes=5  # Based on file size
    )

@app.get("/status/{job_id}", response_model=JobStatusResponse)
async def get_job_status(job_id: str) -> JobStatusResponse:
    """Get current job status and progress"""
    job = await job_manager.get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")

    return JobStatusResponse(
        job_id=job.job_id,
        status=job.status,
        progress=job.progress,
        filename=job.filename,
        created_at=job.created_at,
        completed_at=job.completed_at,
        duration_seconds=job.duration_seconds,
        error_message=job.error_message,
        download_url=job.download_url
    )

@app.get("/download/{job_id}")
async def download_result(job_id: str):
    """Download completed translation"""
    job = await job_manager.get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")

    if job.status != JobStatus.COMPLETED:
        raise HTTPException(400, f"Job not completed (status: {job.status})")

    file_path = await file_storage.get_result_path(job_id)
    if not os.path.exists(file_path):
        raise HTTPException(404, "Result file not found")

    return FileResponse(
        file_path,
        filename=job.output_filename,
        media_type='application/epub+zip'
    )

@app.delete("/jobs/{job_id}")
async def cancel_job(job_id: str):
    """Cancel running translation job"""
    success = await job_manager.cancel_job(job_id)
    if not success:
        raise HTTPException(404, "Job not found or cannot be cancelled")

    return {"message": "Job cancelled successfully"}

@app.get("/models")
async def list_models():
    """List available translation models"""
    return {
        "models": [model.value for model in TranslationModel],
        "descriptions": {
            "chatgptapi": "OpenAI GPT-3.5 Turbo",
            "gpt4": "OpenAI GPT-4",
            "claude": "Anthropic Claude",
            "gemini": "Google Gemini",
            # ... other models
        }
    }

@app.get("/languages")
async def list_languages():
    """List supported target languages"""
    return {
        "languages": {
            "zh": "Chinese (Simplified)",
            "zh-hant": "Chinese (Traditional)",
            "en": "English",
            "ja": "Japanese",
            "ko": "Korean",
            "es": "Spanish",
            "fr": "French",
            "de": "German",
            # ... other languages
        }
    }
```

## Implementation Strategy

### Phase 1: Local Async Foundation (Weeks 1-2)

#### Week 1: Core Infrastructure
```bash
# Task breakdown
1. Job Management System (2 days)
   - In-memory job storage with TTL
   - Job lifecycle management
   - Background cleanup tasks

2. Async Translation Service (2 days)
   - ThreadPoolExecutor integration
   - Progress reporting hooks
   - Error handling and retry logic

3. Updated API Endpoints (1 day)
   - Non-blocking /translate endpoint
   - Status and download endpoints
   - Job cancellation support
```

#### Week 2: Integration & Testing
```bash
4. File Storage Abstraction (1 day)
   - Local filesystem implementation
   - AWS S3 interface preparation
   - Cleanup policies

5. Progress Integration (2 days)
   - Modify existing loaders for progress reporting
   - Real-time status updates
   - Error propagation

6. End-to-End Testing (2 days)
   - Integration tests
   - Load testing with multiple concurrent jobs
   - Error scenario testing
```

### Phase 2: Docker & Production Readiness (Week 3)

```dockerfile
# Dockerfile for local development
FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
  CMD curl -f http://localhost:8000/health || exit 1

CMD ["uvicorn", "api_layer.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

```yaml
# docker-compose.yml for development
version: '3.8'
services:
  epub-translator:
    build: .
    ports:
      - "8000:8000"
    environment:
      - LOG_LEVEL=INFO
      - STORAGE_MODE=local
      - MAX_CONCURRENT_JOBS=4
    volumes:
      - ./temp:/app/temp
      - ./logs:/app/logs
    restart: unless-stopped
```

### Phase 3: AWS Migration Preparation (Week 4)

```python
# Abstract interfaces for AWS migration
from abc import ABC, abstractmethod

class StorageInterface(ABC):
    @abstractmethod
    async def save_upload(self, job_id: str, file: UploadFile) -> str:
        pass

    @abstractmethod
    async def save_result(self, job_id: str, file_path: str) -> str:
        pass

    @abstractmethod
    async def get_download_url(self, job_id: str) -> str:
        pass

class JobStoreInterface(ABC):
    @abstractmethod
    async def save_job(self, job: TranslationJob):
        pass

    @abstractmethod
    async def get_job(self, job_id: str) -> Optional[TranslationJob]:
        pass

    @abstractmethod
    async def update_job_status(self, job_id: str, status: JobStatus):
        pass

# Local implementations
class LocalFileStorage(StorageInterface):
    def __init__(self, base_path: str = "./temp"):
        self.base_path = Path(base_path)
        self.base_path.mkdir(exist_ok=True)

class InMemoryJobStore(JobStoreInterface):
    def __init__(self):
        self.jobs: Dict[str, TranslationJob] = {}

# Future AWS implementations
class S3Storage(StorageInterface):
    pass

class DynamoDBJobStore(JobStoreInterface):
    pass
```

## Code Structure

### Directory Organization

```
bilingual_book_maker/
├── api_layer/
│   ├── api/
│   │   ├── __init__.py
│   │   ├── main.py              # FastAPI app and endpoints
│   │   ├── models.py            # Pydantic models
│   │   ├── config.py            # Configuration management
│   │   ├── dependencies.py      # FastAPI dependencies
│   │   └── services/
│   │       ├── __init__.py
│   │       ├── job_manager.py   # Job lifecycle management
│   │       ├── translation.py   # Async translation service
│   │       ├── storage.py       # File storage abstraction
│   │       └── progress.py      # Progress reporting
│   ├── tests/
│   │   ├── __init__.py
│   │   ├── test_api.py          # API endpoint tests
│   │   ├── test_integration.py  # End-to-end tests
│   │   └── conftest.py          # Test configuration
│   ├── scripts/
│   │   ├── run_dev.py           # Development server
│   │   └── cleanup.py           # Manual cleanup utilities
│   └── requirements.txt         # API-specific dependencies
├── book_maker/                  # Existing CLI code (enhanced)
│   ├── async_adapters/          # New: Async wrappers
│   │   ├── __init__.py
│   │   ├── epub_async.py        # Async EPUB processing
│   │   └── translator_async.py  # Async translator wrapper
│   └── ... (existing structure)
├── docker/
│   ├── Dockerfile
│   ├── docker-compose.yml
│   └── docker-compose.prod.yml
└── docs/
    ├── api_reference.md
    ├── deployment_guide.md
    └── migration_aws.md
```

### Key Files Implementation

#### `/api_layer/api/services/job_manager.py`

```python
import asyncio
import uuid
from datetime import datetime, timedelta
from typing import Dict, Optional, List
from enum import Enum
import logging

from ..models import TranslationJob, JobStatus

logger = logging.getLogger(__name__)

class JobManager:
    """Manages translation job lifecycle and status tracking"""

    def __init__(self, cleanup_interval_minutes: int = 5, job_ttl_hours: int = 3):
        self.jobs: Dict[str, TranslationJob] = {}
        self.cleanup_interval = cleanup_interval_minutes * 60
        self.job_ttl = timedelta(hours=job_ttl_hours)
        self.cleanup_task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()

    async def start(self):
        """Start background cleanup task"""
        if not self.cleanup_task:
            self.cleanup_task = asyncio.create_task(self._cleanup_loop())
            logger.info("Job manager started with cleanup interval: %d minutes",
                       self.cleanup_interval // 60)

    async def stop(self):
        """Stop background tasks"""
        if self.cleanup_task:
            self.cleanup_task.cancel()
            try:
                await self.cleanup_task
            except asyncio.CancelledError:
                pass
            logger.info("Job manager stopped")

    async def create_job(self, job_data: Dict) -> str:
        """Create new translation job"""
        job_id = str(uuid.uuid4())

        job = TranslationJob(
            job_id=job_id,
            status=JobStatus.PENDING,
            created_at=datetime.utcnow(),
            progress=0,
            **job_data
        )

        async with self._lock:
            self.jobs[job_id] = job

        logger.info(f"Created job {job_id} for file {job_data.get('filename')}")
        return job_id

    async def get_job(self, job_id: str) -> Optional[TranslationJob]:
        """Get job by ID"""
        async with self._lock:
            return self.jobs.get(job_id)

    async def update_job_progress(self, job_id: str, progress: int, message: str = None):
        """Update job progress"""
        async with self._lock:
            if job_id in self.jobs:
                self.jobs[job_id].progress = progress
                if message:
                    self.jobs[job_id].current_message = message
                logger.debug(f"Job {job_id}: {progress}% - {message}")

    async def update_job_status(
        self,
        job_id: str,
        status: JobStatus,
        error_message: str = None,
        download_url: str = None
    ):
        """Update job status"""
        async with self._lock:
            if job_id in self.jobs:
                job = self.jobs[job_id]
                job.status = status

                if status in [JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED]:
                    job.completed_at = datetime.utcnow()
                    if job.created_at:
                        job.duration_seconds = int(
                            (job.completed_at - job.created_at).total_seconds()
                        )

                if error_message:
                    job.error_message = error_message

                if download_url:
                    job.download_url = download_url

                logger.info(f"Job {job_id} status updated to {status.value}")

    async def cancel_job(self, job_id: str) -> bool:
        """Cancel running job"""
        async with self._lock:
            if job_id in self.jobs:
                job = self.jobs[job_id]
                if job.status in [JobStatus.PENDING, JobStatus.PROCESSING]:
                    job.status = JobStatus.CANCELLED
                    job.completed_at = datetime.utcnow()
                    logger.info(f"Job {job_id} cancelled")
                    return True
        return False

    async def list_active_jobs(self) -> List[TranslationJob]:
        """List all active (non-completed) jobs"""
        async with self._lock:
            return [
                job for job in self.jobs.values()
                if job.status in [JobStatus.PENDING, JobStatus.PROCESSING]
            ]

    async def _cleanup_loop(self):
        """Background cleanup of expired jobs"""
        while True:
            try:
                await asyncio.sleep(self.cleanup_interval)
                await self._cleanup_expired_jobs()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Cleanup error: {e}")

    async def _cleanup_expired_jobs(self):
        """Remove jobs older than TTL"""
        cutoff_time = datetime.utcnow() - self.job_ttl
        expired_jobs = []

        async with self._lock:
            for job_id, job in list(self.jobs.items()):
                if (job.completed_at and job.completed_at < cutoff_time) or \
                   (not job.completed_at and job.created_at < cutoff_time):
                    expired_jobs.append(job_id)
                    del self.jobs[job_id]

        if expired_jobs:
            logger.info(f"Cleaned up {len(expired_jobs)} expired jobs")
            # TODO: Cleanup associated files
```

#### `/api_layer/api/services/translation.py`

```python
import asyncio
import os
import tempfile
import shutil
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Any, Optional
import logging

from book_maker.loader import BOOK_LOADER_DICT
from book_maker.translator import MODEL_DICT
from ..models import TranslationModel, JobStatus
from .job_manager import JobManager
from .progress import ProgressReporter

logger = logging.getLogger(__name__)

class TranslationService:
    """Async translation service using ThreadPoolExecutor"""

    def __init__(self, max_workers: int = 4, timeout_minutes: int = 30):
        self.executor = ThreadPoolExecutor(max_workers=max_workers)
        self.timeout_seconds = timeout_minutes * 60
        self.active_jobs: Dict[str, asyncio.Task] = {}

    async def process_job(
        self,
        job_id: str,
        input_path: str,
        job_manager: JobManager,
        **kwargs
    ):
        """Process translation job asynchronously"""

        progress_reporter = ProgressReporter(job_id, job_manager)

        try:
            # Update status to processing
            await job_manager.update_job_status(job_id, JobStatus.PROCESSING)

            # Start translation with timeout
            task = asyncio.create_task(
                self._translate_with_timeout(
                    job_id, input_path, progress_reporter, **kwargs
                )
            )
            self.active_jobs[job_id] = task

            # Wait for completion or timeout
            output_path = await asyncio.wait_for(task, timeout=self.timeout_seconds)

            # Generate download URL (local file path for now)
            download_url = f"/download/{job_id}"

            await job_manager.update_job_status(
                job_id,
                JobStatus.COMPLETED,
                download_url=download_url
            )

            logger.info(f"Job {job_id} completed successfully")

        except asyncio.TimeoutError:
            await job_manager.update_job_status(
                job_id,
                JobStatus.FAILED,
                error_message=f"Translation timeout after {self.timeout_seconds // 60} minutes"
            )
            logger.error(f"Job {job_id} timed out")

        except Exception as e:
            await job_manager.update_job_status(
                job_id,
                JobStatus.FAILED,
                error_message=str(e)
            )
            logger.error(f"Job {job_id} failed: {e}")

        finally:
            self.active_jobs.pop(job_id, None)

    async def _translate_with_timeout(
        self,
        job_id: str,
        input_path: str,
        progress_reporter: ProgressReporter,
        **kwargs
    ) -> str:
        """Execute translation in thread pool with progress reporting"""

        loop = asyncio.get_event_loop()

        # Run translation in executor to avoid blocking
        output_path = await loop.run_in_executor(
            self.executor,
            self._translate_sync,
            job_id,
            input_path,
            progress_reporter,
            kwargs
        )

        return output_path

    def _translate_sync(
        self,
        job_id: str,
        input_path: str,
        progress_reporter: ProgressReporter,
        kwargs: Dict[str, Any]
    ) -> str:
        """Synchronous translation with progress callbacks"""

        try:
            # Extract parameters
            target_language = kwargs['target_language']
            model = kwargs['model']
            api_keys = kwargs.get('api_keys', {})
            single_translate = kwargs.get('single_translate', False)
            test_mode = kwargs.get('test_mode', False)
            test_num = kwargs.get('test_num', 10)
            temperature = kwargs.get('temperature', 1.0)

            # Get translator class
            translate_model_class = MODEL_DICT.get(model.value)
            if not translate_model_class:
                raise ValueError(f"Unsupported model: {model}")

            # Get API key for model
            api_key = self._get_api_key_for_model(model, api_keys)

            # Create translator instance
            translator = translate_model_class(
                api_key,
                target_language,
                temperature=temperature
            )

            # Get book loader
            file_ext = os.path.splitext(input_path)[1][1:]  # Remove dot
            loader_class = BOOK_LOADER_DICT.get(file_ext)
            if not loader_class:
                raise ValueError(f"Unsupported file type: {file_ext}")

            # Create enhanced loader with progress reporting
            loader = loader_class(
                input_path,
                translator,
                api_key,
                resume=False,
                language=target_language,
                is_test=test_mode,
                test_num=test_num,
                single_translate=single_translate
            )

            # Monkey patch progress reporting
            original_translate = loader.translate_model.translate

            def translate_with_progress(text, *args, **kwargs):
                # This is a simplified progress hook
                # In practice, you'd need to modify the loaders more extensively
                result = original_translate(text, *args, **kwargs)
                # Progress reporting would happen here based on loader state
                return result

            loader.translate_model.translate = translate_with_progress

            # Report initial progress
            asyncio.run_coroutine_threadsafe(
                progress_reporter.update_progress(10, "Starting translation"),
                asyncio.get_event_loop()
            )

            # Perform translation
            loader.make_bilingual_book()

            # Report completion
            asyncio.run_coroutine_threadsafe(
                progress_reporter.update_progress(100, "Translation completed"),
                asyncio.get_event_loop()
            )

            # Generate output path
            base_name = os.path.splitext(os.path.basename(input_path))[0]
            if single_translate:
                output_filename = f"{base_name}_{target_language}.epub"
            else:
                output_filename = f"{base_name}_bilingual.epub"

            # The loader should have created the output file
            # This is a simplification - actual implementation would need
            # to be coordinated with the existing loader code
            output_dir = os.path.dirname(input_path)
            output_path = os.path.join(output_dir, output_filename)

            if not os.path.exists(output_path):
                raise RuntimeError("Translation completed but output file not found")

            return output_path

        except Exception as e:
            # Report error through progress reporter
            asyncio.run_coroutine_threadsafe(
                progress_reporter.set_status(JobStatus.FAILED, str(e)),
                asyncio.get_event_loop()
            )
            raise

    def _get_api_key_for_model(self, model: TranslationModel, api_keys: Dict[str, str]) -> str:
        """Get appropriate API key for the specified model"""

        key_mapping = {
            'chatgptapi': 'openai_key',
            'gpt4': 'openai_key',
            'gpt4omini': 'openai_key',
            'gpt4o': 'openai_key',
            'claude': 'claude_key',
            'claude-3-opus': 'claude_key',
            'claude-3-sonnet': 'claude_key',
            'claude-3-haiku': 'claude_key',
            'gemini': 'gemini_key',
            'geminipro': 'gemini_key',
            'deepl': 'deepl_key',
            'groq': 'groq_key',
            'xai': 'xai_key',
            'qwen': 'qwen_key'
        }

        key_name = key_mapping.get(model.value)
        if not key_name:
            raise ValueError(f"No API key mapping for model: {model}")

        api_key = api_keys.get(key_name)
        if not api_key:
            # Try environment variables
            env_key = f"BBM_{key_name.upper()}"
            api_key = os.environ.get(env_key)

        if not api_key:
            raise ValueError(f"API key required for model {model}: {key_name}")

        return api_key

    async def cancel_job(self, job_id: str) -> bool:
        """Cancel active translation job"""
        task = self.active_jobs.get(job_id)
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            logger.info(f"Cancelled job {job_id}")
            return True
        return False
```

## Development Workflow

### Local Development Setup

```bash
# 1. Environment Setup
cd bilingual_book_maker
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -r requirements.txt
pip install -r api_layer/requirements.txt

# 2. Configuration
export BBM_OPENAI_API_KEY="your-openai-key"
export BBM_CLAUDE_API_KEY="your-claude-key"
export LOG_LEVEL="DEBUG"
export STORAGE_MODE="local"

# 3. Start Development Server
cd api_layer
python -m uvicorn api.main:app --reload --host 127.0.0.1 --port 8000

# 4. Test API
curl -X GET http://localhost:8000/health
curl -X GET http://localhost:8000/models
curl -X GET http://localhost:8000/languages
```

### Development Testing Workflow

```bash
# Unit Tests
python -m pytest api_layer/tests/test_services.py -v

# Integration Tests
python -m pytest api_layer/tests/test_integration.py -v

# Load Testing (with small files)
python api_layer/scripts/load_test.py --concurrent-jobs 5 --test-file test_books/small.epub

# Manual API Testing
python api_layer/scripts/test_api.py
```

### Test API Script Example

```python
# api_layer/scripts/test_api.py
import asyncio
import aiohttp
import time

async def test_translation_flow():
    """Test complete translation workflow"""

    async with aiohttp.ClientSession() as session:
        # 1. Upload file and start translation
        with open('test_books/small.epub', 'rb') as f:
            data = aiohttp.FormData()
            data.add_field('file', f, filename='test.epub')
            data.add_field('target_language', 'zh')
            data.add_field('model', 'chatgptapi')
            data.add_field('test_mode', 'true')

            async with session.post(
                'http://localhost:8000/translate',
                data=data
            ) as resp:
                result = await resp.json()
                job_id = result['job_id']
                print(f"Started job: {job_id}")

        # 2. Poll for completion
        while True:
            async with session.get(f'http://localhost:8000/status/{job_id}') as resp:
                status = await resp.json()
                print(f"Status: {status['status']} ({status['progress']}%)")

                if status['status'] == 'completed':
                    break
                elif status['status'] == 'failed':
                    print(f"Job failed: {status['error_message']}")
                    return

            await asyncio.sleep(2)

        # 3. Download result
        async with session.get(f'http://localhost:8000/download/{job_id}') as resp:
            if resp.status == 200:
                with open('output.epub', 'wb') as f:
                    f.write(await resp.read())
                print("Downloaded translated file")
            else:
                print(f"Download failed: {resp.status}")

if __name__ == "__main__":
    asyncio.run(test_translation_flow())
```

## Testing Strategy

### Test Categories

#### 1. Unit Tests

```python
# api_layer/tests/test_job_manager.py
import pytest
import asyncio
from datetime import datetime, timedelta

from api.services.job_manager import JobManager
from api.models import JobStatus

@pytest.mark.asyncio
async def test_job_creation():
    """Test basic job creation and retrieval"""
    manager = JobManager()

    job_id = await manager.create_job({
        'filename': 'test.epub',
        'target_language': 'zh',
        'model': 'chatgptapi'
    })

    assert job_id is not None

    job = await manager.get_job(job_id)
    assert job is not None
    assert job.filename == 'test.epub'
    assert job.status == JobStatus.PENDING

@pytest.mark.asyncio
async def test_progress_updates():
    """Test progress reporting"""
    manager = JobManager()
    job_id = await manager.create_job({'filename': 'test.epub'})

    await manager.update_job_progress(job_id, 50, "Half complete")

    job = await manager.get_job(job_id)
    assert job.progress == 50

@pytest.mark.asyncio
async def test_job_cleanup():
    """Test automatic job cleanup"""
    manager = JobManager(job_ttl_hours=0.001)  # 3.6 seconds TTL

    job_id = await manager.create_job({'filename': 'test.epub'})
    await manager.update_job_status(job_id, JobStatus.COMPLETED)

    # Manually trigger cleanup
    await manager._cleanup_expired_jobs()

    job = await manager.get_job(job_id)
    assert job is None
```

#### 2. Integration Tests

```python
# api_layer/tests/test_integration.py
import pytest
import asyncio
from httpx import AsyncClient
from fastapi.testclient import TestClient

from api.main import app

@pytest.mark.asyncio
async def test_full_translation_workflow():
    """Test complete translation workflow"""

    async with AsyncClient(app=app, base_url="http://test") as client:
        # Test file upload
        with open('test_books/small.epub', 'rb') as f:
            response = await client.post(
                '/translate',
                files={'file': ('test.epub', f, 'application/epub+zip')},
                data={
                    'target_language': 'zh',
                    'model': 'chatgptapi',
                    'test_mode': True
                }
            )

        assert response.status_code == 200
        result = response.json()
        job_id = result['job_id']

        # Poll for completion (with timeout)
        max_wait = 60  # seconds
        start_time = time.time()

        while time.time() - start_time < max_wait:
            response = await client.get(f'/status/{job_id}')
            status = response.json()

            if status['status'] == 'completed':
                break
            elif status['status'] == 'failed':
                pytest.fail(f"Translation failed: {status['error_message']}")

            await asyncio.sleep(1)
        else:
            pytest.fail("Translation timed out")

        # Test download
        response = await client.get(f'/download/{job_id}')
        assert response.status_code == 200
        assert len(response.content) > 0

@pytest.mark.asyncio
async def test_concurrent_translations():
    """Test multiple concurrent translations"""

    async with AsyncClient(app=app, base_url="http://test") as client:
        jobs = []

        # Start 3 concurrent jobs
        for i in range(3):
            with open('test_books/small.epub', 'rb') as f:
                response = await client.post(
                    '/translate',
                    files={'file': (f'test_{i}.epub', f, 'application/epub+zip')},
                    data={
                        'target_language': 'zh',
                        'model': 'chatgptapi',
                        'test_mode': True
                    }
                )
            jobs.append(response.json()['job_id'])

        # Wait for all to complete
        completed = 0
        max_wait = 120
        start_time = time.time()

        while completed < 3 and time.time() - start_time < max_wait:
            for job_id in jobs:
                response = await client.get(f'/status/{job_id}')
                status = response.json()
                if status['status'] == 'completed':
                    completed += 1

            await asyncio.sleep(2)

        assert completed == 3, f"Only {completed}/3 jobs completed"
```

#### 3. Load Tests

```python
# api_layer/scripts/load_test.py
import asyncio
import aiohttp
import time
import argparse
from typing import List

class LoadTester:
    def __init__(self, base_url: str = "http://localhost:8000"):
        self.base_url = base_url
        self.results = []

    async def single_translation_test(self, session: aiohttp.ClientSession, test_id: int):
        """Run single translation and measure time"""
        start_time = time.time()

        try:
            # Upload file
            with open('test_books/small.epub', 'rb') as f:
                data = aiohttp.FormData()
                data.add_field('file', f, filename=f'test_{test_id}.epub')
                data.add_field('target_language', 'zh')
                data.add_field('model', 'chatgptapi')
                data.add_field('test_mode', 'true')

                async with session.post(f'{self.base_url}/translate', data=data) as resp:
                    if resp.status != 200:
                        raise Exception(f"Upload failed: {resp.status}")
                    result = await resp.json()
                    job_id = result['job_id']

            # Poll for completion
            while True:
                async with session.get(f'{self.base_url}/status/{job_id}') as resp:
                    status = await resp.json()
                    if status['status'] == 'completed':
                        break
                    elif status['status'] == 'failed':
                        raise Exception(f"Translation failed: {status['error_message']}")
                await asyncio.sleep(1)

            duration = time.time() - start_time
            self.results.append({
                'test_id': test_id,
                'duration': duration,
                'status': 'success'
            })

        except Exception as e:
            duration = time.time() - start_time
            self.results.append({
                'test_id': test_id,
                'duration': duration,
                'status': 'failed',
                'error': str(e)
            })

    async def run_load_test(self, concurrent_jobs: int = 5, total_jobs: int = 20):
        """Run load test with specified parameters"""
        print(f"Starting load test: {concurrent_jobs} concurrent, {total_jobs} total")

        connector = aiohttp.TCPConnector(limit=concurrent_jobs * 2)
        timeout = aiohttp.ClientTimeout(total=300)  # 5 minute timeout

        async with aiohttp.ClientSession(
            connector=connector,
            timeout=timeout
        ) as session:

            # Create semaphore to limit concurrency
            semaphore = asyncio.Semaphore(concurrent_jobs)

            async def run_with_semaphore(test_id):
                async with semaphore:
                    await self.single_translation_test(session, test_id)

            # Run all tests
            tasks = [run_with_semaphore(i) for i in range(total_jobs)]
            await asyncio.gather(*tasks, return_exceptions=True)

        # Print results
        self.print_results()

    def print_results(self):
        """Print load test results"""
        successful = [r for r in self.results if r['status'] == 'success']
        failed = [r for r in self.results if r['status'] == 'failed']

        print(f"\n{'='*50}")
        print(f"LOAD TEST RESULTS")
        print(f"{'='*50}")
        print(f"Total jobs: {len(self.results)}")
        print(f"Successful: {len(successful)}")
        print(f"Failed: {len(failed)}")
        print(f"Success rate: {len(successful)/len(self.results)*100:.1f}%")

        if successful:
            durations = [r['duration'] for r in successful]
            print(f"\nTiming Statistics:")
            print(f"  Average: {sum(durations)/len(durations):.2f}s")
            print(f"  Min: {min(durations):.2f}s")
            print(f"  Max: {max(durations):.2f}s")

        if failed:
            print(f"\nFailures:")
            for fail in failed:
                print(f"  Job {fail['test_id']}: {fail['error']}")

async def main():
    parser = argparse.ArgumentParser(description='Load test the translation API')
    parser.add_argument('--concurrent-jobs', type=int, default=5,
                       help='Number of concurrent jobs')
    parser.add_argument('--total-jobs', type=int, default=20,
                       help='Total number of jobs to run')
    parser.add_argument('--base-url', type=str, default='http://localhost:8000',
                       help='API base URL')

    args = parser.parse_args()

    tester = LoadTester(args.base_url)
    await tester.run_load_test(args.concurrent_jobs, args.total_jobs)

if __name__ == "__main__":
    asyncio.run(main())
```

## Migration Path to AWS

### Architecture Evolution

#### Phase 1: Local Async (Current Implementation)
```
┌─────────────┐    ┌─────────────┐    ┌─────────────┐
│   FastAPI   │    │ In-Memory   │    │   Local     │
│   Server    │────│ Job Store   │────│ File System │
└─────────────┘    └─────────────┘    └─────────────┘
```

#### Phase 2: AWS-Ready with Local Fallback
```
┌─────────────┐    ┌─────────────┐    ┌─────────────┐
│   FastAPI   │    │ Pluggable   │    │ Pluggable   │
│   Server    │────│ Job Store   │────│ Storage     │
└─────────────┘    └─────────────┘    └─────────────┘
                         │                    │
                    ┌────▼────┐        ┌─────▼─────┐
                    │ Memory  │        │   Local   │
                    │   or    │        │    or     │
                    │DynamoDB │        │    S3     │
                    └─────────┘        └───────────┘
```

#### Phase 3: Full AWS Fargate Deployment
```
┌─────────────┐    ┌─────────────┐    ┌─────────────┐    ┌─────────────┐
│  ALB/API    │    │   Fargate   │    │  DynamoDB   │    │     S3      │
│  Gateway    │────│   Service   │────│ Job Store   │────│   Storage   │
└─────────────┘    └─────────────┘    └─────────────┘    └─────────────┘
                         │
                    ┌────▼────┐
                    │ CloudWatch│
                    │ Monitoring │
                    └──────────┘
```

### Implementation Abstractions

#### Storage Abstraction

```python
# api_layer/api/services/storage.py
from abc import ABC, abstractmethod
from typing import Optional
import os
import boto3
from fastapi import UploadFile

class StorageInterface(ABC):
    @abstractmethod
    async def save_upload(self, job_id: str, file: UploadFile) -> str:
        """Save uploaded file and return local path"""
        pass

    @abstractmethod
    async def save_result(self, job_id: str, file_path: str) -> str:
        """Save result file and return storage path"""
        pass

    @abstractmethod
    async def get_download_url(self, job_id: str) -> Optional[str]:
        """Get download URL for result file"""
        pass

    @abstractmethod
    async def cleanup_job_files(self, job_id: str):
        """Cleanup all files for a job"""
        pass

class LocalFileStorage(StorageInterface):
    def __init__(self, base_path: str = "./temp"):
        self.base_path = Path(base_path)
        self.base_path.mkdir(exist_ok=True)

    async def save_upload(self, job_id: str, file: UploadFile) -> str:
        job_dir = self.base_path / job_id
        job_dir.mkdir(exist_ok=True)

        file_path = job_dir / file.filename
        with open(file_path, 'wb') as f:
            content = await file.read()
            f.write(content)

        return str(file_path)

    async def save_result(self, job_id: str, file_path: str) -> str:
        # For local storage, file is already in the right place
        return file_path

    async def get_download_url(self, job_id: str) -> Optional[str]:
        # Return API endpoint for local download
        return f"/download/{job_id}"

    async def cleanup_job_files(self, job_id: str):
        job_dir = self.base_path / job_id
        if job_dir.exists():
            shutil.rmtree(job_dir)

class S3Storage(StorageInterface):
    def __init__(self, bucket_name: str, region: str = "us-east-1"):
        self.bucket_name = bucket_name
        self.s3_client = boto3.client('s3', region_name=region)

    async def save_upload(self, job_id: str, file: UploadFile) -> str:
        # Save to local temp first for processing
        temp_path = f"/tmp/{job_id}_{file.filename}"
        with open(temp_path, 'wb') as f:
            content = await file.read()
            f.write(content)

        # Also upload to S3 for backup
        s3_key = f"uploads/{job_id}/{file.filename}"
        self.s3_client.upload_file(temp_path, self.bucket_name, s3_key)

        return temp_path

    async def save_result(self, job_id: str, file_path: str) -> str:
        filename = os.path.basename(file_path)
        s3_key = f"results/{job_id}/{filename}"

        self.s3_client.upload_file(file_path, self.bucket_name, s3_key)
        return s3_key

    async def get_download_url(self, job_id: str) -> Optional[str]:
        # Generate presigned URL for S3 download
        s3_key = f"results/{job_id}/"

        # Find the result file in S3
        response = self.s3_client.list_objects_v2(
            Bucket=self.bucket_name,
            Prefix=s3_key
        )

        if 'Contents' not in response:
            return None

        # Get first result file
        file_key = response['Contents'][0]['Key']

        return self.s3_client.generate_presigned_url(
            'get_object',
            Params={'Bucket': self.bucket_name, 'Key': file_key},
            ExpiresIn=3600  # 1 hour
        )

    async def cleanup_job_files(self, job_id: str):
        # Delete all files for this job
        prefixes = [f"uploads/{job_id}/", f"results/{job_id}/"]

        for prefix in prefixes:
            response = self.s3_client.list_objects_v2(
                Bucket=self.bucket_name,
                Prefix=prefix
            )

            if 'Contents' in response:
                objects = [{'Key': obj['Key']} for obj in response['Contents']]
                self.s3_client.delete_objects(
                    Bucket=self.bucket_name,
                    Delete={'Objects': objects}
                )

class StorageFactory:
    @staticmethod
    def get_storage() -> StorageInterface:
        storage_mode = os.environ.get('STORAGE_MODE', 'local')

        if storage_mode == 'local':
            return LocalFileStorage()
        elif storage_mode == 's3':
            bucket_name = os.environ.get('S3_BUCKET_NAME')
            if not bucket_name:
                raise ValueError("S3_BUCKET_NAME required for S3 storage")
            return S3Storage(bucket_name)
        else:
            raise ValueError(f"Unknown storage mode: {storage_mode}")
```

#### Job Store Abstraction

```python
# api_layer/api/services/job_store.py
from abc import ABC, abstractmethod
from typing import Dict, List, Optional
import json
import boto3
from datetime import datetime

from ..models import TranslationJob, JobStatus

class JobStoreInterface(ABC):
    @abstractmethod
    async def save_job(self, job: TranslationJob):
        pass

    @abstractmethod
    async def get_job(self, job_id: str) -> Optional[TranslationJob]:
        pass

    @abstractmethod
    async def update_job_status(
        self,
        job_id: str,
        status: JobStatus,
        **kwargs
    ):
        pass

    @abstractmethod
    async def list_active_jobs(self) -> List[TranslationJob]:
        pass

    @abstractmethod
    async def delete_job(self, job_id: str):
        pass

class InMemoryJobStore(JobStoreInterface):
    def __init__(self):
        self.jobs: Dict[str, TranslationJob] = {}

    async def save_job(self, job: TranslationJob):
        self.jobs[job.job_id] = job

    async def get_job(self, job_id: str) -> Optional[TranslationJob]:
        return self.jobs.get(job_id)

    async def update_job_status(self, job_id: str, status: JobStatus, **kwargs):
        if job_id in self.jobs:
            job = self.jobs[job_id]
            job.status = status
            for key, value in kwargs.items():
                if hasattr(job, key):
                    setattr(job, key, value)

    async def list_active_jobs(self) -> List[TranslationJob]:
        return [
            job for job in self.jobs.values()
            if job.status in [JobStatus.PENDING, JobStatus.PROCESSING]
        ]

    async def delete_job(self, job_id: str):
        self.jobs.pop(job_id, None)

class DynamoDBJobStore(JobStoreInterface):
    def __init__(self, table_name: str, region: str = "us-east-1"):
        self.table_name = table_name
        self.dynamodb = boto3.resource('dynamodb', region_name=region)
        self.table = self.dynamodb.Table(table_name)

    async def save_job(self, job: TranslationJob):
        item = job.dict()
        # Convert datetime to ISO string
        for field in ['created_at', 'completed_at']:
            if item.get(field):
                item[field] = item[field].isoformat()

        self.table.put_item(Item=item)

    async def get_job(self, job_id: str) -> Optional[TranslationJob]:
        response = self.table.get_item(Key={'job_id': job_id})

        if 'Item' not in response:
            return None

        item = response['Item']

        # Convert ISO strings back to datetime
        for field in ['created_at', 'completed_at']:
            if item.get(field):
                item[field] = datetime.fromisoformat(item[field])

        return TranslationJob(**item)

    async def update_job_status(self, job_id: str, status: JobStatus, **kwargs):
        update_expression = "SET job_status = :status"
        expression_values = {':status': status.value}

        # Add other fields to update
        for key, value in kwargs.items():
            if key in ['progress', 'error_message', 'download_url', 'completed_at']:
                update_expression += f", {key} = :{key}"
                if isinstance(value, datetime):
                    value = value.isoformat()
                expression_values[f':{key}'] = value

        self.table.update_item(
            Key={'job_id': job_id},
            UpdateExpression=update_expression,
            ExpressionAttributeValues=expression_values
        )

    async def list_active_jobs(self) -> List[TranslationJob]:
        # This would require a GSI on status in production
        response = self.table.scan(
            FilterExpression='job_status IN (:pending, :processing)',
            ExpressionAttributeValues={
                ':pending': JobStatus.PENDING.value,
                ':processing': JobStatus.PROCESSING.value
            }
        )

        jobs = []
        for item in response['Items']:
            # Convert ISO strings back to datetime
            for field in ['created_at', 'completed_at']:
                if item.get(field):
                    item[field] = datetime.fromisoformat(item[field])
            jobs.append(TranslationJob(**item))

        return jobs

    async def delete_job(self, job_id: str):
        self.table.delete_item(Key={'job_id': job_id})
```

### AWS Fargate Deployment Configuration

#### Terraform Infrastructure

```hcl
# terraform/main.tf
terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

# VPC and Networking
resource "aws_vpc" "main" {
  cidr_block           = "10.0.0.0/16"
  enable_dns_hostnames = true
  enable_dns_support   = true

  tags = {
    Name = "epub-translator-vpc"
  }
}

resource "aws_subnet" "private" {
  count             = 2
  vpc_id            = aws_vpc.main.id
  cidr_block        = "10.0.${count.index + 1}.0/24"
  availability_zone = data.aws_availability_zones.available.names[count.index]

  tags = {
    Name = "epub-translator-private-${count.index + 1}"
  }
}

resource "aws_subnet" "public" {
  count                   = 2
  vpc_id                  = aws_vpc.main.id
  cidr_block              = "10.0.${count.index + 10}.0/24"
  availability_zone       = data.aws_availability_zones.available.names[count.index]
  map_public_ip_on_launch = true

  tags = {
    Name = "epub-translator-public-${count.index + 1}"
  }
}

# ECS Cluster
resource "aws_ecs_cluster" "main" {
  name = "epub-translator"

  setting {
    name  = "containerInsights"
    value = "enabled"
  }
}

# ECS Task Definition
resource "aws_ecs_task_definition" "app" {
  family                   = "epub-translator"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = 1024
  memory                   = 2048
  execution_role_arn       = aws_iam_role.ecs_execution_role.arn
  task_role_arn           = aws_iam_role.ecs_task_role.arn

  container_definitions = jsonencode([
    {
      name  = "epub-translator"
      image = "${aws_ecr_repository.app.repository_url}:latest"

      environment = [
        {
          name  = "STORAGE_MODE"
          value = "s3"
        },
        {
          name  = "S3_BUCKET_NAME"
          value = aws_s3_bucket.main.id
        },
        {
          name  = "DYNAMODB_TABLE_NAME"
          value = aws_dynamodb_table.jobs.name
        },
        {
          name  = "AWS_REGION"
          value = var.aws_region
        }
      ]

      secrets = [
        {
          name      = "BBM_OPENAI_API_KEY"
          valueFrom = aws_ssm_parameter.openai_key.arn
        },
        {
          name      = "BBM_CLAUDE_API_KEY"
          valueFrom = aws_ssm_parameter.claude_key.arn
        }
      ]

      portMappings = [
        {
          containerPort = 8000
          protocol      = "tcp"
        }
      ]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.app.name
          awslogs-region        = var.aws_region
          awslogs-stream-prefix = "ecs"
        }
      }

      healthCheck = {
        command = ["CMD-SHELL", "curl -f http://localhost:8000/health || exit 1"]
        interval = 30
        timeout = 5
        retries = 3
      }
    }
  ])
}

# ECS Service
resource "aws_ecs_service" "main" {
  name            = "epub-translator"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.app.arn
  desired_count   = 2
  launch_type     = "FARGATE"

  network_configuration {
    security_groups  = [aws_security_group.ecs_tasks.id]
    subnets         = aws_subnet.private[*].id
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.app.arn
    container_name   = "epub-translator"
    container_port   = 8000
  }

  depends_on = [aws_lb_listener.app]
}

# S3 Bucket for file storage
resource "aws_s3_bucket" "main" {
  bucket = "epub-translator-${random_string.bucket_suffix.result}"
}

resource "aws_s3_bucket_lifecycle_configuration" "main" {
  bucket = aws_s3_bucket.main.id

  rule {
    id     = "cleanup_old_files"
    status = "Enabled"

    expiration {
      days = 7  # Delete files after 7 days
    }
  }
}

# DynamoDB table for job storage
resource "aws_dynamodb_table" "jobs" {
  name           = "epub-translator-jobs"
  billing_mode   = "PAY_PER_REQUEST"
  hash_key       = "job_id"

  attribute {
    name = "job_id"
    type = "S"
  }

  attribute {
    name = "status"
    type = "S"
  }

  global_secondary_index {
    name     = "status-index"
    hash_key = "status"
  }

  ttl {
    attribute_name = "ttl"
    enabled        = true
  }

  tags = {
    Name = "epub-translator-jobs"
  }
}
```

### Migration Steps

#### Step 1: Environment-Based Configuration

```python
# api_layer/api/config.py (Enhanced)
import os
from typing import List, Optional
from pydantic import BaseSettings

class Settings(BaseSettings):
    # Basic settings
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    log_level: str = "INFO"

    # Storage configuration
    storage_mode: str = "local"  # local, s3
    local_storage_path: str = "./temp"
    s3_bucket_name: Optional[str] = None
    aws_region: str = "us-east-1"

    # Job store configuration
    job_store_mode: str = "memory"  # memory, dynamodb
    dynamodb_table_name: Optional[str] = None
    job_ttl_hours: int = 3

    # Processing configuration
    max_workers: int = 4
    translation_timeout_minutes: int = 30
    max_file_size_mb: int = 100
    max_concurrent_jobs: int = 10

    # AWS-specific settings
    ecs_cluster_name: Optional[str] = None
    cloudwatch_log_group: Optional[str] = None

    class Config:
        env_file = ".env"
        case_sensitive = False

    def is_aws_environment(self) -> bool:
        """Check if running in AWS environment"""
        return self.storage_mode == "s3" or self.job_store_mode == "dynamodb"

    def get_storage_config(self) -> dict:
        """Get storage configuration based on mode"""
        if self.storage_mode == "local":
            return {"base_path": self.local_storage_path}
        elif self.storage_mode == "s3":
            return {
                "bucket_name": self.s3_bucket_name,
                "region": self.aws_region
            }
        else:
            raise ValueError(f"Unknown storage mode: {self.storage_mode}")

    def get_job_store_config(self) -> dict:
        """Get job store configuration based on mode"""
        if self.job_store_mode == "memory":
            return {}
        elif self.job_store_mode == "dynamodb":
            return {
                "table_name": self.dynamodb_table_name,
                "region": self.aws_region
            }
        else:
            raise ValueError(f"Unknown job store mode: {self.job_store_mode}")

settings = Settings()
```

#### Step 2: Factory Pattern for Services

```python
# api_layer/api/dependencies.py
from functools import lru_cache
from .config import settings
from .services.storage import StorageInterface, LocalFileStorage, S3Storage
from .services.job_store import JobStoreInterface, InMemoryJobStore, DynamoDBJobStore

@lru_cache()
def get_storage() -> StorageInterface:
    """Get storage implementation based on configuration"""
    if settings.storage_mode == "local":
        return LocalFileStorage(**settings.get_storage_config())
    elif settings.storage_mode == "s3":
        return S3Storage(**settings.get_storage_config())
    else:
        raise ValueError(f"Unknown storage mode: {settings.storage_mode}")

@lru_cache()
def get_job_store() -> JobStoreInterface:
    """Get job store implementation based on configuration"""
    if settings.job_store_mode == "memory":
        return InMemoryJobStore(**settings.get_job_store_config())
    elif settings.job_store_mode == "dynamodb":
        return DynamoDBJobStore(**settings.get_job_store_config())
    else:
        raise ValueError(f"Unknown job store mode: {settings.job_store_mode}")
```

#### Step 3: Environment Configuration Files

```bash
# .env.local (Local development)
STORAGE_MODE=local
JOB_STORE_MODE=memory
LOCAL_STORAGE_PATH=./temp
LOG_LEVEL=DEBUG
MAX_WORKERS=2
```

```bash
# .env.aws (AWS Fargate)
STORAGE_MODE=s3
JOB_STORE_MODE=dynamodb
S3_BUCKET_NAME=epub-translator-production
DYNAMODB_TABLE_NAME=epub-translator-jobs
AWS_REGION=us-east-1
LOG_LEVEL=INFO
MAX_WORKERS=4
```

#### Step 4: Deployment Pipeline

```yaml
# .github/workflows/deploy.yml
name: Deploy to AWS

on:
  push:
    branches: [main]
  workflow_dispatch:

jobs:
  deploy:
    runs-on: ubuntu-latest

    steps:
    - uses: actions/checkout@v3

    - name: Configure AWS credentials
      uses: aws-actions/configure-aws-credentials@v2
      with:
        aws-access-key-id: ${{ secrets.AWS_ACCESS_KEY_ID }}
        aws-secret-access-key: ${{ secrets.AWS_SECRET_ACCESS_KEY }}
        aws-region: us-east-1

    - name: Login to Amazon ECR
      id: login-ecr
      uses: aws-actions/amazon-ecr-login@v1

    - name: Build and push Docker image
      env:
        ECR_REGISTRY: ${{ steps.login-ecr.outputs.registry }}
        ECR_REPOSITORY: epub-translator
        IMAGE_TAG: ${{ github.sha }}
      run: |
        docker build -t $ECR_REGISTRY/$ECR_REPOSITORY:$IMAGE_TAG .
        docker push $ECR_REGISTRY/$ECR_REPOSITORY:$IMAGE_TAG
        docker tag $ECR_REGISTRY/$ECR_REPOSITORY:$IMAGE_TAG $ECR_REGISTRY/$ECR_REPOSITORY:latest
        docker push $ECR_REGISTRY/$ECR_REPOSITORY:latest

    - name: Deploy to ECS
      run: |
        aws ecs update-service \
          --cluster epub-translator \
          --service epub-translator \
          --force-new-deployment
```

## Performance Considerations

### Capacity Planning

#### Local Development Capacity
- **Target**: 5-10 concurrent translations
- **Hardware**: 8GB RAM, 4 CPU cores
- **File Size**: Up to 50MB EPUBs
- **Translation Time**: 2-10 minutes per book

#### Production AWS Capacity
- **Target**: 10-1000 translations/month (1-3 concurrent peak)
- **Fargate**: 1-2 tasks, 1 vCPU, 2GB RAM each
- **Auto-scaling**: Based on queue depth and CPU utilization
- **Storage**: S3 with lifecycle policies

### Memory Management

```python
# Memory optimization for translation processing
import gc
import psutil
from typing import Generator

class MemoryOptimizedTranslator:
    def __init__(self, max_memory_mb: int = 1024):
        self.max_memory_mb = max_memory_mb

    def check_memory_usage(self):
        """Check current memory usage"""
        process = psutil.Process()
        memory_mb = process.memory_info().rss / 1024 / 1024

        if memory_mb > self.max_memory_mb:
            logger.warning(f"High memory usage: {memory_mb:.1f}MB")
            gc.collect()  # Force garbage collection

    def process_in_chunks(self, paragraphs: List[str], chunk_size: int = 50) -> Generator:
        """Process paragraphs in chunks to manage memory"""
        for i in range(0, len(paragraphs), chunk_size):
            chunk = paragraphs[i:i + chunk_size]
            yield chunk

            # Check memory after each chunk
            self.check_memory_usage()
```

### File Size Optimization

```python
# File size limits and streaming
from fastapi import HTTPException
import aiofiles

class FileSizeValidator:
    def __init__(self, max_size_mb: int = 100):
        self.max_size_bytes = max_size_mb * 1024 * 1024

    async def validate_upload(self, file: UploadFile) -> bool:
        """Validate file size without loading entire file"""

        # Check content-length header first
        if hasattr(file, 'size') and file.size:
            if file.size > self.max_size_bytes:
                raise HTTPException(
                    status_code=413,
                    detail=f"File too large: {file.size / 1024 / 1024:.1f}MB > {self.max_size_bytes / 1024 / 1024}MB"
                )

        # Stream and count bytes if no size header
        total_bytes = 0
        async for chunk in file.stream():
            total_bytes += len(chunk)
            if total_bytes > self.max_size_bytes:
                raise HTTPException(
                    status_code=413,
                    detail="File too large"
                )

        # Reset file position for actual processing
        await file.seek(0)
        return True
```

### Caching Strategy

```python
# Translation caching for common phrases
import hashlib
import json
from typing import Dict, Optional
import aioredis

class TranslationCache:
    def __init__(self, redis_url: Optional[str] = None):
        self.redis_url = redis_url
        self.redis = None
        self.local_cache: Dict[str, str] = {}
        self.max_local_cache = 1000

    async def initialize(self):
        """Initialize Redis connection if available"""
        if self.redis_url:
            self.redis = await aioredis.from_url(self.redis_url)

    def _get_cache_key(self, text: str, target_lang: str, model: str) -> str:
        """Generate cache key for translation"""
        content = f"{text}|{target_lang}|{model}"
        return hashlib.md5(content.encode()).hexdigest()

    async def get_translation(
        self,
        text: str,
        target_lang: str,
        model: str
    ) -> Optional[str]:
        """Get cached translation if available"""
        key = self._get_cache_key(text, target_lang, model)

        # Check local cache first
        if key in self.local_cache:
            return self.local_cache[key]

        # Check Redis cache
        if self.redis:
            cached = await self.redis.get(key)
            if cached:
                translation = cached.decode()
                # Store in local cache for faster access
                self.local_cache[key] = translation
                return translation

        return None

    async def cache_translation(
        self,
        text: str,
        target_lang: str,
        model: str,
        translation: str
    ):
        """Cache translation result"""
        key = self._get_cache_key(text, target_lang, model)

        # Store in local cache
        if len(self.local_cache) >= self.max_local_cache:
            # Remove oldest entry (simple FIFO)
            oldest_key = next(iter(self.local_cache))
            del self.local_cache[oldest_key]

        self.local_cache[key] = translation

        # Store in Redis with TTL
        if self.redis:
            await self.redis.setex(key, 3600 * 24 * 7, translation)  # 7 days TTL
```

## Security & Error Handling

### API Security

```python
# Security middleware and validation
from fastapi import HTTPException, Security, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import jwt
from datetime import datetime, timedelta

security = HTTPBearer()

class SecurityManager:
    def __init__(self, secret_key: str):
        self.secret_key = secret_key

    def create_api_token(self, user_id: str, expires_hours: int = 24) -> str:
        """Create JWT token for API access"""
        payload = {
            'user_id': user_id,
            'exp': datetime.utcnow() + timedelta(hours=expires_hours),
            'iat': datetime.utcnow()
        }
        return jwt.encode(payload, self.secret_key, algorithm='HS256')

    def verify_token(self, token: str) -> str:
        """Verify JWT token and return user_id"""
        try:
            payload = jwt.decode(token, self.secret_key, algorithms=['HS256'])
            return payload['user_id']
        except jwt.ExpiredSignatureError:
            raise HTTPException(status_code=401, detail="Token expired")
        except jwt.InvalidTokenError:
            raise HTTPException(status_code=401, detail="Invalid token")

async def get_current_user(credentials: HTTPAuthorizationCredentials = Security(security)):
    """Dependency to get current user from JWT token"""
    if not settings.require_auth:
        return "anonymous"

    security_manager = SecurityManager(settings.jwt_secret)
    return security_manager.verify_token(credentials.credentials)

# Rate limiting
from collections import defaultdict
import time

class RateLimiter:
    def __init__(self, max_requests: int = 10, window_minutes: int = 1):
        self.max_requests = max_requests
        self.window_seconds = window_minutes * 60
        self.requests = defaultdict(list)

    def is_allowed(self, client_id: str) -> bool:
        """Check if request is allowed for client"""
        now = time.time()
        client_requests = self.requests[client_id]

        # Remove old requests outside window
        client_requests[:] = [req_time for req_time in client_requests
                            if now - req_time < self.window_seconds]

        # Check if under limit
        if len(client_requests) >= self.max_requests:
            return False

        # Add current request
        client_requests.append(now)
        return True

rate_limiter = RateLimiter()

async def check_rate_limit(request):
    """Rate limiting middleware"""
    client_ip = request.client.host

    if not rate_limiter.is_allowed(client_ip):
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded. Try again later."
        )
```

### Error Handling & Monitoring

```python
# Comprehensive error handling
import traceback
import logging
from enum import Enum
from dataclasses import dataclass
from typing import Optional, Dict, Any

class ErrorCode(Enum):
    VALIDATION_ERROR = "VALIDATION_ERROR"
    TRANSLATION_FAILED = "TRANSLATION_FAILED"
    FILE_TOO_LARGE = "FILE_TOO_LARGE"
    UNSUPPORTED_FORMAT = "UNSUPPORTED_FORMAT"
    API_KEY_INVALID = "API_KEY_INVALID"
    TIMEOUT_ERROR = "TIMEOUT_ERROR"
    STORAGE_ERROR = "STORAGE_ERROR"
    RATE_LIMIT_EXCEEDED = "RATE_LIMIT_EXCEEDED"
    INTERNAL_ERROR = "INTERNAL_ERROR"

@dataclass
class ErrorInfo:
    code: ErrorCode
    message: str
    details: Optional[Dict[str, Any]] = None
    recoverable: bool = False

class ErrorHandler:
    def __init__(self):
        self.logger = logging.getLogger(__name__)

    def handle_translation_error(self, error: Exception, job_id: str) -> ErrorInfo:
        """Handle translation-specific errors"""

        error_str = str(error).lower()

        if "timeout" in error_str:
            return ErrorInfo(
                code=ErrorCode.TIMEOUT_ERROR,
                message="Translation timed out. Try with a smaller file or contact support.",
                recoverable=True
            )
        elif "api key" in error_str or "unauthorized" in error_str:
            return ErrorInfo(
                code=ErrorCode.API_KEY_INVALID,
                message="Invalid API key for the selected translation model.",
                recoverable=False
            )
        elif "rate limit" in error_str or "quota" in error_str:
            return ErrorInfo(
                code=ErrorCode.RATE_LIMIT_EXCEEDED,
                message="Translation service rate limit exceeded. Please try again later.",
                recoverable=True
            )
        else:
            # Log full error for debugging
            self.logger.error(
                f"Translation failed for job {job_id}: {error}",
                extra={'job_id': job_id, 'error_type': type(error).__name__},
                exc_info=True
            )

            return ErrorInfo(
                code=ErrorCode.TRANSLATION_FAILED,
                message="Translation failed due to an internal error. Please try again or contact support.",
                details={'error_type': type(error).__name__},
                recoverable=True
            )

    def format_user_error(self, error_info: ErrorInfo) -> Dict[str, Any]:
        """Format error for user-facing API response"""
        return {
            'error_code': error_info.code.value,
            'message': error_info.message,
            'recoverable': error_info.recoverable,
            'timestamp': datetime.utcnow().isoformat()
        }

# Monitoring and alerting
import boto3
from typing import Dict, Any

class MonitoringService:
    def __init__(self):
        self.cloudwatch = None
        if settings.is_aws_environment():
            self.cloudwatch = boto3.client('cloudwatch', region_name=settings.aws_region)

    async def record_translation_metrics(
        self,
        job_id: str,
        duration_seconds: float,
        success: bool,
        model: str
    ):
        """Record translation metrics"""

        metrics = [
            {
                'MetricName': 'TranslationDuration',
                'Value': duration_seconds,
                'Unit': 'Seconds',
                'Dimensions': [
                    {'Name': 'Model', 'Value': model},
                    {'Name': 'Success', 'Value': str(success)}
                ]
            },
            {
                'MetricName': 'TranslationCount',
                'Value': 1,
                'Unit': 'Count',
                'Dimensions': [
                    {'Name': 'Model', 'Value': model},
                    {'Name': 'Success', 'Value': str(success)}
                ]
            }
        ]

        if self.cloudwatch:
            try:
                self.cloudwatch.put_metric_data(
                    Namespace='EpubTranslator',
                    MetricData=metrics
                )
            except Exception as e:
                logger.error(f"Failed to send metrics: {e}")
        else:
            # Log metrics locally for development
            logger.info(f"Metrics: {metrics}")

    async def send_alert(self, message: str, severity: str = "WARNING"):
        """Send alert for critical issues"""

        if severity == "CRITICAL" and settings.is_aws_environment():
            # Send SNS notification in production
            sns = boto3.client('sns', region_name=settings.aws_region)
            try:
                sns.publish(
                    TopicArn=settings.alerts_topic_arn,
                    Message=message,
                    Subject=f"EpubTranslator Alert - {severity}"
                )
            except Exception as e:
                logger.error(f"Failed to send alert: {e}")

        # Always log alerts
        logger.error(f"ALERT [{severity}]: {message}")
```

### Health Monitoring

```python
# Health check and system monitoring
from dataclasses import dataclass
from typing import Dict, List
import psutil
import asyncio

@dataclass
class HealthStatus:
    healthy: bool
    issues: List[str]
    metrics: Dict[str, Any]

class HealthChecker:
    def __init__(self):
        self.checks = [
            self._check_memory,
            self._check_disk_space,
            self._check_active_jobs,
            self._check_storage,
            self._check_translation_service
        ]

    async def get_health_status(self) -> HealthStatus:
        """Get comprehensive health status"""
        issues = []
        metrics = {}

        for check in self.checks:
            try:
                check_result = await check()
                if check_result.get('issues'):
                    issues.extend(check_result['issues'])
                metrics.update(check_result.get('metrics', {}))
            except Exception as e:
                issues.append(f"Health check failed: {e}")

        return HealthStatus(
            healthy=len(issues) == 0,
            issues=issues,
            metrics=metrics
        )

    async def _check_memory(self) -> Dict[str, Any]:
        """Check memory usage"""
        memory = psutil.virtual_memory()
        memory_percent = memory.percent

        issues = []
        if memory_percent > 90:
            issues.append(f"High memory usage: {memory_percent:.1f}%")
        elif memory_percent > 80:
            issues.append(f"Warning: memory usage at {memory_percent:.1f}%")

        return {
            'issues': issues,
            'metrics': {
                'memory_usage_percent': memory_percent,
                'memory_available_mb': memory.available / 1024 / 1024
            }
        }

    async def _check_disk_space(self) -> Dict[str, Any]:
        """Check disk space"""
        disk = psutil.disk_usage('/')
        disk_percent = (disk.used / disk.total) * 100

        issues = []
        if disk_percent > 90:
            issues.append(f"Low disk space: {disk_percent:.1f}% used")

        return {
            'issues': issues,
            'metrics': {
                'disk_usage_percent': disk_percent,
                'disk_free_gb': disk.free / 1024 / 1024 / 1024
            }
        }

    async def _check_active_jobs(self) -> Dict[str, Any]:
        """Check active job count"""
        # This would check with the job manager
        active_jobs = 0  # Placeholder

        issues = []
        if active_jobs > settings.max_concurrent_jobs:
            issues.append(f"Too many active jobs: {active_jobs}")

        return {
            'issues': issues,
            'metrics': {
                'active_jobs': active_jobs,
                'max_concurrent_jobs': settings.max_concurrent_jobs
            }
        }

    async def _check_storage(self) -> Dict[str, Any]:
        """Check storage connectivity"""
        issues = []
        metrics = {}

        try:
            storage = get_storage()
            # Test storage connectivity
            if hasattr(storage, 'health_check'):
                await storage.health_check()
            metrics['storage_mode'] = settings.storage_mode
        except Exception as e:
            issues.append(f"Storage connectivity issue: {e}")

        return {'issues': issues, 'metrics': metrics}

    async def _check_translation_service(self) -> Dict[str, Any]:
        """Check translation service health"""
        issues = []
        metrics = {}

        # Check if translation service can initialize
        try:
            service = TranslationService()
            metrics['max_workers'] = service.executor._max_workers
        except Exception as e:
            issues.append(f"Translation service issue: {e}")

        return {'issues': issues, 'metrics': metrics}

# Enhanced health endpoint
@app.get("/health/detailed")
async def detailed_health_check():
    """Detailed health check for monitoring"""
    checker = HealthChecker()
    status = await checker.get_health_status()

    return {
        'healthy': status.healthy,
        'timestamp': datetime.utcnow(),
        'issues': status.issues,
        'metrics': status.metrics,
        'version': '1.0.0'
    }
```

---

## Implementation Conclusion

This comprehensive implementation guide provides a complete roadmap for converting the bilingual_book_maker CLI tool into a robust async API service. The design emphasizes:

1. **Immediate Value**: Local async processing that works out of the box
2. **Cloud-Ready Architecture**: Clean abstractions for seamless AWS migration
3. **Production Quality**: Comprehensive error handling, monitoring, and testing
4. **Scalable Foundation**: Supports growth from 10 to 1000+ translations/month

The implementation can be developed incrementally, with each phase building on the previous foundation while maintaining backward compatibility and enabling smooth migration to AWS Fargate when ready.

**Next Steps:**
1. Start with Phase 1 local async implementation (2 weeks)
2. Add Docker and production testing (1 week)
3. Prepare AWS abstractions and deployment pipeline (1 week)
4. Migrate to AWS Fargate when volume justifies it

This approach ensures that you have a working, valuable system quickly while building the foundation for future cloud scalability.