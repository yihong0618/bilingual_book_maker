from typing import Optional, Dict, Any
from pydantic import BaseModel, Field, validator
from datetime import datetime
from enum import Enum
import uuid


class TranslationStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class TranslationModel(str, Enum):
    CHATGPT = "chatgptapi"
    GPT4 = "gpt4"
    GPT4OMINI = "gpt4omini"
    GPT4O = "gpt4o"
    CLAUDE = "claude"
    CLAUDE_3_OPUS = "claude-3-opus"
    CLAUDE_3_SONNET = "claude-3-sonnet"
    CLAUDE_3_HAIKU = "claude-3-haiku"
    GEMINI = "gemini"
    GEMINIPRO = "geminipro"
    DEEPL = "deepl"
    GOOGLE = "google"
    CAIYUN = "caiyun"
    GROQ = "groq"
    XAI = "xai"
    QWEN = "qwen"
    CUSTOM = "customapi"


class TranslationRequest(BaseModel):
    target_language: str = Field(..., description="Target language code (e.g., 'zh', 'en', 'ja')")
    model: TranslationModel = Field(TranslationModel.CHATGPT, description="Translation model to use")
    api_keys: Optional[Dict[str, str]] = Field(None, description="API keys for translation services")
    single_translate: bool = Field(False, description="Output translated book only (no bilingual)")
    temperature: float = Field(1.0, ge=0.0, le=2.0, description="Temperature for LLM models")
    test_mode: bool = Field(False, description="Only translate first 10 paragraphs for testing")
    test_num: int = Field(10, description="Number of paragraphs to translate in test mode")
    prompt_config: Optional[Dict[str, str]] = Field(None, description="Custom prompt configuration")
    use_context: bool = Field(False, description="Use context for better narrative consistency")
    context_paragraph_limit: int = Field(0, description="Context paragraph limit if using context")
    accumulated_num: int = Field(1, description="Number of tokens to accumulate before translation")
    translation_style: Optional[str] = Field(None, description="CSS style for translation display")

    @validator('target_language')
    def validate_language(cls, v):
        supported_languages = [
            'zh', 'zh-hans', 'zh-hant', 'en', 'ja', 'ko', 'es', 'fr', 'de', 'tr',
            'ru', 'pt', 'pt-br', 'ar', 'hi', 'it', 'pl', 'nl', 'sv', 'vi', 'id'
        ]
        if v.lower() not in supported_languages:
            raise ValueError(f"Unsupported language: {v}. Supported: {', '.join(supported_languages)}")
        return v.lower()


class TranslationJob(BaseModel):
    job_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    filename: str
    target_language: str
    model: TranslationModel
    status: TranslationStatus = TranslationStatus.PENDING
    created_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = None
    duration_seconds: Optional[int] = None
    error_message: Optional[str] = None
    storage_path: Optional[str] = None
    download_url: Optional[str] = None
    progress: int = Field(0, ge=0, le=100)


class TranslationResponse(BaseModel):
    job_id: str
    status: TranslationStatus
    message: str
    download_url: Optional[str] = None
    filename: Optional[str] = None
    duration_seconds: Optional[int] = None
    model_used: Optional[str] = None


class JobStatusResponse(BaseModel):
    job_id: str
    status: TranslationStatus
    progress: int
    filename: str
    created_at: datetime
    completed_at: Optional[datetime] = None
    duration_seconds: Optional[int] = None
    error_message: Optional[str] = None
    download_url: Optional[str] = None


class HealthCheckResponse(BaseModel):
    status: str = "healthy"
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    storage_mode: str
    version: str = "1.0.0"


class ErrorResponse(BaseModel):
    error: str
    detail: Optional[str] = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)