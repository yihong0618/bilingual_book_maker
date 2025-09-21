#!/bin/bash

# Test runner script to help debug issues

echo "🧪 Testing environment setup..."

# Change to api_layer directory
cd "$(dirname "$0")"
echo "📁 Current directory: $(pwd)"

# Check if virtual environment exists
if [ ! -d "venv" ]; then
    echo "❌ Virtual environment not found! Please create one first:"
    echo "   python -m venv venv"
    echo "   source venv/bin/activate"
    echo "   pip install -r requirements.txt"
    exit 1
fi

# Activate virtual environment
echo "🔄 Activating virtual environment..."
source venv/bin/activate

# Check Python version
echo "🐍 Python version: $(python --version)"

# Check critical packages
echo "📦 Checking installed packages..."
for pkg in pytest pydantic fastapi tqdm; do
    if python -c "import $pkg" 2>/dev/null; then
        version=$(python -c "import $pkg; print($pkg.__version__)" 2>/dev/null || echo "unknown")
        echo "   ✅ $pkg ($version)"
    else
        echo "   ❌ $pkg - MISSING!"
        echo "      Install with: pip install $pkg"
    fi
done

# Check if we can import our modules
echo "🔍 Testing module imports..."
python -c "
try:
    from api.models import TranslationModel, JobStatus
    from api.job_manager import JobManager
    from api.progress_monitor import AsyncProgressTracker
    print('   ✅ All API modules import successfully')
except ImportError as e:
    print(f'   ❌ Import error: {e}')
    exit(1)
"

if [ $? -ne 0 ]; then
    echo "❌ Module import failed!"
    exit 1
fi

# Run tests
echo ""
echo "🧪 Running tests..."
echo "==============================================="

# Try simple test first
echo "🔸 Testing single simple test..."
python -m pytest tests/test_job_manager.py::TestJobManager::test_create_job -v

if [ $? -eq 0 ]; then
    echo "✅ Simple test passed!"
    echo ""
    echo "🔸 Running all job manager tests..."
    python -m pytest tests/test_job_manager.py -v

    echo ""
    echo "🔸 Running all progress monitor tests..."
    python -m pytest tests/test_progress_monitor.py -v

    echo ""
    echo "🔸 Running working tests with coverage..."
    python -m pytest tests/test_job_manager.py tests/test_progress_monitor.py --cov=api --cov-report=term-missing
else
    echo "❌ Simple test failed!"
fi