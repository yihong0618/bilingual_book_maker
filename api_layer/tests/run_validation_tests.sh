#!/bin/bash
# Integration test runner for file validation
# Starts Docker container and runs validation tests

set -e  # Exit on any error

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}=== File Validation Integration Test Runner ===${NC}"

# Configuration
CONTAINER_NAME="bilingual-api-test"
IMAGE_NAME="bilingual-book-maker"
API_PORT="8000"
API_URL="http://localhost:${API_PORT}"

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

# Build the image if it doesn't exist
if ! docker image inspect ${IMAGE_NAME} >/dev/null 2>&1; then
    echo -e "${YELLOW}Building Docker image...${NC}"
    cd "$(dirname "$0")/../.."  # Go to project root
    docker build -t ${IMAGE_NAME} .
fi

# Stop and remove existing container if running
echo -e "${YELLOW}Stopping existing container...${NC}"
docker stop ${CONTAINER_NAME} 2>/dev/null || true
docker rm ${CONTAINER_NAME} 2>/dev/null || true

# Start the container
echo -e "${YELLOW}Starting Docker container...${NC}"
docker run -d \
    --name ${CONTAINER_NAME} \
    -p ${API_PORT}:8000 \
    -e ENVIRONMENT=development \
    ${IMAGE_NAME}

# Wait for container to be ready
echo -e "${YELLOW}Waiting for API to be ready...${NC}"
for i in {1..30}; do
    if curl -s ${API_URL}/health >/dev/null 2>&1; then
        echo -e "${GREEN}API is ready!${NC}"
        break
    fi
    echo -n "."
    sleep 1
    if [ $i -eq 30 ]; then
        echo -e "\n${RED}Error: API failed to start within 30 seconds${NC}"
        docker logs ${CONTAINER_NAME}
        exit 1
    fi
done

# Run the validation tests
echo -e "\n${BLUE}Running validation tests...${NC}"
cd "$(dirname "$0")"
python3 integration_validation_test.py --url ${API_URL}

# Check exit code
if [ $? -eq 0 ]; then
    echo -e "\n${GREEN}✅ All validation tests passed!${NC}"
    exit 0
else
    echo -e "\n${RED}❌ Some validation tests failed!${NC}"
    echo -e "${YELLOW}Container logs:${NC}"
    docker logs ${CONTAINER_NAME} --tail 50
    exit 1
fi