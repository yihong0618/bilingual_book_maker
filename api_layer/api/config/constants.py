"""
Application constants for the Bilingual Book Maker API
Centralized storage for all hardcoded values, URLs, ports, and magic numbers
"""


class NetworkConstants:
    """Network-related constants"""

    # Hosts
    LOCALHOST = "localhost"
    LOCALHOST_IP = "127.0.0.1"
    ALL_INTERFACES = "0.0.0.0"

    # Ports
    API_PORT = 8000
    FRONTEND_DEV_PORT = 3000
    FRONTEND_ALT_PORT = 8080

    # Protocols
    HTTP = "http"
    HTTPS = "https"


class EnvironmentConstants:
    """Environment type constants"""

    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"


class SecurityConstants:
    """Security-related constants"""

    # CORS Headers
    CONTENT_TYPE_HEADER = "Content-Type"
    AUTHORIZATION_HEADER = "Authorization"
    ACCEPT_HEADER = "Accept"
    ORIGIN_HEADER = "Origin"
    X_REQUESTED_WITH_HEADER = "X-Requested-With"

    # HTTP Methods
    GET_METHOD = "GET"
    POST_METHOD = "POST"
    DELETE_METHOD = "DELETE"
    OPTIONS_METHOD = "OPTIONS"
    ALL_METHODS = "*"


class HttpStatusConstants:
    """HTTP status code constants"""

    # 2xx Success
    OK = 200
    CREATED = 201
    ACCEPTED = 202
    NO_CONTENT = 204

    # 4xx Client Error
    BAD_REQUEST = 400
    UNAUTHORIZED = 401
    FORBIDDEN = 403
    NOT_FOUND = 404
    METHOD_NOT_ALLOWED = 405
    CONFLICT = 409
    PAYLOAD_TOO_LARGE = 413
    UNPROCESSABLE_ENTITY = 422

    # 5xx Server Error
    INTERNAL_SERVER_ERROR = 500
    NOT_IMPLEMENTED = 501
    BAD_GATEWAY = 502
    SERVICE_UNAVAILABLE = 503


class DefaultValues:
    """Default configuration values"""

    # Job Manager Defaults
    DEFAULT_MAX_WORKERS = 4
    DEFAULT_JOB_TTL_HOURS = 3
    DEFAULT_CLEANUP_INTERVAL_MINUTES = 30

    # API Server Defaults
    DEFAULT_API_HOST = "0.0.0.0"
    DEFAULT_API_PORT = 8000
    DEFAULT_RELOAD = True
    DEFAULT_DEBUG = True

    # Storage Defaults
    DEFAULT_UPLOAD_DIR = "uploads"
    DEFAULT_OUTPUT_DIR = "outputs"
    DEFAULT_TEMP_DIR = "temp"

    # Logging Defaults
    DEFAULT_LOG_LEVEL = "INFO"


class DomainConstants:
    """Domain and URL constants (customize for your domains)"""

    # Placeholder domains (replace with actual domains)
    YOUR_FRONTEND_DOMAIN = "yourfrontend.com"
    YOUR_FRONTEND_WWW = "www.yourfrontend.com"
    YOUR_FRONTEND_APP = "app.yourfrontend.com"
    YOUR_API_DOMAIN = "api.yourfrontend.com"

    # Staging domains
    STAGING_DOMAIN = "staging.yourdomain.com"

    @classmethod
    def get_localhost_with_port(cls, port: int) -> str:
        """Generate localhost URL with port"""
        return f"{NetworkConstants.LOCALHOST}:{port}"

    @classmethod
    def get_localhost_ip_with_port(cls, port: int) -> str:
        """Generate localhost IP URL with port"""
        return f"{NetworkConstants.LOCALHOST_IP}:{port}"

    @classmethod
    def get_all_interfaces_with_port(cls, port: int) -> str:
        """Generate all interfaces URL with port"""
        return f"{NetworkConstants.ALL_INTERFACES}:{port}"

    @classmethod
    def get_http_url(cls, host: str, port: int = None) -> str:
        """Generate HTTP URL"""
        if port:
            return f"{NetworkConstants.HTTP}://{host}:{port}"
        return f"{NetworkConstants.HTTP}://{host}"

    @classmethod
    def get_https_url(cls, host: str) -> str:
        """Generate HTTPS URL"""
        return f"{NetworkConstants.HTTPS}://{host}"


class StorageConstants:
    """Storage and file-related constants"""

    # File extensions
    EPUB_EXT = ".epub"
    TXT_EXT = ".txt"
    SRT_EXT = ".srt"
    MD_EXT = ".md"

    # MIME types
    EPUB_MIME = "application/epub+zip"
    TXT_MIME = "text/plain"
    SRT_MIME = "application/x-subrip"
    MD_MIME = "text/markdown"
    OCTET_STREAM_MIME = "application/octet-stream"

    # File naming
    BILINGUAL_SUFFIX = "_bilingual"
    TEMP_FILE_PREFIX = "."
    TEMP_FILE_SUFFIX = ".temp.bin"


class TimeConstants:
    """Time-related constants"""

    # Conversion factors
    MINUTES_PER_HOUR = 60
    SECONDS_PER_MINUTE = 60
    MILLISECONDS_PER_SECOND = 1000

    # Default timeouts and intervals
    DEFAULT_REQUEST_TIMEOUT_SECONDS = 30
    DEFAULT_RETRY_DELAY_SECONDS = 1
    MAX_RETRY_ATTEMPTS = 3


class ValidationConstants:
    """Validation-related constants"""

    # Size conversion constants
    BYTES_PER_KB = 1024
    BYTES_PER_MB = 1024 * 1024

    # Size limits
    MAX_FILE_SIZE_MB = 0.5  # 500KB limit for all users
    MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_MB * BYTES_PER_MB  # Convert MB to bytes
    MAX_FILENAME_LENGTH = 255

    # TODO: Future user-based file size limits
    # MAX_FILE_SIZE_REGISTERED_MB = 3  # For registered users
    # MAX_FILE_SIZE_PAID_MB = 10       # For paid users

    # String lengths
    MIN_PASSWORD_LENGTH = 8
    MAX_API_KEY_LENGTH = 512

    # Temperature ranges for AI models
    MIN_TEMPERATURE = 0.0
    MAX_TEMPERATURE = 2.0
    DEFAULT_TEMPERATURE = 1.0

    # Common defaults
    INITIAL_VALUE = 0  # For progress, counts, retries, etc.
    PROGRESS_COMPLETE = 100
    DEFAULT_TEST_PARAGRAPH_COUNT = 5

    # Language defaults
    DEFAULT_SOURCE_LANGUAGE = "auto"
    DEFAULT_TARGET_LANGUAGE = "zh-cn"
