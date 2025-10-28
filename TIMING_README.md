# Golden Patch Timing Script

This directory contains scripts to time the execution of golden patches (ground truth patches) on R2E-Gym environments.

## Quick Start

### 1. Setup (One-time)

```bash
# Make scripts executable
chmod +x setup_conda_env.sh
chmod +x run_timing_job.sh

# Run the setup
./setup_conda_env.sh
```

### 2. Run Locally

```bash
# Activate the environment
conda activate r2egym

# Run timing on first environment
python time_golden_patch.py --env_idx 0
```

### 3. Run on HPC (SLURM)

```bash
# Submit a job for environment index 0
sbatch run_timing_job.sh 0

# Submit for multiple environments
sbatch run_timing_job.sh 0
sbatch run_timing_job.sh 1
sbatch run_timing_job.sh 2
```

## What Gets Timed

The script times the following operations:

1. **Load dataset entry** - Loading the environment data from HuggingFace
2. **Get golden patch** - Extracting the ground truth patch
3. **Initialize environment** - Setting up the Docker container
4. **Apply golden patch** - Applying the patch to the codebase
5. **Calculate reward (run tests)** - Running the test suite to verify the patch
6. **Close environment** - Cleaning up the Docker container

## Output

Results are saved in two places:

1. **Timing logs**: `timing_logs/golden_patch_<env_idx>.log` - Detailed execution logs
2. **Timing results**: `timing_results/timing_<env_idx>_<timestamp>.json` - JSON file with all timings

## Command Line Options

```bash
python time_golden_patch.py --help
```

Available options:
- `--dataset`: HuggingFace dataset name (default: R2E-Gym/R2E-Gym-Lite)
- `--split`: Dataset split (default: train)
- `--env_idx`: Index of environment to test (default: 0)
- `--backend`: Backend to use - docker or kubernetes (default: docker)

## Example Usage

```bash
# Test on R2E-Gym-Lite
python time_golden_patch.py --env_idx 0

# Test on SWE-Bench-Verified
python time_golden_patch.py \
    --dataset "R2E-Gym/SWE-Bench-Verified" \
    --split "test" \
    --env_idx 0

# Use Kubernetes backend
python time_golden_patch.py --env_idx 0 --backend kubernetes

# Test multiple environments in a loop
for i in {0..4}; do
    python time_golden_patch.py --env_idx $i
done
```

## Understanding the Results

The script outputs a summary table showing:
- Individual operation timings
- Total execution time
- Reward (1 = success, 0 = failure)
- Success status

Example output:
```
================================================================================
Timing Results for Environment 0
================================================================================
Docker Image: r2egym/aiohttp_4aaf91
--------------------------------------------------------------------------------
Initialize environment              :   45.30s
Calculate reward (run tests)       :  180.50s
Close environment                  :   15.20s
Load dataset entry                  :    2.50s
Apply golden patch                  :    1.20s
Get golden patch                    :    0.05s
--------------------------------------------------------------------------------
TOTAL TIME                          :  244.75s
REWARD                              : 1
SUCCESS                             : YES
================================================================================
```

## Troubleshooting

### Common Issues

1. **Conda not found**
   ```bash
   module load anaconda
   ```

2. **Import errors**
   ```bash
   export PYTHONPATH="${PYTHONPATH}:$(pwd)/src"
   ```

3. **Docker permission errors**
   ```bash
   # Add your user to the docker group (may require admin)
   sudo usermod -aG docker $USER
   # Log out and back in
   ```

4. **Out of memory**
   - Increase SLURM job memory: `#SBATCH --mem=64GB`
   - Or use smaller environments

5. **Network issues downloading images**
   - Check network connectivity
   - Some HPC clusters may have restrictions on external network access
   - Contact your cluster admin if needed

## Files Created

- `time_golden_patch.py` - Main timing script
- `setup_conda_env.sh` - Conda environment setup
- `run_timing_job.sh` - Example SLURM job script
- `CONDA_SETUP_INSTRUCTIONS.md` - Detailed setup instructions
- `timing_logs/` - Execution logs
- `timing_results/` - JSON results files

## Multiple Environments

To time multiple environments:

```bash
# Bash loop
for idx in {0..9}; do
    python time_golden_patch.py --env_idx $idx
done

# Or use GNU parallel
parallel -j 4 "python time_golden_patch.py --env_idx {}" ::: {0..9}
```

## Performance Notes

Expected timings per environment:
- Small environments: 30-60 seconds
- Medium environments: 1-3 minutes
- Large environments: 3-10 minutes

The longest operation is typically "Calculate reward" (running tests).

## Next Steps

After running the timing script, you can:
1. Analyze the results to find performance bottlenecks
2. Compare timings across different environments
3. Use the results to optimize your workflow
4. Create visualizations of the timing data

