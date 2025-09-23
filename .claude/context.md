# Bilingual Book Maker - Context for Claude Code

## Project Overview
This is a FastAPI-based translation service that converts EPUB, TXT, SRT, and MD files into bilingual versions using various AI translation models.

## Key Components

### API Layer (`api_layer/`)
- **FastAPI application** with async endpoints
- **Job management system** with progress tracking
- **Progress monitoring** using tqdm interception
- **Async translator** wrapper around core translation logic

### Core Translation (`book_maker/`)
- **File loaders** for different formats (EPUB, TXT, SRT, MD)
- **Translator integrations** for various AI models
- **Translation logic** with paragraph-level processing

### Current State
- ✅ Progress tracking is working correctly
- ✅ API shows real-time progress (e.g., "55% (55/100)")
- ✅ Docker containerization is set up
- ✅ Comprehensive documentation is available

## Common Development Tasks

### Testing
```bash
# Run API tests
python -m pytest tests/ -v

# Test specific component
python -m pytest tests/test_async_translator.py -v

# Test with actual file
curl -X POST "http://localhost:8000/translate" \
  -H "Content-Type: multipart/form-data" \
  -F "file=@test_books/animal_farm.epub" \
  -F "model=google" \
  -F "is_test=true"
```

### Docker Development
```bash
# Build and run
docker build -t bilingual-book-maker .
docker run -p 8000:8000 bilingual-book-maker

# Check logs
docker logs [container-id]
```

### API Endpoints
- **POST /translate** - Start translation job
- **GET /status/{job_id}** - Check progress
- **GET /download/{job_id}** - Download result
- **GET /jobs** - List all jobs
- **POST /cancel/{job_id}** - Cancel job

## Architecture Notes

### Progress Tracking System
- Uses singleton pattern for `global_progress_tracker`
- Dependency injection to share tracker instance
- tqdm interception for progress updates
- Callback system for real-time updates

### Translation Models Supported
- Google Translate (free, no API key)
- ChatGPT/GPT-4 (requires OpenAI API key)
- Claude (requires Anthropic API key)
- DeepL/DeepL Free (requires DeepL API key)
- Gemini, Groq, Qwen, XAI (various API keys)

### File Format Support
- **EPUB**: E-book format (primary use case)
- **TXT**: Plain text files
- **SRT**: Subtitle files
- **MD**: Markdown files

## Deployment
- Configured for Docker deployment
- AWS deployment options discussed (ECS/Fargate recommended)
- Environment variables for configuration