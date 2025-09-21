"""
Configuration settings for the Bilingual Book Maker API
Environment-based configuration with security defaults
"""
import os
from typing import List, Optional
from pydantic import Field
from pydantic_settings import BaseSettings
from .constants import (
    NetworkConstants, EnvironmentConstants, SecurityConstants,
    DefaultValues, DomainConstants, TimeConstants
)


class SecurityConfig:
    """Security configuration constants for different environments"""

    # Development environment settings
    DEV_CORS_ORIGINS = [
        DomainConstants.get_http_url(NetworkConstants.LOCALHOST, NetworkConstants.API_PORT),
        DomainConstants.get_http_url(NetworkConstants.LOCALHOST_IP, NetworkConstants.API_PORT)
    ]

    DEV_TRUSTED_HOSTS = [
        DomainConstants.get_localhost_with_port(NetworkConstants.API_PORT),
        DomainConstants.get_localhost_ip_with_port(NetworkConstants.API_PORT),
        DomainConstants.get_all_interfaces_with_port(NetworkConstants.API_PORT)
    ]

    # Staging environment settings
    STAGING_CORS_ORIGINS = [
        DomainConstants.get_https_url(DomainConstants.STAGING_DOMAIN),
        DomainConstants.get_http_url(NetworkConstants.LOCALHOST, NetworkConstants.FRONTEND_ALT_PORT)
    ]

    STAGING_TRUSTED_HOSTS = [
        DomainConstants.STAGING_DOMAIN,
        DomainConstants.get_localhost_with_port(NetworkConstants.API_PORT),
        DomainConstants.get_localhost_ip_with_port(NetworkConstants.API_PORT)
    ]

    # Production environment settings (customize these for your domains)
    PRODUCTION_CORS_ORIGINS = [
        DomainConstants.get_https_url(DomainConstants.YOUR_FRONTEND_DOMAIN),
        DomainConstants.get_https_url(DomainConstants.YOUR_FRONTEND_WWW),
        DomainConstants.get_https_url(DomainConstants.YOUR_FRONTEND_APP)
    ]

    PRODUCTION_TRUSTED_HOSTS = [
        DomainConstants.YOUR_FRONTEND_DOMAIN,
        DomainConstants.YOUR_FRONTEND_WWW,
        DomainConstants.YOUR_API_DOMAIN
    ]

    # CORS methods by environment
    PRODUCTION_CORS_METHODS = [
        SecurityConstants.GET_METHOD,
        SecurityConstants.POST_METHOD,
        SecurityConstants.DELETE_METHOD,
        SecurityConstants.OPTIONS_METHOD
    ]
    DEV_STAGING_CORS_METHODS = [SecurityConstants.ALL_METHODS]

    # Standard CORS headers
    CORS_HEADERS = [
        SecurityConstants.CONTENT_TYPE_HEADER,
        SecurityConstants.AUTHORIZATION_HEADER,
        SecurityConstants.ACCEPT_HEADER,
        SecurityConstants.ORIGIN_HEADER,
        SecurityConstants.X_REQUESTED_WITH_HEADER
    ]


class Settings(BaseSettings):
    """Main application settings with environment-based defaults"""

    # Environment
    environment: str = Field(default=EnvironmentConstants.DEVELOPMENT, env="ENVIRONMENT")
    debug: bool = Field(default=DefaultValues.DEFAULT_DEBUG, env="DEBUG")

    # API Server settings
    api_host: str = Field(default=DefaultValues.DEFAULT_API_HOST, env="API_HOST")
    api_port: int = Field(default=DefaultValues.DEFAULT_API_PORT, env="API_PORT")
    reload: bool = Field(default=DefaultValues.DEFAULT_RELOAD, env="API_RELOAD")

    # Job Manager settings
    max_workers: int = Field(default=DefaultValues.DEFAULT_MAX_WORKERS, env="MAX_WORKERS")
    job_ttl_hours: int = Field(default=DefaultValues.DEFAULT_JOB_TTL_HOURS, env="JOB_TTL_HOURS")
    cleanup_interval_minutes: int = Field(default=DefaultValues.DEFAULT_CLEANUP_INTERVAL_MINUTES, env="CLEANUP_INTERVAL_MINUTES")

    # Storage paths
    upload_dir: str = Field(default=DefaultValues.DEFAULT_UPLOAD_DIR, env="UPLOAD_DIR")
    output_dir: str = Field(default=DefaultValues.DEFAULT_OUTPUT_DIR, env="OUTPUT_DIR")
    temp_dir: str = Field(default=DefaultValues.DEFAULT_TEMP_DIR, env="TEMP_DIR")

    # Security overrides (optional environment variables)
    custom_cors_origins: Optional[str] = Field(default=None, env="CORS_ORIGINS")
    custom_trusted_hosts: Optional[str] = Field(default=None, env="TRUSTED_HOSTS")

    # Logging
    log_level: str = Field(default=DefaultValues.DEFAULT_LOG_LEVEL, env="LOG_LEVEL")

    class Config:
        env_file = ".env"
        case_sensitive = False

    @property
    def is_development(self) -> bool:
        """Check if running in development mode"""
        return self.environment.lower() == EnvironmentConstants.DEVELOPMENT

    @property
    def is_staging(self) -> bool:
        """Check if running in staging mode"""
        return self.environment.lower() == EnvironmentConstants.STAGING

    @property
    def is_production(self) -> bool:
        """Check if running in production mode"""
        return self.environment.lower() == EnvironmentConstants.PRODUCTION

    def get_cors_origins(self) -> List[str]:
        """Get CORS origins based on environment"""
        # Allow override via environment variable
        if self.custom_cors_origins:
            return [origin.strip() for origin in self.custom_cors_origins.split(",")]

        if self.is_production:
            return SecurityConfig.PRODUCTION_CORS_ORIGINS
        elif self.is_staging:
            return SecurityConfig.STAGING_CORS_ORIGINS
        else:
            return SecurityConfig.DEV_CORS_ORIGINS

    def get_cors_methods(self) -> List[str]:
        """Get CORS methods based on environment"""
        if self.is_production:
            return SecurityConfig.PRODUCTION_CORS_METHODS
        else:
            return SecurityConfig.DEV_STAGING_CORS_METHODS

    def get_trusted_hosts(self) -> List[str]:
        """Get trusted hosts based on environment"""
        # Allow override via environment variable
        if self.custom_trusted_hosts:
            return [host.strip() for host in self.custom_trusted_hosts.split(",")]

        if self.is_production:
            return SecurityConfig.PRODUCTION_TRUSTED_HOSTS
        elif self.is_staging:
            return SecurityConfig.STAGING_TRUSTED_HOSTS
        else:
            return SecurityConfig.DEV_TRUSTED_HOSTS

    def get_cors_headers(self) -> List[str]:
        """Get CORS headers"""
        return SecurityConfig.CORS_HEADERS


# Global settings instance
settings = Settings()