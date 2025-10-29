#!/usr/bin/env python3
"""
Script to randomly sample and test multiple golden patch instances from the R2E-Gym dataset.

This script:
1. Randomly samples N instances from the dataset
2. Tests each instance using the golden patch execution flow
3. Tracks pass/fail status for each instance
4. Provides a summary with failed instances at the end

Usage:
    python test_random_golden_patches.py --num_instances 18 --backend apptainer
"""

import time
import argparse
import sys
import random
import json
from pathlib import Path
from typing import Dict, Any, Optional, List
from datetime import datetime

# Add the project to the path
sys.path.insert(0, str(Path(__file__).parent))

from r2egym.logging import setup_logging, INFO
from r2egym.agenthub.environment.env import EnvArgs, RepoEnv
from datasets import load_dataset

# Import functions from time_golden_patch.py
from time_golden_patch import (
    TimingResults,
    load_dataset_entry,
    get_golden_patch,
    time_golden_patch_execution
)


class InstanceResult:
    """Track results for a single instance."""
    
    def __init__(self, env_idx: int, docker_image: str):
        self.env_idx = env_idx
        self.docker_image = docker_image
        self.success = False
        self.reward: Optional[float] = None
        self.error_message: Optional[str] = None
        self.timings: Dict[str, float] = {}
        
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            'env_idx': self.env_idx,
            'docker_image': self.docker_image,
            'success': self.success,
            'reward': self.reward,
            'error_message': self.error_message,
            'timings': self.timings,
            'total_time': sum(self.timings.values()) if self.timings else 0.0
        }


class TestSummary:
    """Track overall test summary."""
    
    def __init__(self, total_tested: int, passed: List[InstanceResult], 
                 failed: List[InstanceResult], sampled_indices: List[int]):
        self.total_tested = total_tested
        self.passed = passed
        self.failed = failed
        self.sampled_indices = sampled_indices
        self.pass_count = len(passed)
        self.fail_count = len(failed)
        
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            'total_tested': self.total_tested,
            'pass_count': self.pass_count,
            'fail_count': self.fail_count,
            'sampled_indices': self.sampled_indices,
            'passed': [r.to_dict() for r in self.passed],
            'failed': [r.to_dict() for r in self.failed]
        }
    
    def print_summary(self):
        """Print a summary of the test results."""
        print("\n" + "="*80)
        print("Random Golden Patch Test Summary")
        print("="*80)
        print(f"Total Instances Tested: {self.total_tested}")
        print(f"Passed: {self.pass_count}")
        print(f"Failed: {self.fail_count}")
        print(f"Pass Rate: {(self.pass_count / self.total_tested * 100):.1f}%")
        print("-"*80)
        
        if self.failed:
            print("\nFAILED INSTANCES:")
            print("-"*80)
            for result in self.failed:
                print(f"  Index {result.env_idx}: {result.docker_image}")
                if result.error_message:
                    print(f"    Error: {result.error_message}")
                if result.reward is not None:
                    print(f"    Reward: {result.reward}")
                print()
        else:
            print("\nAll instances passed! ✓")
        
        print("="*80 + "\n")


