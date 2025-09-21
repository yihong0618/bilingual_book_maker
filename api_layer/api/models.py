"""
Enhanced data models for async translation API
"""
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, Dict, Any
from pydantic import BaseModel, Field


class JobStatus(str, Enum):
    """Translation job status enumeration"""
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TranslationModel(str, Enum):
    """Supported translation models"""
    CHATGPT = "chatgpt"
    CLAUDE = "claude"
    GEMINI = "gemini"
    DEEPL = "deepl"
    DEEPL_FREE = "deepl_free"
    GOOGLE = "google"
    GROQ = "groq"
    QWEN = "qwen"
    XAI = "xai"


@dataclass
class TranslationJob:
    """
    Translation job data class for tracking async translation state
    """
    job_id: str
    status: JobStatus
    filename: str
    created_at: datetime
    progress: int = 0  # 0-100%
    completed_at: Optional[datetime] = None
    error_message: Optional[str] = None
    output_path: Optional[str] = None
    total_paragraphs: int = 0
    processed_paragraphs: int = 0

    # Translation parameters
    model: Optional[str] = None
    source_language: str = "auto"
    target_language: str = "zh-cn"
    model_api_base: Optional[str] = None
    temperature: float = 1.0

    # Advanced options
    context_flag: bool = False
    context_paragraph_limit: int = 0
    single_translate: bool = False
    is_test: bool = False
    test_num: int = 5

    # Internal tracking
    retry_count: int = 0
    last_progress_update: datetime = field(default_factory=datetime.now)

    def update_progress(self, processed: int, total: Optional[int] = None) -> None:
        """Update job progress with processed paragraph count"""
        if total is not None:
            self.total_paragraphs = total

        self.processed_paragraphs = processed

        if self.total_paragraphs > 0:
            self.progress = min(100, int((processed / self.total_paragraphs) * 100))
        else:
            self.progress = 0

        self.last_progress_update = datetime.now()

    def mark_completed(self, output_path: str) -> None:
        """Mark job as completed with output file path"""
        self.status = JobStatus.COMPLETED
        self.progress = 100
        self.completed_at = datetime.now()
        self.output_path = output_path

    def mark_failed(self, error_message: str) -> None:
        """Mark job as failed with error message"""
        self.status = JobStatus.FAILED
        self.error_message = error_message
        self.completed_at = datetime.now()

    def mark_cancelled(self) -> None:
        """Mark job as cancelled"""
        self.status = JobStatus.CANCELLED
        self.completed_at = datetime.now()


# Pydantic models for API requests/responses
class TranslationRequest(BaseModel):
    """Request model for translation endpoint"""
    model: TranslationModel
    key: str
    language: str = Field(default="zh-cn", description="Target language code")
    model_api_base: Optional[str] = Field(default=None, description="Custom API base URL")
    resume: bool = Field(default=False, description="Resume from previous translation")
    is_test: bool = Field(default=False, description="Test mode with limited paragraphs")
    test_num: int = Field(default=5, description="Number of paragraphs for test mode")
    single_translate: bool = Field(default=False, description="Single translation mode")
    context_flag: bool = Field(default=False, description="Use context for translation")
    context_paragraph_limit: int = Field(default=0, description="Context paragraph limit")
    temperature: float = Field(default=1.0, ge=0.0, le=2.0, description="Translation temperature")
    source_lang: str = Field(default="auto", description="Source language detection")


class TranslationResponse(BaseModel):
    """Response model for translation request"""
    job_id: str
    status: JobStatus
    message: str
    estimated_duration: Optional[str] = None


class JobStatusResponse(BaseModel):
    """Response model for job status endpoint"""
    job_id: str
    status: JobStatus
    progress: int = Field(ge=0, le=100, description="Progress percentage (0-100)")
    filename: str
    created_at: datetime
    completed_at: Optional[datetime] = None
    error_message: Optional[str] = None
    download_url: Optional[str] = None

    # Progress details
    total_paragraphs: int = 0
    processed_paragraphs: int = 0

    # Translation parameters
    model: Optional[str] = None
    target_language: str = "zh-cn"

    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


class JobListResponse(BaseModel):
    """Response model for listing jobs"""
    jobs: list[JobStatusResponse]
    total_count: int
    active_count: int
    completed_count: int
    failed_count: int


class ErrorResponse(BaseModel):
    """Standard error response model"""
    error: str
    detail: Optional[str] = None
    job_id: Optional[str] = None
    timestamp: datetime = Field(default_factory=datetime.now)

    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


class HealthResponse(BaseModel):
    """Health check response model"""
    status: str
    timestamp: datetime = Field(default_factory=datetime.now)
    active_jobs: int = 0
    total_jobs: int = 0
    system_info: Dict[str, Any] = Field(default_factory=dict)

    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }