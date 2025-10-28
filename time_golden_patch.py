#!/usr/bin/env python3
"""
Script to time the golden patch execution flow on HPC cluster.

This script:
1. Loads an environment from the R2E-Gym dataset
2. Applies the golden patch (ground truth patch)
3. Calculates the reward by running tests
4. Times each operation separately

Usage:
    python time_golden_patch.py --env_idx 0 --dataset "R2E-Gym/R2E-Gym-Lite" --split "train"
"""

import time
import argparse
import sys
from pathlib import Path
from typing import Dict, Any, Optional
from datetime import datetime

# Add the project to the path
sys.path.insert(0, str(Path(__file__).parent))

from r2egym.logging import setup_logging, INFO
from r2egym.agenthub.environment.env import EnvArgs, RepoEnv
from r2egym.agenthub.runtime.apptainer import ApptainerRuntime
from datasets import load_dataset


class TimingResults:
    """Stores timing results for each operation."""
    
    def __init__(self, env_idx: int, docker_image: str):
        self.env_idx = env_idx
        self.docker_image = docker_image
        self.timings: Dict[str, float] = {}
        self.reward: Optional[float] = None
        self.success: bool = False
        
    def add_timing(self, operation: str, duration: float):
        """Add a timing measurement."""
        self.timings[operation] = duration
        
    def print_summary(self):
        """Print a summary of timings."""
        print("\n" + "="*80)
        print(f"Timing Results for Environment {self.env_idx}")
        print("="*80)
        print(f"Docker Image: {self.docker_image}")
        print("-"*80)
        
        # Sort timings by duration
        sorted_timings = sorted(self.timings.items(), key=lambda x: x[1], reverse=True)
        
        total_time = 0
        for operation, duration in sorted_timings:
            print(f"{operation:40s}: {duration:8.2f}s")
            total_time += duration
            
        print("-"*80)
        print(f"{'TOTAL TIME':40s}: {total_time:8.2f}s")
        
        if self.reward is not None:
            print(f"{'REWARD':40s}: {self.reward}")
            print(f"{'SUCCESS':40s}: {'YES' if self.success else 'NO'}")
        
        print("="*80 + "\n")
        
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            'env_idx': self.env_idx,
            'docker_image': self.docker_image,
            'timings': self.timings,
            'reward': self.reward,
            'success': self.success,
            'total_time': sum(self.timings.values())
        }


def load_dataset_entry(dataset: str, split: str, env_idx: int) -> Dict[str, Any]:
    """
    Load a specific entry from the dataset.
    
    Args:
        dataset: Name of the HuggingFace dataset
        split: Dataset split (train/test)
        env_idx: Index of the environment to load
        
    Returns:
        Dictionary containing the dataset entry
    """
    print(f"Loading dataset: {dataset}, split: {split}, index: {env_idx}")
    ds = load_dataset(dataset, split=split)
    
    if env_idx >= len(ds):
        raise ValueError(f"Index {env_idx} is out of range. Dataset has {len(ds)} entries.")
    
    entry = ds[env_idx]
    print(f"Loaded environment: {entry.get('docker_image', 'Unknown')}")
    return entry


def get_golden_patch(ds: Dict[str, Any]) -> str:
    """
    Extract the golden patch from the dataset entry.
    
    Args:
        ds: Dataset entry
        
    Returns:
        The golden patch string
    """
    if 'patch' in ds:
        return ds['patch']
    else:
        raise ValueError("No 'patch' key found in dataset entry. Cannot get golden patch.")


