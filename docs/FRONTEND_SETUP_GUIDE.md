# Frontend Setup Guide - Bilingual Book Translation Service

## Table of Contents
1. [Repository Setup & Installation](#1-repository-setup--installation)
2. [Project Structure & Architecture](#2-project-structure--architecture)
3. [UI/UX Design Specifications](#3-uiux-design-specifications)
4. [Page Designs & User Flows](#4-page-designs--user-flows)
5. [Component Specifications](#5-component-specifications)
6. [Development Workflow](#6-development-workflow)
7. [Integration Guidelines](#7-integration-guidelines)

---

## 1. Repository Setup & Installation

### Tech Stack Recommendations

**Core Framework:**
- **React 18** with TypeScript for type safety
- **Vite** for fast development and building
- **React Router DOM** for client-side routing
- **React Query (TanStack Query)** for API state management

**Styling & UI:**
- **Tailwind CSS** for utility-first styling
- **Headless UI** for accessible components
- **Heroicons** for consistent icons
- **Framer Motion** for animations

**State Management:**
- **Zustand** for lightweight global state
- **React Hook Form** for form handling
- **Zod** for schema validation

**Development Tools:**
- **ESLint & Prettier** for code quality
- **Vitest** for unit testing
- **React Testing Library** for component testing
- **MSW (Mock Service Worker)** for API mocking

### Initial Setup Commands

```bash
# Create new React project with Vite
npm create vite@latest bilingual-book-frontend -- --template react-ts
cd bilingual-book-frontend

# Install core dependencies
npm install react-router-dom @tanstack/react-query axios zustand

# Install UI and styling
npm install tailwindcss @headlessui/react @heroicons/react framer-motion
npm install -D @tailwindcss/forms @tailwindcss/typography

# Install form handling and validation
npm install react-hook-form @hookform/resolvers zod

# Install development dependencies
npm install -D @types/node @vitejs/plugin-react
npm install -D eslint @typescript-eslint/eslint-plugin @typescript-eslint/parser
npm install -D prettier eslint-config-prettier eslint-plugin-prettier
npm install -D vitest @testing-library/react @testing-library/jest-dom jsdom
npm install -D msw
```

### Package.json Configuration

```json
{
  "name": "bilingual-book-frontend",
  "version": "1.0.0",
  "type": "module",
  "scripts": {
    "dev": "vite --host 0.0.0.0 --port 3000",
    "build": "tsc && vite build",
    "preview": "vite preview",
    "lint": "eslint . --ext ts,tsx --report-unused-disable-directives --max-warnings 0",
    "lint:fix": "eslint . --ext ts,tsx --fix",
    "format": "prettier --write \"src/**/*.{ts,tsx,js,jsx,json,css,md}\"",
    "test": "vitest",
    "test:ui": "vitest --ui",
    "test:coverage": "vitest --coverage"
  },
  "dependencies": {
    "react": "^18.2.0",
    "react-dom": "^18.2.0",
    "react-router-dom": "^6.15.0",
    "@tanstack/react-query": "^4.32.0",
    "axios": "^1.5.0",
    "zustand": "^4.4.1",
    "@headlessui/react": "^1.7.17",
    "@heroicons/react": "^2.0.18",
    "framer-motion": "^10.16.0",
    "tailwindcss": "^3.3.3",
    "react-hook-form": "^7.45.4",
    "@hookform/resolvers": "^3.3.1",
    "zod": "^3.22.2"
  },
  "devDependencies": {
    "@types/react": "^18.2.15",
    "@types/react-dom": "^18.2.7",
    "@typescript-eslint/eslint-plugin": "^6.0.0",
    "@typescript-eslint/parser": "^6.0.0",
    "@vitejs/plugin-react": "^4.0.3",
    "eslint": "^8.45.0",
    "eslint-plugin-react-hooks": "^4.6.0",
    "eslint-plugin-react-refresh": "^0.4.3",
    "typescript": "^5.0.2",
    "vite": "^4.4.5",
    "vitest": "^0.34.0",
    "@testing-library/react": "^13.4.0",
    "@testing-library/jest-dom": "^6.0.0",
    "jsdom": "^22.1.0",
    "msw": "^1.3.0"
  }
}
```

### Environment Setup

Create `.env.local`:
```bash
# API Configuration
VITE_API_BASE_URL=http://localhost:8000
VITE_API_TIMEOUT=30000

# Feature Flags
VITE_ENABLE_AUTH=true
VITE_ENABLE_PREMIUM=true
VITE_MAX_FILE_SIZE=524288000  # 500MB in bytes

# Development
VITE_LOG_LEVEL=debug
VITE_MOCK_API=false
```

Create `.env.example`:
```bash
# Copy this file to .env.local and configure for your environment

# API Configuration (required)
VITE_API_BASE_URL=http://localhost:8000
VITE_API_TIMEOUT=30000

# Feature Flags (optional)
VITE_ENABLE_AUTH=true
VITE_ENABLE_PREMIUM=true
VITE_MAX_FILE_SIZE=524288000

# Development (optional)
VITE_LOG_LEVEL=info
VITE_MOCK_API=false
```

### Tailwind Configuration

`tailwind.config.js`:
```js
/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        primary: {
          50: '#eff6ff',
          500: '#3b82f6',
          600: '#2563eb',
          700: '#1d4ed8',
        },
        secondary: {
          50: '#f8fafc',
          500: '#64748b',
          600: '#475569',
        },
        success: {
          50: '#f0fdf4',
          500: '#22c55e',
          600: '#16a34a',
        },
        warning: {
          50: '#fffbeb',
          500: '#f59e0b',
          600: '#d97706',
        },
        error: {
          50: '#fef2f2',
          500: '#ef4444',
          600: '#dc2626',
        }
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', 'sans-serif'],
      },
      animation: {
        'fade-in': 'fadeIn 0.5s ease-in-out',
        'slide-up': 'slideUp 0.3s ease-out',
        'pulse-custom': 'pulseCustom 2s cubic-bezier(0.4, 0, 0.6, 1) infinite',
      },
      keyframes: {
        fadeIn: {
          '0%': { opacity: '0' },
          '100%': { opacity: '1' },
        },
        slideUp: {
          '0%': { transform: 'translateY(20px)', opacity: '0' },
          '100%': { transform: 'translateY(0)', opacity: '1' },
        },
        pulseCustom: {
          '0%, 100%': { opacity: '1' },
          '50%': { opacity: '0.7' },
        }
      }
    },
  },
  plugins: [
    require('@tailwindcss/forms'),
    require('@tailwindcss/typography'),
  ],
}
```

### Vite Configuration

`vite.config.ts`:
```ts
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
      '@components': path.resolve(__dirname, './src/components'),
      '@pages': path.resolve(__dirname, './src/pages'),
      '@hooks': path.resolve(__dirname, './src/hooks'),
      '@utils': path.resolve(__dirname, './src/utils'),
      '@types': path.resolve(__dirname, './src/types'),
      '@store': path.resolve(__dirname, './src/store'),
      '@api': path.resolve(__dirname, './src/api'),
    }
  },
  server: {
    port: 3000,
    host: '0.0.0.0',
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, '')
      }
    }
  },
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
  }
})
```

---

## 2. Project Structure & Architecture

### Folder Organization

```
src/
├── api/                     # API layer and client setup
│   ├── client.ts           # Axios instance configuration
│   ├── endpoints.ts        # API endpoint definitions
│   ├── types.ts           # API request/response types
│   └── mutations.ts       # React Query mutations
├── components/             # Reusable UI components
│   ├── common/            # Generic components
│   │   ├── Button.tsx
│   │   ├── Modal.tsx
│   │   ├── LoadingSpinner.tsx
│   │   └── ErrorBoundary.tsx
│   ├── forms/             # Form-related components
│   │   ├── FileUpload.tsx
│   │   ├── ModelSelector.tsx
│   │   └── AuthForm.tsx
│   ├── layout/            # Layout components
│   │   ├── Header.tsx
│   │   ├── Sidebar.tsx
│   │   └── Footer.tsx
│   └── translation/       # Translation-specific components
│       ├── ProgressTracker.tsx
│       ├── JobCard.tsx
│       └── DownloadButton.tsx
├── hooks/                 # Custom React hooks
│   ├── useApi.ts         # API interaction hooks
│   ├── useAuth.ts        # Authentication hooks
│   ├── useTranslation.ts # Translation job hooks
│   └── useLocalStorage.ts # Local storage hooks
├── pages/                # Page components
│   ├── Home.tsx          # Landing page
│   ├── Dashboard.tsx     # User dashboard
│   ├── Login.tsx         # Authentication page
│   ├── Translation.tsx   # Translation interface
│   └── Settings.tsx      # User settings
├── store/                # State management
│   ├── authStore.ts      # Authentication state
│   ├── translationStore.ts # Translation jobs state
│   └── uiStore.ts        # UI state
├── types/                # TypeScript type definitions
│   ├── api.ts            # API types
│   ├── user.ts           # User types
│   └── translation.ts    # Translation types
├── utils/                # Utility functions
│   ├── formatters.ts     # Data formatting
│   ├── validators.ts     # Validation functions
│   ├── constants.ts      # App constants
│   └── helpers.ts        # General helpers
├── styles/               # Global styles
│   ├── globals.css       # Global CSS
│   └── components.css    # Component-specific CSS
├── test/                 # Test utilities
│   ├── setup.ts          # Test setup
│   ├── mocks/            # Mock data
│   └── utils.ts          # Test utilities
├── App.tsx               # Root component
├── main.tsx              # Application entry point
└── router.tsx            # Route configuration
```

### Component Hierarchy

```
App
├── Router
│   ├── Layout
│   │   ├── Header
│   │   │   ├── Navigation
│   │   │   ├── UserMenu
│   │   │   └── AuthButton
│   │   ├── Main Content
│   │   │   └── [Page Components]
│   │   └── Footer
│   └── Error Boundary
└── Query Client Provider
    └── Store Providers
```

### State Management Approach

**1. Authentication State (Zustand)**
```ts
interface AuthState {
  user: User | null
  token: string | null
  isAuthenticated: boolean
  login: (credentials: LoginCredentials) => Promise<void>
  logout: () => void
  register: (data: RegisterData) => Promise<void>
}
```

**2. Translation Jobs State (Zustand + React Query)**
```ts
interface TranslationState {
  activeJobs: TranslationJob[]
  completedJobs: TranslationJob[]
  addJob: (job: TranslationJob) => void
  updateJob: (jobId: string, updates: Partial<TranslationJob>) => void
  removeJob: (jobId: string) => void
}
```

**3. UI State (Zustand)**
```ts
interface UIState {
  theme: 'light' | 'dark'
  sidebarOpen: boolean
  notifications: Notification[]
  toggleSidebar: () => void
  addNotification: (notification: Notification) => void
  removeNotification: (id: string) => void
}
```

### API Integration Patterns

**1. API Client Setup**
```ts
// src/api/client.ts
import axios from 'axios'
import { authStore } from '@store/authStore'

const apiClient = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL,
  timeout: parseInt(import.meta.env.VITE_API_TIMEOUT) || 30000,
})

// Request interceptor for auth
apiClient.interceptors.request.use((config) => {
  const token = authStore.getState().token
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

// Response interceptor for error handling
apiClient.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      authStore.getState().logout()
    }
    return Promise.reject(error)
  }
)
```

**2. React Query Integration**
```ts
// src/hooks/useTranslation.ts
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { translationApi } from '@api/endpoints'

export const useStartTranslation = () => {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: translationApi.startTranslation,
    onSuccess: (data) => {
      queryClient.invalidateQueries(['jobs'])
      // Add to active jobs
    },
    onError: (error) => {
      // Handle error
    }
  })
}

export const useJobStatus = (jobId: string) => {
  return useQuery({
    queryKey: ['job', jobId],
    queryFn: () => translationApi.getJobStatus(jobId),
    enabled: !!jobId,
    refetchInterval: 2000, // Poll every 2 seconds
  })
}
```

---

## 3. UI/UX Design Specifications

### Freemium User Experience Design

**Anonymous Users (Free Tier):**
- Immediate access to Google Translate
- No registration required
- Limited to 3 files per session
- File size limit: 500KB
- Basic progress tracking
- Download links expire in 1 hour

**Registered Users (Premium Tier):**
- Access to all translation models (ChatGPT, Claude, Gemini, DeepL)
- API key management interface
- Unlimited file uploads
- Job history and management
- Advanced progress tracking with ETA
- Persistent download links

### Color Scheme & Design System

**Primary Colors:**
- Brand Blue: `#3b82f6` (Trustworthy, professional)
- Success Green: `#22c55e` (Completed translations)
- Warning Orange: `#f59e0b` (Processing states)
- Error Red: `#ef4444` (Failed translations)

**Neutral Colors:**
- Background: `#f8fafc` (Light gray)
- Surface: `#ffffff` (Pure white)
- Text Primary: `#1e293b` (Dark gray)
- Text Secondary: `#64748b` (Medium gray)
- Border: `#e2e8f0` (Light border)

**Typography Scale:**
- Heading 1: `3rem` (48px) - Hero titles
- Heading 2: `2.25rem` (36px) - Page titles
- Heading 3: `1.875rem` (30px) - Section titles
- Body Large: `1.125rem` (18px) - Important text
- Body: `1rem` (16px) - Default text
- Body Small: `0.875rem` (14px) - Secondary text
- Caption: `0.75rem` (12px) - Captions, labels

**Spacing System:**
- xs: `0.25rem` (4px)
- sm: `0.5rem` (8px)
- base: `1rem` (16px)
- lg: `1.5rem` (24px)
- xl: `2rem` (32px)
- 2xl: `3rem` (48px)
- 3xl: `4rem` (64px)

### Component Design Guidelines

**Buttons:**
```css
/* Primary Button */
.btn-primary {
  @apply bg-primary-600 hover:bg-primary-700 text-white font-medium py-2 px-4 rounded-lg transition-colors duration-200;
}

/* Secondary Button */
.btn-secondary {
  @apply bg-white hover:bg-gray-50 text-gray-700 font-medium py-2 px-4 rounded-lg border border-gray-300 transition-colors duration-200;
}

/* Danger Button */
.btn-danger {
  @apply bg-error-600 hover:bg-error-700 text-white font-medium py-2 px-4 rounded-lg transition-colors duration-200;
}
```

**Cards:**
```css
.card {
  @apply bg-white rounded-xl shadow-sm border border-gray-200 p-6;
}

.card-hover {
  @apply hover:shadow-md transition-shadow duration-200;
}
```

**Form Elements:**
```css
.form-input {
  @apply w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-primary-500;
}

.form-label {
  @apply block text-sm font-medium text-gray-700 mb-1;
}
```

### Responsive Design Requirements

**Breakpoints:**
- Mobile: `< 640px`
- Tablet: `640px - 1024px`
- Desktop: `> 1024px`

**Mobile-First Approach:**
- Stack layouts vertically on mobile
- Hide secondary navigation on mobile
- Use full-width cards and buttons
- Implement touch-friendly interactions
- Optimize file upload for mobile browsers

**Component Responsiveness:**
```css
/* Navigation */
.nav-desktop {
  @apply hidden md:flex;
}

.nav-mobile {
  @apply flex md:hidden;
}

/* Grid Layouts */
.job-grid {
  @apply grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6;
}

/* Text Sizing */
.responsive-heading {
  @apply text-2xl md:text-3xl lg:text-4xl;
}
```

---

## 4. Page Designs & User Flows

### Home Page Layout (Anonymous Users)

```
┌─────────────────────────────────────────────────────────────────┐
│                        Header                                    │
│  Logo    Navigation    [Login] [Sign Up]                       │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│                       Hero Section                              │
│     "Transform Any Book into Bilingual Text"                   │
│       Try Free with Google Translate                           │
│                                                                  │
│              [Start Translation]                                │
│                                                                  │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│                    Quick Upload Section                         │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │           Drag & Drop File Upload                       │   │
│  │        Support: EPUB, TXT, SRT, MD                     │   │
│  │               Max 500KB                                 │   │
│  │                                                         │   │
│  │         [Browse Files] or Drag Here                    │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                  │
│     ┌─────────────┐  ┌─────────────┐                          │
│     │ Model       │  │ Language    │                          │
│     │ [Google ▼]  │  │ [Chinese ▼] │                          │
│     └─────────────┘  └─────────────┘                          │
│                                                                  │
│              [Translate Now - Free]                             │
│                                                                  │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│                    Features Section                             │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐      │
│  │   Fast   │  │   Free   │  │ Multiple │  │ Premium  │      │
│  │ Process  │  │ Google   │  │ Formats  │  │ Options  │      │
│  │          │  │Translate │  │          │  │Available │      │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘      │
│                                                                  │
├─────────────────────────────────────────────────────────────────┤
│                       Footer                                    │
│         Privacy | Terms | Support | API Docs                  │
└─────────────────────────────────────────────────────────────────┘
```

### Dashboard Page Layout (Registered Users)

```
┌─────────────────────────────────────────────────────────────────┐
│                        Header                                    │
│  Logo    Dashboard Jobs Settings    UserMenu [Avatar ▼]        │
├─────┬───────────────────────────────────────────────────────────┤
│     │                    Main Content                           │
│ S   │  ┌─────────────────────────────────────────────────────┐  │
│ i   │  │                Quick Stats                          │  │
│ d   │  │  Active: 2  Completed: 15  Failed: 1              │  │
│ e   │  └─────────────────────────────────────────────────────┘  │
│ b   │                                                           │
│ a   │  ┌─────────────────────────────────────────────────────┐  │
│ r   │  │               New Translation                       │  │
│     │  │  [File Upload Area]                                │  │
│ -   │  │  Model: [ChatGPT ▼] Language: [Chinese ▼]        │  │
│ R   │  │  API Key: [Configure Keys] or [Use Saved]         │  │
│ e   │  │                                                     │  │
│ c   │  │              [Start Translation]                   │  │
│ e   │  └─────────────────────────────────────────────────────┘  │
│ n   │                                                           │
│ t   │  ┌─────────────────────────────────────────────────────┐  │
│     │  │                Active Jobs                          │  │
│ J   │  │  ┌─────────────────────────────────────────────┐   │  │
│ o   │  │  │ book.epub → Chinese                          │   │  │
│ b   │  │  │ Progress: ████████░░ 80%                     │   │  │
│ s   │  │  │ ETA: 5 minutes                               │   │  │
│     │  │  │                          [Cancel] [Details] │   │  │
│ -   │  │  └─────────────────────────────────────────────┘   │  │
│     │  │                                                     │  │
│ M   │  │  ┌─────────────────────────────────────────────┐   │  │
│ o   │  │  │ document.txt → Spanish                       │   │  │
│ d   │  │  │ Progress: ██░░░░░░░░ 20%                     │   │  │
│ e   │  │  │ ETA: 12 minutes                              │   │  │
│ l   │  │  │                          [Cancel] [Details] │   │  │
│ s   │  │  └─────────────────────────────────────────────┘   │  │
│     │  └─────────────────────────────────────────────────────┘  │
│ -   │                                                           │
│     │  ┌─────────────────────────────────────────────────────┐  │
│ S   │  │               Completed Jobs                        │  │
│ e   │  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐ │  │
│ t   │  │  │ novel.epub  │  │ guide.txt   │  │ script.srt  │ │  │
│ t   │  │  │ ✓ Chinese   │  │ ✓ French    │  │ ✓ German    │ │  │
│ i   │  │  │ 2 hrs ago   │  │ 1 day ago   │  │ 3 days ago  │ │  │
│ n   │  │  │ [Download]  │  │ [Download]  │  │ [Download]  │ │  │
│ g   │  │  └─────────────┘  └─────────────┘  └─────────────┘ │  │
│ s   │  └─────────────────────────────────────────────────────┘  │
│     │                                                           │
├─────┴───────────────────────────────────────────────────────────┤
│                       Footer                                    │
└─────────────────────────────────────────────────────────────────┘
```

### Translation Progress Interface

```
┌─────────────────────────────────────────────────────────────────┐
│                     Translation Details                         │
│  book.epub → Chinese (Simplified)                              │
│  Model: ChatGPT-4 | Started: 2:30 PM                          │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│                    Progress Overview                            │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  Overall Progress                                       │   │
│  │  ████████████████████████████░░░░░░░░░░ 75%             │   │
│  │                                                         │   │
│  │  Processed: 150 / 200 paragraphs                       │   │
│  │  Speed: 45 paragraphs/min                              │   │
│  │  ETA: 3 minutes remaining                               │   │
│  │                                                         │   │
│  │  Current: Chapter 8 - "The Journey Begins"             │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                  │
│                    Live Translation Preview                     │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  English: "The morning sun cast long shadows..."       │   │
│  │  ────────────────────────────────────────────────────── │   │
│  │  Chinese: "晨光投下长长的阴影..."                         │   │
│  │                                                         │   │
│  │  English: "She walked slowly through the garden..."    │   │
│  │  ────────────────────────────────────────────────────── │   │
│  │  Chinese: "她慢慢地走过花园..."                          │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                  │
│                      Action Buttons                            │
│              [Pause Translation] [Cancel] [Details]            │
│                                                                  │
├─────────────────────────────────────────────────────────────────┤
│                      Statistics Panel                          │
│  Words Translated: 2,847    Quality Score: 92%                │
│  Cost Estimate: $1.20       Time Elapsed: 8:45                │
└─────────────────────────────────────────────────────────────────┘
```

### Complete User Journey Mapping

**Anonymous User Flow:**
```
1. Landing Page
   ↓ [Upload File]
2. File Upload
   ↓ [Select Google Translate + Language]
3. Translation Progress
   ↓ [Wait for completion]
4. Download Page
   ↓ [Optional: Sign up for premium]
5. Registration Prompt
```

**Registered User Flow:**
```
1. Login/Dashboard
   ↓ [New Translation]
2. Advanced Upload
   ↓ [Select Premium Model + API Key]
3. Job Queue Management
   ↓ [Monitor multiple jobs]
4. Enhanced Progress Tracking
   ↓ [Real-time updates + ETA]
5. Job History & Management
```

**First-Time User Onboarding:**
```
1. Welcome Modal
   ↓ [Try Free or Sign Up]
2. Feature Tour (3 steps)
   ↓ [File formats, Models, Progress]
3. Quick Start Tutorial
   ↓ [Upload sample file]
4. Success & Benefits
   ↓ [Show premium features]
```

---

## 5. Component Specifications

### File Upload Component with Drag & Drop

```tsx
// src/components/forms/FileUpload.tsx
import React, { useCallback, useState } from 'react'
import { useDropzone } from 'react-dropzone'
import { CloudArrowUpIcon, DocumentIcon, XMarkIcon } from '@heroicons/react/24/outline'
import { motion, AnimatePresence } from 'framer-motion'

interface FileUploadProps {
  onFileSelect: (file: File) => void
  maxSize?: number
  acceptedFormats?: string[]
  disabled?: boolean
}

export const FileUpload: React.FC<FileUploadProps> = ({
  onFileSelect,
  maxSize = 500 * 1024 * 1024, // 500MB
  acceptedFormats = ['.epub', '.txt', '.srt', '.md'],
  disabled = false
}) => {
  const [selectedFile, setSelectedFile] = useState<File | null>(null)
  const [error, setError] = useState<string>('')

  const onDrop = useCallback((acceptedFiles: File[], rejectedFiles: any[]) => {
    setError('')

    if (rejectedFiles.length > 0) {
      const rejection = rejectedFiles[0]
      if (rejection.errors[0]?.code === 'file-too-large') {
        setError(`File too large. Maximum size is ${formatFileSize(maxSize)}`)
      } else if (rejection.errors[0]?.code === 'file-invalid-type') {
        setError(`Invalid file type. Supported formats: ${acceptedFormats.join(', ')}`)
      }
      return
    }

    if (acceptedFiles.length > 0) {
      const file = acceptedFiles[0]
      setSelectedFile(file)
      onFileSelect(file)
    }
  }, [maxSize, acceptedFormats, onFileSelect])

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    maxSize,
    accept: {
      'application/epub+zip': ['.epub'],
      'text/plain': ['.txt'],
      'text/srt': ['.srt'],
      'text/markdown': ['.md']
    },
    multiple: false,
    disabled
  })

  const removeFile = () => {
    setSelectedFile(null)
    setError('')
  }

  return (
    <div className="w-full">
      <motion.div
        {...getRootProps()}
        className={cn(
          "relative border-2 border-dashed rounded-xl p-8 text-center cursor-pointer transition-all duration-200",
          isDragActive
            ? "border-primary-400 bg-primary-50"
            : "border-gray-300 hover:border-primary-400 hover:bg-gray-50",
          disabled && "opacity-50 cursor-not-allowed",
          error && "border-error-400 bg-error-50"
        )}
        whileHover={!disabled ? { scale: 1.02 } : {}}
        whileTap={!disabled ? { scale: 0.98 } : {}}
      >
        <input {...getInputProps()} />

        <AnimatePresence mode="wait">
          {selectedFile ? (
            <motion.div
              key="file-selected"
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -20 }}
              className="flex items-center justify-center space-x-4"
            >
              <DocumentIcon className="h-12 w-12 text-primary-600" />
              <div className="flex-1 text-left">
                <p className="font-medium text-gray-900">{selectedFile.name}</p>
                <p className="text-sm text-gray-500">{formatFileSize(selectedFile.size)}</p>
              </div>
              <button
                onClick={(e) => {
                  e.stopPropagation()
                  removeFile()
                }}
                className="p-2 hover:bg-gray-100 rounded-full transition-colors"
              >
                <XMarkIcon className="h-5 w-5 text-gray-400" />
              </button>
            </motion.div>
          ) : (
            <motion.div
              key="upload-area"
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -20 }}
            >
              <CloudArrowUpIcon
                className={cn(
                  "mx-auto h-16 w-16 mb-4",
                  isDragActive ? "text-primary-500" : "text-gray-400"
                )}
              />
              <p className="text-lg font-medium text-gray-900 mb-2">
                {isDragActive ? "Drop your file here" : "Choose a file to translate"}
              </p>
              <p className="text-sm text-gray-500 mb-4">
                Drag and drop your file here, or click to browse
              </p>
              <div className="flex justify-center space-x-4 text-xs text-gray-400">
                <span>EPUB</span>
                <span>TXT</span>
                <span>SRT</span>
                <span>MD</span>
              </div>
              <p className="text-xs text-gray-400 mt-2">
                Maximum file size: {formatFileSize(maxSize)}
              </p>
            </motion.div>
          )}
        </AnimatePresence>
      </motion.div>

      {error && (
        <motion.div
          initial={{ opacity: 0, y: -10 }}
          animate={{ opacity: 1, y: 0 }}
          className="mt-2 text-sm text-error-600 bg-error-50 px-3 py-2 rounded-lg"
        >
          {error}
        </motion.div>
      )}
    </div>
  )
}

const formatFileSize = (bytes: number): string => {
  if (bytes === 0) return '0 Bytes'
  const k = 1024
  const sizes = ['Bytes', 'KB', 'MB', 'GB']
  const i = Math.floor(Math.log(bytes) / Math.log(k))
  return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i]
}
```

### Model Selector Component

```tsx
// src/components/forms/ModelSelector.tsx
import React from 'react'
import { Listbox, Transition } from '@headlessui/react'
import { CheckIcon, ChevronUpDownIcon } from '@heroicons/react/20/solid'
import { Fragment } from 'react'

interface TranslationModel {
  id: string
  name: string
  description: string
  isPremium: boolean
  icon: string
  speed: 'Fast' | 'Medium' | 'Slow'
  quality: 'Basic' | 'Good' | 'Excellent'
}

const MODELS: TranslationModel[] = [
  {
    id: 'google',
    name: 'Google Translate',
    description: 'Fast and free translation',
    isPremium: false,
    icon: '🔤',
    speed: 'Fast',
    quality: 'Basic'
  },
  {
    id: 'chatgpt',
    name: 'ChatGPT-4',
    description: 'High-quality AI translation',
    isPremium: true,
    icon: '🤖',
    speed: 'Medium',
    quality: 'Excellent'
  },
  {
    id: 'claude',
    name: 'Claude 3',
    description: 'Advanced context-aware translation',
    isPremium: true,
    icon: '🧠',
    speed: 'Medium',
    quality: 'Excellent'
  },
  {
    id: 'gemini',
    name: 'Google Gemini',
    description: 'Google\'s advanced AI model',
    isPremium: true,
    icon: '💎',
    speed: 'Fast',
    quality: 'Good'
  },
  {
    id: 'deepl',
    name: 'DeepL',
    description: 'Professional translation service',
    isPremium: true,
    icon: '🔹',
    speed: 'Fast',
    quality: 'Good'
  }
]

interface ModelSelectorProps {
  selectedModel: string
  onModelChange: (modelId: string) => void
  isAuthenticated: boolean
  showPremiumPrompt?: boolean
}

export const ModelSelector: React.FC<ModelSelectorProps> = ({
  selectedModel,
  onModelChange,
  isAuthenticated,
  showPremiumPrompt = true
}) => {
  const availableModels = isAuthenticated
    ? MODELS
    : MODELS.filter(model => !model.isPremium)

  const selected = MODELS.find(model => model.id === selectedModel) || MODELS[0]

  return (
    <div className="w-full">
      <Listbox value={selectedModel} onChange={onModelChange}>
        <div className="relative">
          <Listbox.Button className="relative w-full cursor-pointer rounded-lg bg-white py-3 pl-4 pr-10 text-left border border-gray-300 focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-primary-500">
            <div className="flex items-center space-x-3">
              <span className="text-2xl">{selected.icon}</span>
              <div className="flex-1 min-w-0">
                <span className="block truncate font-medium">{selected.name}</span>
                <span className="block truncate text-sm text-gray-500">
                  {selected.description}
                </span>
              </div>
              <div className="flex space-x-2">
                <span className={cn(
                  "inline-flex items-center px-2 py-1 rounded-full text-xs font-medium",
                  selected.speed === 'Fast' && "bg-green-100 text-green-800",
                  selected.speed === 'Medium' && "bg-yellow-100 text-yellow-800",
                  selected.speed === 'Slow' && "bg-red-100 text-red-800"
                )}>
                  {selected.speed}
                </span>
                <span className={cn(
                  "inline-flex items-center px-2 py-1 rounded-full text-xs font-medium",
                  selected.quality === 'Basic' && "bg-gray-100 text-gray-800",
                  selected.quality === 'Good' && "bg-blue-100 text-blue-800",
                  selected.quality === 'Excellent' && "bg-purple-100 text-purple-800"
                )}>
                  {selected.quality}
                </span>
                {selected.isPremium && (
                  <span className="inline-flex items-center px-2 py-1 rounded-full text-xs font-medium bg-primary-100 text-primary-800">
                    Premium
                  </span>
                )}
              </div>
            </div>
            <span className="pointer-events-none absolute inset-y-0 right-0 flex items-center pr-3">
              <ChevronUpDownIcon className="h-5 w-5 text-gray-400" />
            </span>
          </Listbox.Button>

          <Transition
            as={Fragment}
            leave="transition ease-in duration-100"
            leaveFrom="opacity-100"
            leaveTo="opacity-0"
          >
            <Listbox.Options className="absolute z-10 mt-1 max-h-80 w-full overflow-auto rounded-lg bg-white py-1 shadow-lg ring-1 ring-black ring-opacity-5 focus:outline-none">
              {availableModels.map((model) => (
                <Listbox.Option
                  key={model.id}
                  className={({ active }) =>
                    cn(
                      "relative cursor-pointer select-none py-3 pl-4 pr-10",
                      active ? "bg-primary-50 text-primary-900" : "text-gray-900"
                    )
                  }
                  value={model.id}
                >
                  {({ selected, active }) => (
                    <>
                      <div className="flex items-center space-x-3">
                        <span className="text-2xl">{model.icon}</span>
                        <div className="flex-1 min-w-0">
                          <span className={cn(
                            "block truncate font-medium",
                            selected ? "text-primary-900" : "text-gray-900"
                          )}>
                            {model.name}
                          </span>
                          <span className="block truncate text-sm text-gray-500">
                            {model.description}
                          </span>
                        </div>
                        <div className="flex space-x-2">
                          <span className={cn(
                            "inline-flex items-center px-2 py-1 rounded-full text-xs font-medium",
                            model.speed === 'Fast' && "bg-green-100 text-green-800",
                            model.speed === 'Medium' && "bg-yellow-100 text-yellow-800",
                            model.speed === 'Slow' && "bg-red-100 text-red-800"
                          )}>
                            {model.speed}
                          </span>
                          <span className={cn(
                            "inline-flex items-center px-2 py-1 rounded-full text-xs font-medium",
                            model.quality === 'Basic' && "bg-gray-100 text-gray-800",
                            model.quality === 'Good' && "bg-blue-100 text-blue-800",
                            model.quality === 'Excellent' && "bg-purple-100 text-purple-800"
                          )}>
                            {model.quality}
                          </span>
                          {model.isPremium && (
                            <span className="inline-flex items-center px-2 py-1 rounded-full text-xs font-medium bg-primary-100 text-primary-800">
                              Premium
                            </span>
                          )}
                        </div>
                      </div>

                      {selected ? (
                        <span className="absolute inset-y-0 right-0 flex items-center pr-3 text-primary-600">
                          <CheckIcon className="h-5 w-5" />
                        </span>
                      ) : null}
                    </>
                  )}
                </Listbox.Option>
              ))}

              {!isAuthenticated && showPremiumPrompt && (
                <div className="px-4 py-3 border-t border-gray-200 bg-gray-50">
                  <p className="text-sm text-gray-600 mb-2">
                    Sign up for premium models:
                  </p>
                  <div className="flex space-x-2">
                    <span className="text-lg">🤖</span>
                    <span className="text-lg">🧠</span>
                    <span className="text-lg">💎</span>
                    <span className="text-lg">🔹</span>
                  </div>
                  <button className="mt-2 text-sm text-primary-600 font-medium hover:text-primary-700">
                    Create Free Account →
                  </button>
                </div>
              )}
            </Listbox.Options>
          </Transition>
        </div>
      </Listbox>
    </div>
  )
}
```

### Real-time Progress Tracker

```tsx
// src/components/translation/ProgressTracker.tsx
import React, { useEffect, useState } from 'react'
import { motion } from 'framer-motion'
import { ClockIcon, DocumentIcon, CheckCircleIcon, XCircleIcon } from '@heroicons/react/24/outline'
import { useJobStatus } from '@hooks/useTranslation'

interface ProgressTrackerProps {
  jobId: string
  onComplete?: (result: any) => void
  onError?: (error: string) => void
}

export const ProgressTracker: React.FC<ProgressTrackerProps> = ({
  jobId,
  onComplete,
  onError
}) => {
  const { data: job, isLoading, error } = useJobStatus(jobId)
  const [startTime] = useState(Date.now())
  const [estimatedCompletion, setEstimatedCompletion] = useState<Date | null>(null)

  useEffect(() => {
    if (job?.status === 'completed' && onComplete) {
      onComplete(job)
    }
    if (job?.status === 'failed' && onError) {
      onError(job.error || 'Translation failed')
    }
  }, [job?.status, onComplete, onError])

  useEffect(() => {
    if (job?.progress && job.progress.total > 0) {
      const elapsed = Date.now() - startTime
      const rate = job.progress.current / elapsed
      const remaining = (job.progress.total - job.progress.current) / rate
      setEstimatedCompletion(new Date(Date.now() + remaining))
    }
  }, [job?.progress, startTime])

  if (isLoading) {
    return <div className="animate-pulse bg-gray-200 h-32 rounded-lg" />
  }

  if (error) {
    return (
      <div className="bg-error-50 border border-error-200 rounded-lg p-6">
        <div className="flex items-center space-x-3">
          <XCircleIcon className="h-8 w-8 text-error-600" />
          <div>
            <h3 className="font-medium text-error-900">Translation Failed</h3>
            <p className="text-sm text-error-700">{error.message}</p>
          </div>
        </div>
      </div>
    )
  }

  if (!job) return null

  const progress = job.progress || { current: 0, total: 100 }
  const percentage = progress.total > 0 ? (progress.current / progress.total) * 100 : 0

  return (
    <div className="bg-white border border-gray-200 rounded-lg p-6 shadow-sm">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div className="flex items-center space-x-3">
          <DocumentIcon className="h-8 w-8 text-primary-600" />
          <div>
            <h3 className="font-semibold text-gray-900">{job.filename}</h3>
            <p className="text-sm text-gray-500">
              {job.source_language} → {job.target_language}
            </p>
          </div>
        </div>
        <div className="flex items-center space-x-2">
          <StatusBadge status={job.status} />
          {job.model && (
            <span className="px-2 py-1 bg-gray-100 text-gray-700 text-sm rounded-full">
              {job.model}
            </span>
          )}
        </div>
      </div>

      {/* Progress Bar */}
      <div className="mb-6">
        <div className="flex justify-between text-sm text-gray-600 mb-2">
          <span>Progress</span>
          <span>{Math.round(percentage)}%</span>
        </div>
        <div className="w-full bg-gray-200 rounded-full h-3 overflow-hidden">
          <motion.div
            className="bg-primary-600 h-full rounded-full"
            initial={{ width: 0 }}
            animate={{ width: `${percentage}%` }}
            transition={{ duration: 0.5, ease: "easeOut" }}
          />
        </div>
        <div className="flex justify-between text-xs text-gray-500 mt-1">
          <span>{progress.current} / {progress.total} segments</span>
          {job.status === 'processing' && (
            <span className="flex items-center space-x-1">
              <ClockIcon className="h-3 w-3" />
              <span>
                {estimatedCompletion
                  ? `ETA: ${formatTimeRemaining(estimatedCompletion)}`
                  : 'Calculating ETA...'
                }
              </span>
            </span>
          )}
        </div>
      </div>

      {/* Statistics */}
      {job.status === 'processing' && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
          <div className="text-center">
            <div className="text-lg font-semibold text-gray-900">
              {formatDuration(Date.now() - startTime)}
            </div>
            <div className="text-xs text-gray-500">Elapsed</div>
          </div>
          <div className="text-center">
            <div className="text-lg font-semibold text-gray-900">
              {job.progress?.speed || '—'}
            </div>
            <div className="text-xs text-gray-500">Segments/min</div>
          </div>
          <div className="text-center">
            <div className="text-lg font-semibold text-gray-900">
              {job.progress?.words_translated || '—'}
            </div>
            <div className="text-xs text-gray-500">Words</div>
          </div>
          <div className="text-center">
            <div className="text-lg font-semibold text-gray-900">
              {job.progress?.quality_score ? `${job.progress.quality_score}%` : '—'}
            </div>
            <div className="text-xs text-gray-500">Quality</div>
          </div>
        </div>
      )}

      {/* Current Section (for EPUB) */}
      {job.progress?.current_section && (
        <div className="bg-gray-50 rounded-lg p-4 mb-4">
          <h4 className="font-medium text-gray-900 mb-2">Currently Processing:</h4>
          <p className="text-sm text-gray-700">{job.progress.current_section}</p>
        </div>
      )}

      {/* Live Translation Preview */}
      {job.progress?.latest_translation && (
        <div className="bg-blue-50 rounded-lg p-4 mb-4">
          <h4 className="font-medium text-gray-900 mb-2">Latest Translation:</h4>
          <div className="space-y-2 text-sm">
            <div className="text-gray-700">
              <span className="font-medium">Original:</span> {job.progress.latest_translation.original}
            </div>
            <div className="text-blue-700">
              <span className="font-medium">Translated:</span> {job.progress.latest_translation.translated}
            </div>
          </div>
        </div>
      )}

      {/* Action Buttons */}
      <div className="flex justify-between">
        <div className="flex space-x-2">
          {job.status === 'processing' && (
            <>
              <button className="px-4 py-2 text-sm border border-gray-300 rounded-lg hover:bg-gray-50 transition-colors">
                Pause
              </button>
              <button className="px-4 py-2 text-sm border border-error-300 text-error-700 rounded-lg hover:bg-error-50 transition-colors">
                Cancel
              </button>
            </>
          )}
        </div>
        <div className="flex space-x-2">
          <button className="px-4 py-2 text-sm border border-gray-300 rounded-lg hover:bg-gray-50 transition-colors">
            View Details
          </button>
          {job.status === 'completed' && (
            <button className="px-4 py-2 text-sm bg-primary-600 text-white rounded-lg hover:bg-primary-700 transition-colors">
              Download
            </button>
          )}
        </div>
      </div>
    </div>
  )
}

const StatusBadge: React.FC<{ status: string }> = ({ status }) => {
  const configs = {
    pending: { color: 'bg-yellow-100 text-yellow-800', icon: ClockIcon, text: 'Pending' },
    processing: { color: 'bg-blue-100 text-blue-800', icon: ClockIcon, text: 'Processing' },
    completed: { color: 'bg-green-100 text-green-800', icon: CheckCircleIcon, text: 'Completed' },
    failed: { color: 'bg-red-100 text-red-800', icon: XCircleIcon, text: 'Failed' },
    cancelled: { color: 'bg-gray-100 text-gray-800', icon: XCircleIcon, text: 'Cancelled' }
  }

  const config = configs[status] || configs.pending
  const Icon = config.icon

  return (
    <span className={`inline-flex items-center px-2 py-1 rounded-full text-xs font-medium ${config.color}`}>
      <Icon className="h-3 w-3 mr-1" />
      {config.text}
    </span>
  )
}

const formatDuration = (ms: number): string => {
  const seconds = Math.floor(ms / 1000) % 60
  const minutes = Math.floor(ms / (1000 * 60)) % 60
  const hours = Math.floor(ms / (1000 * 60 * 60))

  if (hours > 0) {
    return `${hours}:${minutes.toString().padStart(2, '0')}:${seconds.toString().padStart(2, '0')}`
  }
  return `${minutes}:${seconds.toString().padStart(2, '0')}`
}

const formatTimeRemaining = (date: Date): string => {
  const now = new Date()
  const diff = date.getTime() - now.getTime()

  if (diff <= 0) return 'Almost done'

  const minutes = Math.floor(diff / (1000 * 60))
  const seconds = Math.floor((diff % (1000 * 60)) / 1000)

  if (minutes > 0) {
    return `${minutes}m ${seconds}s`
  }
  return `${seconds}s`
}
```

### Download Interface Component

```tsx
// src/components/translation/DownloadButton.tsx
import React, { useState } from 'react'
import { ArrowDownTrayIcon, ShareIcon, TrashIcon } from '@heroicons/react/24/outline'
import { motion } from 'framer-motion'
import { useDownloadJob } from '@hooks/useTranslation'

interface DownloadButtonProps {
  jobId: string
  filename: string
  fileSize?: number
  downloadUrl?: string
  onDelete?: () => void
  showShare?: boolean
}

export const DownloadButton: React.FC<DownloadButtonProps> = ({
  jobId,
  filename,
  fileSize,
  downloadUrl,
  onDelete,
  showShare = false
}) => {
  const [isDownloading, setIsDownloading] = useState(false)
  const downloadMutation = useDownloadJob()

  const handleDownload = async () => {
    setIsDownloading(true)
    try {
      if (downloadUrl) {
        // Direct download link
        const link = document.createElement('a')
        link.href = downloadUrl
        link.download = filename
        document.body.appendChild(link)
        link.click()
        document.body.removeChild(link)
      } else {
        // API download
        const blob = await downloadMutation.mutateAsync(jobId)
        const url = window.URL.createObjectURL(blob)
        const link = document.createElement('a')
        link.href = url
        link.download = filename
        document.body.appendChild(link)
        link.click()
        window.URL.revokeObjectURL(url)
        document.body.removeChild(link)
      }
    } catch (error) {
      console.error('Download failed:', error)
    } finally {
      setIsDownloading(false)
    }
  }

  const handleShare = async () => {
    if (navigator.share && downloadUrl) {
      try {
        await navigator.share({
          title: `Translated: ${filename}`,
          text: 'Check out this translated document',
          url: downloadUrl
        })
      } catch (error) {
        // Fallback to copying link
        navigator.clipboard.writeText(downloadUrl)
      }
    }
  }

  return (
    <div className="flex items-center space-x-2">
      <motion.button
        onClick={handleDownload}
        disabled={isDownloading || downloadMutation.isLoading}
        className="inline-flex items-center px-4 py-2 bg-primary-600 text-white text-sm font-medium rounded-lg hover:bg-primary-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
        whileHover={{ scale: 1.05 }}
        whileTap={{ scale: 0.95 }}
      >
        {isDownloading || downloadMutation.isLoading ? (
          <div className="animate-spin rounded-full h-4 w-4 border-b-2 border-white mr-2" />
        ) : (
          <ArrowDownTrayIcon className="h-4 w-4 mr-2" />
        )}
        {isDownloading ? 'Downloading...' : 'Download'}
      </motion.button>

      {showShare && (
        <button
          onClick={handleShare}
          className="inline-flex items-center px-3 py-2 border border-gray-300 text-gray-700 text-sm font-medium rounded-lg hover:bg-gray-50 transition-colors"
        >
          <ShareIcon className="h-4 w-4" />
        </button>
      )}

      {onDelete && (
        <button
          onClick={onDelete}
          className="inline-flex items-center px-3 py-2 border border-error-300 text-error-700 text-sm font-medium rounded-lg hover:bg-error-50 transition-colors"
        >
          <TrashIcon className="h-4 w-4" />
        </button>
      )}

      {fileSize && (
        <span className="text-sm text-gray-500">
          {formatFileSize(fileSize)}
        </span>
      )}
    </div>
  )
}
```

### Authentication Forms

```tsx
// src/components/forms/AuthForm.tsx
import React, { useState } from 'react'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import { EyeIcon, EyeSlashIcon } from '@heroicons/react/24/outline'
import { motion } from 'framer-motion'
import { useAuth } from '@hooks/useAuth'

const loginSchema = z.object({
  email: z.string().email('Invalid email address'),
  password: z.string().min(6, 'Password must be at least 6 characters')
})

const registerSchema = loginSchema.extend({
  confirmPassword: z.string(),
  acceptTerms: z.boolean()
}).refine((data) => data.password === data.confirmPassword, {
  message: "Passwords don't match",
  path: ["confirmPassword"]
}).refine((data) => data.acceptTerms, {
  message: "You must accept the terms and conditions",
  path: ["acceptTerms"]
})

type LoginFormData = z.infer<typeof loginSchema>
type RegisterFormData = z.infer<typeof registerSchema>

interface AuthFormProps {
  mode: 'login' | 'register'
  onToggleMode: () => void
  onSuccess?: () => void
}

export const AuthForm: React.FC<AuthFormProps> = ({
  mode,
  onToggleMode,
  onSuccess
}) => {
  const [showPassword, setShowPassword] = useState(false)
  const [showConfirmPassword, setShowConfirmPassword] = useState(false)
  const { login, register } = useAuth()

  const {
    register: registerField,
    handleSubmit,
    formState: { errors, isSubmitting },
    setError
  } = useForm<RegisterFormData>({
    resolver: zodResolver(mode === 'login' ? loginSchema : registerSchema)
  })

  const onSubmit = async (data: RegisterFormData) => {
    try {
      if (mode === 'login') {
        await login({ email: data.email, password: data.password })
      } else {
        await register({
          email: data.email,
          password: data.password
        })
      }
      onSuccess?.()
    } catch (error: any) {
      setError('root', {
        message: error.response?.data?.message || 'Authentication failed'
      })
    }
  }

  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      className="w-full max-w-md mx-auto"
    >
      <div className="bg-white rounded-xl shadow-lg border border-gray-200 p-8">
        <div className="text-center mb-8">
          <h2 className="text-2xl font-bold text-gray-900">
            {mode === 'login' ? 'Welcome Back' : 'Create Account'}
          </h2>
          <p className="text-gray-600 mt-2">
            {mode === 'login'
              ? 'Sign in to access premium translation models'
              : 'Join to unlock advanced translation features'
            }
          </p>
        </div>

        <form onSubmit={handleSubmit(onSubmit)} className="space-y-6">
          {/* Email Field */}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Email Address
            </label>
            <input
              {...registerField('email')}
              type="email"
              className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-primary-500 transition-colors"
              placeholder="Enter your email"
            />
            {errors.email && (
              <p className="mt-1 text-sm text-error-600">{errors.email.message}</p>
            )}
          </div>

          {/* Password Field */}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Password
            </label>
            <div className="relative">
              <input
                {...registerField('password')}
                type={showPassword ? 'text' : 'password'}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-primary-500 pr-10 transition-colors"
                placeholder="Enter your password"
              />
              <button
                type="button"
                onClick={() => setShowPassword(!showPassword)}
                className="absolute inset-y-0 right-0 pr-3 flex items-center"
              >
                {showPassword ? (
                  <EyeSlashIcon className="h-5 w-5 text-gray-400" />
                ) : (
                  <EyeIcon className="h-5 w-5 text-gray-400" />
                )}
              </button>
            </div>
            {errors.password && (
              <p className="mt-1 text-sm text-error-600">{errors.password.message}</p>
            )}
          </div>

          {/* Confirm Password (Register only) */}
          {mode === 'register' && (
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Confirm Password
              </label>
              <div className="relative">
                <input
                  {...registerField('confirmPassword')}
                  type={showConfirmPassword ? 'text' : 'password'}
                  className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-primary-500 pr-10 transition-colors"
                  placeholder="Confirm your password"
                />
                <button
                  type="button"
                  onClick={() => setShowConfirmPassword(!showConfirmPassword)}
                  className="absolute inset-y-0 right-0 pr-3 flex items-center"
                >
                  {showConfirmPassword ? (
                    <EyeSlashIcon className="h-5 w-5 text-gray-400" />
                  ) : (
                    <EyeIcon className="h-5 w-5 text-gray-400" />
                  )}
                </button>
              </div>
              {errors.confirmPassword && (
                <p className="mt-1 text-sm text-error-600">{errors.confirmPassword.message}</p>
              )}
            </div>
          )}

          {/* Terms Checkbox (Register only) */}
          {mode === 'register' && (
            <div>
              <label className="flex items-center">
                <input
                  {...registerField('acceptTerms')}
                  type="checkbox"
                  className="rounded border-gray-300 text-primary-600 focus:ring-primary-500"
                />
                <span className="ml-2 text-sm text-gray-600">
                  I agree to the{' '}
                  <a href="/terms" className="text-primary-600 hover:text-primary-700">
                    Terms of Service
                  </a>{' '}
                  and{' '}
                  <a href="/privacy" className="text-primary-600 hover:text-primary-700">
                    Privacy Policy
                  </a>
                </span>
              </label>
              {errors.acceptTerms && (
                <p className="mt-1 text-sm text-error-600">{errors.acceptTerms.message}</p>
              )}
            </div>
          )}

          {/* Error Message */}
          {errors.root && (
            <div className="bg-error-50 border border-error-200 rounded-lg p-3">
              <p className="text-sm text-error-600">{errors.root.message}</p>
            </div>
          )}

          {/* Submit Button */}
          <button
            type="submit"
            disabled={isSubmitting}
            className="w-full bg-primary-600 text-white py-2 px-4 rounded-lg font-medium hover:bg-primary-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            {isSubmitting ? (
              <div className="flex items-center justify-center">
                <div className="animate-spin rounded-full h-5 w-5 border-b-2 border-white mr-2" />
                {mode === 'login' ? 'Signing In...' : 'Creating Account...'}
              </div>
            ) : (
              mode === 'login' ? 'Sign In' : 'Create Account'
            )}
          </button>

          {/* Toggle Mode */}
          <div className="text-center">
            <button
              type="button"
              onClick={onToggleMode}
              className="text-sm text-primary-600 hover:text-primary-700"
            >
              {mode === 'login'
                ? "Don't have an account? Sign up"
                : "Already have an account? Sign in"
              }
            </button>
          </div>
        </form>
      </div>
    </motion.div>
  )
}
```

---

## 6. Development Workflow

### Local Development Setup

```bash
# 1. Clone and setup
git clone [repository-url]
cd bilingual-book-frontend
npm install

# 2. Environment configuration
cp .env.example .env.local
# Edit .env.local with your settings

# 3. Start development server
npm run dev

# 4. Start backend API (in separate terminal)
cd ../api_layer
python -m uvicorn api.main:app --reload --host 0.0.0.0 --port 8000

# 5. Access application
# Frontend: http://localhost:3000
# Backend API: http://localhost:8000
# API Docs: http://localhost:8000/docs
```

### API Connection Configuration

```ts
// src/utils/constants.ts
export const API_CONFIG = {
  BASE_URL: import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000',
  TIMEOUT: parseInt(import.meta.env.VITE_API_TIMEOUT) || 30000,
  MAX_FILE_SIZE: parseInt(import.meta.env.VITE_MAX_FILE_SIZE) || 500 * 1024 * 1024,
} as const

export const SUPPORTED_FORMATS = ['.epub', '.txt', '.srt', '.md'] as const

export const POLLING_INTERVALS = {
  JOB_STATUS: 2000, // 2 seconds
  JOB_LIST: 10000,  // 10 seconds
} as const

// src/api/client.ts - Full configuration
import axios, { AxiosError, AxiosResponse } from 'axios'
import { API_CONFIG } from '@utils/constants'
import { authStore } from '@store/authStore'
import { uiStore } from '@store/uiStore'

export const apiClient = axios.create({
  baseURL: API_CONFIG.BASE_URL,
  timeout: API_CONFIG.TIMEOUT,
  headers: {
    'Content-Type': 'application/json',
  },
})

// Request interceptor
apiClient.interceptors.request.use(
  (config) => {
    const token = authStore.getState().token
    if (token) {
      config.headers.Authorization = `Bearer ${token}`
    }
    return config
  },
  (error) => Promise.reject(error)
)

// Response interceptor
apiClient.interceptors.response.use(
  (response: AxiosResponse) => response,
  (error: AxiosError) => {
    const { addNotification } = uiStore.getState()

    if (error.response?.status === 401) {
      authStore.getState().logout()
      addNotification({
        id: Date.now().toString(),
        type: 'error',
        title: 'Authentication Required',
        message: 'Please sign in to continue',
      })
    }

    if (error.response?.status === 429) {
      addNotification({
        id: Date.now().toString(),
        type: 'warning',
        title: 'Rate Limited',
        message: 'Too many requests. Please wait before trying again.',
      })
    }

    if (error.response?.status >= 500) {
      addNotification({
        id: Date.now().toString(),
        type: 'error',
        title: 'Server Error',
        message: 'Something went wrong. Please try again later.',
      })
    }

    return Promise.reject(error)
  }
)
```

### Testing Approach

```ts
// src/test/setup.ts
import '@testing-library/jest-dom'
import { vi } from 'vitest'

// Mock IntersectionObserver
global.IntersectionObserver = vi.fn(() => ({
  disconnect: vi.fn(),
  observe: vi.fn(),
  unobserve: vi.fn(),
}))

// Mock ResizeObserver
global.ResizeObserver = vi.fn(() => ({
  disconnect: vi.fn(),
  observe: vi.fn(),
  unobserve: vi.fn(),
}))

// Mock environment variables
vi.mock('@utils/constants', () => ({
  API_CONFIG: {
    BASE_URL: 'http://localhost:8000',
    TIMEOUT: 30000,
    MAX_FILE_SIZE: 500 * 1024 * 1024,
  },
  SUPPORTED_FORMATS: ['.epub', '.txt', '.srt', '.md'],
  POLLING_INTERVALS: {
    JOB_STATUS: 2000,
    JOB_LIST: 10000,
  },
}))
```

```ts
// src/test/utils.tsx
import React, { ReactElement } from 'react'
import { render, RenderOptions } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { BrowserRouter } from 'react-router-dom'

const AllTheProviders: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
      },
    },
  })

  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        {children}
      </BrowserRouter>
    </QueryClientProvider>
  )
}

const customRender = (ui: ReactElement, options?: Omit<RenderOptions, 'wrapper'>) =>
  render(ui, { wrapper: AllTheProviders, ...options })

export * from '@testing-library/react'
export { customRender as render }
```

```ts
// Example component test: src/components/forms/__tests__/FileUpload.test.tsx
import { render, screen, fireEvent, waitFor } from '@test/utils'
import { FileUpload } from '../FileUpload'

describe('FileUpload', () => {
  const mockOnFileSelect = vi.fn()

  beforeEach(() => {
    mockOnFileSelect.mockClear()
  })

  it('renders upload area correctly', () => {
    render(<FileUpload onFileSelect={mockOnFileSelect} />)

    expect(screen.getByText('Choose a file to translate')).toBeInTheDocument()
    expect(screen.getByText('Drag and drop your file here, or click to browse')).toBeInTheDocument()
  })

  it('handles file selection', async () => {
    render(<FileUpload onFileSelect={mockOnFileSelect} />)

    const file = new File(['test content'], 'test.epub', { type: 'application/epub+zip' })
    const input = screen.getByRole('textbox', { hidden: true })

    fireEvent.change(input, { target: { files: [file] } })

    await waitFor(() => {
      expect(mockOnFileSelect).toHaveBeenCalledWith(file)
    })
  })

  it('shows error for invalid file type', async () => {
    render(<FileUpload onFileSelect={mockOnFileSelect} />)

    const file = new File(['test'], 'test.pdf', { type: 'application/pdf' })
    const input = screen.getByRole('textbox', { hidden: true })

    fireEvent.change(input, { target: { files: [file] } })

    await waitFor(() => {
      expect(screen.getByText(/Invalid file type/)).toBeInTheDocument()
    })
  })
})
```

### Build and Deployment Preparation

```json
// package.json - Build scripts
{
  "scripts": {
    "build": "tsc && vite build",
    "build:staging": "tsc && vite build --mode staging",
    "build:production": "tsc && vite build --mode production",
    "preview": "vite preview",
    "analyze": "npm run build && npx vite-bundle-analyzer dist"
  }
}
```

```ts
// vite.config.ts - Production optimizations
export default defineConfig(({ mode }) => ({
  plugins: [react()],
  build: {
    rollupOptions: {
      output: {
        manualChunks: {
          vendor: ['react', 'react-dom'],
          ui: ['@headlessui/react', '@heroicons/react'],
          router: ['react-router-dom'],
          query: ['@tanstack/react-query'],
        },
      },
    },
    chunkSizeWarningLimit: 1000,
  },
  define: {
    __APP_VERSION__: JSON.stringify(process.env.npm_package_version),
  },
}))
```

---

## 7. Integration Guidelines

### CORS Configuration Requirements

The backend API needs to be configured to allow the frontend origin. Update the API's CORS settings:

```python
# In api_layer/api/main.py or config
CORS_ORIGINS = [
    "http://localhost:3000",    # Development
    "http://127.0.0.1:3000",    # Alternative localhost
    "https://yourdomain.com",   # Production
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["*"],
)
```

### Authentication Flow Implementation

```ts
// src/hooks/useAuth.ts
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { authApi } from '@api/endpoints'
import { authStore } from '@store/authStore'

export const useAuth = () => {
  const queryClient = useQueryClient()

  const loginMutation = useMutation({
    mutationFn: authApi.login,
    onSuccess: (data) => {
      authStore.getState().setAuth(data.user, data.token)
      queryClient.invalidateQueries(['user'])
    },
  })

  const registerMutation = useMutation({
    mutationFn: authApi.register,
    onSuccess: (data) => {
      authStore.getState().setAuth(data.user, data.token)
      queryClient.invalidateQueries(['user'])
    },
  })

  const logoutMutation = useMutation({
    mutationFn: authApi.logout,
    onSuccess: () => {
      authStore.getState().logout()
      queryClient.clear()
    },
  })

  return {
    login: loginMutation.mutateAsync,
    register: registerMutation.mutateAsync,
    logout: logoutMutation.mutateAsync,
    isLoading: loginMutation.isLoading || registerMutation.isLoading,
  }
}

// Authentication store
export const authStore = create<AuthState>((set, get) => ({
  user: null,
  token: localStorage.getItem('auth_token'),
  isAuthenticated: false,

  setAuth: (user, token) => {
    localStorage.setItem('auth_token', token)
    set({ user, token, isAuthenticated: true })
  },

  logout: () => {
    localStorage.removeItem('auth_token')
    set({ user: null, token: null, isAuthenticated: false })
  },

  // Initialize from localStorage
  initialize: () => {
    const token = localStorage.getItem('auth_token')
    if (token) {
      // Verify token with API
      authApi.verifyToken(token)
        .then((user) => set({ user, token, isAuthenticated: true }))
        .catch(() => {
          localStorage.removeItem('auth_token')
          set({ user: null, token: null, isAuthenticated: false })
        })
    }
  },
}))
```

### Error Handling Strategies

```ts
// src/components/common/ErrorBoundary.tsx
import React, { Component, ErrorInfo, ReactNode } from 'react'

interface Props {
  children: ReactNode
  fallback?: ReactNode
}

interface State {
  hasError: boolean
  error?: Error
}

export class ErrorBoundary extends Component<Props, State> {
  public state: State = {
    hasError: false
  }

  public static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error }
  }

  public componentDidCatch(error: Error, errorInfo: ErrorInfo) {
    console.error('Uncaught error:', error, errorInfo)

    // Send to error reporting service
    if (import.meta.env.PROD) {
      // reportError(error, errorInfo)
    }
  }

  public render() {
    if (this.state.hasError) {
      return this.props.fallback || (
        <div className="min-h-screen flex items-center justify-center bg-gray-50">
          <div className="max-w-md w-full bg-white rounded-lg shadow-lg p-6 text-center">
            <div className="text-6xl mb-4">😵</div>
            <h1 className="text-xl font-semibold text-gray-900 mb-2">
              Something went wrong
            </h1>
            <p className="text-gray-600 mb-4">
              We're sorry, but something unexpected happened. Please refresh the page or try again later.
            </p>
            <button
              onClick={() => window.location.reload()}
              className="bg-primary-600 text-white px-4 py-2 rounded-lg hover:bg-primary-700 transition-colors"
            >
              Refresh Page
            </button>
          </div>
        </div>
      )
    }

    return this.props.children
  }
}

// Global error handler for async operations
export const handleAsyncError = (error: unknown) => {
  console.error('Async error:', error)

  if (error instanceof Error) {
    uiStore.getState().addNotification({
      id: Date.now().toString(),
      type: 'error',
      title: 'Error',
      message: error.message,
    })
  }
}
```

### Performance Optimization

```ts
// src/utils/performance.ts
import { lazy } from 'react'

// Lazy load heavy components
export const LazyDashboard = lazy(() => import('@pages/Dashboard'))
export const LazyTranslationView = lazy(() => import('@pages/Translation'))

// Image optimization
export const optimizeImage = (file: File, maxWidth = 800, quality = 0.8): Promise<Blob> => {
  return new Promise((resolve) => {
    const canvas = document.createElement('canvas')
    const ctx = canvas.getContext('2d')
    const img = new Image()

    img.onload = () => {
      const ratio = Math.min(maxWidth / img.width, maxWidth / img.height)
      canvas.width = img.width * ratio
      canvas.height = img.height * ratio

      ctx?.drawImage(img, 0, 0, canvas.width, canvas.height)
      canvas.toBlob(resolve!, 'image/jpeg', quality)
    }

    img.src = URL.createObjectURL(file)
  })
}

// Debounce utility
export const debounce = <T extends (...args: any[]) => any>(
  func: T,
  wait: number
): ((...args: Parameters<T>) => void) => {
  let timeout: NodeJS.Timeout
  return (...args: Parameters<T>) => {
    clearTimeout(timeout)
    timeout = setTimeout(() => func(...args), wait)
  }
}

// Virtual scrolling for large lists
export const useVirtualList = <T>(
  items: T[],
  itemHeight: number,
  containerHeight: number
) => {
  const [scrollTop, setScrollTop] = useState(0)

  const startIndex = Math.floor(scrollTop / itemHeight)
  const endIndex = Math.min(
    startIndex + Math.ceil(containerHeight / itemHeight) + 1,
    items.length
  )

  const visibleItems = items.slice(startIndex, endIndex)
  const totalHeight = items.length * itemHeight
  const offsetY = startIndex * itemHeight

  return {
    visibleItems,
    totalHeight,
    offsetY,
    onScroll: (e: React.UIEvent<HTMLDivElement>) => {
      setScrollTop(e.currentTarget.scrollTop)
    }
  }
}
```

This comprehensive frontend setup guide provides everything needed to build a complete bilingual book translation service frontend. The guide includes modern React patterns, TypeScript for type safety, responsive design with Tailwind CSS, and proper integration with the FastAPI backend.

Key features covered:
- **Complete project setup** with modern tooling
- **Responsive design system** with consistent UI components
- **Authentication flow** for freemium business model
- **Real-time progress tracking** with polling and WebSocket support
- **File upload and management** with drag & drop
- **Error handling and performance optimization**
- **Testing infrastructure** with comprehensive examples

The architecture is scalable, maintainable, and follows React best practices while providing an excellent user experience for both anonymous and registered users.