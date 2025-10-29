#!/bin/bash
#SBATCH --partition=short
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=32GB
#SBATCH --time=02:00:00
#SBATCH --job-name=r2egym_random_test
#SBATCH --output=logs/%x.%j.out

cd $SLURM_SUBMIT_DIR    # ensure we start where sbatch was called

# load env
source ~/.bashrc
eval "$(conda shell.bash hook)"
conda activate r2egym

# Set Python path
export PYTHONPATH="${PYTHONPATH}:$(pwd)/src"
export PYTHONUNBUFFERED=1

# Get number of instances from command line argument or use default (18)
NUM_INSTANCES=${1:-18}

# run script
python3 -u test_random_golden_patches.py \
    --dataset "R2E-Gym/R2E-Gym-Lite" \
    --split "train" \
    --backend apptainer \
    --num_instances ${NUM_INSTANCES}