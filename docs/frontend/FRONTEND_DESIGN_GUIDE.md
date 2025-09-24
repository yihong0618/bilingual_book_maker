# Frontend Design Guide: Bilingual Book Translation Service

## Table of Contents
1. [Technology Stack Recommendations](#technology-stack-recommendations)
2. [Project Structure](#project-structure)
3. [UI/UX Design Specifications](#uiux-design-specifications)
4. [Page Layouts & Wireframes](#page-layouts--wireframes)
5. [Component Design Specifications](#component-design-specifications)
6. [User Experience Flows](#user-experience-flows)
7. [Responsive Design Strategy](#responsive-design-strategy)
8. [Integration Architecture](#integration-architecture)
9. [Development Workflow](#development-workflow)

---

## Technology Stack Recommendations

### Core Framework
- **React 18**: Latest stable version with concurrent features and improved performance
- **TypeScript**: Full type safety for better developer experience and reduced runtime errors
- **Vite**: Fast build tool with hot module replacement for optimal development experience

### State Management
- **Zustand**: Lightweight state management for global app state (user auth, translation jobs)
- **React Query (TanStack Query)**: Server state management for API calls, caching, and synchronization
- **React Hook Form**: Performant form handling with minimal re-renders

### Styling & UI
- **Tailwind CSS**: Utility-first CSS framework for rapid prototyping and consistent design
- **Headless UI**: Unstyled, accessible UI components that integrate seamlessly with Tailwind
- **Framer Motion**: Smooth animations and transitions for enhanced user experience
- **React Hot Toast**: Clean, customizable toast notifications

### File Handling
- **React Dropzone**: Drag-and-drop file upload with validation
- **File Type Detection**: Client-side validation for EPUB, TXT, SRT, MD files

### Development Tools
- **ESLint + Prettier**: Code formatting and linting
- **Husky**: Git hooks for pre-commit validation
- **Vitest**: Fast unit testing framework
- **React Testing Library**: Component testing with user-centric approach
- **Storybook**: Component development and documentation

### Build & Deployment
- **Vite**: Production builds with code splitting and optimization
- **Docker**: Containerization for consistent deployment
- **GitHub Actions**: CI/CD pipeline for automated testing and deployment

---

## Project Structure

```
frontend/
├── public/
│   ├── icons/
│   └── images/
├── src/
│   ├── components/
│   │   ├── ui/                    # Reusable UI components
│   │   ├── forms/                 # Form-specific components
│   │   ├── layout/                # Layout components
│   │   └── features/              # Feature-specific components
│   ├── pages/                     # Page components
│   ├── hooks/                     # Custom React hooks
│   ├── services/                  # API service functions
│   ├── stores/                    # Zustand stores
│   ├── types/                     # TypeScript type definitions
│   ├── utils/                     # Utility functions
│   ├── constants/                 # App constants
│   └── styles/                    # Global styles and Tailwind config
├── tests/
│   ├── components/
│   ├── pages/
│   └── utils/
├── docs/
│   └── components/                # Component documentation
└── .storybook/                    # Storybook configuration
```

---

## UI/UX Design Specifications

### Design Language
**Modern Minimalism with Professional Trust Signals**

### Color Palette
- **Primary Blue**: #2563EB (Professional, trustworthy)
- **Secondary Purple**: #7C3AED (Premium feel)
- **Success Green**: #059669 (Completion states)
- **Warning Orange**: #EA580C (Attention states)
- **Error Red**: #DC2626 (Error states)
- **Neutral Grays**: #F8FAFC, #E2E8F0, #64748B, #1E293B
- **Background**: #FFFFFF with subtle #F8FAFC sections

### Typography
- **Primary Font**: Inter (Clean, readable, professional)
- **Headings**: Font weights 600-700, sizes 24px-48px
- **Body Text**: Font weight 400, size 16px, line height 1.6
- **Small Text**: Font weight 400, size 14px
- **Code/File Names**: JetBrains Mono (monospace)

### Spacing System
- **Base Unit**: 4px
- **Component Spacing**: 8px, 16px, 24px, 32px
- **Section Spacing**: 48px, 64px, 96px
- **Container Max Width**: 1200px with responsive padding

### Visual Elements
- **Border Radius**: 8px for cards, 12px for buttons, 6px for inputs
- **Shadows**: Subtle elevation with warm gray shadows
- **Borders**: 1px solid with neutral colors
- **Focus States**: 2px blue outline with 2px offset

### Iconography
- **Icon Library**: Heroicons (consistent with Headless UI)
- **Icon Sizes**: 16px, 20px, 24px, 32px
- **Icon Style**: Outline for most cases, solid for active states

---

## Page Layouts & Wireframes

### Landing Page Layout
**Header Section**
- Fixed navigation bar with logo left, auth buttons right
- Hero section with large headline, subtitle, and primary CTA
- Visual element: Abstract book/translation graphic or animation

**Features Section**
- Three-column grid showcasing key features
- Icons, headlines, and brief descriptions
- Emphasis on speed, accuracy, and ease of use

**Pricing Tiers Section**
- Two-column comparison: Free vs Premium
- Clear feature differentiation
- Prominent upgrade CTA for premium tier

**Upload Preview Section**
- Interactive file upload demonstration
- Supported file types with icons
- File size and format restrictions clearly stated

**Footer**
- Links to documentation, support, terms
- Social media links and company information

### Dashboard Layout (Authenticated Users)
**Sidebar Navigation**
- Collapsible sidebar with main navigation items
- User profile section at top
- Translation history, settings, billing links

**Main Content Area**
- Breadcrumb navigation
- Page-specific content with consistent spacing
- Action buttons aligned to right

**Status Panel**
- Real-time job status updates
- Progress indicators for active translations
- Quick access to recent translations

### Translation Upload Page
**Centered Card Layout**
- Large file drop zone as primary focal point
- Step-by-step process indicator at top
- Configuration options below upload area
- Progress through: Upload → Configure → Process → Download

**Configuration Panel**
- Source and target language selectors
- Translation model selection (free vs premium)
- Additional options for premium users
- Clear pricing information for premium features

### Progress Tracking Page
**Real-time Dashboard**
- Large progress circle or bar showing completion percentage
- Estimated time remaining
- Current processing step with detailed status
- Cancel option with confirmation modal

**Job Details Card**
- File information (name, size, type)
- Selected languages and model
- Start time and estimated completion
- Option to navigate away and return later

---

## Component Design Specifications

### File Upload Component
**Visual Design**
- Large dashed border rectangle (300px height minimum)
- Drag-and-drop area with hover state animations
- Central upload icon (cloud with arrow)
- Instructional text below icon
- Supported file types listed at bottom
- File size limit clearly stated

**Interaction States**
- Default: Subtle gray border with upload icon
- Hover: Border color shifts to primary blue
- Drag Over: Background color changes to light blue
- Error: Red border with error message
- Success: Green border with file preview

### Language Selector Component
**Design Pattern**
- Searchable dropdown with flag icons
- Popular languages at top of list
- Alphabetical sorting for remaining languages
- Clear visual distinction between source and target
- Swap languages button between selectors

**Features**
- Type-ahead search functionality
- Flag icons for visual language identification
- Recent language history for quick selection
- Validation for same source/target language

### Progress Indicator Component
**Circular Progress Design**
- Large central circle with percentage text
- Animated progress ring in primary color
- Background ring in light gray
- Processing stage text below circle
- Estimated time remaining at bottom

**Linear Progress Alternative**
- Multi-step progress bar for complex processes
- Current step highlighted in primary color
- Completed steps in success green
- Future steps in light gray
- Step labels below progress bar

### Translation Model Selector
**Card-Based Layout**
- Two distinct cards: Free and Premium
- Clear feature comparison
- Pricing information for premium
- Model quality indicators (speed, accuracy)
- User tier badge for current selection

**Premium Upsell Elements**
- "Upgrade" badge on premium card
- Feature comparison tooltips
- Quality examples or previews
- Clear call-to-action for account creation

### Navigation Header
**Layout Structure**
- Logo/brand on far left
- Main navigation items in center
- User account/auth controls on right
- Responsive hamburger menu for mobile

**Authentication States**
- Anonymous: Sign In and Sign Up buttons
- Authenticated: User avatar, dropdown menu
- Premium badge for upgraded users
- Logout option in user dropdown

### File History Table
**Table Design**
- Clean table with alternating row colors
- Sortable columns (date, file name, status)
- Status indicators with color coding
- Action buttons for download/retry
- Pagination for large lists

**Status Indicators**
- Processing: Animated spinner with blue color
- Completed: Green checkmark icon
- Failed: Red X icon with error tooltip
- Cancelled: Gray X icon

### Toast Notification System
**Notification Types**
- Success: Green background with checkmark
- Error: Red background with X icon
- Warning: Orange background with triangle
- Info: Blue background with info icon

**Behavior**
- Slide in from top-right corner
- Auto-dismiss after 5 seconds (except errors)
- Manual dismiss option
- Stack multiple notifications
- Responsive positioning

---

## User Experience Flows (MVP)

### Anonymous User Journey
**Initial Landing**
1. User arrives at landing page
2. Sees clear value proposition: "Translate books instantly"
3. Views simple upload form with Google Translate (free)
4. Sees signup prompt for access to better models

**Free Translation Flow**
1. User drags/selects file for upload
2. File validation occurs client-side
3. Selects source and target languages
4. Only Google Translate available (no premium options)
5. Clicks "Start Translation"
6. Uploads file and receives job ID
7. Redirected to progress tracking page
8. Monitors basic progress updates
9. Downloads completed translation
10. Sees signup prompt for premium models

**Signup Consideration**
1. User sees "Want better translations?" section
2. Views benefits: ChatGPT-4, Claude, Gemini Pro
3. Understands they bring their own API keys
4. No payment required, just account creation

### Registered User Journey
**Authentication Flow**
1. User clicks "Sign Up/Login" from header
2. Simple form with email/password
3. No social login complexity in MVP
4. Successful login redirects to main page (same layout)
5. Failed login shows clear error messages

**Enhanced Translation Flow**
1. User sees same upload form but with all models available
2. Models with 🔑 icon require API keys
3. Can select premium models if API keys configured
4. Same upload → progress → download flow
5. No file storage or history tracking

**API Key Setup**
1. User clicks "API Keys" in navigation
2. Views simple setup page for each provider
3. Adds API keys for desired models
4. Keys are encrypted and not stored permanently
5. Can test keys and update as needed

**No Complex Management**
- No dashboards or analytics
- No translation history or file management
- No billing or subscription flows
- No complex user preferences
- Files automatically cleaned up after hours

### Error Recovery Flows
**File Upload Errors**
1. Invalid file type: Clear error message with supported formats
2. File too large: Error with size limit and compression suggestions
3. Network error: Retry option with exponential backoff
4. Server error: Contact support option with error ID

**Translation Failures**
1. Processing error: Retry option with different model
2. Timeout: Explanation and retry with smaller chunks
3. Language detection failure: Manual language selection
4. API limit reached: Clear explanation and upgrade path

---

## Responsive Design Strategy

### Breakpoint System
- **Mobile**: 320px - 767px
- **Tablet**: 768px - 1023px
- **Desktop**: 1024px - 1439px
- **Large Desktop**: 1440px+

### Mobile-First Approach
**Layout Adaptations**
- Single column layouts on mobile
- Collapsible navigation with hamburger menu
- Touch-friendly button sizes (44px minimum)
- Optimized file upload for mobile cameras
- Swipe gestures for table navigation

**Component Responsive Behavior**
- Language selectors stack vertically on mobile
- Progress indicators scale to fit screen width
- Tables become horizontally scrollable
- Forms use full-width inputs with proper spacing
- Modals become full-screen on small devices

**Performance Considerations**
- Progressive image loading
- Compressed images for mobile devices
- Reduced animations on slower devices
- Optimized bundle splitting for mobile
- Service worker for offline functionality

### Tablet Optimization
- Two-column layouts where appropriate
- Larger touch targets for better usability
- Optimized keyboard interactions
- Portrait and landscape mode support
- Sidebar navigation remains accessible

### Desktop Enhancement
- Multi-column layouts for efficient space usage
- Hover states and tooltips for better UX
- Keyboard shortcuts for power users
- Drag-and-drop functionality
- Side-by-side comparison views

---

## Integration Architecture

### API Communication Strategy
**HTTP Client Configuration**
- Axios or Fetch with interceptors
- Base URL configuration for environment switching
- Request/response transformation
- Error handling with user-friendly messages
- Retry logic for network failures

**Authentication Integration**
- Bearer token storage in httpOnly cookies
- Token refresh mechanism
- Automatic logout on token expiration
- Protected route handling
- API key management for premium users

### Real-time Updates
**WebSocket Integration**
- Connection management with reconnection logic
- Progress updates for translation jobs
- Real-time status notifications
- Heartbeat mechanism for connection health
- Graceful fallback to polling if needed

**Polling Strategy**
- Progressive polling intervals (start fast, slow down)
- Exponential backoff on errors
- User-aware polling (pause when tab inactive)
- Automatic stop when job completes
- Bandwidth-conscious implementation

### File Handling
**Upload Strategy**
- Chunked uploads for large files
- Progress tracking during upload
- Resume capability for interrupted uploads
- Client-side file validation
- Preview generation for supported formats

**Download Management**
- Streaming downloads for large files
- Progress indication during download
- Automatic filename generation
- Browser compatibility handling
- Error recovery for failed downloads

### Caching Strategy
**API Response Caching**
- Translation history cached locally
- Language lists cached with long TTL
- User preferences stored locally
- Job status updates with smart invalidation
- Offline functionality for cached data

**Asset Caching**
- Component code splitting
- Lazy loading for non-critical components
- Image optimization and caching
- Font loading optimization
- Service worker for asset caching

---

## Development Workflow

### Project Setup Process
**Initial Environment**
1. Node.js version management with nvm
2. Package manager preference (npm, yarn, or pnpm)
3. Environment variable configuration
4. Local development server setup
5. Backend API connection configuration

**Development Dependencies**
1. Code formatting tools installation
2. Git hooks setup for pre-commit validation
3. Testing framework configuration
4. Storybook setup for component development
5. Build tools and optimization plugins

### Build System Configuration
**Development Build**
- Hot module replacement for fast iteration
- Source maps for debugging
- Development-only features (Redux DevTools)
- Mock API integration for offline development
- Error overlay for runtime errors

**Production Build**
- Code minification and tree shaking
- Asset optimization and compression
- Bundle analysis for size optimization
- Source map generation for error tracking
- Environment-specific configurations

### Testing Strategy
**Unit Testing**
- Component testing with React Testing Library
- Hook testing with specialized utilities
- Utility function testing
- Mock API responses for isolated testing
- Coverage requirements and reporting

**Integration Testing**
- User flow testing with realistic scenarios
- API integration testing with mock server
- Error handling and edge case testing
- Performance testing for critical paths
- Accessibility testing with automated tools

**End-to-End Testing**
- Full user journey automation
- Cross-browser compatibility testing
- Mobile device testing
- Performance monitoring in real browsers
- Visual regression testing

### Quality Assurance
**Code Quality Gates**
- ESLint rules for code consistency
- Prettier for automatic formatting
- TypeScript strict mode for type safety
- Pre-commit hooks for validation
- Automated dependency vulnerability scanning

**Performance Monitoring**
- Bundle size tracking and alerts
- Core Web Vitals monitoring
- User interaction tracking
- Error boundary implementation
- Performance budgets and enforcement

### Deployment Pipeline
**Staging Environment**
- Automatic deployment from feature branches
- Integration testing with staging API
- Manual QA validation process
- Performance testing in staging
- User acceptance testing platform

**Production Deployment**
- Automated deployment from main branch
- Blue-green deployment strategy
- Database migration coordination
- Feature flag management
- Rollback procedures and monitoring

**Monitoring and Maintenance**
- Application performance monitoring
- Error tracking and alerting
- User analytics and behavior tracking
- A/B testing framework
- Regular dependency updates and security patches

---

## Additional Considerations

### Accessibility Requirements
- WCAG 2.1 AA compliance
- Keyboard navigation support
- Screen reader optimization
- Color contrast validation
- Focus management for dynamic content

### Security Measures
- Content Security Policy implementation
- XSS prevention mechanisms
- Secure file upload validation
- API request sanitization
- User data protection compliance

### Performance Targets
- First Contentful Paint: < 1.5s
- Largest Contentful Paint: < 2.5s
- Cumulative Layout Shift: < 0.1
- First Input Delay: < 100ms
- Bundle size: < 300KB gzipped

### Internationalization
- Multi-language UI support
- RTL language compatibility
- Cultural adaptation for different markets
- Date and number formatting
- Currency and region-specific features

This comprehensive design guide provides the foundation for building a professional, user-friendly frontend for the bilingual book translation service while maintaining focus on the freemium business model and seamless user experience.