#!/bin/bash

# Simple setup script for testing environment

echo "🔧 Setting up test environment..."

# Activate virtual environment
source venv/bin/activate

# Install all dependencies
echo "📦 Installing dependencies..."
pip install -r requirements.txt

echo ""
echo "✅ Setup complete! Now you can run:"
echo ""
echo "   # Run working tests"
echo "   python -m pytest tests/test_job_manager.py tests/test_progress_monitor.py -v"
echo ""
echo "   # Run with coverage"
echo "   python -m pytest tests/test_job_manager.py tests/test_progress_monitor.py --cov=api --cov-report=html"
echo ""
echo "   # Run all tests (some may fail due to test logic, not dependencies)"
echo "   python -m pytest tests/ -v"