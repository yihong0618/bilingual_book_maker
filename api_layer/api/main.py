"""
FastAPI application with async translation endpoints
"""
import os
import logging
from pathlib import Path
from typing import List, Optional
from datetime import datetime, timedelta

from fastapi import FastAPI, File, UploadFile, HTTPException, BackgroundTasks, Depends, Form
from pydantic import Field
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from contextlib import asynccontextmanager
import uvicorn

from .models import (
    TranslationRequest, TranslationResponse, JobStatusResponse,
    JobListResponse, ErrorResponse, HealthResponse, JobStatus, TranslationModel
)
from .async_translator import async_translator
from .job_manager import job_manager
from .progress_monitor import global_progress_tracker


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler"""
    # Startup
    logger.info("Starting Bilingual Book Maker API")

    # Create necessary directories
    for directory in ["uploads", "outputs", "temp"]:
        Path(directory).mkdir(exist_ok=True)

    yield

    # Shutdown
    logger.info("Shutting down Bilingual Book Maker API")
    job_manager.shutdown(wait=False)


# Create FastAPI app
app = FastAPI(
    title="Bilingual Book Maker API",
    description="Async translation API for EPUB books with job tracking and progress monitoring",
    version="1.0.0",
    lifespan=lifespan
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure appropriately for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Add trusted host middleware
app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=["*"]  # Configure appropriately for production
)


@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    """Global exception handler"""
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content=ErrorResponse(
            error="Internal server error",
            detail=str(exc),
            timestamp=datetime.now()
        ).dict()
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
        "status": "running"
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
        system_info=system_stats
    )


@app.post("/translate", response_model=TranslationResponse)
async def start_translation(
    file: UploadFile = File(..., description="File to translate (EPUB, TXT, SRT, MD formats supported)"),
    model: TranslationModel = Form(default=TranslationModel.GOOGLE, description="Translation model to use"),
    key: str = Form(default="no-key-required", description="API key for the translation service (not required for Google Translate)"),
    language: str = Form(default="zh-cn", description="Target language code"),
    model_api_base: Optional[str] = Form(default=None, description="Custom API base URL (optional)"),
    resume: bool = Form(default=False, description="Resume from previous translation"),
    is_test: bool = Form(default=False, description="Test mode with limited paragraphs"),
    test_num: int = Form(default=5, description="Number of paragraphs for test mode"),
    single_translate: bool = Form(default=False, description="Single translation mode"),
    context_flag: bool = Form(default=False, description="Use context for translation"),
    context_paragraph_limit: int = Form(default=0, description="Context paragraph limit"),
    temperature: float = Form(default=1.0, description="Translation temperature (0.0-2.0)"),
    source_lang: str = Form(default="auto", description="Source language detection")
):
    """
    Start a new translation job

    **Models Available:**
    - chatgpt: OpenAI ChatGPT/GPT-4
    - claude: Anthropic Claude
    - gemini: Google Gemini
    - deepl: DeepL Translator
    - google: Google Translate
    - groq: Groq API
    - qwen: Alibaba Qwen
    - xai: xAI Grok

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
    - key: Your API key for the selected model
    - language: Target language code
    - is_test: Enable for testing (translates only 5 paragraphs)
    - temperature: Controls randomness (0.0=deterministic, 2.0=creative)

    Returns job_id immediately for async processing. Use /status/{job_id} to monitor progress.
    """
    # Validate file
    if not file.filename:
        raise HTTPException(status_code=400, detail="No file provided")

    # Check supported file formats
    supported_formats = ['.epub', '.txt', '.srt', '.md']
    file_ext = '.' + file.filename.lower().split('.')[-1] if '.' in file.filename else ''

    if file_ext not in supported_formats:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file format. Supported formats: {', '.join(supported_formats)}"
        )

    # Validate required parameters
    if not key:
        raise HTTPException(status_code=400, detail="API key is required")

    # Validate temperature
    if not 0.0 <= temperature <= 2.0:
        raise HTTPException(status_code=400, detail="Temperature must be between 0.0 and 2.0")

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
            resume_file_path = f"{unique_upload_path.parent}/.{unique_upload_path.stem}.temp.bin"
            if not os.path.exists(resume_file_path):
                raise HTTPException(
                    status_code=400,
                    detail=f"Resume requested but no resume file found. Start a new translation without resume option first."
                )

        # Start translation job
        job_id = async_translator.start_translation(
            file_path=str(unique_upload_path),
            model=model,
            key=key,
            language=language,
            model_api_base=model_api_base,
            resume=resume,
            is_test=is_test,
            test_num=test_num,
            single_translate=single_translate,
            context_flag=context_flag,
            context_paragraph_limit=context_paragraph_limit,
            temperature=temperature,
            source_lang=source_lang
        )

        # Estimate duration (rough)
        estimated_duration = "5-30 minutes depending on file size and model"
        if is_test:
            estimated_duration = "1-5 minutes (test mode)"

        return TranslationResponse(
            job_id=job_id,
            status=JobStatus.PENDING,
            message="Translation job started successfully",
            estimated_duration=estimated_duration
        )

    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error starting translation: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to start translation: {str(e)}")


@app.get("/status/{job_id}", response_model=JobStatusResponse)
async def get_job_status(job_id: str):
    """Get status and progress of a translation job"""
    job = async_translator.get_job_status(job_id)

    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

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
        target_language=job.target_language
    )


@app.get("/jobs", response_model=JobListResponse)
async def list_jobs(
    status: Optional[JobStatus] = None,
    limit: int = 50,
    offset: int = 0
):
    """List translation jobs with optional status filtering"""
    jobs = async_translator.list_jobs(status_filter=status)

    # Apply pagination
    total_count = len(jobs)
    jobs = jobs[offset:offset + limit]

    # Convert to response format
    job_responses = []
    for job in jobs:
        download_url = None
        if job.status == JobStatus.COMPLETED and job.output_path:
            download_url = f"/download/{job.job_id}"

        job_responses.append(JobStatusResponse(
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
            target_language=job.target_language
        ))

    # Get stats
    stats = job_manager.get_job_stats()

    return JobListResponse(
        jobs=job_responses,
        total_count=total_count,
        active_count=stats.get("active", 0),
        completed_count=stats.get("completed", 0),
        failed_count=stats.get("failed", 0)
    )


@app.post("/cancel/{job_id}")
async def cancel_job(job_id: str):
    """Cancel a running translation job"""
    success = async_translator.cancel_translation(job_id)

    if not success:
        job = async_translator.get_job_status(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        else:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot cancel job in {job.status} status"
            )

    return {"message": f"Job {job_id} cancelled successfully"}


@app.get("/download/{job_id}")
async def download_result(job_id: str):
    """Download the translated EPUB file"""
    file_path = async_translator.get_download_path(job_id)

    if not file_path:
        job = async_translator.get_job_status(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        elif job.status != JobStatus.COMPLETED:
            raise HTTPException(
                status_code=400,
                detail=f"Job is not completed. Current status: {job.status}"
            )
        else:
            raise HTTPException(status_code=404, detail="Translated file not found")

    # Get original filename and create download filename
    job = async_translator.get_job_status(job_id)
    if job:
        name, ext = os.path.splitext(job.filename)
        download_filename = f"{name}_bilingual{ext}"

        # Set appropriate media type based on file extension
        media_type_map = {
            '.epub': 'application/epub+zip',
            '.txt': 'text/plain',
            '.srt': 'application/x-subrip',
            '.md': 'text/markdown'
        }
        media_type = media_type_map.get(ext.lower(), 'application/octet-stream')
    else:
        download_filename = f"translated_{job_id}.epub"
        media_type = "application/epub+zip"

    return FileResponse(
        path=file_path,
        filename=download_filename,
        media_type=media_type
    )


@app.delete("/jobs/{job_id}")
async def delete_job(job_id: str):
    """Delete a job and its associated files"""
    job = async_translator.get_job_status(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # Can only delete completed, failed, or cancelled jobs
    if job.status in [JobStatus.PENDING, JobStatus.PROCESSING]:
        raise HTTPException(
            status_code=400,
            detail="Cannot delete active job. Cancel it first."
        )

    # Force cleanup (this would normally happen via TTL)
    success = job_manager._remove_job(job_id)

    if not success:
        raise HTTPException(status_code=500, detail="Failed to delete job")

    return {"message": f"Job {job_id} deleted successfully"}


@app.post("/cleanup")
async def manual_cleanup():
    """Manually trigger cleanup of expired jobs"""
    cleaned_count = job_manager.cleanup_expired_jobs()
    return {
        "message": f"Cleanup completed. Removed {cleaned_count} expired jobs.",
        "cleaned_count": cleaned_count
    }


@app.get("/models", response_model=List[dict])
async def list_models():
    """List available translation models"""
    models = []
    for model in TranslationModel:
        models.append({
            "name": model.value,
            "display_name": model.value.replace("_", " ").title(),
            "description": f"{model.value} translation service"
        })

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
        "uptime": "N/A"  # Could implement uptime tracking
    }


if __name__ == "__main__":
    # Development server
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info"
    )