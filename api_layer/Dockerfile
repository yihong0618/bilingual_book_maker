FROM python:3.11-slim

WORKDIR /app

# Copy the entire bilingual_book_maker project first
COPY .. /app/

# Try to install main project dependencies (may fail due to complex deps)
RUN pip install --no-cache-dir -r requirements.txt || echo "Main requirements failed, continuing..."

# Set working directory to api_layer and copy its requirements
WORKDIR /app/api_layer
COPY requirements.txt /app/api_layer/requirements.txt

# Install API layer specific dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Try to install essential packages individually
RUN pip install --no-cache-dir beautifulsoup4 ebooklib tqdm langdetect requests bs4 rich lxml || echo "Some deps failed"

# Ensure API dependencies are available
RUN pip install --no-cache-dir fastapi uvicorn[standard] python-multipart pydantic

# Expose the port the app runs on
EXPOSE 8000

# Set Python path to include the parent directory
ENV PYTHONPATH=/app:$PYTHONPATH

# Run the original API server (using environment variables for host/port)
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]