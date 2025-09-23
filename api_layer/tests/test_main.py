"""
Simple test version of the FastAPI app to verify Docker setup
"""

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import uvicorn

app = FastAPI(title="Bilingual Book Maker API - Test Version", version="1.0.0")


class HealthResponse(BaseModel):
    status: str
    message: str


@app.get("/")
async def root():
    """Root endpoint"""
    return {"message": "Bilingual Book Maker API is running!"}


@app.get("/health", response_model=HealthResponse)
async def health_check():
    """Health check endpoint"""
    return HealthResponse(status="healthy", message="API is running successfully")


@app.get("/test")
async def test_endpoint():
    """Test endpoint to verify functionality"""
    return {"test": "success", "endpoints": ["/", "/health", "/test", "/docs"]}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
