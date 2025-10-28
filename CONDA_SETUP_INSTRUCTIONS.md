# Conda Setup Instructions for HPC Cluster

This guide will help you set up the conda environment on your HPC cluster to run the golden patch timing script.

## Prerequisites

1. Access to your HPC cluster
2. Conda environment manager available on the cluster
3. Network access to download packages and Docker images

## Step 1: Load Conda Module on HPC

On most HPC clusters, you need to load the conda module first:

```bash
# Common commands for different clusters:
module load anaconda        # OR
module load conda          # OR
module load miniconda      # OR just use the system conda if available
```

Check if conda is available:
```bash
which conda
```

## Step 2: Clone/Navigate to the R2E-Gym Directory

Make sure you're in the R2E-Gym project directory:

```bash
cd /path/to/R2E-Gym
```

## Step 3: Run the Setup Script

Make the setup script executable and run it:

```bash
chmod +x setup_conda_env.sh
./setup_conda_env.sh
```

This script will:
- Create a conda environment named `r2egym`
- Install all necessary dependencies
- Install Docker client for Docker backend
- Install HuggingFace datasets
- Install the R2E-Gym package in editable mode

**Expected time:** 5-10 minutes depending on network speed.

## Step 4: Activate the Conda Environment

After the setup completes, activate the environment:

```bash
conda activate r2egym
```

## Step 5: Verify Installation

Check that everything is installed correctly:

```bash
python -c "import r2egym; print('R2E-Gym imported successfully')"
```

If you see an error, you may need to add the project to Python path:

```bash
export PYTHONPATH="${PYTHONPATH}:$(pwd)/src"
```

## Step 6: Run the Golden Patch Timing Script

Now you can run the timing script:

```bash
# Basic usage - test on first environment
python time_golden_patch.py --env_idx 0

# Test on a specific environment from SWE-Bench-Verified
python time_golden_patch.py --dataset "R2E-Gym/SWE-Bench-Verified" --split "test" --env_idx 0

# Use Kubernetes backend instead of Docker
python time_golden_patch.py --env_idx 0 --backend kubernetes

# Get help
python time_golden_patch.py --help
```

## Troubleshooting

### Issue: Conda command not found

**Solution:** Load the conda module:
```bash
module load anaconda
# or
module load conda
```

### Issue: Docker not available

If Docker is not available on the compute nodes, you can:
1. Use Kubernetes backend: `--backend kubernetes`
2. Or run on a login node that has Docker access

### Issue: Permission denied

Make the script executable:
```bash
chmod +x setup_conda_env.sh
```

### Issue: Python path errors

Add the project to Python path:
```bash
export PYTHONPATH="${PYTHONPATH}:$(pwd)/src"
```

You can add this to your `~/.bashrc` to make it permanent.

### Issue: Out of disk space

If you're running out of disk space:
1. Use conda clean:
   ```bash
   conda clean --all
   ```
2. Check Docker images taking up space:
   ```bash
   docker images
   docker system prune -a  # WARNING: Removes all unused images
   ```

## Expected Output

When you run the timing script, you should see output like:

```
================================================================================
Golden Patch Timing Configuration
================================================================================
Dataset:     R2E-Gym/R2E-Gym-Lite
Split:       train
Environment: 0
Backend:     docker
================================================================================

Loading dataset entry...
Loaded environment: ...
Retrieved golden patch in ... seconds
Applied golden patch in ... seconds
Calculated reward in ... seconds

================================================================================
Timing Results for Environment 0
================================================================================
Docker Image: r2egym/...
--------------------------------------------------------------------------------
Load dataset entry                  :    2.50s
Initialize environment              :   45.30s
Apply golden patch                  :    1.20s
Calculate reward (run tests)       :  180.50s
Get golden patch                    :    0.05s
Close environment                  :   15.20s
--------------------------------------------------------------------------------
TOTAL TIME                          :  244.75s
REWARD                              : 1
SUCCESS                             : YES
================================================================================

Results saved to: timing_results/timing_0_20240101_120000.json
```

## Running on Compute Nodes

If you need to run this on compute nodes via a job scheduler (SLURM, PBS, etc.):

### Example SLURM Job Script:

```bash
#!/bin/bash
#SBATCH --job-name=r2egym_timing
#SBATCH --time=01:00:00
#SBATCH --partition=your_partition
#SBATCH --mem=32GB
#SBATCH --output=timing_%j.out
#SBATCH --error=timing_%j.err

# Load modules
module load anaconda

# Activate environment
conda activate r2egym

# Set Python path
export PYTHONPATH="${PYTHONPATH}:$(pwd)/src"

# Run the script
python time_golden_patch.py --env_idx 0 --backend docker
```

Submit with:
```bash
sbatch timing_job.sh
```

## Manual Installation (Alternative)

If the automatic setup script doesn't work, you can install manually:

```bash
# Activate conda
conda activate r2egym

# Install core dependencies
pip install --upgrade pip
pip install uv
pip install docker datasets fire pydantic pyyaml kubernetes

# Install from project
cd /path/to/R2E-Gym
pip install -e .
```

## Using Apptainer Backend (for HPC clusters)

If your HPC cluster uses Apptainer (formerly Singularity) instead of Docker:

1. **Check Apptainer availability:**
   ```bash
   which apptainer
   apptainer --version
   ```

2. **Pre-pull Docker images (recommended to avoid timeouts):**
   ```bash
   # Pre-pull common R2E-Gym images
   bash ./prepull_apptainer_images.sh
   
   # Or pre-pull specific images
   bash ./prepull_apptainer_images.sh namanjain12/aiohttp_final:f0d74880deec8fcd982bce639c93c5e130d41198
   ```

3. **Run timing script with Apptainer:**
   ```bash
   python time_golden_patch.py --env_idx 0 --backend apptainer
   ```

4. **Apptainer automatically handles Docker images:**
   - Converts Docker URIs to Apptainer format
   - Pulls images from Docker Hub transparently
   - Caches images locally (usually in `~/.apptainer/cache/`)
   - No root privileges required

### Troubleshooting Apptainer Timeouts

If you get timeout errors when starting Apptainer instances:

1. **Pre-pull the image first:**
   ```bash
   apptainer pull docker://namanjain12/aiohttp_final:f0d74880deec8fcd982bce639c93c5e130d41198
   ```

2. **Check if image is cached:**
   ```bash
   apptainer cache list
   ```

3. **Clean cache if corrupted:**
   ```bash
   apptainer cache clean
   ```

4. **Use the pre-pull script:**
   ```bash
   bash ./prepull_apptainer_images.sh
   ```

## Additional Resources

- [R2E-Gym Documentation](README.md)
- [SWE-Bench Documentation](https://github.com/swe-bench/swebench)
- Dataset: [R2E-Gym on HuggingFace](https://huggingface.co/R2E-Gym)

## Getting Help

If you encounter issues:
1. Check the logs in `timing_logs/` directory
2. Check the timing results in `timing_results/` directory
3. Review the setup logs for error messages
4. Contact your HPC support team for cluster-specific issues

