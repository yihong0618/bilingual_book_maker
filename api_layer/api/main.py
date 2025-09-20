import os
import logging
import tempfile
import shutil
from typing import Optional
from datetime import datetime

from fastapi import FastAPI, File, UploadFile, Form, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
import uvicorn

from .config import settings
from .models import TranslationModel, HealthCheckResponse, ErrorResponse
from .translator import TranslationService
from .storage import StorageFactory

# Configure logging
logging.basicConfig(level=settings.log_level)
logger = logging.getLogger(__name__)

# Create FastAPI app
app = FastAPI(
    title="EPUB Translator API",
    description="API service for translating EPUB files using various translation models",
    version="1.0.0"
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize services
translator = TranslationService()
storage = StorageFactory.get_storage()


@app.get("/health", response_model=HealthCheckResponse)
async def health_check():
    """Check the health status of the API."""
    return HealthCheckResponse(
        status="healthy",
        timestamp=datetime.now(),
        storage_mode=settings.storage_mode.value,
        version="1.0.0"
    )


@app.post("/translate")
async def translate_epub(
    file: UploadFile = File(..., description="EPUB file to translate"),
    target_language: str = Form(..., description="Target language code (e.g., 'zh', 'en', 'ja')"),
    model: TranslationModel = Form(TranslationModel.CHATGPT, description="Translation model to use"),
    openai_key: Optional[str] = Form(None, description="OpenAI API key"),
    claude_key: Optional[str] = Form(None, description="Claude API key"),
    gemini_key: Optional[str] = Form(None, description="Gemini API key"),
    deepl_key: Optional[str] = Form(None, description="DeepL API key"),
    single_translate: bool = Form(False, description="Output translated only (no bilingual)"),
    temperature: float = Form(1.0, description="Temperature for LLM models"),
    test_mode: bool = Form(False, description="Test mode - translate only first 10 paragraphs")
):
    """
    Translate an EPUB file synchronously.

    This endpoint accepts an EPUB file and translation parameters,
    processes the translation, and returns the translated file directly.
    """

    # Validate file type
    if not file.filename.endswith('.epub'):
        raise HTTPException(status_code=400, detail="Only EPUB files are supported")

    # Check file size
    file_content = await file.read()
    file_size_mb = len(file_content) / (1024 * 1024)

    if file_size_mb > settings.max_file_size_mb:
        raise HTTPException(
            status_code=413,
            detail=f"File size ({file_size_mb:.1f}MB) exceeds maximum allowed size ({settings.max_file_size_mb}MB)"
        )

    # Prepare API keys
    api_keys = {}
    if openai_key:
        api_keys['openai_key'] = openai_key
    if claude_key:
        api_keys['claude_key'] = claude_key
    if gemini_key:
        api_keys['gemini_key'] = gemini_key
    if deepl_key:
        api_keys['deepl_key'] = deepl_key

    temp_dir = None
    try:
        # Create temporary directory for processing
        temp_dir = tempfile.mkdtemp(prefix="epub_trans_")

        # Save uploaded file
        input_path = os.path.join(temp_dir, file.filename)
        with open(input_path, 'wb') as f:
            f.write(file_content)

        logger.info(f"Processing translation for {file.filename}")

        # Translate the file
        output_path = await translator.translate_epub(
            input_path=input_path,
            target_language=target_language,
            model=model,
            api_keys=api_keys,
            single_translate=single_translate,
            temperature=temperature,
            test_mode=test_mode
        )

        # Generate output filename
        base_name = os.path.splitext(file.filename)[0]
        if single_translate:
            output_filename = f"{base_name}_{target_language}.epub"
        else:
            output_filename = f"{base_name}_bilingual.epub"

        # For production (S3), upload and return presigned URL
        if settings.storage_mode.value == "s3":
            import uuid
            job_id = str(uuid.uuid4())

            # Save to S3
            await storage.save_result(job_id, output_path, output_filename)
            download_url = await storage.get_download_url(job_id, output_filename)

            return JSONResponse(content={
                "status": "success",
                "filename": output_filename,
                "download_url": download_url,
                "message": "Translation completed successfully"
            })

        # For local development, return file directly
        else:
            return FileResponse(
                path=output_path,
                filename=output_filename,
                media_type='application/epub+zip'
            )

    except Exception as e:
        logger.error(f"Translation failed: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

    finally:
        # Cleanup temp directory
        if temp_dir and os.path.exists(temp_dir):
            try:
                shutil.rmtree(temp_dir)
            except Exception as e:
                logger.error(f"Failed to cleanup temp directory: {e}")


@app.exception_handler(Exception)
async def general_exception_handler(request, exc):
    """Handle unexpected exceptions."""
    logger.error(f"Unexpected error: {str(exc)}")
    return JSONResponse(
        status_code=500,
        content={
            "error": "Internal server error",
            "detail": str(exc) if settings.log_level == "DEBUG" else "An unexpected error occurred"
        }
    )


if __name__ == "__main__":
    uvicorn.run(
        "api.main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=True
    )