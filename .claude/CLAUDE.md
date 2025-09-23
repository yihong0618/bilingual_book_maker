# Claude Code Configuration

This repository is configured for Claude Code with the following setup:

## Project Structure
- **API Layer**: FastAPI backend in `api_layer/` directory
- **Core Logic**: Translation logic in `book_maker/` directory
- **Documentation**: Comprehensive docs in `docs/` directory
- **Tests**: Unit and integration tests in `tests/` directory

## Development Commands

### Docker Development
```bash
# Build the Docker image
docker build -t bilingual-book-maker .

# Run the container
docker run -p 8000:8000 bilingual-book-maker

# Run with environment variables
docker run -p 8000:8000 -e API_HOST=0.0.0.0 -e API_PORT=8000 bilingual-book-maker
```

### Local Development
```bash
# Install dependencies
pip install -r api_layer/requirements.txt

# Run the API server
cd api_layer && python -m uvicorn api.main:app --reload --host 0.0.0.0 --port 8000

# Run tests
python -m pytest tests/ -v
```

### Git Workflow
```bash
# Check status
git status

# Add changes
git add .

# Commit changes
git commit -m "Description of changes"

# Push to remote
git push origin [branch-name]
```

## API Endpoints
- **Health Check**: `GET /health`
- **Start Translation**: `POST /translate`
- **Check Status**: `GET /status/{job_id}`
- **Download Result**: `GET /download/{job_id}`
- **List Jobs**: `GET /jobs`
- **Cancel Job**: `POST /cancel/{job_id}`

## Testing
```bash
# Run all tests
python -m pytest tests/ -v

# Run specific test file
python -m pytest tests/test_async_translator.py -v

# Run with coverage
python -m pytest tests/ --cov=api_layer --cov-report=html
```

## Common Tasks
- Build and test locally before deploying
- Check logs with `docker logs [container-id]`
- Monitor API health at `/health` endpoint
- Use test mode (`is_test=true`) for quick validation