#!/bin/bash
# Load testing runner for configuration validation
# Tests production settings under realistic load

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}=== Configuration Load Testing ===${NC}"

# Configuration
CONTAINER_NAME="bilingual-load-test"
IMAGE_NAME="bilingual-book-maker"
API_PORT="8000"

# Function to cleanup
cleanup() {
    echo -e "\n${YELLOW}Cleaning up...${NC}"
    docker stop ${CONTAINER_NAME} 2>/dev/null || true
    docker rm ${CONTAINER_NAME} 2>/dev/null || true
}

# Set trap to cleanup on exit
trap cleanup EXIT

# Check if Docker is running
if ! docker info >/dev/null 2>&1; then
    echo -e "${RED}Error: Docker is not running${NC}"
    exit 1
fi

# Build image if needed
if ! docker image inspect ${IMAGE_NAME} >/dev/null 2>&1; then
    echo -e "${YELLOW}Building Docker image...${NC}"
    cd "$(dirname "$0")/../.."
    docker build -t ${IMAGE_NAME} .
fi

# Start container for load testing
echo -e "${YELLOW}Starting container for load testing...${NC}"
docker run -d \
    --name ${CONTAINER_NAME} \
    -p ${API_PORT}:8000 \
    -e ENVIRONMENT=production \
    -e DEBUG=false \
    ${IMAGE_NAME}

# Wait for API to be ready
echo -e "${YELLOW}Waiting for API to be ready...${NC}"
for i in {1..30}; do
    if curl -s http://localhost:${API_PORT}/health >/dev/null 2>&1; then
        echo -e "${GREEN}API is ready!${NC}"
        break
    fi
    echo -n "."
    sleep 1
    if [ $i -eq 30 ]; then
        echo -e "\n${RED}Error: API failed to start${NC}"
        docker logs ${CONTAINER_NAME}
        exit 1
    fi
done

# Install test dependencies if needed
echo -e "\n${YELLOW}Installing test dependencies...${NC}"
pip3 install aiohttp 2>/dev/null || echo "aiohttp already installed"

# Run load tests
echo -e "\n${BLUE}Running configuration load tests...${NC}"
cd "$(dirname "$0")"
python3 load_test_config.py

# Check results
if [ $? -eq 0 ]; then
    echo -e "\n${GREEN}✅ Load testing completed successfully!${NC}"
    echo -e "${BLUE}Configuration validation finished.${NC}"
else
    echo -e "\n${RED}❌ Load testing failed!${NC}"
    echo -e "${YELLOW}Check the output above for configuration issues.${NC}"
fi