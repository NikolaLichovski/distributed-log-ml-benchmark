#!/bin/bash

###############################################################################
# Distributed Log Anomaly Detection - Experiment Runner
#
# Orchestrates systematic performance experiments with varying configurations
# of producers, consumers, and Kafka partitions.
#
# Usage: ./run_experiment.sh <name> <producers> <consumers> <partitions> [duration_minutes]
# Example: ./run_experiment.sh baseline 2 2 3 10
###############################################################################

set -e

# Color output for better readability
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
EXPERIMENT_NAME=${1:-"test"}
NUM_PRODUCERS=${2:-1}
NUM_CONSUMERS=${3:-1}
NUM_PARTITIONS=${4:-3}
DURATION_MINUTES=${5:-10}
LOG_RATE=${6:-1.0}

# Validation
if [ -z "$1" ] || [ -z "$2" ] || [ -z "$3" ] || [ -z "$4" ]; then
    echo -e "${RED}Error: Missing required arguments${NC}"
    echo "Usage: $0 <experiment_name> <num_producers> <num_consumers> <num_partitions> [duration_minutes] [log_rate]"
    echo "Example: $0 baseline 2 2 3 10 1.0"
    exit 1
fi

# Generate unique experiment ID based on timestamp
EXPERIMENT_ID=$(date +%s)

echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}  Distributed Log Anomaly Detection - Experiment Runner${NC}"
echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
echo ""
echo -e "${GREEN}Experiment Configuration:${NC}"
echo -e "  Name:        ${YELLOW}${EXPERIMENT_NAME}${NC}"
echo -e "  ID:          ${YELLOW}${EXPERIMENT_ID}${NC}"
echo -e "  Producers:   ${YELLOW}${NUM_PRODUCERS}${NC}"
echo -e "  Consumers:   ${YELLOW}${NUM_CONSUMERS}${NC}"
echo -e "  Partitions:  ${YELLOW}${NUM_PARTITIONS}${NC}"
echo -e "  Duration:    ${YELLOW}${DURATION_MINUTES} minutes${NC}"
echo -e "  Log Rate:    ${YELLOW}${LOG_RATE} msg/s per producer${NC}"
echo ""

# Export environment variables for docker-compose
export EXPERIMENT_ID=$EXPERIMENT_ID
export KAFKA_NUM_PARTITIONS=$NUM_PARTITIONS
export LOG_RATE=$LOG_RATE

# Function to check if PostgreSQL is ready
wait_for_postgres() {
    echo -e "${BLUE}→ Waiting for PostgreSQL...${NC}"
    for i in {1..30}; do
        if docker-compose exec -T db pg_isready -U postgres -d logsdb > /dev/null 2>&1; then
            echo -e "${GREEN}✓ PostgreSQL is ready${NC}"
            return 0
        fi
        sleep 1
    done
    echo -e "${RED}✗ PostgreSQL failed to start${NC}"
    return 1
}

# Function to create experiment record in database
create_experiment_record() {
    echo -e "${BLUE}→ Creating experiment record...${NC}"

    docker-compose exec -T db psql -U postgres -d logsdb <<EOF
INSERT INTO experiments (id, experiment_name, num_producers, num_consumers, num_partitions, start_time, status, notes)
VALUES (
    $EXPERIMENT_ID,
    '$EXPERIMENT_NAME',
    $NUM_PRODUCERS,
    $NUM_CONSUMERS,
    $NUM_PARTITIONS,
    NOW(),
    'running',
    'Log rate: ${LOG_RATE} msg/s per producer'
);
EOF

    if [ $? -eq 0 ]; then
        echo -e "${GREEN}✓ Experiment record created${NC}"
    else
        echo -e "${RED}✗ Failed to create experiment record${NC}"
        exit 1
    fi
}

# Function to update experiment status
update_experiment_status() {
    local status=$1
    local end_time=$2

    echo -e "${BLUE}→ Updating experiment status to '${status}'...${NC}"

    if [ "$end_time" = "true" ]; then
        docker-compose exec -T db psql -U postgres -d logsdb <<EOF
UPDATE experiments
SET status = '$status', end_time = NOW()
WHERE id = $EXPERIMENT_ID;
EOF
    else
        docker-compose exec -T db psql -U postgres -d logsdb <<EOF
UPDATE experiments
SET status = '$status'
WHERE id = $EXPERIMENT_ID;
EOF
    fi

    echo -e "${GREEN}✓ Experiment status updated${NC}"
}