def test_random_golden_patches(
    num_instances: int = 18,
    dataset: str = "R2E-Gym/R2E-Gym-Lite",
    split: str = "train",
    backend: str = "apptainer",
    seed: Optional[int] = None,
    output_dir: str = "random_test_results"
):
    """
    Test randomly sampled instances from the dataset.
    
    Args:
        num_instances: Number of instances to randomly sample
        dataset: Name of the HuggingFace dataset
        split: Dataset split (train/test)
        backend: Backend to use (docker/apptainer/kubernetes)
        seed: Random seed for reproducibility
        output_dir: Directory to save results
        
    Returns:
        TestSummary object with results
    """
    
    # Set -- set random seed if provided
    if seed is not None:
        random.seed(seed)
        print(f"Using random seed: {seed}")
    
    # Create output directories
    output_path = Path(output_dir)
    output_path.mkdir(exist_ok=True)
    logs_path = output_path / "logs"
    logs_path.mkdir(exist_ok=True)
    results_path = output_path / "results"
    results_path.mkdir(exist_ok=True)
    
    # Setup main logging
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    main_logger = setup_logging(
        name="random_golden_patch_test",
        log_file=str(logs_path / f"main_{timestamp}.log"),
        console=True,
        level=INFO,
    )
    
    main_logger.info(f"Starting random golden patch test")
    main_logger.info(f"Dataset: {dataset}")
    main_logger.info(f"Split: {split}")
    main_logger.info(f"Backend: {backend}")
    main_logger.info(f"Number of instances: {num_instances}")
    
    # Load dataset to get total size
    print(f"\nLoading dataset: {dataset}, split: {split}")
    main_logger.info(f"Loading dataset: {dataset}, split: {split}")
    ds = load_dataset(dataset, split=split)
    total_size = len(ds)
    print(f"Dataset loaded: {total_size} total instances")
    main_logger.info(f"Dataset has {total_size} total instances")
    
    # Randomly sample indices
    num_to_sample = min(num_instances, total_size)
    sampled_indices = random.sample(range(total_size), num_to_sample)
    sampled_indices.sort()  # Sort for easier reading
    
    print(f"\nRandomly sampled {num_to_sample} indices: {sampled_indices}")
    main_logger.info(f"Sampled indices: {sampled_indices}")
    
    # Track results
    passed = []
    failed = []
    
    # Test each sampled instance
    print(f"\n{'='*80}")
    print(f"Testing {num_to_sample} instances...")
    print(f"{'='*80}\n")
    
    for i, env_idx in enumerate(sampled_indices, 1):
        print(f"\n[{i}/{num_to_sample}] Testing instance {env_idx}...")
        main_logger.info(f"[{i}/{num_to_sample}] Testing instance {env_idx}")
        
        result = InstanceResult(env_idx=env_idx, docker_image="")
        
        try:
            # Use the same timing logic as time_golden_patch.py
            timing_results = time_golden_patch_execution(
                dataset=dataset,
                split=split,
                env_idx=env_idx,
                backend=backend
            )
            
            # Update result from timing results
            result.docker_image = timing_results.docker_image
            result.success = timing_results.success
            result.reward = timing_results.reward
            result.timings = timing_results.timings
            
            # Save individual result
            result_file = results_path / f"instance_{env_idx}_{timestamp}.json"
            with open(result_file, 'w') as f:
                json.dump(result.to_dict(), f, indent=2)
            
            if result.success:
                passed.append(result)
                print(f"  ✓ PASSED (Reward: {result.reward}, Time: {sum(result.timings.values()):.2f}s)")
                main_logger.info(f"Instance {env_idx} PASSED - Reward: {result.reward}")
            else:
                failed.append(result)
                print(f"  ✗ FAILED (Reward: {result.reward}, Time: {sum(result.timings.values()):.2f}s)")
                main_logger.warning(f"Instance {env_idx} FAILED - Reward: {result.reward}")
                
        except Exception as e:
            # Mark as failed with error message
            result.success = False
            result.error_message = str(e)
            failed.append(result)
            
            print(f"  ✗ ERROR: {e}")
            main_logger.error(f"Instance {env_idx} ERROR: {e}", exc_info=True)
            
            # Save individual result even on error
            result_file = results_path / f"instance_{env_idx}_{timestamp}.json"
            with open(result_file, 'w') as f:
                json.dump(result.to_dict(), f, indent=2)
    
    # Create summary
    summary = TestSummary(
        total_tested=num_to_sample,
        passed=passed,
        failed=failed,
        sampled_indices=sampled_indices
    )
    
    # Save summary
    summary_file = output_path / f"summary_{timestamp}.json"
    with open(summary_file, 'w') as f:
        json.dump(summary.to_dict(), f, indent=2)
    
    print(f"\nSummary saved to: {summary_file}")
    main_logger.info(f"Summary saved to: {summary_file}")
    
    # Print summary
    summary.print_summary()
    
    return summary


def main():
    """Main entry point for the script."""
    parser = argparse.ArgumentParser(
        description="Randomly sample and test golden patches from R2E-Gym dataset"
    )
    
    parser.add_argument(
        "--num_instances",
        type=int,
        default=18,
        help="Number of instances to randomly sample (default: 18)"
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
        "--backend",
        type=str,
        default="apptainer",
        choices=["docker", "kubernetes", "apptainer"],
        help="Backend to use (default: apptainer)"
    )
    
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Random seed for reproducibility (optional)"
    )
    
    parser.add_argument(
        "--output_dir",
        type=str,
        default="random_test_results",
        help="Directory for results (default: random_test_results)"
    )
    
    args = parser.parse_args()
    
    # Print configuration
    print("\n" + "="*80)
    print("Random Golden Patch Test Configuration")
    print("="*80)
    print(f"Dataset:       {args.dataset}")
    print(f"Split:         {args.split}")
    print(f"Backend:       {args.backend}")
    print(f"Instances:     {args.num_instances}")
    if args.seed is not None:
        print(f"Random Seed:   {args.seed}")
    print(f"Output Dir:    {args.output_dir}")
    print("="*80)
    
    # Run the test
    summary = test_random_golden_patches(
        num_instances=args.num_instances,
        dataset=args.dataset,
        split=args.split,
        backend=args.backend,
        seed=args.seed,
        output_dir=args.output_dir
    )
    
    # Return appropriate exit code
    return 0 if summary.fail_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
