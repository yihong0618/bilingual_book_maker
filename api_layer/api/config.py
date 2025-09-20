import os
from typing import Optional
from pydantic_settings import BaseSettings
from enum import Enum


class StorageMode(str, Enum):
    LOCAL = "local"
    S3 = "s3"


class Settings(BaseSettings):
    # Storage Configuration
    storage_mode: StorageMode = StorageMode.LOCAL
    local_storage_path: str = "/tmp/epub-translations"
    s3_bucket: Optional[str] = None
    aws_region: str = "us-east-1"

    # API Configuration
    max_file_size_mb: int = 50
    translation_timeout: int = 600
    log_level: str = "INFO"
    api_host: str = "0.0.0.0"
    api_port: int = 8000

    # Translation Configuration
    translator_image: str = "epub-translator:latest"
    docker_socket: str = "/var/run/docker.sock"

    # Database Configuration (for future phases)
    database_url: str = "sqlite:///./epub_jobs.db"

    # Security
    cors_origins: list = ["*"]
    api_key_header: str = "X-API-Key"
    require_api_key: bool = False
    api_keys: list = []  # List of valid API keys if enabled

    class Config:
        env_file = ".env"
        case_sensitive = False


settings = Settings()