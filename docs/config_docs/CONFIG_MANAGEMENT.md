# Configuration Management System

## Overview

The API now uses a centralized configuration management system to eliminate hardcoded values and improve maintainability. All configuration is environment-based with secure defaults.

## Architecture

```
api/
├── config/
│   ├── __init__.py
│   ├── constants.py         # Application constants and magic numbers
│   └── settings.py          # Environment-based configuration
├── main.py                  # Uses settings for CORS, hosts, server config
├── job_manager.py           # Uses settings for workers, TTL, paths
└── .env.example             # Sample environment configuration
```

## Configuration Classes

### `Constants` (constants.py)
Application constants and magic numbers organized by category:
- **NetworkConstants**: Hosts, ports, protocols
- **SecurityConstants**: HTTP methods, CORS headers
- **DefaultValues**: Default configuration values
- **DomainConstants**: Domain names and URL generation
- **StorageConstants**: File extensions, MIME types
- **TimeConstants**: Time conversion factors
- **ValidationConstants**: Size limits, validation rules

### `SecurityConfig` (settings.py)
Environment-specific security configuration using constants:
- **CORS origins** by environment (built from constants)
- **Trusted hosts** by environment (built from constants)
- **CORS methods** by environment (built from constants)
- **Standard headers** (from SecurityConstants)

### `Settings` (settings.py)
Main configuration class with environment variables support:
- Server settings (host, port, reload) using DefaultValues
- Job manager settings (workers, TTL, cleanup) using DefaultValues
- Storage paths using DefaultValues
- Security overrides
- Logging configuration using DefaultValues

## Environment Variables

### Core Settings
```bash
ENVIRONMENT=development          # development, staging, production
API_HOST=0.0.0.0                # API server host
API_PORT=8000                   # API server port
DEBUG=true                      # Debug mode
```

### Job Manager
```bash
MAX_WORKERS=4                   # Concurrent translation workers
JOB_TTL_HOURS=3                # Job time-to-live in hours
CLEANUP_INTERVAL_MINUTES=30     # Cleanup interval
```

### Storage
```bash
UPLOAD_DIR=uploads              # Upload directory
OUTPUT_DIR=outputs              # Output directory
TEMP_DIR=temp                   # Temporary directory
```

### Security Overrides (Optional)
```bash
CORS_ORIGINS=https://yourfrontend.com,https://www.yourfrontend.com
TRUSTED_HOSTS=yourfrontend.com,www.yourfrontend.com
```

## Environment-Specific Defaults

### Development
```python
CORS_ORIGINS = ["http://localhost:8000", "http://127.0.0.1:8000"]
TRUSTED_HOSTS = ["localhost:8000", "127.0.0.1:8000", "0.0.0.0:8000"]
CORS_METHODS = ["*"]
```

### Staging
```python
CORS_ORIGINS = ["https://staging.yourdomain.com", "http://localhost:8080"]
TRUSTED_HOSTS = ["staging.yourdomain.com", "localhost:8000", "127.0.0.1:8000"]
CORS_METHODS = ["*"]
```

### Production
```python
CORS_ORIGINS = ["https://yourfrontend.com", "https://www.yourfrontend.com"]
TRUSTED_HOSTS = ["yourfrontend.com", "www.yourfrontend.com", "api.yourfrontend.com"]
CORS_METHODS = ["GET", "POST", "DELETE", "OPTIONS"]
```

## Usage Examples

### 1. Development (Default)
```bash
# No .env file needed - uses defaults
python main.py
```

### 2. Custom Development
```bash
# Create .env file
echo "MAX_WORKERS=8" > .env
echo "JOB_TTL_HOURS=6" >> .env
python main.py
```

### 3. Production Deployment
```bash
# Set environment variables
export ENVIRONMENT=production
export API_HOST=0.0.0.0
export API_PORT=8000
export MAX_WORKERS=8
export JOB_TTL_HOURS=24
export DEBUG=false

# Or use .env file
cp .env.example .env
# Edit .env with production values

uvicorn api.main:app --host 0.0.0.0 --port 8000
```

### 4. Custom Domain Configuration
```bash
# Override security settings for your domain
export CORS_ORIGINS="https://myapp.com,https://www.myapp.com"
export TRUSTED_HOSTS="myapp.com,www.myapp.com,api.myapp.com"
```

## Security Benefits

### ✅ Before (Hardcoded - Insecure)
```python
allow_origins=["*"]                    # CORS bypass vulnerability
allowed_hosts=["*"]                    # Host header injection
max_workers=4                          # Not configurable
host="0.0.0.0", port=8000             # Not configurable
```

### ✅ After (Configuration-Based - Secure)
```python
allow_origins=settings.get_cors_origins()     # Environment-specific
allowed_hosts=settings.get_trusted_hosts()    # Environment-specific
max_workers=settings.max_workers               # Configurable
host=settings.api_host                         # Configurable
```

## Customizing for Your Domains

1. **Update SecurityConfig** in `api/config/settings.py`:
```python
PRODUCTION_CORS_ORIGINS = [
    "https://yourapp.com",        # Replace with your domain
    "https://www.yourapp.com",    # Replace with your domain
]

PRODUCTION_TRUSTED_HOSTS = [
    "yourapp.com",                # Replace with your domain
    "www.yourapp.com",            # Replace with your domain
    "api.yourapp.com"             # Replace with your API domain
]
```

2. **Or use environment variables** (recommended for production):
```bash
export CORS_ORIGINS="https://yourapp.com,https://www.yourapp.com"
export TRUSTED_HOSTS="yourapp.com,www.yourapp.com,api.yourapp.com"
```

## Migration Benefits

1. **Security**: Eliminates hardcoded wildcards and unsafe defaults
2. **Maintainability**: Single source of truth for configuration
3. **Flexibility**: Easy environment-specific customization
4. **Deployment**: Simple production configuration via environment variables
5. **Documentation**: Clear understanding of all configurable options

## Validation

The configuration system includes automatic validation:
- Type checking for all settings
- Environment variable parsing
- Default value fallbacks
- Error messages for invalid configurations

## Testing

```bash
# Test configuration system
python -c "from api.config import settings; print(settings.get_cors_origins())"

# Test different environments
ENVIRONMENT=production python -c "from api.config import settings; print(settings.get_cors_origins())"

# Test custom overrides
CORS_ORIGINS="https://test.com" python -c "from api.config import settings; print(settings.get_cors_origins())"
```