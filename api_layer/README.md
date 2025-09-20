# Bilingual Book Maker - Async API Layer

A comprehensive async wrapper around the bilingual_book_maker library that enables non-blocking translation processing with job tracking and progress monitoring.

## Overview

This async API layer provides:

- **Non-blocking translation**: Start translation jobs that run in the background
- **Job tracking**: Monitor translation progress and status in real-time
- **Progress monitoring**: Get detailed progress updates every 10% completion
- **Error handling**: Comprehensive error handling with retry logic and timeouts
- **Thread safety**: Full thread-safe operation for concurrent requests
- **TTL cleanup**: Automatic cleanup of completed jobs after 3 hours
- **RESTful API**: Clean REST endpoints for all operations

## Architecture

### Core Components

1. **AsyncEPUBTranslator** (`async_translator.py`): Main wrapper around bilingual_book_maker
2. **JobManager** (`job_manager.py`): Thread-safe job lifecycle management
3. **ProgressMonitor** (`progress_monitor.py`): Intercepts tqdm progress from bilingual_book_maker
4. **ErrorHandler** (`error_handler.py`): Comprehensive error handling and timeout management
5. **FastAPI App** (`main.py`): REST API endpoints

### Data Flow

```
1. Client uploads EPUB file via POST /translate
2. AsyncEPUBTranslator creates job and starts translation in background
3. ProgressMonitor intercepts tqdm progress from bilingual_book_maker
4. Client polls GET /status/{job_id} for progress updates
5. When complete, client downloads via GET /download/{job_id}
```

## API Endpoints

### Core Translation Endpoints

- `POST /translate` - Start new translation job
- `GET /status/{job_id}` - Get job status and progress
- `GET /download/{job_id}` - Download completed translation
- `POST /cancel/{job_id}` - Cancel running job

### Management Endpoints

- `GET /jobs` - List all jobs with filtering
- `DELETE /jobs/{job_id}` - Delete completed job
- `POST /cleanup` - Manual cleanup of expired jobs
- `GET /health` - Health check and system stats

### Information Endpoints

- `GET /models` - List available translation models
- `GET /stats` - Detailed system statistics

## Installation and Setup

### Prerequisites

1. Python 3.9+
2. All bilingual_book_maker dependencies installed
3. API keys for translation services

### Install Dependencies

```bash
cd api_layer
pip install -r requirements.txt
```

### Run the API Server

```bash
# Development mode
cd api_layer/api
python main.py

# Production mode with uvicorn
uvicorn api.main:app --host 0.0.0.0 --port 8000 --workers 4
```

The API will be available at `http://localhost:8000` with automatic documentation at `http://localhost:8000/docs`.

## Usage Examples

### 1. Start Translation

```bash
curl -X POST "http://localhost:8000/translate" \
  -H "Content-Type: multipart/form-data" \
  -F "file=@book.epub" \
  -F "model=chatgpt" \
  -F "key=your-api-key" \
  -F "language=zh-cn"
```

Response:
```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "pending",
  "message": "Translation job started successfully",
  "estimated_duration": "5-30 minutes depending on file size and model"
}
```

### 2. Check Progress

```bash
curl "http://localhost:8000/status/550e8400-e29b-41d4-a716-446655440000"
```

Response:
```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "processing",
  "progress": 45,
  "filename": "book.epub",
  "created_at": "2024-01-15T10:30:00Z",
  "total_paragraphs": 1250,
  "processed_paragraphs": 562,
  "model": "chatgpt",
  "target_language": "zh-cn"
}
```

### 3. Download Result

```bash
curl -O "http://localhost:8000/download/550e8400-e29b-41d4-a716-446655440000"
```

### 4. List Jobs

```bash
curl "http://localhost:8000/jobs?status=completed&limit=10"
```

## Configuration

### Environment Variables

- `TRANSLATION_TIMEOUT_MINUTES`: Timeout for translation jobs (default: 30)
- `MAX_CONCURRENT_JOBS`: Maximum concurrent translation jobs (default: 4)
- `JOB_TTL_HOURS`: Time-to-live for completed jobs (default: 3)
- `LOG_LEVEL`: Logging level (default: INFO)

### Translation Parameters

All parameters from the original bilingual_book_maker are supported:

