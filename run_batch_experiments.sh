#!/bin/bash

###############################################################################
# Automated Experiment Suite Runner
#
# Runs a comprehensive set of experiments to evaluate system performance
# across different configurations of producers, consumers, and partitions.
###############################################################################

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Default configuration
DURATION=${1:-5}  # Default 5 minutes per experiment
COOLDOWN=${2:-30}  # Default 30 seconds between experiments

echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}  Automated Experiment Suite${NC}"
echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
echo ""
echo -e "  Duration per experiment: ${YELLOW}${DURATION} minutes${NC}"
echo -e "  Cooldown between experiments: ${YELLOW}${COOLDOWN} seconds${NC}"
echo ""

# Ensure script is executable
chmod +x run_experiment.sh

# Define experiments (name, producers, consumers, partitions)
experiments=(
    "baseline-1p-1c-1part 1 1 1"
    "baseline-1p-1c-3part 1 1 3"
    "scale-producers-2p 2 1 3"
    "scale-producers-3p 3 1 3"
    "scale-consumers-2c 1 2 3"
    "scale-consumers-3c 1 3 3"
    "balanced-2p-2c 2 2 3"
    "balanced-3p-3c 3 3 3"
    "high-partitions 2 2 6"
    "stress-test-4p-4c 4 4 6"
)

total_experiments=${#experiments[@]}
current=1

for exp in "${experiments[@]}"; do
    read -r name producers consumers partitions <<< "$exp"

    echo ""
    echo -e "${GREEN}═══════════════════════════════════════════════════════════${NC}"
    echo -e "${GREEN}  Experiment ${current}/${total_experiments}: ${name}${NC}"
    echo -e "${GREEN}═══════════════════════════════════════════════════════════${NC}"

    # Run experiment
    ./run_experiment.sh "$name" "$producers" "$consumers" "$partitions" "$DURATION"

    # Cooldown between experiments (except after last one)
    if [ $current -lt $total_experiments ]; then
        echo ""
        echo -e "${YELLOW}→ Cooldown period: ${COOLDOWN} seconds...${NC}"
        sleep $COOLDOWN
    fi

    current=$((current + 1))
done

echo ""
echo -e "${GREEN}═══════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  All Experiments Completed!${NC}"
echo -e "${GREEN}═══════════════════════════════════════════════════════════${NC}"
echo ""
echo -e "  View comprehensive results in the dashboard:"
echo -e "  ${YELLOW}http://localhost:8501${NC}"
echo ""