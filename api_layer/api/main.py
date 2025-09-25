"""
FastAPI application with async translation endpoints
"""

import os
import logging
from pathlib import Path
from typing import List, Optional
from datetime import datetime, timedelta

from fastapi import (
    FastAPI,
    File,
    UploadFile,
    HTTPException,
    BackgroundTasks,
    Depends,
    Form,
)
from pydantic import Field
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from contextlib import asynccontextmanager
import uvicorn

from .models import (
    TranslationRequest,
    TranslationResponse,
    JobStatusResponse,
    JobListResponse,
    ErrorResponse,
    HealthResponse,
    JobStatus,
    TranslationModel,
)
from .async_translator import async_translator
from .job_manager import job_manager
from .progress_monitor import global_progress_tracker
from .config import settings, HttpStatusConstants, ValidationConstants, StorageConstants
from .auth import auth_optional, add_security_headers


# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler"""
    # Startup
    logger.info("Starting Bilingual Book Maker API")

    # Create necessary directories
    for directory in [settings.upload_dir, settings.output_dir, settings.temp_dir]:
        Path(directory).mkdir(exist_ok=True)

    # TODO: Future authentication features
    # - Add user registration system for API key management
    # - Implement credit/token system for paid translations
    # - Add rate limiting for premium models using your API keys
    # - Create user tiers: free (Google only) vs paid (premium models with credits)
    # - Add billing integration for credit purchases
    # - Implement basic rate limiting as foundation for future paid tiers

    yield

    # Shutdown
    logger.info("Shutting down Bilingual Book Maker API")
    job_manager.shutdown(wait=False)


# Utility functions
def sanitize_filename(filename: str) -> str:
    """
    Sanitize filename to prevent path traversal and other security issues

    Args:
        filename: Original filename from upload

    Returns:
        Sanitized filename safe for storage

    Raises:
        HTTPException: If filename cannot be sanitized safely
    """
    if not filename:
        raise HTTPException(
            status_code=HttpStatusConstants.BAD_REQUEST,
            detail="Filename cannot be empty"
        )

    # Extract extension first to preserve it
    if "." in filename:
        name_part, ext = filename.rsplit(".", 1)
        ext = "." + ext.lower()
    else:
        name_part = filename
        ext = ""

    # Check for path traversal patterns
    for pattern in ValidationConstants.PATH_TRAVERSAL_PATTERNS:
        if pattern in filename:
            raise HTTPException(
                status_code=HttpStatusConstants.BAD_REQUEST,
                detail=f"Filename contains invalid path pattern: {pattern}"
            )

    # Check for forbidden filenames (case-insensitive)
    name_upper = name_part.upper()
    full_name_upper = filename.upper()
    for forbidden in ValidationConstants.FORBIDDEN_FILENAMES:
        if name_upper == forbidden.upper() or full_name_upper == forbidden.upper():
            raise HTTPException(
                status_code=HttpStatusConstants.BAD_REQUEST,
                detail=f"Filename '{filename}' is reserved and cannot be used"
            )

    # Sanitize characters - replace invalid chars with replacement char
    sanitized_name = ""
    for char in name_part:
        if char in ValidationConstants.ALLOWED_FILENAME_CHARS:
            sanitized_name += char
        else:
            sanitized_name += ValidationConstants.REPLACEMENT_CHAR

    # Remove multiple consecutive replacement chars
    while ValidationConstants.REPLACEMENT_CHAR * 2 in sanitized_name:
        sanitized_name = sanitized_name.replace(
            ValidationConstants.REPLACEMENT_CHAR * 2,
            ValidationConstants.REPLACEMENT_CHAR
        )

    # Remove leading/trailing replacement chars
    sanitized_name = sanitized_name.strip(ValidationConstants.REPLACEMENT_CHAR)

    # Ensure we still have a valid name
    if not sanitized_name:
        sanitized_name = "file"

    # Reconstruct filename with extension
    sanitized_filename = sanitized_name + ext

    # Final length check
    if len(sanitized_filename) > ValidationConstants.MAX_FILENAME_LENGTH:
        # Truncate name part while preserving extension
        max_name_length = ValidationConstants.MAX_FILENAME_LENGTH - len(ext)
        sanitized_name = sanitized_name[:max_name_length]
        sanitized_filename = sanitized_name + ext

    return sanitized_filename