# Function to display experiment summary
show_experiment_summary() {
    echo ""
    echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
    echo -e "${BLUE}  Experiment Summary${NC}"
    echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"

    docker-compose exec -T db psql -U postgres -d logsdb <<EOF
SELECT
    'Total Messages Processed' as metric,
    COUNT(*)::text as value
FROM logs
WHERE created_at >= (SELECT start_time FROM experiments WHERE id = $EXPERIMENT_ID)
UNION ALL
SELECT
    'Anomalies Detected' as metric,
    COUNT(*)::text as value
FROM anomalies a
JOIN logs l ON a.log_id = l.id
WHERE l.created_at >= (SELECT start_time FROM experiments WHERE id = $EXPERIMENT_ID)
  AND a.is_anomaly = true
UNION ALL
SELECT
    'Avg Producer Throughput (msg/s)' as metric,
    ROUND(AVG(metric_value), 2)::text as value
FROM performance_metrics
WHERE experiment_id = $EXPERIMENT_ID
  AND metric_name = 'throughput'
  AND component_type = 'producer'
UNION ALL
SELECT
    'Avg Consumer Throughput (msg/s)' as metric,
    ROUND(AVG(metric_value), 2)::text as value
FROM performance_metrics
WHERE experiment_id = $EXPERIMENT_ID
  AND metric_name = 'throughput'
  AND component_type = 'consumer'
UNION ALL
SELECT
    'Avg Processing Latency (ms)' as metric,
    ROUND(AVG(metric_value), 2)::text as value
FROM performance_metrics
WHERE experiment_id = $EXPERIMENT_ID
  AND metric_name = 'latency_avg'
UNION ALL
SELECT
    'Max Consumer Lag' as metric,
    MAX(lag)::text as value
FROM consumer_lag
WHERE experiment_id = $EXPERIMENT_ID;
EOF

    echo ""
    echo -e "${GREEN}✓ Experiment completed successfully${NC}"
    echo -e "  View detailed results in the dashboard: ${YELLOW}http://localhost:8501${NC}"
    echo ""
}

# Function to cleanup on exit
cleanup() {
    echo ""
    echo -e "${YELLOW}→ Cleaning up experiment...${NC}"

    # Mark experiment as stopped if it was running
    docker-compose exec -T db psql -U postgres -d logsdb <<EOF > /dev/null 2>&1
UPDATE experiments
SET status = 'stopped', end_time = NOW()
WHERE id = $EXPERIMENT_ID AND status = 'running';
EOF

    echo -e "${GREEN}✓ Cleanup complete${NC}"
}

# Set up signal handlers
trap cleanup EXIT INT TERM

# Main execution
echo -e "${BLUE}→ Starting infrastructure services...${NC}"
docker-compose up -d zookeeper kafka db

# Wait for PostgreSQL
wait_for_postgres

# Create experiment record
create_experiment_record

echo ""
echo -e "${BLUE}→ Scaling services to match experiment configuration...${NC}"
echo -e "  Producers: ${NUM_PRODUCERS}, Consumers: ${NUM_CONSUMERS}"

# Scale and start producers and consumers
docker-compose up -d --scale kafka-producer=$NUM_PRODUCERS --scale ml-consumer=$NUM_CONSUMERS kafka-producer ml-consumer

echo -e "${GREEN}✓ Services scaled successfully${NC}"

# Start dashboard if not already running
docker-compose up -d dashboard

echo ""
echo -e "${GREEN}═══════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  Experiment Started${NC}"
echo -e "${GREEN}═══════════════════════════════════════════════════════════${NC}"
echo ""
echo -e "  Experiment ID: ${YELLOW}${EXPERIMENT_ID}${NC}"
echo -e "  Duration: ${YELLOW}${DURATION_MINUTES} minutes${NC}"
echo -e "  Dashboard: ${YELLOW}http://localhost:8501${NC}"
echo ""
echo -e "${BLUE}→ Running experiment (press Ctrl+C to stop early)...${NC}"

# Run for specified duration
DURATION_SECONDS=$((DURATION_MINUTES * 60))
ELAPSED=0
UPDATE_INTERVAL=30

while [ $ELAPSED -lt $DURATION_SECONDS ]; do
    sleep $UPDATE_INTERVAL
    ELAPSED=$((ELAPSED + UPDATE_INTERVAL))
    REMAINING=$((DURATION_SECONDS - ELAPSED))
    REMAINING_MINUTES=$((REMAINING / 60))

    echo -e "${BLUE}  ⏱  ${ELAPSED}s elapsed, ${REMAINING_MINUTES}m remaining...${NC}"
done

echo ""
echo -e "${GREEN}✓ Experiment duration completed${NC}"

# Update experiment status
update_experiment_status "completed" "true"

# Show summary
show_experiment_summary

echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"