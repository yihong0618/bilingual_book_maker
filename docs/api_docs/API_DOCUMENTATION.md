# Bilingual Book Maker API Layer - Comprehensive Documentation

## Table of Contents
1. [System Architecture Overview](#1-system-architecture-overview)
2. [Core Components Deep Dive](#2-core-components-deep-dive)
3. [Progress Tracking System](#3-progress-tracking-system)
4. [Translation Flow Step-by-Step](#4-translation-flow-step-by-step)
5. [Key Patterns and Design Decisions](#5-key-patterns-and-design-decisions)
6. [Common Issues and Debugging](#6-common-issues-and-debugging)
7. [Code Examples](#7-code-examples)

---

## 1. System Architecture Overview

### High-Level Component Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        FastAPI Application                       │
│                           (main.py)                             │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌──────────────┐    ┌─────────────────┐    ┌───────────────┐  │
│  │   Endpoints  │───▶│ AsyncTranslator │───▶│  JobManager   │  │
│  │  /translate  │    │                 │    │               │  │
│  │   /status    │    │  Orchestration  │    │ Thread Pool   │  │
│  │  /download   │    │                 │    │   Executor    │  │
│  └──────────────┘    └─────────────────┘    └───────────────┘  │
│          │                    │                      │          │
│          ▼                    ▼                      ▼          │
│  ┌──────────────┐    ┌─────────────────┐    ┌───────────────┐  │
│  │   Models     │    │ Progress Monitor│    │   Storage     │  │
│  │              │    │                 │    │               │  │
│  │ Data Types   │    │ TqdmInterceptor │    │  Upload Dir   │  │
│  │ Validation   │    │ AsyncTracker    │    │  Output Dir   │  │
│  └──────────────┘    └─────────────────┘    └───────────────┘  │
│                              │                                  │
└──────────────────────────────┼──────────────────────────────────┘
                               │
                               ▼
                    ┌──────────────────────┐
                    │   Book Maker Core    │
                    │                      │
                    │  ┌──────────────┐    │
                    │  │ EPUB Loader  │    │
                    │  │ TXT Loader   │    │
                    │  │ SRT Loader   │    │
                    │  │ MD Loader    │    │
                    │  └──────────────┘    │
                    │          │            │
                    │          ▼            │
                    │  ┌──────────────┐    │
                    │  │ Translation  │    │
                    │  │   Engines    │    │
                    │  │              │    │
                    │  │ ChatGPT      │    │
                    │  │ Claude       │    │
                    │  │ Gemini       │    │
                    │  │ DeepL        │    │
                    │  │ Google       │    │
                    │  └──────────────┘    │
                    └──────────────────────┘
```

### Data Flow Diagram

```
User Request → FastAPI → Job Creation → Thread Pool → Translation Engine → Progress Updates → Response

1. HTTP Request (/translate)
   ↓
2. File Upload & Validation
   ↓
3. Job Creation (UUID generation)
   ↓
4. Thread Submission (async execution)
   ↓
5. File Processing (EPUB/TXT/SRT/MD)
   ↓
6. Translation Engine Invocation
   ↓ (with progress callbacks)
7. Progress Updates via TqdmInterceptor
   ↓
8. Output File Generation
   ↓
9. Job Completion & File Storage
```

### API Request Lifecycle

```
1. Request Reception
   - FastAPI endpoint receives multipart/form-data
   - File validation (format, size)
   - Parameter validation (model, language, API key)

2. Job Initialization
   - UUID generation for job_id
   - File storage with unique prefix
   - Job record creation in JobManager

3. Async Processing
   - Thread pool executor submission
   - Translation function execution
   - Progress monitoring activation

4. Translation Execution
   - Loader instantiation (EPUB/TXT/etc.)
   - Model initialization (ChatGPT/Claude/etc.)
   - Paragraph-by-paragraph translation
   - Progress updates via callbacks

5. Completion Handling
   - Output file generation
   - Job status update
   - Resource cleanup scheduling
   - Download URL availability
```

---

## 2. Core Components Deep Dive

### API Layer Components

#### `main.py` - FastAPI Application Core
**Purpose**: Entry point and HTTP endpoint management

**Key Responsibilities**:
- Request validation and routing
- Middleware configuration (CORS, TrustedHost)
- Exception handling and error responses
- File upload/download management
- Job lifecycle endpoints

**Critical Functions**:
```python
- start_translation(): Creates and starts translation jobs
- get_job_status(): Returns current job progress and status
- download_result(): Serves completed translation files
- list_jobs(): Provides job listing with filtering
```

**Configuration Integration**:
- Uses `settings` from config for environment-based behavior
- Dynamically adjusts CORS origins based on environment
- Configurable worker pools and TTL settings

#### `async_translator.py` - Translation Orchestration
**Purpose**: Bridge between API and book_maker core

**Key Responsibilities**:
- Job execution management
- Model class mapping and initialization
- File type detection and loader selection
- Progress callback integration
- Retry logic with exponential backoff

**Translation Flow**:
```python
1. start_translation()
   - Validates input file
   - Creates job record
   - Registers progress callbacks
   - Submits to thread pool

2. _execute_translation()
   - Sets up timeout monitoring
   - Creates TqdmInterceptor patch
   - Invokes appropriate loader
   - Handles retries on failure

3. _translate_with_loader()
   - Determines file type
   - Instantiates correct loader
   - Manages output file placement
   - Handles format-specific quirks
```

**Model Mapping**:
```python
MODEL_CLASSES = {
    TranslationModel.CHATGPT: ChatGPTAPI,
    TranslationModel.CLAUDE: Claude,
    TranslationModel.GEMINI: Gemini,
    TranslationModel.DEEPL: DeepL,
    TranslationModel.GOOGLE: Google,
    # ... more models
}
```

#### `job_manager.py` - Job Lifecycle Management
**Purpose**: Thread-safe job storage and execution

**Key Features**:
- Thread pool executor for concurrent translations
- Job TTL and automatic cleanup
- File management (upload/output/temp)
- Progress tracking integration
- Statistics and monitoring

**Thread Safety**:
```python
- Uses threading.RLock for all job operations
- Thread-safe collections for job storage
- Atomic status transitions
- Safe cleanup operations
```

**Storage Management**:
```python
- Upload directory: Unique prefixed files
- Output directory: Job-specific outputs
- Temp directory: Working files
- Automatic cleanup after TTL expiration
```

#### `progress_monitor.py` - Real-time Progress Tracking
**Purpose**: Intercept and relay translation progress

**Architecture**:
```python
ProgressMonitor (Core)
    ├── Callback Registry (job_id → callback)
    ├── Progress Storage (current/total tracking)
    └── Update Broadcasting

TqdmInterceptor (Patching)
    ├── Inherits from tqdm
    ├── Overrides update() method
    └── Forwards to ProgressMonitor

AsyncProgressTracker (Singleton)
    ├── Global instance
    ├── High-level API
    └── Context managers for patching
```

**Key Innovation**:
The TqdmInterceptor monkey-patches the tqdm library used by book_maker, allowing transparent progress tracking without modifying the core translation code.

#### `models.py` - Data Models and Validation
**Purpose**: Type safety and data validation

**Model Categories**:
1. **Enums**:
   - `JobStatus`: pending, processing, completed, failed, cancelled
   - `TranslationModel`: chatgpt, claude, gemini, deepl, google, etc.

2. **Dataclasses**:
   - `TranslationJob`: Complete job state and metadata

3. **Pydantic Models**:
   - Request validation (TranslationRequest)
   - Response serialization (TranslationResponse, JobStatusResponse)
   - Error handling (ErrorResponse)

#### `config/` - Configuration Management
**Purpose**: Environment-based settings

**Structure**:
```python
settings.py
    ├── SecurityConfig (CORS, Trusted Hosts)
    ├── Settings (Pydantic BaseSettings)
    └── Environment detection

constants.py
    ├── NetworkConstants (ports, hosts)
    ├── SecurityConstants (methods, headers)
    ├── HttpStatusConstants (status codes)
    └── DefaultValues (fallbacks)
```

### Book Maker Components

#### `epub_loader.py` - EPUB Processing Engine
**Purpose**: Handle EPUB file translation

**Key Features**:
- BeautifulSoup HTML parsing
- Paragraph extraction and translation
- Progress callback integration
- Resume capability with pickle
- Context-aware translation support

**Progress Integration**:
```python
def _update_global_progress(self, current, total):
    if self.progress_tracker and self.job_id:
        self.progress_tracker.monitor.update_progress(
            self.job_id, current, total
        )
```

#### Other Loaders (TXT, SRT, MD)
**Purpose**: Format-specific handlers

**Common Pattern**:
```python
class XXXLoader(BaseBookLoader):
    def __init__(self, file_name, model, key, ...):
        # Initialize translator
        # Set up file handling

    def make_bilingual_book(self):
        # Read source file
        # Process content
        # Translate segments
        # Generate output file
```

#### `base_loader.py` - Abstract Base Class
**Purpose**: Define loader interface

**Required Methods**:
- `_make_new_book()`: Create output structure
- `make_bilingual_book()`: Main translation logic
- `load_state()`: Resume functionality
- `_save_progress()`: Checkpoint creation

---

## 3. Progress Tracking System

### The Architecture Fix

**Original Problem**:
The progress tracking system had an instance reference issue where the `AsyncProgressTracker` singleton wasn't properly maintaining callback references across thread boundaries.

**Solution Architecture**:
```python
# Global singleton instance
global_progress_tracker = AsyncProgressTracker()

# Registration in job_manager.py
global_progress_tracker.start_tracking(job_id, callback)

# Patching in async_translator.py
with global_progress_tracker.create_tqdm_patch(job_id):
    # Translation happens here
    # tqdm is monkey-patched to use TqdmInterceptor

# Update flow in epub_loader.py
self.progress_tracker.monitor.update_progress(job_id, current, total)
```

### Callback Flow

```
1. Job Start
   └─> job_manager.start_job()
       └─> global_progress_tracker.start_tracking(job_id, callback)
           └─> Registers callback in ProgressMonitor

2. Translation Execution
   └─> async_translator._execute_translation()
       └─> Creates TqdmInterceptor patch
           └─> Replaces tqdm.tqdm with TqdmInterceptor

3. Progress Updates (two paths):

   Path A: Via tqdm
   └─> book_maker uses tqdm(iterable)
       └─> TqdmInterceptor.update()
           └─> ProgressMonitor.update_progress()
               └─> Callback execution

   Path B: Direct call
   └─> epub_loader._update_global_progress()
       └─> ProgressMonitor.update_progress()
           └─> Callback execution

4. Job Completion
   └─> job_manager cleans up
       └─> global_progress_tracker.stop_tracking(job_id)
           └─> Unregisters callback
```

### Update Throttling

The system implements intelligent throttling to prevent excessive updates:

```python
should_update = (
    self._last_update_time is None or                    # First update
    now - self._last_update_time >= interval or          # Time-based
    self.n == 1 or                                       # Start
    (self.total and self.n >= self.total) or            # End
    (self.total and (self.n * 100 // self.total) % 5 == 0)  # 5% milestones
)
```

---

## 4. Translation Flow Step-by-Step

### Complete Request Flow

```python
# 1. HTTP Request Reception
POST /translate
Content-Type: multipart/form-data
- file: example.epub
- model: chatgpt
- key: sk-xxx
- language: zh-cn

# 2. FastAPI Endpoint Processing (main.py)
async def start_translation():
    # Validate file format
    # Save with unique prefix
    unique_path = job_manager.get_upload_path(file.filename)
    # Start translation job
    job_id = async_translator.start_translation(...)

# 3. Job Creation (async_translator.py)
def start_translation():
    # Create job record
    job = job_manager.create_job(...)
    # Register progress callback
    def progress_callback(update):
        job_manager.update_job_progress(...)
    # Submit to thread pool
    job_manager.start_job(job_id, translation_func, progress_callback)

# 4. Thread Execution (job_manager.py)
def _execute_job():
    # Update status to PROCESSING
    # Call translation function
    output_path = translation_func(job)
    # Mark as COMPLETED or FAILED
    # Clean up resources

# 5. Translation Processing (async_translator.py)
def _execute_translation():
    # Set up timeout monitoring
    # Apply TqdmInterceptor patch
    with global_progress_tracker.create_tqdm_patch(job_id):
        self._translate_with_loader(...)

# 6. Loader Execution (epub_loader.py)
def make_bilingual_book():
    # Parse EPUB structure
    # Extract paragraphs
    for paragraph in tqdm(paragraphs):
        # Translate paragraph
        translated = self.translate_model.translate(text)
        # Update progress
        self._update_global_progress(current, total)
        # Save checkpoint periodically

# 7. Output Generation
# Move generated file to output directory
shutil.move(generated_file, output_path)

# 8. Job Completion
# Update job record
job.mark_completed(output_path)
# Stop progress tracking
global_progress_tracker.stop_tracking(job_id)

# 9. Client Polling
GET /status/{job_id}
# Returns progress, status, download_url

# 10. File Download
GET /download/{job_id}
# Serves completed file
```

### Progress Update Flow

```python
# Real-time updates during translation
1. tqdm iteration in book_maker
   ↓
2. TqdmInterceptor.update() intercepts
   ↓
3. ProgressMonitor.update_progress() called
   ↓
4. Registered callback executed
   ↓
5. JobManager.update_job_progress()
   ↓
6. TranslationJob.progress updated
   ↓
7. Available via GET /status/{job_id}
```

---

## 5. Key Patterns and Design Decisions

### Async/Threading Architecture

**Design Choice**: Thread Pool Executor instead of asyncio
- **Reason**: book_maker core is synchronous and CPU-intensive
- **Implementation**: ThreadPoolExecutor with configurable max_workers
- **Benefits**: True parallelism for CPU-bound translation tasks

```python
self._executor = ThreadPoolExecutor(
    max_workers=max_workers,
    thread_name_prefix="translation-"
)
```

### Singleton Pattern

**Used For**: Progress tracking and job management
- **Global Instances**: `job_manager`, `async_translator`, `global_progress_tracker`
- **Benefits**: Consistent state across all components
- **Thread Safety**: All operations protected by locks

### Monkey Patching

**Purpose**: Intercept tqdm progress without modifying book_maker
```python
# Temporary replacement of tqdm
import tqdm as tqdm_module
original_tqdm = tqdm_module.tqdm
tqdm_module.tqdm = TqdmInterceptor
try:
    # Translation happens here
finally:
    tqdm_module.tqdm = original_tqdm
```

### Error Handling Strategy

**Multi-Level Protection**:
1. **Global Exception Handler**: Catches all unhandled exceptions
2. **Job-Level Try-Catch**: Each job isolated from others
3. **Retry Logic**: Exponential backoff for transient failures
4. **Graceful Degradation**: Failed jobs don't affect system

```python
try:
    output_path = translation_func(job)
    job.mark_completed(output_path)
except Exception as e:
    job.mark_failed(str(e))
    if job.retry_count < self.max_retries:
        # Retry with backoff
```

### File Management

**Three-Tier Storage**:
```python
uploads/     # Original files with UUID prefix
├── a1b2c3d4_book.epub
├── e5f6g7h8_document.txt

outputs/     # Completed translations
├── book_bilingual_job123.epub
├── document_bilingual_job456.txt

temp/        # Working files and checkpoints
├── job123/
│   ├── .book.temp.bin
```

**Cleanup Strategy**:
- TTL-based expiration (configurable, default 24 hours)
- Periodic cleanup task
- Manual cleanup endpoint
- Automatic on shutdown

### Configuration Management

**Environment-Based Settings**:
```python
ENVIRONMENT=development  # development, staging, production
├── Different CORS origins
├── Different security settings
├── Different logging levels
└── Different resource limits
```

**Override Hierarchy**:
1. Environment variables (highest priority)
2. .env file
3. Default values in code (lowest priority)

---

## 6. Common Issues and Debugging

### Instance Reference Problem

**Symptom**: Progress updates not reaching API despite being logged
**Cause**: Different object instances between registration and update
**Solution**: Use singleton pattern with global instance

```python
# WRONG - Creates new instance
progress_tracker = AsyncProgressTracker()

# CORRECT - Uses global singleton
from .progress_monitor import global_progress_tracker
```

### Threading Issues

**Common Problems**:
1. **Deadlocks**: Always use timeout on locks
2. **Race Conditions**: Protected by RLock
3. **Resource Leaks**: Ensure cleanup in finally blocks

**Debugging Approach**:
```python
# Add extensive logging
logger.warning(f"DEBUG: Thread {threading.current_thread().name}")
logger.warning(f"DEBUG: Lock acquired: {self._lock}")
```

### File Path Issues

**Problem**: Import errors or file not found
**Common Causes**:
- Relative vs absolute paths
- Working directory changes
- Docker volume mounting

**Solution**:
```python
# Always use absolute paths
from pathlib import Path
absolute_path = Path(__file__).parent.parent / "uploads" / filename
```

### Progress Not Updating

**Debugging Checklist**:
1. Check callback registration:
   ```python
   logger.warning(f"Registered callbacks: {list(self._callbacks.keys())}")
   ```

2. Verify job_id consistency:
   ```python
   logger.warning(f"Job ID in tracker: {self.job_id}")
   logger.warning(f"Job ID in update: {job_id}")
   ```

3. Confirm patch is active:
   ```python
   logger.warning(f"TqdmInterceptor active: {isinstance(tqdm.tqdm, TqdmInterceptor)}")
   ```

4. Monitor update frequency:
   ```python
   logger.warning(f"Update throttled: {not should_update}")
   ```

### Memory Leaks

**Potential Sources**:
- Uncleaned job records
- Unremoved callbacks
- Temporary files not deleted

**Prevention**:
```python
# Always clean up in finally blocks
try:
    # Processing
finally:
    self._callbacks.pop(job_id, None)
    self._active_jobs.pop(job_id, None)
    # Remove temp files
```

### Translation Model Errors

**Common Issues**:
1. **API Key Invalid**: Check key format and permissions
2. **Rate Limiting**: Implement backoff and retry
3. **Network Timeouts**: Configure appropriate timeouts
4. **Model Unavailable**: Fallback to alternative models

**Error Handling Pattern**:
```python
try:
    result = model.translate(text)
except RateLimitError:
    time.sleep(2 ** retry_count)
    return self.retry_translation()
except AuthenticationError:
    logger.error("Invalid API key")
    job.mark_failed("Authentication failed")
```

---

## 7. Code Examples

### Adding a New Endpoint

```python
# In main.py

@app.post("/translate-batch")
async def batch_translation(
    files: List[UploadFile] = File(...),
    model: TranslationModel = Form(...),
    key: str = Form(...)
):
    """Start batch translation of multiple files"""
    job_ids = []

    for file in files:
        # Validate each file
        if not file.filename.endswith(('.epub', '.txt', '.srt', '.md')):
            continue

        # Save file
        upload_path = job_manager.get_upload_path(file.filename)
        with open(upload_path, "wb") as f:
            content = await file.read()
            f.write(content)

        # Start translation
        try:
            job_id = async_translator.start_translation(
                file_path=str(upload_path),
                model=model,
                key=key,
                language="zh-cn"
            )
            job_ids.append(job_id)
        except Exception as e:
            logger.error(f"Failed to start translation for {file.filename}: {e}")

    return {
        "message": f"Started {len(job_ids)} translation jobs",
        "job_ids": job_ids
    }
```

### Modifying Progress Tracking

```python
# Add custom progress metrics

class ExtendedProgressUpdate(ProgressUpdate):
    """Extended progress with additional metrics"""
    words_per_minute: float = 0.0
    estimated_completion: Optional[datetime] = None
    quality_score: float = 0.0

# In progress_monitor.py
def update_progress_with_metrics(
    self,
    job_id: str,
    current: int,
    total: int,
    start_time: datetime
):
    # Calculate words per minute
    elapsed = (datetime.now() - start_time).total_seconds() / 60
    wpm = (current * 50) / elapsed if elapsed > 0 else 0  # Assume 50 words per paragraph

    # Estimate completion
    if current > 0:
        rate = current / elapsed
        remaining = (total - current) / rate if rate > 0 else 0
        estimated = datetime.now() + timedelta(minutes=remaining)
    else:
        estimated = None

    # Create extended update
    update = ExtendedProgressUpdate(
        job_id=job_id,
        current=current,
        total=total,
        percentage=(current / total * 100) if total > 0 else 0,
        timestamp=datetime.now(),
        words_per_minute=wpm,
        estimated_completion=estimated
    )

    # Call callback
    callback = self._callbacks.get(job_id)
    if callback:
        callback(update)
```

### Adding a New File Format Loader

```python
# Create new loader in book_maker/loader/pdf_loader.py

from .base_loader import BaseBookLoader
import PyPDF2

class PDFBookLoader(BaseBookLoader):
    def __init__(self, pdf_name, model, key, **kwargs):
        self.pdf_name = pdf_name
        self.translate_model = model(key, **kwargs)
        self.pages_to_save = []

    def make_bilingual_book(self):
        """Main translation method for PDF files"""
        # Read PDF
        with open(self.pdf_name, 'rb') as file:
            pdf_reader = PyPDF2.PdfReader(file)
            pdf_writer = PyPDF2.PdfWriter()

            total_pages = len(pdf_reader.pages)

            # Process each page
            for i, page in enumerate(tqdm(pdf_reader.pages, desc="Translating PDF")):
                # Extract text
                text = page.extract_text()

                # Translate
                translated = self.translate_model.translate(text)

                # Create new page with translation
                # (This is simplified - real implementation would be more complex)
                new_page = self._create_bilingual_page(text, translated)
                pdf_writer.add_page(new_page)

                # Update progress if job_id exists
                if hasattr(self, 'progress_tracker') and self.job_id:
                    self.progress_tracker.monitor.update_progress(
                        self.job_id, i + 1, total_pages
                    )

            # Save output
            output_name = self.pdf_name.replace('.pdf', '_bilingual.pdf')
            with open(output_name, 'wb') as output:
                pdf_writer.write(output)

    def _create_bilingual_page(self, original, translated):
        """Create a page with both original and translated text"""
        # Implementation would depend on PDF library capabilities
        pass

    def load_state(self):
        """Load saved state for resume"""
        # Implement pickle-based state loading
        pass

    def _save_progress(self):
        """Save current progress"""
        # Implement checkpoint saving
        pass

    def _make_new_book(self, book):
        """Create new PDF structure"""
        return PyPDF2.PdfWriter()

# Register in BOOK_LOADER_DICT
from book_maker.loader import BOOK_LOADER_DICT
BOOK_LOADER_DICT['pdf'] = PDFBookLoader

# Update supported formats in async_translator.py
supported_formats = ['.epub', '.txt', '.srt', '.md', '.pdf']
```

### Debugging Progress Issues

```python
# Add debug middleware to track progress flow

class ProgressDebugMiddleware:
    """Middleware to debug progress tracking issues"""

    def __init__(self):
        self.events = []

    def log_event(self, event_type: str, job_id: str, data: dict):
        """Log a progress-related event"""
        self.events.append({
            'timestamp': datetime.now(),
            'event_type': event_type,
            'job_id': job_id,
            'thread': threading.current_thread().name,
            'data': data
        })

    def get_job_timeline(self, job_id: str):
        """Get timeline of events for a specific job"""
        return [e for e in self.events if e['job_id'] == job_id]

    def print_timeline(self, job_id: str):
        """Print formatted timeline"""
        timeline = self.get_job_timeline(job_id)
        for event in timeline:
            print(f"{event['timestamp']} [{event['thread']}] {event['event_type']}: {event['data']}")

# Use in progress_monitor.py
debug_middleware = ProgressDebugMiddleware()

def update_progress(self, job_id: str, current: int, total: int):
    debug_middleware.log_event('progress_update', job_id, {
        'current': current,
        'total': total,
        'callbacks_registered': list(self._callbacks.keys())
    })
    # ... rest of the method

# Access debug info via endpoint
@app.get("/debug/progress/{job_id}")
async def get_progress_debug(job_id: str):
    return {
        "timeline": debug_middleware.get_job_timeline(job_id),
        "current_callbacks": list(global_progress_tracker.monitor._callbacks.keys()),
        "active_jobs": list(global_progress_tracker.monitor._active_jobs.keys())
    }
```

### Custom Translation Model Integration

```python
# Add a new translation model

# In book_maker/translator/custom_model.py
class CustomTranslator:
    def __init__(self, key, language, **kwargs):
        self.api_key = key
        self.target_language = language
        self.api_base = kwargs.get('api_base', 'https://api.custom.com')

    def translate(self, text):
        """Translate single text"""
        response = requests.post(
            f"{self.api_base}/translate",
            json={
                "text": text,
                "target": self.target_language,
                "source": "auto"
            },
            headers={
                "Authorization": f"Bearer {self.api_key}"
            }
        )
        return response.json()["translation"]

# Register in async_translator.py
from book_maker.translator import CustomTranslator

MODEL_CLASSES = {
    # ... existing models
    TranslationModel.CUSTOM: CustomTranslator
}

# Add to TranslationModel enum in models.py
class TranslationModel(str, Enum):
    # ... existing models
    CUSTOM = "custom"
```

---

## Summary

The Bilingual Book Maker API Layer is a sophisticated async translation system that:

1. **Provides RESTful API** for file translation with job tracking
2. **Supports multiple file formats** (EPUB, TXT, SRT, MD)
3. **Integrates various translation models** (ChatGPT, Claude, Gemini, DeepL, Google)
4. **Offers real-time progress tracking** through tqdm interception
5. **Handles concurrent translations** via thread pool execution
6. **Manages file lifecycle** with automatic cleanup
7. **Ensures reliability** through retry logic and error handling
8. **Adapts to environments** with configuration-based settings

The architecture emphasizes:
- **Separation of Concerns**: Clear boundaries between API, orchestration, and translation
- **Thread Safety**: Careful synchronization for concurrent operations
- **Extensibility**: Easy addition of new models and formats
- **Monitoring**: Comprehensive progress and status tracking
- **Resilience**: Graceful error handling and recovery

This documentation provides both theoretical understanding and practical examples for maintaining, extending, and debugging the system.