# Dependencies
async def validate_file_comprehensive(
    file: UploadFile = File(
        ...,
        description=f"File to translate (EPUB, TXT, SRT, MD formats supported). Maximum size: {ValidationConstants.MAX_FILE_SIZE_MB}MB"
    )
) -> UploadFile:
    """
    Comprehensive file validation including size, format, content, and security checks

    Args:
        file: Uploaded file to validate

    Returns:
        The file if validation passes

    Raises:
        HTTPException: If validation fails (size, format, content, security)
    """
    # 1. Basic file checks
    if not file.filename:
        raise HTTPException(
            status_code=HttpStatusConstants.BAD_REQUEST,
            detail="Filename is required"
        )

    # 2. Filename sanitization (includes path traversal prevention)
    sanitized_filename = sanitize_filename(file.filename)
    # Update the file object with sanitized name for downstream processing
    file.filename = sanitized_filename

    # 3. File extension validation
    file_ext = "." + file.filename.lower().split(".")[-1] if "." in file.filename else ""
    if file_ext not in ValidationConstants.SUPPORTED_FILE_EXTENSIONS:
        supported_formats = ", ".join(ValidationConstants.SUPPORTED_FILE_EXTENSIONS)
        raise HTTPException(
            status_code=HttpStatusConstants.BAD_REQUEST,
            detail=f"Unsupported file format '{file_ext}'. Supported formats: {supported_formats}"
        )

    # 4. MIME type validation
    if file.content_type:
        allowed_mime_types = ValidationConstants.ALLOWED_MIME_TYPES.get(file_ext, [])
        # Remove charset and other parameters for comparison
        content_type_base = file.content_type.split(';')[0].strip()
        if allowed_mime_types and content_type_base not in allowed_mime_types:
            raise HTTPException(
                status_code=HttpStatusConstants.BAD_REQUEST,
                detail=f"Invalid content type '{file.content_type}' for {file_ext} file"
            )

    # 5. File size validation
    if file.size is None:
        # If size is not available, read content to get size
        content = await file.read()
        file_size = len(content)
        # Reset file pointer
        await file.seek(ValidationConstants.INITIAL_VALUE)
    else:
        file_size = file.size
        # Read content for further validation
        content = await file.read()
        await file.seek(ValidationConstants.INITIAL_VALUE)

    if file_size > ValidationConstants.MAX_FILE_SIZE_BYTES:
        file_size_mb = file_size / ValidationConstants.BYTES_PER_MB
        max_size_mb = ValidationConstants.MAX_FILE_SIZE_MB
        raise HTTPException(
            status_code=HttpStatusConstants.PAYLOAD_TOO_LARGE,
            detail=f"File too large: {file_size_mb:.1f}MB exceeds {max_size_mb}MB limit"
        )

    # 6. File magic bytes validation (for formats that have them)
    magic_bytes = ValidationConstants.FILE_MAGIC_BYTES.get(file_ext, [])
    if magic_bytes:
        file_header = content[:10]  # First 10 bytes should be enough for magic byte detection
        if not any(file_header.startswith(magic) for magic in magic_bytes):
            raise HTTPException(
                status_code=HttpStatusConstants.BAD_REQUEST,
                detail=f"File content doesn't match {file_ext} format (invalid file header)"
            )

    # 7. Content security scanning
    scan_bytes = content[:ValidationConstants.MAX_CONTENT_SCAN_BYTES]
    for pattern in ValidationConstants.SUSPICIOUS_CONTENT_PATTERNS:
        if pattern in scan_bytes:
            raise HTTPException(
                status_code=HttpStatusConstants.BAD_REQUEST,
                detail="File contains potentially malicious content and cannot be processed"
            )

    # 8. Final filename length validation (after sanitization)
    if len(file.filename) > ValidationConstants.MAX_FILENAME_LENGTH:
        raise HTTPException(
            status_code=HttpStatusConstants.BAD_REQUEST,
            detail=f"Filename too long: {len(file.filename)} chars exceeds {ValidationConstants.MAX_FILENAME_LENGTH} limit"
        )

    return file


