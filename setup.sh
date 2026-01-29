#!/bin/bash

###############################################################################
# Initial Setup Script
#
# Prepares the environment and validates prerequisites for the
# Distributed Log Anomaly Detection System.
###############################################################################

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}  Distributed Log Anomaly Detection - Setup${NC}"
echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
echo ""

# Check Docker
echo -e "${BLUE}→ Checking Docker installation...${NC}"
if ! command -v docker &> /dev/null; then
    echo -e "${RED}✗ Docker is not installed${NC}"
    echo "  Please install Docker from https://docs.docker.com/get-docker/"
    exit 1
fi

DOCKER_VERSION=$(docker --version | grep -oE '[0-9]+\.[0-9]+' | head -1)
echo -e "${GREEN}✓ Docker ${DOCKER_VERSION} detected${NC}"

# Check Docker Compose
echo -e "${BLUE}→ Checking Docker Compose installation...${NC}"
if ! command -v docker-compose &> /dev/null; then
    echo -e "${RED}✗ Docker Compose is not installed${NC}"
    echo "  Please install Docker Compose from https://docs.docker.com/compose/install/"
    exit 1
fi

COMPOSE_VERSION=$(docker-compose --version | grep -oE '[0-9]+\.[0-9]+' | head -1)
echo -e "${GREEN}✓ Docker Compose ${COMPOSE_VERSION} detected${NC}"

# Check available ports
echo -e "${BLUE}→ Checking required ports...${NC}"
REQUIRED_PORTS=(5432 8501 9092 9094 2181)
PORTS_OK=true

for port in "${REQUIRED_PORTS[@]}"; do
    if lsof -Pi :$port -sTCP:LISTEN -t >/dev/null 2>&1 || netstat -tuln 2>/dev/null | grep -q ":$port "; then
        echo -e "${YELLOW}⚠ Port $port is already in use${NC}"
        PORTS_OK=false
    fi
done

if [ "$PORTS_OK" = true ]; then
    echo -e "${GREEN}✓ All required ports are available${NC}"
else
    echo -e "${YELLOW}⚠ Some ports are in use. Please stop conflicting services or the system may fail to start.${NC}"
fi

# Check system resources
echo -e "${BLUE}→ Checking system resources...${NC}"

if command -v free &> /dev/null; then
    TOTAL_MEM=$(free -g | awk '/^Mem:/{print $2}')
    if [ "$TOTAL_MEM" -lt 4 ]; then
        echo -e "${YELLOW}⚠ System has ${TOTAL_MEM}GB RAM. 4GB minimum recommended.${NC}"
    else
        echo -e "${GREEN}✓ System has ${TOTAL_MEM}GB RAM${NC}"
    fi
fi

# Create shared directory
echo -e "${BLUE}→ Creating shared directory...${NC}"
mkdir -p shared
echo -e "${GREEN}✓ Shared directory created${NC}"

# Make scripts executable
echo -e "${BLUE}→ Making scripts executable...${NC}"
chmod +x run_experiment.sh 2>/dev/null || true
chmod +x run_batch_experiments.sh 2>/dev/null || true
echo -e "${GREEN}✓ Scripts are executable${NC}"

# Pull Docker images
echo -e "${BLUE}→ Pulling required Docker images (this may take a few minutes)...${NC}"
docker-compose pull

echo ""
echo -e "${GREEN}═══════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  Setup Complete!${NC}"
echo -e "${GREEN}═══════════════════════════════════════════════════════════${NC}"
echo ""
echo -e "Next steps:"
echo -e "  1. Start the system: ${YELLOW}docker-compose up -d${NC}"
echo -e "  2. View logs: ${YELLOW}docker-compose logs -f${NC}"
echo -e "  3. Access dashboard: ${YELLOW}http://localhost:8501${NC}"
echo -e "  4. Run experiments: ${YELLOW}./run_experiment.sh <name> <p> <c> <parts>${NC}"
echo ""
echo -e "For detailed usage, see README.md"
echo ""