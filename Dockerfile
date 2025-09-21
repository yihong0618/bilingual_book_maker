FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Copy the entire project
COPY . .

# Install API layer dependencies (skip system packages for now)
RUN pip install --no-cache-dir -r api_layer/requirements.txt

# Set working directory to api_layer
WORKDIR /app/api_layer

# Create necessary directories
RUN mkdir -p uploads outputs temp

# Set environment variables for production
ENV ENVIRONMENT=production
ENV API_HOST=0.0.0.0
ENV API_PORT=8000
ENV DEBUG=false
ENV PYTHONPATH=/app

# Expose port
EXPOSE 8000

# No health check since curl won't be available
# HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
#     CMD curl -f http://localhost:8000/health || exit 1

# Run the application
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]