# Create FastAPI app with conditional docs based on environment
app_kwargs = {
    "title": "Bilingual Book Maker API",
    "description": """
    Async translation API for EPUB, TXT, SRT, and Markdown files.

    **MVP: All models available to everyone!**
    - Google Translate: Free, no API key required
    - Premium models (ChatGPT, Claude, Gemini, etc.): Use your own API keys
    - All file formats supported
    - No cost to the service provider since you use your own API keys

    **How it works:**
    1. Choose any translation model
    2. Provide your API key (except for Google Translate)
    3. Upload your file and start translation
    4. Monitor progress and download results

    **Authentication:** Optional - only affects frontend UI visibility
    API access is unrestricted in MVP phase.
    """,
    "version": "1.0.0",
    "lifespan": lifespan,
}

# Disable docs in production for security
if settings.is_production:
    app_kwargs.update({
        "docs_url": None,
        "redoc_url": None,
        "openapi_url": None,
    })
    logger.info("🔒 Production mode: API documentation disabled")
else:
    logger.info("🔓 Development mode: API documentation enabled at /docs and /redoc")

app = FastAPI(**app_kwargs)

# Add security headers middleware
app.middleware("http")(add_security_headers)

# Configure security settings using configuration system
logger.info(f"Environment: {settings.environment}")
logger.info(f"CORS Origins: {settings.get_cors_origins()}")
logger.info(f"CORS Methods: {settings.get_cors_methods()}")
logger.info(f"Trusted Hosts: {settings.get_trusted_hosts()}")
logger.info(f"API Host: {settings.api_host}:{settings.api_port}")
logger.info(f"Max Workers: {settings.max_workers}")
logger.info(f"Job TTL: {settings.job_ttl_hours}h")

# Add CORS middleware with configuration-based settings
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.get_cors_origins(),
    allow_credentials=True,
    allow_methods=settings.get_cors_methods(),
    allow_headers=settings.get_cors_headers(),
)

# Add trusted host middleware with configuration-based settings
app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.get_trusted_hosts())


@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    """Global exception handler"""
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    return JSONResponse(
        status_code=HttpStatusConstants.INTERNAL_SERVER_ERROR,
        content=ErrorResponse(
            error="Internal server error", detail=str(exc), timestamp=datetime.now()
        ).dict(),
    )


@app.get("/", response_model=dict)
async def root():
    """Root endpoint with API information"""
    return {
        "name": "Bilingual Book Maker API",
        "version": "1.0.0",
        "description": "Async translation API for EPUB books",
        "docs_url": "/docs",
        "health_url": "/health",
        "status": "running",
    }


@app.get("/health", response_model=HealthResponse)
async def health_check():
    """Health check endpoint"""
    job_stats = job_manager.get_job_stats()
    system_stats = async_translator.get_system_stats()

    return HealthResponse(
        status="healthy",
        timestamp=datetime.now(),
        active_jobs=job_stats.get("active", 0),
        total_jobs=job_stats.get("total", 0),
        system_info=system_stats,
    )


