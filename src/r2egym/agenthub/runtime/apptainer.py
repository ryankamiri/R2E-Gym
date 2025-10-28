import os
import subprocess
import tempfile
import concurrent.futures
import re
from typing import Tuple

from r2egym.agenthub.runtime.base import ExecutionEnvironment, CMD_TIMEOUT
from r2egym.swesmith.utils import get_test_command


##############################################################################
# Apptainer runtime
##############################################################################
class ApptainerRuntime(ExecutionEnvironment):
    """
    Apptainer runtime implementation for HPC clusters.
    Uses Apptainer (formerly Singularity) to run Docker images without requiring root privileges.
    """

    def __init__(self, ds, repo_path="/testbed", alt_path="/root", docker_image=None, 
                 command="/bin/bash", logger=None, **kwargs):
        """
        Initialize Apptainer runtime.
        
        Args:
            ds: Dataset entry containing environment information
            repo_path: Repository path in container
            alt_path: Alternative path for scripts
            docker_image: Docker image to use (optional, inferred from ds)
            command: Command to run in container
            logger: Logger instance
            **kwargs: Additional arguments
        """
        super().__init__(ds, repo_path, alt_path, docker_image, command, logger, **kwargs)
        
        # Convert Docker image to Apptainer URI if needed
        if not self.docker_image.startswith("docker://"):
            self.apptainer_image = f"docker://{self.docker_image}"
        else:
            self.apptainer_image = self.docker_image
        
        # Start the container
        self.start_container(
            self.apptainer_image, command, self.container_name, **kwargs
        )
        
        # Initialize the environment
        self.setup_env()
        self.logger.info("Apptainer environment initialized")
        self.logger.info("repo name: %s", self.repo_name)
        self.logger.info("Docker image: %s", self.docker_image)
        self.logger.info("Apptainer image: %s", self.apptainer_image)
        self.logger.info("Container name: %s", self.container_name)
    
    def setup_env(self):
        """Apptainer-specific environment setup that avoids read-only file system issues."""
        if self.swebench_verified:
            return self.setup_env_swebench()
        elif self.swesmith:
            return self.setup_env_swesmith()
        
        try:
            # For Apptainer, we'll work with the existing environment
            # and avoid creating symlinks in read-only areas
            self.logger.info("Setting up Apptainer environment (read-only aware)")
            
            # Try to install chardet if possible
            try:
                # First try uv pip (the package manager used in Docker images)
                try:
                    self.run("uv pip install chardet")
                except Exception:
                    # Try with python -m pip
                    try:
                        self.run("python -m pip install chardet")
                    except Exception:
                        # Try with python3
                        try:
                            self.run("python3 -m pip install chardet")
                        except Exception:
                            # Try with pip directly
                            try:
                                self.run("pip install chardet")
                            except Exception:
                                # Try with pip3
                                self.run("pip3 install chardet")
            except Exception as e:
                self.logger.warning(f"Could not install chardet: {e}")
            
            # Clean up Python cache files if possible
            try:
                self.run("find . -name '*.pyc' -delete")
                self.run("find . -name '__pycache__' -exec rm -rf {} +")
            except Exception as e:
                self.logger.warning(f"Could not clean Python cache: {e}")
            
            # Try to handle r2e_tests if it exists
            try:
                self.run("find /r2e_tests -name '*.pyc' -delete")
                self.run("find /r2e_tests -name '__pycache__' -exec rm -rf {} +")
            except Exception as e:
                self.logger.warning(f"Could not clean r2e_tests cache: {e}")
            
            # Skip file operations that require write access to read-only areas
            self.logger.info("Apptainer environment setup completed (read-only aware)")
            
        except Exception as e:
            self.logger.error(f"Error setting up Apptainer environment: {repr(e)}")
    
    def setup_env_swebench(self):
        """SWE-bench setup for Apptainer."""
        try:
            self.run("chmod +x /run_tests.sh")
            self.alt_path = "/"
            # Skip symlink creation in read-only areas
            try:
                self.run(f"ln -s /opt/miniconda3/envs/testbed /root/.venv")
            except Exception as e:
                self.logger.warning(f"Could not create .venv symlink: {e}")
            try:
                # First try uv pip (the package manager used in Docker images)
                try:
                    self.run("uv pip install chardet")
                except Exception:
                    # Try with python -m pip
                    try:
                        self.run("python -m pip install chardet")
                    except Exception:
                        # Try with python3
                        try:
                            self.run("python3 -m pip install chardet")
                        except Exception:
                            # Try with pip directly
                            try:
                                self.run("pip install chardet")
                            except Exception:
                                # Try with pip3
                                self.run("pip3 install chardet")
            except Exception as e:
                self.logger.warning(f"Could not install chardet: {e}")
        except Exception as e:
            self.logger.error(f"Error setting up SWE-bench environment: {repr(e)} @ {self.docker_image}")
    
    def setup_env_swesmith(self):
        """SWEsmith setup for Apptainer."""
        try:
            commit_id = self.ds['base_commit']
            self.run("git fetch")
            self.run(f"git checkout {commit_id}")
            test_command, _ = get_test_command(self.ds)
            eval_script_content = "\n".join(
                [
                    "#!/bin/bash", "set -uxo pipefail", "source /opt/miniconda3/bin/activate",
                    f"conda activate testbed", f"cd testbed/", f": '>>>>> Start Test Output'",
                    test_command, f": '>>>>> End Test Output'",
                ]
            ) + "\n"
            
            with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.sh') as temp_file:
                temp_file.write(eval_script_content)
                temp_file.flush()
                temp_file_path = temp_file.name
            
            self.copy_to_container(temp_file_path, "/tmp/run_tests.sh")
            os.unlink(temp_file_path)
            
            self.run("chmod +x /tmp/run_tests.sh")
            # Copy to a writable location if needed
            try:
                self.run("cp /tmp/run_tests.sh /run_tests.sh")
            except Exception as e:
                self.logger.warning(f"Could not copy test script to /run_tests.sh: {e}")
                # Use the tmp version
                self.run("ln -sf /tmp/run_tests.sh /run_tests.sh")
            # Skip symlink creation in read-only areas
            try:
                self.run(f"ln -s /opt/miniconda3/envs/testbed /root/.venv")
            except Exception as e:
                self.logger.warning(f"Could not create .venv symlink: {e}")
            try:
                self.run('echo \'export PATH="/usr/local/bin:$PATH"\' >> ~/.bashrc')
            except Exception as e:
                self.logger.warning(f"Could not update bashrc: {e}")
            try:
                # First try uv pip (the package manager used in Docker images)
                try:
                    self.run("uv pip install chardet")
                except Exception:
                    # Try with python -m pip
                    try:
                        self.run("python -m pip install chardet")
                    except Exception:
                        # Try with python3
                        try:
                            self.run("python3 -m pip install chardet")
                        except Exception:
                            # Try with pip directly
                            try:
                                self.run("pip install chardet")
                            except Exception:
                                # Try with pip3
                                self.run("pip3 install chardet")
            except Exception as e:
                self.logger.warning(f"Could not install chardet: {e}")
        except Exception as e:
            self.logger.error(f"Error setting up SWEsmith environment: {repr(e)}")
    
    def run_tests(self, timeout: int = 300) -> Tuple[str, str]:
        """Override run_tests to handle Apptainer-specific test script locations."""
        # Try different locations for the test script
        test_script_locations = [
            f"{self.alt_path}/run_tests.sh",
            "/run_tests.sh", 
            "/tmp/run_tests.sh",
            "/testbed/run_tests.sh",
            f"{self.repo_path}/run_tests.sh"
        ]
        
        self.logger.info(f"Looking for test script in locations: {test_script_locations}")
        
        # Debug: List files in common locations
        try:
            self.logger.info("Debug: Listing files in /testbed:")
            ls_output, _ = self.run("ls -la /testbed/")
            self.logger.info(f"/testbed contents: {ls_output}")
        except Exception as e:
            self.logger.warning(f"Could not list /testbed: {e}")
        
        try:
            self.logger.info("Debug: Listing files in /:")
            ls_output, _ = self.run("ls -la /")
            self.logger.info(f"/ contents: {ls_output}")
        except Exception as e:
            self.logger.warning(f"Could not list /: {e}")
        
        for script_path in test_script_locations:
            try:
                # Check if the script exists
                check_output, _ = self.run(f"test -f {script_path} && echo 'exists'")
                if "exists" in check_output:
                    self.logger.info(f"Found test script at: {script_path}")
                    output, error_code = self.run(f"bash {script_path}", timeout=timeout)
                    output = re.sub(r"\x1b\[[0-9;]*m|\r", "", output)
                    return output, error_code
                else:
                    self.logger.debug(f"Test script not found at: {script_path}")
            except Exception as e:
                self.logger.warning(f"Error checking test script at {script_path}: {e}")
                continue
        
        # If no test script found, return error
        self.logger.error("No test script found in any expected location")
        return "No test script found in any expected location", "-1"
    
    def demux_run_tests(self) -> Tuple[str, str, str]:
        """Override demux_run_tests to handle Apptainer-specific test script locations."""
        # Try different locations for the test script
        test_script_locations = [
            f"{self.alt_path}/run_tests.sh",
            "/run_tests.sh", 
            "/tmp/run_tests.sh",
            "/testbed/run_tests.sh",
            f"{self.repo_path}/run_tests.sh"
        ]
        
        self.logger.info(f"Looking for test script in locations: {test_script_locations}")
        
        for script_path in test_script_locations:
            try:
                # Check if the script exists
                check_output, _ = self.run(f"test -f {script_path} && echo 'exists'")
                if "exists" in check_output:
                    self.logger.info(f"Found test script at: {script_path}")
                    stdout, stderr, error_code = self.demux_run(f"bash {script_path}")
                    stdout = re.sub(r"\x1b\[[0-9;]*m|\r", "", stdout)
                    stderr = re.sub(r"\x1b\[[0-9;]*m|\r", "", stderr)
                    return stdout, stderr, error_code
                else:
                    self.logger.debug(f"Test script not found at: {script_path}")
            except Exception as e:
                self.logger.warning(f"Error checking test script at {script_path}: {e}")
                continue
        
        # If no test script found, return error
        self.logger.error("No test script found in any expected location")
        return "", "No test script found in any expected location", "-1"

    def start_container(self, image: str, command: str, name: str, **kwargs):
        """Start an Apptainer instance."""
        # Convert to Apptainer URI if needed
        if not image.startswith("docker://"):
            apptainer_uri = f"docker://{image}"
        else:
            apptainer_uri = image
        
        self.logger.info(f"Starting Apptainer instance for image: {apptainer_uri}")
        
        # Check if image is already cached
        try:
            cache_check = subprocess.run(
                ["apptainer", "cache", "list"],
                capture_output=True,
                text=True,
                timeout=30
            )
            if apptainer_uri in cache_check.stdout:
                self.logger.info("Image found in cache, starting instance...")
            else:
                self.logger.info("Image not in cache, will pull from registry...")
        except Exception as e:
            self.logger.warning(f"Could not check cache: {e}")
        
        cmd = ["apptainer", "instance", "start"]
        
        # Add writable tmpfs for file system operations
        cmd.extend(["--writable-tmpfs"])
        
        # Add bind mounts for working directories
        cmd.extend(["--bind", "/tmp:/tmp"])
        cmd.extend(["--bind", "/var/tmp:/var/tmp"])
        
        # Add environment variables if provided
        if "environment" in kwargs:
            for key, val in kwargs["environment"].items():
                cmd.extend(["--env", f"{key}={val}"])
        
        cmd.extend([apptainer_uri, name])
        
        try:
            # Use longer timeout for image pulling (10 minutes)
            result = subprocess.run(
                cmd,
                check=True,
                capture_output=True,
                text=True,
                timeout=600  # 10 minutes for image pull
            )
            self.container_name = name
            self.container = name
            self.logger.info(f"Started Apptainer instance: {name}")
        except subprocess.TimeoutExpired:
            self.logger.error(f"Timeout starting Apptainer instance (10 minutes). Image may be too large or network too slow.")
            self.logger.info("Consider pre-pulling the image with: apptainer pull docker://<image>")
            raise RuntimeError(f"Timeout starting Apptainer instance. Image pull took too long.")
        except subprocess.CalledProcessError as e:
            self.logger.error(f"Failed to start Apptainer instance: {e.stderr}")
            raise RuntimeError(f"Failed to start Apptainer instance: {e.stderr}")
        except Exception as e:
            self.logger.error(f"Unexpected error starting Apptainer instance: {e}")
            raise

    def stop_container(self):
        """Stop an Apptainer instance."""
        if self.container_name:
            try:
                result = subprocess.run(
                    ["apptainer", "instance", "stop", self.container_name],
                    capture_output=True,
                    text=True,
                    timeout=60
                )
                self.logger.info(f"Stopped Apptainer instance: {self.container_name}")
            except subprocess.CalledProcessError as e:
                self.logger.warning(f"Error stopping Apptainer instance: {e.stderr}")
            except Exception as e:
                self.logger.warning(f"Unexpected error stopping Apptainer instance: {e}")
            finally:
                self.container = None
                self.container_name = None

    def run(
        self,
        code: str,
        timeout: int = CMD_TIMEOUT,
        args: str = "",
        workdir=None,
        type: str = None,
    ) -> Tuple[str, str]:
        """
        Execute command in Apptainer instance.
        
        Args:
            code: Command to execute
            timeout: Timeout in seconds
            args: Command arguments
            workdir: Working directory (optional)
            type: Not used in Apptainer
            
        Returns:
            Tuple of (output, error_code)
        """
        exec_workdir = self.repo_path if workdir is None else workdir
        # Set up environment variables to match Docker image setup
        env_setup = "export VIRTUAL_ENV=/testbed/.venv && export PATH=$VIRTUAL_ENV/bin:$PATH && export PATH=/root/.cargo/bin:$PATH && export PATH=/root/.local/bin:$PATH && "
        command = f"cd {exec_workdir} && {env_setup}timeout {timeout} {code} {args}"
        
        cmd = [
            "apptainer", "exec",
            f"instance://{self.container_name}",
            "/bin/sh", "-c", command
        ]
        
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(
                    subprocess.run,
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=timeout + 5
                )
                result = future.result(timeout=timeout + 5)
            
            output = result.stdout + result.stderr
            error_code = result.returncode
            
            if error_code == 124:  # timeout exit code
                self.logger.error(f"Internal Timeout: {timeout}s")
                return f"The command took too long to execute (>{timeout}s)", "-1"
            
            if error_code != 0:
                self.logger.error(
                    f"Error: Exit code {error_code} \nError Message: {output}"
                )
                return output, f"Error: Exit code {error_code}"
            
            # Remove ANSI escape codes and \r characters
            output = re.sub(r"\x1b\[[0-9;]*m|\r", "", output)
            return output, str(error_code)
            
        except concurrent.futures.TimeoutError:
            self.logger.error(f"Timeout: {timeout + 5}s")
            return f"The command took too long to execute (>{timeout}s)", "-1"
        except subprocess.TimeoutExpired:
            self.logger.error(f"Command timeout: {timeout}s")
            return f"The command took too long to execute (>{timeout}s)", "-1"
        except Exception as e:
            return f"Error: {repr(e)}", "-1"

    def demux_run(
        self,
        code: str,
        timeout: int = CMD_TIMEOUT,
        args: str = "",
        workdir=None,
    ) -> Tuple[str, str, str]:
        """Execute command with separate stdout/stderr streams."""
        exec_workdir = self.repo_path if workdir is None else workdir
        # Set up environment variables to match Docker image setup
        env_setup = "export VIRTUAL_ENV=/testbed/.venv && export PATH=$VIRTUAL_ENV/bin:$PATH && export PATH=/root/.cargo/bin:$PATH && export PATH=/root/.local/bin:$PATH && "
        command = f"cd {exec_workdir} && {env_setup}timeout {timeout} {code} {args}"
        
        cmd = [
            "apptainer", "exec",
            f"instance://{self.container_name}",
            "/bin/sh", "-c", command
        ]
        
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(
                    subprocess.run,
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=timeout + 5
                )
                result = future.result(timeout=timeout + 5)
            
            stdout = re.sub(r"\x1b\[[0-9;]*m|\r", "", result.stdout)
            stderr = re.sub(r"\x1b\[[0-9;]*m|\r", "", result.stderr)
            error_code = str(result.returncode)
            
            if error_code != "0":
                self.logger.error(
                    f"Error: Exit code {error_code} \nStdout: {stdout} \nStderr: {stderr}"
                )
            
            return stdout, stderr, error_code
        except Exception as e:
            return f"Error: {repr(e)}", f"Error: {repr(e)}", "-1"

    def copy_to_container(self, src_path: str, dest_path: str):
        """Copy file into Apptainer instance."""
        try:
            with open(src_path, 'rb') as f:
                content = f.read()
            
            # Create parent directory if needed
            parent_dir = os.path.dirname(dest_path)
            if parent_dir:
                self.run(f"mkdir -p {parent_dir}")
            
            # Copy file using stdin redirection
            cmd = [
                "apptainer", "exec",
                f"instance://{self.container_name}",
                "/bin/sh", "-c", f"cat > {dest_path}"
            ]
            
            subprocess.run(cmd, input=content, check=True)
            self.logger.debug(f"Copied {src_path} to container:{dest_path}")
        except subprocess.CalledProcessError as e:
            self.logger.error(f"Failed to copy file to container: {e}")
            raise
        except Exception as e:
            self.logger.error(f"Unexpected error copying file to container: {e}")
            raise

    def close(self):
        """Clean up and close Apptainer instance."""
        self.stop_container()
        self.logger.info("Apptainer runtime closed")

