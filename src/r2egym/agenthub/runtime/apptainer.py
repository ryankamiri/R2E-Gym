import os
import subprocess
import tempfile
import concurrent.futures
import re
from typing import Tuple

from r2egym.agenthub.runtime.base import ExecutionEnvironment, CMD_TIMEOUT


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

    def start_container(self, image: str, command: str, name: str, **kwargs):
        """Start an Apptainer instance."""
        cmd = ["apptainer", "instance", "start"]
        
        # Add environment variables if provided
        if "environment" in kwargs:
            for key, val in kwargs["environment"].items():
                cmd.extend(["--env", f"{key}={val}"])
        
        cmd.extend([image, name])
        
        try:
            result = subprocess.run(
                cmd,
                check=True,
                capture_output=True,
                text=True,
                timeout=120
            )
            self.container_name = name
            self.container = name
            self.logger.info(f"Started Apptainer instance: {name}")
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
        command = f"cd {exec_workdir} && timeout {timeout} {code} {args}"
        
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
        command = f"cd {exec_workdir} && timeout {timeout} {code} {args}"
        
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