@app.post("/translate", response_model=TranslationResponse, tags=["Translation"])
async def start_translation(
    file: UploadFile = Depends(validate_file_comprehensive),
    current_api_key: Optional[str] = Depends(auth_optional),
    model: TranslationModel = Form(
        default=TranslationModel.GOOGLE, description="Translation model to use"
    ),
    key: str = Form(
        default="no-key-required",
        description="API key for the translation service (not required for Google Translate, required for DeepL Free)",
    ),
    language: str = Form(default="zh-cn", description="Target language code"),
    model_api_base: Optional[str] = Form(
        default=None, description="Custom API base URL (optional)"
    ),
    resume: bool = Form(default=False, description="Resume from previous translation"),
    is_test: bool = Form(
        default=False, description="Test mode with limited paragraphs"
    ),
    test_num: int = Form(default=5, description="Number of paragraphs for test mode"),
    single_translate: bool = Form(default=False, description="Single translation mode"),
    context_flag: bool = Form(default=False, description="Use context for translation"),
    context_paragraph_limit: int = Form(
        default=0, description="Context paragraph limit"
    ),
    temperature: float = Form(
        default=1.0, description="Translation temperature (0.0-2.0)"
    ),
    source_lang: str = Form(default="auto", description="Source language detection"),
):
    """
    Start a new translation job

    **MVP: All models are available to everyone!**
    Since users provide their own API keys, all models are accessible through the API.
    Authentication only affects frontend UI visibility.

    **Models Available:**
    - chatgpt: OpenAI ChatGPT/GPT-4 (requires your API key)
    - claude: Anthropic Claude (requires your API key)
    - gemini: Google Gemini Flash (requires your API key)
    - deepl: DeepL Translator Pro (requires your API key)
    - deepl_free: DeepL Free API (requires your free API key from https://www.deepl.com/pro-api)
    - google: Google Translate (free, no API key required)
    - groq: Groq API (requires your API key)
    - qwen: Alibaba Qwen (requires your API key)
    - xai: xAI Grok (requires your API key)

    **Common Language Codes:**
    - zh-cn: Chinese (Simplified)
    - zh-tw: Chinese (Traditional)
    - en: English
    - es: Spanish
    - fr: French
    - de: German
    - ja: Japanese
    - ko: Korean
    - ru: Russian

    **Parameters:**
    - file: File to translate (supports EPUB, TXT, SRT, MD formats)
    - model: Choose translation model
    - key: Your API key for the selected model (not required for Google Translate)
    - language: Target language code
    - is_test: Enable for testing (translates only 5 paragraphs)
    - temperature: Controls randomness (0.0=deterministic, 2.0=creative)

    **API Key Requirements:**
    - Most models require an API key from their respective providers
    - DeepL Free: Get free API key from https://www.deepl.com/pro-api
    - Google Translate: No API key required (use "no-key-required" or leave default)

    Returns job_id immediately for async processing. Use /status/{job_id} to monitor progress.
    """
    # Note: File validation is now handled by validate_file_comprehensive dependency

    # MVP: All models are accessible to everyone since users provide their own API keys
    # Authentication only controls UI visibility, not API access

    # Validate required parameters for translation service API keys
    if model != TranslationModel.GOOGLE and not key:
        raise HTTPException(
            status_code=HttpStatusConstants.BAD_REQUEST,
            detail=f"Translation service API key is required for {model.value}"
        )

    # Validate temperature using constants
    if not ValidationConstants.MIN_TEMPERATURE <= temperature <= ValidationConstants.MAX_TEMPERATURE:
        raise HTTPException(
            status_code=HttpStatusConstants.BAD_REQUEST,
            detail=f"Temperature must be between {ValidationConstants.MIN_TEMPERATURE} and {ValidationConstants.MAX_TEMPERATURE}",
        )

    try:
        # Create unique upload path to avoid conflicts
        unique_upload_path = job_manager.get_upload_path(file.filename)

        # Save uploaded file to unique path
        with open(unique_upload_path, "wb") as buffer:
            content = await file.read()
            buffer.write(content)

        # Validate resume functionality
        if resume:
            # Check if resume file exists for this EPUB
            resume_file_path = (
                f"{unique_upload_path.parent}/.{unique_upload_path.stem}.temp.bin"
            )
            if not os.path.exists(resume_file_path):
                raise HTTPException(
                    status_code=HttpStatusConstants.BAD_REQUEST,
                    detail=f"Resume requested but no resume file found. Start a new translation without resume option first.",
                )

        # Start translation job
        file_size = file.size if file.size is not None else ValidationConstants.INITIAL_VALUE
        job_id = async_translator.start_translation(
            file_path=str(unique_upload_path),
            model=model,
            key=key,
            language=language,
            file_size_bytes=file_size,
            model_api_base=model_api_base,
            resume=resume,
            is_test=is_test,
            test_num=test_num,
            single_translate=single_translate,
            context_flag=context_flag,
            context_paragraph_limit=context_paragraph_limit,
            temperature=temperature,
            source_lang=source_lang,
        )

        # Estimate duration (rough)
        estimated_duration = "5-30 minutes depending on file size and model"
        if is_test:
            estimated_duration = "1-5 minutes (test mode)"

        return TranslationResponse(
            job_id=job_id,
            status=JobStatus.PENDING,
            message="Translation job started successfully",
            estimated_duration=estimated_duration,
        )

    except FileNotFoundError as e:
        raise HTTPException(status_code=HttpStatusConstants.NOT_FOUND, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=HttpStatusConstants.BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.error(f"Error starting translation: {e}", exc_info=True)
        raise HTTPException(
            status_code=HttpStatusConstants.INTERNAL_SERVER_ERROR,
            detail=f"Failed to start translation: {str(e)}",
        )


@app.get("/status/{job_id}", response_model=JobStatusResponse)
async def get_job_status(job_id: str):
    """Get status and progress of a translation job"""
    job = async_translator.get_job_status(job_id)

    if not job:
        raise HTTPException(
            status_code=HttpStatusConstants.NOT_FOUND, detail="Job not found"
        )

    # Build download URL if job is completed
    download_url = None
    if job.status == JobStatus.COMPLETED and job.output_path:
        download_url = f"/download/{job_id}"

    return JobStatusResponse(
        job_id=job.job_id,
        status=job.status,
        progress=job.progress,
        filename=job.filename,
        created_at=job.created_at,
        completed_at=job.completed_at,
        error_message=job.error_message,
        download_url=download_url,
        total_paragraphs=job.total_paragraphs,
        processed_paragraphs=job.processed_paragraphs,
        model=job.model,
        target_language=job.target_language,
    )


@app.get("/jobs", response_model=JobListResponse)
async def list_jobs(
    status: Optional[JobStatus] = None, limit: int = 50, offset: int = 0
):
    """List translation jobs with optional status filtering"""
    jobs = async_translator.list_jobs(status_filter=status)

    # Apply pagination
    total_count = len(jobs)
    jobs = jobs[offset : offset + limit]

    # Convert to response format
    job_responses = []
    for job in jobs:
        download_url = None
        if job.status == JobStatus.COMPLETED and job.output_path:
            download_url = f"/download/{job.job_id}"

        job_responses.append(
            JobStatusResponse(
                job_id=job.job_id,
                status=job.status,
                progress=job.progress,
                filename=job.filename,
                created_at=job.created_at,
                completed_at=job.completed_at,
                error_message=job.error_message,
                download_url=download_url,
                total_paragraphs=job.total_paragraphs,
                processed_paragraphs=job.processed_paragraphs,
                model=job.model,
                target_language=job.target_language,
            )
        )

    # Get stats
    stats = job_manager.get_job_stats()

    return JobListResponse(
        jobs=job_responses,
        total_count=total_count,
        active_count=stats.get("active", 0),
        completed_count=stats.get("completed", 0),
        failed_count=stats.get("failed", 0),
    )


@app.post("/cancel/{job_id}")
async def cancel_job(job_id: str):
    """Cancel a running translation job"""
    success = async_translator.cancel_translation(job_id)

    if not success:
        job = async_translator.get_job_status(job_id)
        if not job:
            raise HTTPException(
                status_code=HttpStatusConstants.NOT_FOUND, detail="Job not found"
            )
        else:
            raise HTTPException(
                status_code=HttpStatusConstants.BAD_REQUEST,
                detail=f"Cannot cancel job in {job.status} status",
            )

    return {"message": f"Job {job_id} cancelled successfully"}


@app.get("/download/{job_id}")
async def download_result(job_id: str):
    """Download the translated EPUB file"""
    file_path = async_translator.get_download_path(job_id)

    if not file_path:
        job = async_translator.get_job_status(job_id)
        if not job:
            raise HTTPException(
                status_code=HttpStatusConstants.NOT_FOUND, detail="Job not found"
            )
        elif job.status != JobStatus.COMPLETED:
            raise HTTPException(
                status_code=HttpStatusConstants.BAD_REQUEST,
                detail=f"Job is not completed. Current status: {job.status}",
            )
        else:
            raise HTTPException(
                status_code=HttpStatusConstants.NOT_FOUND,
                detail="Translated file not found",
            )

    # Get original filename and create download filename
    job = async_translator.get_job_status(job_id)
    if job:
        name, ext = os.path.splitext(job.filename)
        download_filename = f"{name}{StorageConstants.BILINGUAL_SUFFIX}{ext}"

        # Set appropriate media type based on file extension
        media_type_map = {
            StorageConstants.EPUB_EXT: StorageConstants.EPUB_MIME,
            StorageConstants.TXT_EXT: StorageConstants.TXT_MIME,
            StorageConstants.SRT_EXT: StorageConstants.SRT_MIME,
            StorageConstants.MD_EXT: StorageConstants.MD_MIME,
        }
        media_type = media_type_map.get(ext.lower(), StorageConstants.OCTET_STREAM_MIME)
    else:
        download_filename = f"translated_{job_id}.epub"
        media_type = StorageConstants.EPUB_MIME

    return FileResponse(
        path=file_path, filename=download_filename, media_type=media_type
    )


@app.delete("/jobs/{job_id}")
async def delete_job(job_id: str):
    """Delete a job and its associated files"""
    job = async_translator.get_job_status(job_id)
    if not job:
        raise HTTPException(
            status_code=HttpStatusConstants.NOT_FOUND, detail="Job not found"
        )

    # Can only delete completed, failed, or cancelled jobs
    if job.status in [JobStatus.PENDING, JobStatus.PROCESSING]:
        raise HTTPException(
            status_code=HttpStatusConstants.BAD_REQUEST,
            detail="Cannot delete active job. Cancel it first.",
        )

    # Force cleanup (this would normally happen via TTL)
    success = job_manager._remove_job(job_id)

    if not success:
        raise HTTPException(
            status_code=HttpStatusConstants.INTERNAL_SERVER_ERROR,
            detail="Failed to delete job",
        )

    return {"message": f"Job {job_id} deleted successfully"}


@app.post("/cleanup")
async def manual_cleanup():
    """Manually trigger cleanup of expired jobs"""
    cleaned_count = job_manager.cleanup_expired_jobs()
    return {
        "message": f"Cleanup completed. Removed {cleaned_count} expired jobs.",
        "cleaned_count": cleaned_count,
    }


@app.get("/models", response_model=List[dict])
async def list_models():
    """List available translation models"""
    models = []
    for model in TranslationModel:
        models.append(
            {
                "name": model.value,
                "display_name": model.value.replace("_", " ").title(),
                "description": f"{model.value} translation service",
            }
        )

    return models


@app.get("/stats")
async def get_system_stats():
    """Get detailed system statistics"""
    job_stats = job_manager.get_job_stats()
    system_stats = async_translator.get_system_stats()

    return {
        "timestamp": datetime.now().isoformat(),
        "jobs": job_stats,
        "system": system_stats,
        "uptime": "N/A",  # Could implement uptime tracking
    }


if __name__ == "__main__":
    # Development server with configurable settings
    uvicorn.run(
        "main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=settings.reload,
        log_level=settings.log_level.lower(),
    )
