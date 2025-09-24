# Bilingual Book Maker - Service Architecture & UX Technical Guide

## Table of Contents
1. [Service Architecture Overview](#1-service-architecture-overview)
2. [Business Logic & User Tiers](#2-business-logic--user-tiers)
3. [API Contract Specifications](#3-api-contract-specifications)
4. [User Experience Flows](#4-user-experience-flows)
5. [Error Handling Patterns](#5-error-handling-patterns)
6. [Real-time Communication](#6-real-time-communication)
7. [Security Model](#7-security-model)
8. [Performance Characteristics](#8-performance-characteristics)
9. [Integration Guidelines](#9-integration-guidelines)
10. [Development Environment Setup](#10-development-environment-setup)

---

## 1. Service Architecture Overview

### Core Service Components

The bilingual book translation service is built on a production-ready FastAPI backend with async job processing. Understanding the architecture is crucial for designing appropriate user experiences.

```
┌─────────────────────────────────────────────────────────┐
│                Frontend Application                      │
│             (Your UI/UX Implementation)                 │
└─────────────────────┬───────────────────────────────────┘
                      │ HTTP/REST API
                      ▼
┌─────────────────────────────────────────────────────────┐
│              FastAPI Backend (localhost:8000)           │
├─────────────────────────────────────────────────────────┤
│ ┌─────────────┐ ┌─────────────────┐ ┌─────────────────┐ │
│ │  Endpoints  │ │ AsyncTranslator │ │   JobManager    │ │
│ │             │ │                 │ │                 │ │
│ │ /translate  │ │ • Job Creation  │ │ • Thread Pool   │ │
│ │ /status     │ │ • Progress      │ │ • Job Storage   │ │
│ │ /download   │ │ • Translation   │ │ • TTL Cleanup   │ │
│ │ /health     │ │   Orchestration │ │ • File Mgmt     │ │
│ └─────────────┘ └─────────────────┘ └─────────────────┘ │
└─────────────────────┬───────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────┐
│            Translation Engine Layer                     │
│                                                         │
│  • ChatGPT/GPT-4    • Claude      • Google Translate   │
│  • Gemini           • DeepL       • Groq               │
│  • Qwen             • xAI Grok    • DeepL Free         │
└─────────────────────────────────────────────────────────┘
```

### Key Architectural Patterns

**1. Async Request-Response Pattern**
- File upload returns immediate `job_id`
- Translation happens in background thread
- Client polls `/status/{job_id}` for progress
- Download via `/download/{job_id}` when complete

**2. Stateful Job Management**
- Jobs persist in memory with TTL (24 hours default)
- Comprehensive job lifecycle tracking
- Thread-safe concurrent processing
- Automatic cleanup of expired jobs

**3. File Processing Pipeline**
- Comprehensive validation (size, format, security)
- Temporary file storage with unique paths
- Progress tracking via tqdm interception
- Output file generation with bilingual suffix

### Service Capabilities & Constraints

**Supported File Formats:**
For MVP phase we set it to 500KB, will increased in the future.
- EPUB (eBooks) - up to 500KB
- TXT (Plain text) - up to 500KB
- SRT (Subtitles) - up to 500KB
- MD (Markdown) - up to 500KB

**Processing Characteristics:**
- Concurrent job limit: 4 workers (configurable)
- Job TTL: 24 hours (configurable)
- Progress granularity: Per paragraph/chapter
- Test mode: 5 paragraphs only

---

## 2. Business Logic & User Tiers

### Freemium Model Implementation

The service implements a freemium business model with two distinct user tiers:

#### Anonymous Users (Free Tier)
```json
{
  "authentication": "None required",
  "models_available": ["google"],
  "api_key_required": false,
  "rate_limits": "None (Google Translate is free)",
  "quality": "Basic translation",
  "features": ["All file formats", "Test mode", "Progress tracking"]
}
```

#### Registered Users (Premium Tier)
```json
{
  "authentication": "Bearer token required",
  "models_available": [
    "chatgpt", "claude", "gemini", "deepl",
    "deepl_free", "groq", "qwen", "xai"
  ],
  "api_key_required": true,
  "rate_limits": "Per-user configurable",
  "quality": "High-quality AI translation",
  "features": ["All free features", "Premium models", "API management"]
}
```

### Authentication Flow

**For Anonymous Users:**
```javascript
// No authentication needed
const response = await fetch('/translate', {
  method: 'POST',
  body: formData // model: 'google'
});
```

**For Premium Users:**
```javascript
// Bearer token required for premium models
const response = await fetch('/translate', {
  method: 'POST',
  headers: {
    'Authorization': 'Bearer bbm_your_api_key_here'
  },
  body: formData // model: 'chatgpt', 'claude', etc.
});
```

### Business Logic Validation

The backend enforces business rules automatically:

1. **Model Access Control**: Premium models require authentication
2. **API Key Validation**: Translation service keys validated per model
3. **Rate Limiting**: Per-user limits for premium tiers
4. **Usage Tracking**: Per-key usage statistics

---

## 3. API Contract Specifications

### Core Endpoints

#### POST /translate - Start Translation Job

**Request Format:**
```http
POST /translate HTTP/1.1
Content-Type: multipart/form-data
Authorization: Bearer bbm_api_key (optional, required for premium models)

file: [EPUB/TXT/SRT/MD file, max 500KB]
model: "google" | "chatgpt" | "claude" | "gemini" | "deepl" | "deepl_free" | "groq" | "qwen" | "xai"
key: "translation_service_api_key" (default: "no-key-required")
language: "zh-cn" (default, target language code)
is_test: false (boolean, test mode with 5 paragraphs)
temperature: 1.0 (0.0-2.0, creativity level)
model_api_base: "https://custom-api.com" (optional)
resume: false (resume previous translation)
single_translate: false (single vs dual language output)
context_flag: false (use context for translation)
source_lang: "auto" (source language detection)
```

**Response Format:**
```json
{
  "job_id": "uuid-string",
  "status": "pending",
  "message": "Translation job started successfully",
  "estimated_duration": "5-30 minutes depending on file size and model"
}
```

**Error Responses:**
```json
// File validation error (400)
{
  "error": "File validation failed",
  "detail": "File too large: 1.2MB exceeds 0.5MB limit",
  "timestamp": "2024-01-15T10:30:00Z"
}

// Authentication required (401)
{
  "error": "Authentication required",
  "detail": "Authentication required for premium models (chatgpt, claude, gemini, deepl, groq, qwen, xai). Available without auth: Google Translate (free)",
  "timestamp": "2024-01-15T10:30:00Z"
}

// Invalid API key (400)
{
  "error": "Invalid API key",
  "detail": "Translation service API key is required for chatgpt",
  "timestamp": "2024-01-15T10:30:00Z"
}
```

#### GET /status/{job_id} - Check Job Progress

**Response Format:**
```json
{
  "job_id": "uuid-string",
  "status": "processing", // pending|processing|completed|failed|cancelled
  "progress": 45, // 0-100 percentage
  "filename": "book.epub",
  "created_at": "2024-01-15T10:30:00Z",
  "completed_at": null,
  "error_message": null,
  "download_url": null, // Available when status=completed
  "total_paragraphs": 150,
  "processed_paragraphs": 67,
  "model": "chatgpt",
  "target_language": "zh-cn"
}
```

**Real-time Progress Example:**
```json
// Initial state
{"status": "pending", "progress": 0}

// Processing
{"status": "processing", "progress": 23, "processed_paragraphs": 34, "total_paragraphs": 148}

// Completed
{"status": "completed", "progress": 100, "download_url": "/download/uuid-string"}

// Failed
{"status": "failed", "error_message": "API key invalid or expired"}
```

#### GET /download/{job_id} - Download Result

**Response:**
- Content-Type: application/epub+zip | text/plain | application/x-subrip | text/markdown
- Content-Disposition: attachment; filename="book_bilingual.epub"
- File stream with translated content

#### Additional Endpoints

```http
GET /health          # Service health check
GET /jobs            # List user jobs (with pagination)
POST /cancel/{job_id} # Cancel running job
DELETE /jobs/{job_id} # Delete completed job
GET /models          # List available models
GET /stats           # System statistics
```

---

## 4. User Experience Flows

### Flow 1: Anonymous User Translation

**UX Design Considerations:**
1. **No signup friction** - Users can start immediately
2. **Clear model limitation** - Google Translate only
3. **Progress feedback** - Real-time progress bar
4. **File constraints** - Clear 500KB limit messaging

```mermaid
sequenceDiagram
    participant U as User
    participant F as Frontend
    participant B as Backend

    U->>F: Upload EPUB file
    F->>F: Validate file size/format client-side
    F->>B: POST /translate (model: google)
    B->>B: Validate file, create job
    B-->>F: 200 {job_id, status: pending}
    F->>F: Show "Translation starting..."

    loop Every 2-3 seconds
        F->>B: GET /status/{job_id}
        B-->>F: {status: processing, progress: 45%}
        F->>F: Update progress bar
    end

    B-->>F: {status: completed, download_url}
    F->>F: Show "Translation complete!"
    U->>F: Click download
    F->>B: GET /download/{job_id}
    B-->>F: Translated file
    F->>U: Download bilingual book
```

**Key UX Timing Expectations:**
- **Job creation**: < 500ms
- **Status polling**: Every 2-3 seconds
- **Translation time**: 3-15 minutes for typical EPUB
- **Download**: Immediate once completed

### Flow 2: Premium User Translation

**UX Design Considerations:**
1. **Authentication flow** - API key entry/management
2. **Model selection** - Dropdown with quality descriptions
3. **API key validation** - Real-time feedback
4. **Advanced options** - Collapsible settings panel

```mermaid
sequenceDiagram
    participant U as User
    participant F as Frontend
    participant B as Backend

    U->>F: Enter API key & select model
    F->>F: Store API key securely
    U->>F: Upload file, select "Claude"
    F->>B: POST /translate (Authorization: Bearer key)

    alt Valid authentication & API key
        B-->>F: 200 {job_id, estimated_duration}
        F->>F: Show enhanced progress with model info
    else Authentication failed
        B-->>F: 401 {error: "Authentication required"}
        F->>F: Show upgrade prompt
    else Invalid translation API key
        B-->>F: 400 {error: "Invalid Claude API key"}
        F->>F: Show API key help
    end
```

### Flow 3: Error Recovery Patterns

**File Validation Errors:**
```javascript
// Handle client-side validation
function validateFile(file) {
  const maxSize = 500 * 1024 * 1024; // 500KB
  const allowedTypes = ['.epub', '.txt', '.srt', '.md'];

  if (file.size > maxSize) {
    return {
      valid: false,
      message: `File too large (${(file.size/1024/1024).toFixed(1)}MB). Maximum size is 500KB.`
    };
  }

  const extension = file.name.toLowerCase().split('.').pop();
  if (!allowedTypes.includes('.' + extension)) {
    return {
      valid: false,
      message: `Unsupported format (.${extension}). Supported: EPUB, TXT, SRT, MD`
    };
  }

  return {valid: true};
}
```

**Translation Failure Recovery:**
```javascript
// Handle various failure modes
function handleTranslationError(error) {
  switch(error.status) {
    case 401:
      showAuthenticationModal();
      break;
    case 400:
      if (error.detail.includes('API key')) {
        showAPIKeyHelp();
      } else {
        showFileValidationError(error.detail);
      }
      break;
    case 429:
      showRateLimitMessage(error.headers['Retry-After']);
      break;
    case 500:
      showRetryOption();
      break;
  }
}
```

---

## 5. Error Handling Patterns

### Error Categories & Frontend Responses

#### 1. File Validation Errors (400 Bad Request)
```json
{
  "error": "File validation failed",
  "detail": "File too large: 1.2MB exceeds 0.5MB limit"
}
```

**UX Response:**
- Show inline validation message
- Highlight file upload area in red
- Provide clear remediation steps
- Don't block other functionality

#### 2. Authentication Errors (401 Unauthorized)
```json
{
  "error": "Authentication required",
  "detail": "Authentication required for premium models"
}
```

**UX Response:**
- Show authentication modal/form
- Explain benefit of premium models
- Offer fallback to Google Translate
- Preserve user's file selection

#### 3. Translation Service Errors (400 Bad Request)
```json
{
  "error": "Translation service error",
  "detail": "Invalid OpenAI API key"
}
```

**UX Response:**
- Show API key validation error
- Link to service API key documentation
- Suggest trying different model
- Validate keys before job submission

#### 4. Rate Limiting (429 Too Many Requests)
```json
{
  "error": "Rate limit exceeded",
  "detail": "Rate limit exceeded. Please try again later.",
  "headers": {"Retry-After": "3600"}
}
```

**UX Response:**
- Show countdown timer
- Explain rate limiting
- Suggest model alternatives
- Queue request for retry

#### 5. Job Processing Errors (Job Status: failed)
```json
{
  "status": "failed",
  "error_message": "Translation model timeout after 30 minutes"
}
```

**UX Response:**
- Show specific failure reason
- Offer retry with different model
- Suggest test mode for large files
- Preserve user settings for retry

### Error Recovery Strategies

**Progressive Degradation:**
1. Try premium model → fallback to Google Translate
2. Try full translation → suggest test mode
3. Try current settings → reset to defaults

**User Communication:**
```javascript
const errorMessages = {
  'file_too_large': 'Your file is larger than our 500KB limit. Try compressing or splitting it.',
  'invalid_format': 'We support EPUB, TXT, SRT, and MD files. Please convert your file.',
  'api_key_invalid': 'Your API key seems to be invalid. Check your account settings.',
  'rate_limited': 'You\'ve hit your hourly limit. Upgrade for higher limits or try again later.',
  'translation_timeout': 'Translation took too long. Try test mode first or use a different model.'
};
```

---

## 6. Real-time Communication

### Progress Tracking Implementation

The backend doesn't use WebSockets - instead it uses efficient polling with tqdm interception for granular progress updates.

#### Polling Strategy

**Recommended Polling Pattern:**
```javascript
class TranslationProgressTracker {
  constructor(jobId) {
    this.jobId = jobId;
    this.pollInterval = 2000; // Start with 2 seconds
    this.maxPollInterval = 10000; // Max 10 seconds
    this.backoffMultiplier = 1.2;
  }

  async startPolling() {
    while (true) {
      try {
        const status = await this.checkStatus();

        if (status.status === 'completed') {
          this.onComplete(status);
          break;
        }

        if (status.status === 'failed') {
          this.onError(status);
          break;
        }

        this.onProgress(status);
        await this.sleep(this.pollInterval);

        // Adaptive polling - slow down if no progress
        if (this.lastProgress === status.progress) {
          this.pollInterval = Math.min(
            this.pollInterval * this.backoffMultiplier,
            this.maxPollInterval
          );
        } else {
          this.pollInterval = 2000; // Reset to fast polling
        }

        this.lastProgress = status.progress;

      } catch (error) {
        this.onError({error_message: error.message});
        break;
      }
    }
  }

  async checkStatus() {
    const response = await fetch(`/status/${this.jobId}`);
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    return response.json();
  }
}
```

#### Progress Granularity

The backend provides detailed progress information:

```json
{
  "progress": 67, // Overall percentage 0-100
  "total_paragraphs": 450, // Total units to process
  "processed_paragraphs": 301, // Units completed
  "status": "processing" // Current stage
}
```

**UX Implementation:**
```javascript
function updateProgressUI(status) {
  // Main progress bar
  document.getElementById('progress-bar').style.width = `${status.progress}%`;

  // Detailed progress text
  document.getElementById('progress-text').textContent =
    `Processing ${status.processed_paragraphs} of ${status.total_paragraphs} paragraphs (${status.progress}%)`;

  // Estimated time remaining (calculate based on progress rate)
  const timeRemaining = this.calculateETA(status);
  document.getElementById('eta').textContent = `About ${timeRemaining} remaining`;
}
```

#### Performance Optimizations

**Efficient Polling:**
- Start with 2-second intervals during active processing
- Back off to 10-second intervals during stall periods
- Stop polling immediately on completion/failure
- Use `If-Modified-Since` headers to reduce bandwidth

**Battery/Performance Considerations:**
```javascript
// Pause polling when tab/window is not visible
document.addEventListener('visibilitychange', () => {
  if (document.hidden) {
    this.pausePolling();
  } else {
    this.resumePolling();
  }
});
```

---

## 7. Security Model

### Authentication Architecture

**API Key Format:**
- Prefix: `bbm_` (Bilingual Book Maker)
- Length: 44 characters total
- Generation: Cryptographically secure random tokens
- Storage: SHA-256 hashed in backend

**Token Validation:**
```http
Authorization: Bearer bbm_abcdef1234567890abcdef1234567890abcdef12
```

### File Upload Security

The backend implements comprehensive security validation:

#### 1. File Content Validation
```javascript
// These validations happen automatically on backend
const securityValidations = {
  'file_size_check': 'Files limited to 500KB',
  'extension_validation': 'Only .epub, .txt, .srt, .md allowed',
  'magic_bytes_check': 'File headers validated against claimed type',
  'content_scanning': 'Malicious pattern detection',
  'path_traversal_prevention': 'Filename sanitization',
  'mime_type_validation': 'Content-Type header validation'
};
```

#### 2. Frontend Security Responsibilities
```javascript
// Client-side validation (supplement, not replacement)
function secureFileHandling(file) {
  // Validate on client-side for immediate feedback
  const validation = validateFile(file);
  if (!validation.valid) {
    throw new Error(validation.message);
  }

  // Never trust client-side validation alone
  // Backend will re-validate everything

  // Sanitize filename for display
  const displayName = sanitizeDisplayName(file.name);

  return {file, displayName};
}

function sanitizeDisplayName(filename) {
  // Remove path separators and limit length for display
  return filename.replace(/[\/\\]/g, '').slice(0, 50);
}
```

### Data Protection

**File Storage Security:**
- Temporary upload files with unique UUIDs
- Automatic cleanup after TTL expires
- No permanent storage of user files
- Output files auto-deleted after 24 hours

**API Security Headers:**
```http
X-Content-Type-Options: nosniff
X-Frame-Options: DENY
X-XSS-Protection: 1; mode=block
Referrer-Policy: strict-origin-when-cross-origin
Content-Security-Policy: default-src 'self'
```

### CORS Configuration

**Development (localhost:8000):**
```json
{
  "allowed_origins": ["http://localhost:3000", "http://localhost:8080"],
  "allowed_methods": ["*"],
  "allowed_headers": ["*"]
}
```

**Production:**
```json
{
  "allowed_origins": ["https://yourdomain.com"],
  "allowed_methods": ["GET", "POST", "DELETE", "OPTIONS"],
  "allowed_headers": ["Content-Type", "Authorization", "Accept"]
}
```

---

## 8. Performance Characteristics

### Response Time Expectations

#### API Response Times
```json
{
  "endpoint": "/translate",
  "typical_response_time": "< 500ms",
  "includes": "File validation, job creation, response generation"
}
```

```json
{
  "endpoint": "/status/{job_id}",
  "typical_response_time": "< 100ms",
  "includes": "Job status lookup, progress calculation"
}
```

```json
{
  "endpoint": "/download/{job_id}",
  "typical_response_time": "< 200ms to start",
  "includes": "File stream initiation (streaming continues)"
}
```

#### Translation Processing Times

**File Size vs Processing Time:**
```javascript
const processingTimeEstimates = {
  'small_file': {
    'size_range': '< 50KB',
    'paragraphs': '< 100',
    'google_translate': '1-3 minutes',
    'premium_models': '2-5 minutes'
  },
  'medium_file': {
    'size_range': '50KB - 200KB',
    'paragraphs': '100-400',
    'google_translate': '3-8 minutes',
    'premium_models': '5-15 minutes'
  },
  'large_file': {
    'size_range': '200KB - 500KB',
    'paragraphs': '400-1000+',
    'google_translate': '8-20 minutes',
    'premium_models': '15-30 minutes'
  }
};
```

### Scaling Characteristics

**Concurrent Processing:**
- **Job limit**: 4 simultaneous translations (configurable)
- **Queue behavior**: Additional jobs queue in PENDING status
- **Memory usage**: ~50-100MB per active translation job

**Rate Limiting:**
```json
{
  "anonymous_users": "No limits (Google Translate only)",
  "premium_users": "60 requests/hour default (configurable per key)"
}
```

### Frontend Performance Optimization

#### File Upload Optimization
```javascript
// Show progress during file upload
function uploadFileWithProgress(file, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();

    xhr.upload.addEventListener('progress', (e) => {
      if (e.lengthComputable) {
        const percentComplete = (e.loaded / e.total) * 100;
        onProgress(percentComplete, 'upload');
      }
    });

    xhr.addEventListener('load', () => {
      if (xhr.status === 200) {
        resolve(JSON.parse(xhr.responseText));
      } else {
        reject(new Error(`Upload failed: ${xhr.status}`));
      }
    });

    // Set up form data and send
    const formData = new FormData();
    formData.append('file', file);
    // ... other form fields

    xhr.open('POST', '/translate');
    xhr.send(formData);
  });
}
```

#### Progress UI Optimization
```css
/* Smooth progress animations */
.progress-bar {
  transition: width 0.3s ease-in-out;
  will-change: width;
}

.progress-text {
  font-variant-numeric: tabular-nums; /* Prevents text jumping */
}
```

---

## 9. Integration Guidelines

### Frontend Architecture Recommendations

#### State Management
```javascript
// Recommended state structure for translation jobs
const translationState = {
  currentJob: {
    id: null,
    status: 'idle', // idle|uploading|pending|processing|completed|failed
    progress: 0,
    file: null,
    settings: {
      model: 'google',
      language: 'zh-cn',
      isTest: false
    },
    result: null,
    error: null
  },
  jobHistory: [], // Previous jobs for user
  apiKey: null, // For premium users
  availableModels: [] // Fetched from /models
};
```

#### Component Architecture
```jsx
// React example structure
const TranslationApp = () => {
  return (
    <div>
      <FileUpload onFileSelect={handleFileSelect} />
      <ModelSelector
        models={availableModels}
        selectedModel={settings.model}
        onChange={handleModelChange}
      />
      <AuthenticationPanel
        show={requiresAuth}
        onApiKeyEntered={handleApiKey}
      />
      <TranslationProgress
        job={currentJob}
        onCancel={handleCancel}
      />
      <ResultDownload
        job={currentJob}
        onDownload={handleDownload}
      />
    </div>
  );
};
```

### API Integration Patterns

#### Error-First Approach
```javascript
async function startTranslation(file, settings) {
  try {
    // Validate before sending
    const validation = validateTranslationRequest(file, settings);
    if (!validation.valid) {
      throw new ValidationError(validation.errors);
    }

    // Send request
    const job = await apiClient.startTranslation(file, settings);

    // Start progress tracking
    const progressTracker = new ProgressTracker(job.job_id);
    progressTracker.onProgress = updateUI;
    progressTracker.onComplete = handleComplete;
    progressTracker.onError = handleError;

    await progressTracker.start();

  } catch (error) {
    handleError(error);
  }
}
```

#### Retry Logic
```javascript
class APIClient {
  async makeRequest(url, options, retries = 3) {
    for (let attempt = 1; attempt <= retries; attempt++) {
      try {
        const response = await fetch(url, options);

        if (response.ok) {
          return response.json();
        }

        // Don't retry client errors (4xx)
        if (response.status >= 400 && response.status < 500) {
          throw new APIError(response.status, await response.json());
        }

        // Retry server errors (5xx)
        if (attempt === retries) {
          throw new APIError(response.status, 'Max retries exceeded');
        }

        // Exponential backoff
        await this.sleep(Math.pow(2, attempt) * 1000);

      } catch (error) {
        if (attempt === retries) throw error;
        await this.sleep(Math.pow(2, attempt) * 1000);
      }
    }
  }
}
```

### Testing Integration

#### Mock API for Development
```javascript
// Mock API responses for frontend development
const mockAPI = {
  '/translate': {
    method: 'POST',
    response: {
      job_id: 'mock-job-123',
      status: 'pending',
      message: 'Translation job started successfully',
      estimated_duration: '5-10 minutes (mock)'
    }
  },
  '/status/mock-job-123': {
    method: 'GET',
    response: (callCount) => ({
      job_id: 'mock-job-123',
      status: callCount < 5 ? 'processing' : 'completed',
      progress: Math.min(callCount * 20, 100),
      processed_paragraphs: Math.min(callCount * 30, 150),
      total_paragraphs: 150,
      download_url: callCount >= 5 ? '/download/mock-job-123' : null
    })
  }
};
```

---

## 10. Development Environment Setup

### Backend Setup for Frontend Development

#### Quick Start (Docker)
```bash
# Clone repository
git clone <repo-url>
cd bilingual_book_maker

# Start backend with Docker
docker build -t bilingual-book-maker .
docker run -p 8000:8000 bilingual-book-maker

# Backend available at: http://localhost:8000
# API docs at: http://localhost:8000/docs
```

#### Local Development Setup
```bash
# Install dependencies
cd api_layer
pip install -r requirements.txt

# Set environment variables
export API_HOST=0.0.0.0
export API_PORT=8000
export ENVIRONMENT=development

# Start development server
python -m uvicorn api.main:app --reload --host 0.0.0.0 --port 8000
```

### Frontend Development Workflow

#### 1. API Discovery
```javascript
// First, explore available endpoints
const endpoints = [
  'GET /health',      // Check if backend is running
  'GET /models',      // Get available translation models
  'GET /stats',       // System statistics
  'POST /translate',  // Start translation
  'GET /status/{id}', // Check progress
  'GET /download/{id}' // Download result
];

// Test basic connectivity
fetch('http://localhost:8000/health')
  .then(r => r.json())
  .then(data => console.log('Backend is running:', data));
```

#### 2. Development API Key Setup
```javascript
// For testing premium features, get a demo API key
// The backend creates demo keys on startup (development only)

// Check logs for demo API keys:
// "Demo API key created: bbm_abc123..."
// "Admin API key created: bbm_xyz789..."

const DEMO_API_KEY = 'bbm_demo_key_from_logs';

// Test premium endpoints
fetch('http://localhost:8000/translate', {
  method: 'POST',
  headers: {
    'Authorization': `Bearer ${DEMO_API_KEY}`
  },
  body: formData // with model: 'chatgpt'
});
```

#### 3. File Testing Assets
```javascript
// Create test files for development
const testFiles = {
  'small_epub': 'test-files/small-book.epub', // < 50KB
  'text_file': 'test-files/sample.txt',      // Plain text
  'subtitle_file': 'test-files/movie.srt',   // SRT format
  'markdown_file': 'test-files/readme.md'    // Markdown
};

// Test different scenarios
const testScenarios = [
  {name: 'Anonymous + Google', file: testFiles.small_epub, model: 'google'},
  {name: 'Premium + ChatGPT', file: testFiles.text_file, model: 'chatgpt'},
  {name: 'Test Mode', file: testFiles.small_epub, model: 'google', is_test: true},
  {name: 'Large File Error', file: 'large-file.epub'} // > 500KB
];
```

#### 4. Error Testing
```javascript
// Test error scenarios during development
const errorTests = [
  {
    test: 'File too large',
    action: () => uploadFile(createLargeFile(1024*1024)) // 1MB file
  },
  {
    test: 'Invalid API key',
    action: () => translateWithAuth('invalid-key', 'chatgpt')
  },
  {
    test: 'Unsupported format',
    action: () => uploadFile(createFile('test.pdf'))
  },
  {
    test: 'Missing translation API key',
    action: () => translate({model: 'chatgpt', key: ''})
  }
];
```

### CORS Configuration for Development

If you're running frontend on a different port, ensure CORS is configured:

```javascript
// Backend allows these origins in development:
const devOrigins = [
  'http://localhost:3000',  // React default
  'http://localhost:8080',  // Vue/Webpack default
  'http://localhost:5173',  // Vite default
  'http://127.0.0.1:3000'   // Alternative localhost
];

// If your frontend runs on a different port, either:
// 1. Set CORS_ORIGINS environment variable:
//    CORS_ORIGINS="http://localhost:3001,http://localhost:4200"
//
// 2. Or update backend settings.py with your port
```

### Production Deployment Considerations

#### Environment Configuration
```bash
# Production environment variables
export ENVIRONMENT=production
export API_HOST=0.0.0.0
export API_PORT=8000
export CORS_ORIGINS="https://yourdomain.com,https://www.yourdomain.com"
export TRUSTED_HOSTS="yourdomain.com,www.yourdomain.com"
export JOB_TTL_HOURS=12  # Shorter TTL for production
export MAX_WORKERS=8     # Scale based on server capacity
```

#### Frontend Build Configuration
```javascript
// Update API base URL for production
const config = {
  development: {
    API_BASE_URL: 'http://localhost:8000'
  },
  production: {
    API_BASE_URL: 'https://api.yourdomain.com'
  }
};
```

---

## Summary

This comprehensive guide provides UI/UX engineers with the technical foundation needed to build effective user experiences for the bilingual book translation service. Key takeaways:

1. **Architecture**: Async job-based processing with polling for progress
2. **Business Model**: Freemium with clear authentication boundaries
3. **API Design**: RESTful with comprehensive error handling
4. **Real-time UX**: Polling-based progress with adaptive intervals
5. **Security**: Multi-layer validation with secure file handling
6. **Performance**: Predictable timing with clear user expectations
7. **Integration**: Error-first approach with comprehensive testing

The service is production-ready with robust error handling, security validation, and scalable architecture. Frontend implementations should focus on progressive enhancement, clear error communication, and responsive progress feedback to create optimal user experiences.