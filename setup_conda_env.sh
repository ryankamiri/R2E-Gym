#!/bin/bash
# Setup conda environment for R2E-Gym on HPC cluster

set -e  # Exit on error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}Setting up conda environment for R2E-Gym${NC}"

# Check if conda is available
if ! command -v conda &> /dev/null; then
    echo -e "${RED}Error: conda is not available. Please load conda module first.${NC}"
    echo -e "${YELLOW}On most HPC clusters, you can do:${NC}"
    echo -e "${YELLOW}  module load anaconda${NC}"
    echo -e "${YELLOW}  # or${NC}"
    echo -e "${YELLOW}  module load conda${NC}"
    exit 1
fi

# Set environment name
ENV_NAME="r2egym"
PYTHON_VERSION="3.10"

echo -e "${GREEN}Creating conda environment: ${ENV_NAME}${NC}"
conda create -n ${ENV_NAME} python=${PYTHON_VERSION} -y

# Activate the environment
echo -e "${GREEN}Activating conda environment${NC}"
source $(conda info --base)/etc/profile.d/conda.sh
conda activate ${ENV_NAME}

# Install uv
echo -e "${GREEN}Installing uv package manager${NC}"
curl -LsSf https://astral.sh/uv/install.sh | sh

# Set up uv in the conda environment
export PATH="$HOME/.local/bin:$PATH"

# Upgrade pip
echo -e "${GREEN}Upgrading pip${NC}"
pip install --upgrade pip

# Install uv with pip (alternative method if the binary installation fails)
echo -e "${GREEN}Installing uv via pip${NC}"
pip install uv

# Install Docker client (if needed for docker backend)
echo -e "${GREEN}Installing docker-py${NC}"
pip install docker

# Install huggingface datasets
echo -e "${GREEN}Installing huggingface datasets${NC}"
pip install datasets

# Install fire (for the CLI)
echo -e "${GREEN}Installing fire${NC}"
pip install fire

# Install pydantic
echo -e "${GREEN}Installing pydantic${NC}"
pip install pydantic

# Install pyyaml
echo -e "${GREEN}Installing pyyaml${NC}"
pip install pyyaml

# Install swebench dependencies
echo -e "${GREEN}Installing swebench dependencies${NC}"
pip install kubernetes

# Try to install the R2E-Gym package in editable mode
echo -e "${GREEN}Installing R2E-Gym package${NC}"
PROJECT_ROOT=$(dirname "$(realpath "$0")")
cd "${PROJECT_ROOT}"

# Install with uv if available
if command -v uv &> /dev/null; then
    echo -e "${GREEN}Using uv to install R2E-Gym${NC}"
    uv pip install -e .
else
    echo -e "${YELLOW}uv not found, using pip${NC}"
    pip install -e .
fi

# Create necessary directories
echo -e "${GREEN}Creating necessary directories${NC}"
mkdir -p timing_logs
mkdir -p timing_results
mkdir -p run_logs

echo -e "${GREEN}Setup complete!${NC}"
echo ""
echo -e "${YELLOW}To use this environment:${NC}"
echo -e "${YELLOW}  conda activate ${ENV_NAME}${NC}"
echo ""
echo -e "${GREEN}You can now run the timing script:${NC}"
echo -e "${GREEN}  python time_golden_patch.py --env_idx 0${NC}"