def time_golden_patch_execution(
    dataset: str = "R2E-Gym/R2E-Gym-Lite",
    split: str = "train",
    env_idx: int = 0,
    backend: str = "docker"
) -> TimingResults:
    """
    Time the execution of a golden patch on a specific environment.
    
    Args:
        dataset: Name of the dataset to use
        split: Dataset split (train/test)
        env_idx: Index of the environment to use
        backend: Backend to use (docker or kubernetes)
        
    Returns:
        TimingResults object with all timing information
    """
    
    results = TimingResults(env_idx=env_idx, docker_image="")
    
    # Setup logging
    logger = setup_logging(
        name=f"golden_patch_timing_{env_idx}",
        log_file=f"timing_logs/golden_patch_{env_idx}.log",
        console=True,
        level=INFO,
    )
    
    try:
        # Step 1: Load the dataset entry
        start_time = time.time()
        ds = load_dataset_entry(dataset, split, env_idx)
        results.docker_image = ds.get('docker_image', 'Unknown')
        load_time = time.time() - start_time
        results.add_timing("Load dataset entry", load_time)
        logger.info(f"Loaded dataset entry in {load_time:.2f}s")
        
        # Step 2: Get the golden patch
        start_time = time.time()
        golden_patch = get_golden_patch(ds)
        patch_get_time = time.time() - start_time
        results.add_timing("Get golden patch", patch_get_time)
        logger.info(f"Retrieved golden patch in {patch_get_time:.2f}s")
        logger.info(f"Golden patch length: {len(golden_patch)} characters")
        
        # Step 3: Initialize the environment
        start_time = time.time()
        env_args = EnvArgs(ds=ds)
        env = RepoEnv(env_args, logger=logger, backend=backend)
        env.reset()
        env_init_time = time.time() - start_time
        results.add_timing("Initialize environment", env_init_time)
        logger.info(f"Initialized environment in {env_init_time:.2f}s")
        
        # Step 4: Apply the golden patch
        start_time = time.time()
        apply_output, apply_error_code = env.runtime.apply_patch(golden_patch)
        patch_apply_time = time.time() - start_time
        results.add_timing("Apply golden patch", patch_apply_time)
        logger.info(f"Applied golden patch in {patch_apply_time:.2f}s")
        logger.info(f"Apply output: {apply_output}")
        logger.info(f"Apply error code: {apply_error_code}")
        
        if apply_error_code != "0":
            logger.warning(f"Patch application had error code: {apply_error_code}")
            results.success = False
        
        # Step 5: Calculate reward (run tests)
        start_time = time.time()
        reward, test_output = env.runtime._calculate_reward(get_test_output=True, timeout=300)
        reward_calc_time = time.time() - start_time
        results.add_timing("Calculate reward (run tests)", reward_calc_time)
        results.reward = reward
        results.success = (reward == 1.0)
        logger.info(f"Calculated reward in {reward_calc_time:.2f}s")
        logger.info(f"Reward: {reward}")
        
        # Step 6: Close the environment
        start_time = time.time()
        env.close()
        env_close_time = time.time() - start_time
        results.add_timing("Close environment", env_close_time)
        logger.info(f"Closed environment in {env_close_time:.2f}s")
        
        logger.info("Golden patch timing completed successfully!")
        
    except Exception as e:
        logger.error(f"Error during golden patch timing: {e}", exc_info=True)
        results.success = False
        
    return results


def main():
    """Main entry point for the script."""
    parser = argparse.ArgumentParser(
        description="Time the execution of a golden patch on R2E-Gym environment"
    )
    
    parser.add_argument(
        "--dataset",
        type=str,
        default="R2E-Gym/R2E-Gym-Lite",
        help="HuggingFace dataset name (default: R2E-Gym/R2E-Gym-Lite)"
    )
    
    parser.add_argument(
        "--split",
        type=str,
        default="train",
        help="Dataset split (default: train)"
    )
    
    parser.add_argument(
        "--env_idx",
        type=int,
        default=0,
        help="Index of the environment to test (default: 0)"
    )
    
    parser.add_argument(
        "--backend",
        type=str,
        default="docker",
        choices=["docker", "kubernetes", "apptainer"],
        help="Backend to use (default: docker)"
    )
    
    args = parser.parse_args()
    
    # Print configuration
    print("\n" + "="*80)
    print("Golden Patch Timing Configuration")
    print("="*80)
    print(f"Dataset:     {args.dataset}")
    print(f"Split:       {args.split}")
    print(f"Environment: {args.env_idx}")
    print(f"Backend:     {args.backend}")
    print("="*80 + "\n")
    
    # Run the timing
    results = time_golden_patch_execution(
        dataset=args.dataset,
        split=args.split,
        env_idx=args.env_idx,
        backend=args.backend
    )
    
    # Print summary
    results.print_summary()
    
    # Save results to file
    output_dir = Path("timing_results")
    output_dir.mkdir(exist_ok=True)
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_file = output_dir / f"timing_{args.env_idx}_{timestamp}.json"
    
    import json
    with open(output_file, 'w') as f:
        json.dump(results.to_dict(), f, indent=2)
    
    print(f"Results saved to: {output_file}")
    
    return 0 if results.success else 1


if __name__ == "__main__":
    sys.exit(main())

