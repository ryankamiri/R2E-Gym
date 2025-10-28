#!/bin/bash
# Example SLURM job script for running golden patch timing on HPC

#SBATCH --job-name=r2egym_timing
#SBATCH --time=01:00:00          # 1 hour should be enough for most environments
#SBATCH --partition=standard     # Adjust to your cluster's partition name
#SBATCH --mem=32GB                # Adjust based on your needs
#SBATCH --cpus-per-task=4        # Adjust based on your needs
#SBATCH --output=timing_%j.out
#SBATCH --error=timing_%j.err

# Print the job information
echo "Job ID: $SLURM_JOB_ID"
echo "Job started at: $(date)"
echo "Running on node: $(hostname)"
echo "Working directory: $(pwd)"

# Load required modules (adjust for your cluster)
module load anaconda     # or whatever conda module you have
# module load docker      # if needed on your cluster

# Get the Python executable
PYTHON=$(which python)

# Activate conda environment
echo "Activating conda environment..."
source $(conda info --base)/etc/profile.d/conda.sh
conda activate r2egym

# Set Python path
export PYTHONPATH="${PYTHONPATH}:$(pwd)/src"

# Set the environment index from command line argument or default
ENV_IDX=${1:-0}

echo "Running golden patch timing for environment index: $ENV_IDX"

# Run the timing script
python time_golden_patch.py \
    --dataset "R2E-Gym/R2E-Gym-Lite" \
    --split "train" \
    --env_idx $ENV_IDX \
    --backend docker

# Print completion info
echo "Job completed at: $(date)"
echo "Check timing_results/ for results"