- `model`: Translation model (chatgpt, claude, gemini, etc.)
- `key`: API key for the translation service
- `language`: Target language code (default: zh-cn)
- `model_api_base`: Custom API base URL
- `temperature`: Translation temperature (0.0-2.0)
- `context_flag`: Use context for translation
- `single_translate`: Single translation mode
- `is_test`: Test mode with limited paragraphs

## Job Lifecycle

### Job Status Flow

```
PENDING → PROCESSING → COMPLETED/FAILED/CANCELLED
```

### Automatic Cleanup

- Completed jobs are kept for 3 hours by default
- Cleanup runs every 30 minutes automatically
- Manual cleanup via `POST /cleanup` endpoint

### Progress Reporting

- Progress updates every 10% completion
- Real-time paragraph count tracking
- Estimated completion time based on progress

## Error Handling

### Error Types

- **TimeoutError**: Translation exceeds 30-minute timeout
- **NetworkError**: Network connectivity issues
- **APIError**: Translation service API errors
- **FileError**: File-related errors
- **ValidationError**: Invalid parameters

### Retry Logic

- 1 automatic retry on failure by default
- Exponential backoff for retry delays
- Permanent errors (401, 403) are not retried

### Timeout Management

- 30-minute timeout per translation job
- Background timeout monitoring
- Graceful job cancellation on timeout

## Testing

### Run Unit Tests

```bash
cd api_layer
pytest tests/ -v
```

### Test Coverage

```bash
pytest tests/ --cov=api --cov-report=html
```

### Integration Testing

```bash
# Start the API server
python api/main.py

# Run integration tests
pytest tests/integration/ -v
```

## Monitoring and Observability

### Health Check

```bash
curl "http://localhost:8000/health"
```

### System Statistics

```bash
curl "http://localhost:8000/stats"
```

### Logging

- Structured logging with timestamps
- Job lifecycle events logged
- Error tracking with stack traces
- Progress updates logged at debug level

## Performance Considerations

### Scalability

- Designed for horizontal scaling
- Thread-safe operation
- Configurable worker pool size
- Memory-efficient job storage

### Resource Management

- Automatic file cleanup
- Memory usage monitoring
- Connection pooling for API calls
- Efficient progress monitoring

### Optimization Tips

1. Adjust `max_workers` based on available CPU cores
2. Monitor memory usage with large EPUB files
3. Use `is_test=true` for development/testing
4. Configure appropriate timeout values

## Security Considerations

### API Security

- Input validation on all endpoints
- File type validation for uploads
- Rate limiting recommended for production
- CORS configuration for web clients

### Key Management

- API keys are not stored persistently
- Keys only exist in memory during job execution
- Recommend environment-based key management

### File Security

- Uploaded files stored in isolated directories
- Automatic cleanup of temporary files
- No persistent storage of user content

## Future Enhancements

### Phase 2: Cloud Migration

The current implementation provides clean interfaces for future migration to AWS:

- **JobManager** → DynamoDB for job storage
- **File Storage** → S3 for file management
- **Progress Monitoring** → CloudWatch for metrics
- **Job Queue** → SQS for job distribution

### Additional Features

- WebSocket support for real-time progress
- Batch translation for multiple files
- Custom translation models
- Advanced scheduling options
- Webhook notifications

## Troubleshooting

### Common Issues

1. **Job stuck in PROCESSING**: Check translator service availability
2. **High memory usage**: Monitor large EPUB file processing
3. **Timeout errors**: Adjust timeout configuration
4. **Progress not updating**: Check tqdm interception setup

### Debug Mode

```bash
# Enable debug logging
LOG_LEVEL=DEBUG python api/main.py
```

### Performance Monitoring

Monitor key metrics:
- Active job count
- Average translation time
- Error rates by type
- Memory and CPU usage

## Contributing

1. Fork the repository
2. Create feature branch
3. Add comprehensive tests
4. Update documentation
5. Submit pull request

### Development Setup

```bash
# Install development dependencies
pip install -r requirements.txt
pip install -r requirements-dev.txt

# Run tests
pytest tests/ -v

# Code formatting
black api/
isort api/

# Type checking
mypy api/
```

## License

This project follows the same license as the bilingual_book_maker library.