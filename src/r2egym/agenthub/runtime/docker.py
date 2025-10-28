import os
import time
import uuid
import tempfile
import docker
from docker.models.containers import Container
import kubernetes
import tarfile
import io
from r2egym.agenthub.runtime.base import ExecutionEnvironment
import concurrent.futures
import re
from r2egym.agenthub import CMD_TIMEOUT
from kubernetes import client, config, watch
from kubernetes.stream import stream
from typing import Tuple

DEFAULT_NAMESPACE = "default"
DOCKER_PATH = "/root/.venv/bin:/root/.local/bin:/root/.cargo/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"


##############################################################################
# Docker runtime
##############################################################################
class DockerRuntime(ExecutionEnvironment):
    """
    Docker/Kubernetes runtime implementation.
    Handles both Docker and Kubernetes backends for running containers.
    """

    def __init__(
        self,
        ds,
        repo_path: str = "/testbed",
        alt_path: str = "/root",
        docker_image: str = None,
        command: str = "/bin/bash",
        logger=None,
        backend="docker",
        **docker_kwargs,
    ):
        # Validate backend
        assert backend in ["docker", "kubernetes"], f"Invalid backend: {backend}"
        self.backend = backend
        self.docker_kwargs = docker_kwargs
        
        # Call parent initialization
        super().__init__(ds, repo_path, alt_path, docker_image, command, logger, **docker_kwargs)
        
        # Initialize backend-specific client
        if self.backend == "docker":
            self.client = docker.from_env(timeout=120)
        elif self.backend == "kubernetes":
            try:
                config.load_incluster_config()
            except Exception:
                config.load_kube_config()
            self.client = client.CoreV1Api()
        
        # Override container name for Kubernetes
        if self.backend == "kubernetes":
            self.container_name = str(uuid.uuid4())
        
        # Start the container
        self.start_container(
            self.docker_image, command, self.container_name, **docker_kwargs
        )
        
        # Initialize the environment
        self.setup_env()
        
        # Log initialization
        if self.backend == "kubernetes":
            self.logger.info("Kubernetes environment initialized")
            pod_name = (
                self.container.metadata.name
                if self.container and self.container.metadata
                else "N/A"
            )
            self.logger.info("Pod Name: %s", pod_name)
        else:
            self.logger.info("Docker environment initialized")
            self.logger.info("Container ID: %s", self.container.id)
        
        self.logger.info("repo name: %s", self.repo_name)
        self.logger.info("Docker image: %s", self.docker_image)

    def _start_kubernetes_pod(
        self, docker_image: str, command: str, pod_name: str, **docker_kwargs
    ):
        """Starts or connects to a Kubernetes pod with the specified configuration."""
        not_found_error = None
        try:
            self.container = self.client.read_namespaced_pod(
                name=pod_name, namespace=DEFAULT_NAMESPACE, _request_timeout=60,
            )
            self.logger.info(f"Found existing Kubernetes pod: {pod_name}")
            return
        except client.ApiException as e:
            not_found_error = e

        if not_found_error.status != 404:
            self.logger.error(
                f"Error checking Kubernetes pod '{pod_name}' status: {not_found_error}. Check Kubernetes configuration and permissions."
            )
            raise not_found_error

        env_vars = {"PATH": DOCKER_PATH, **docker_kwargs.get("environment", {})}
        env_spec = [{"name": k, "value": str(v)} for k, v in env_vars.items()]
        pod_body = {
            "apiVersion": "v1",
            "kind": "Pod",
            "metadata": {"name": pod_name},
            "spec": {
                "restartPolicy": "Never",
                "containers": [
                    {
                        "name": pod_name,
                        "image": docker_image,
                        "command": ["/bin/sh", "-c"],
                        "args": [command] if isinstance(command, str) else command,
                        "stdin": True,
                        "tty": True,
                        "env": env_spec,
                        "resources": {
                            "requests": {"cpu": "1", "memory": "1Gi"},
                        },
                    }
                ],
                "imagePullSecrets": [{"name": "dockerhub-pro"}],
                "nodeSelector": {"karpenter.sh/nodepool": "bigcpu-standby"},
                "tolerations": [
                    {
                        "key": "node.kubernetes.io/disk-pressure",
                        "operator": "Exists",
                        "effect": "NoExecute",
                        "tolerationSeconds": 10800
                    }
                ],
            },
        }

        # Create the Pod with retry logic
        max_retries = 5
        backoff = 5
        pod = None
        for attempt in range(1, max_retries + 1):
            try:
                pod = self.client.create_namespaced_pod(
                    namespace=DEFAULT_NAMESPACE, body=pod_body, _request_timeout=120,
                )
                break
            except client.ApiException as e:
                if e.status in (409, 429, 500, 503):
                    self.logger.warning(
                        f"Transient Kubernetes error {e.status} while creating pod "
                        f"'{pod_name}' (attempt {attempt}/{max_retries}); "
                        f"retrying in {backoff}s"
                    )
                    time.sleep(backoff)
                    backoff = min(backoff * 2, 60)
                    continue
                self.logger.error(f"Failed to create Kubernetes pod '{pod_name}': {e}")
                raise
        else:
            raise RuntimeError(
                f"Exceeded retry limit ({max_retries}) while creating pod '{pod_name}'."
            )

        try:
            rv = pod.metadata.resource_version
            w = watch.Watch()
            stream = w.stream(
                self.client.list_namespaced_pod,
                namespace=DEFAULT_NAMESPACE,
                field_selector=f"metadata.name={pod_name}",
                resource_version=rv,
                timeout_seconds=1200,
            )
            start_time = time.time()
            for event in stream:
                obj = event["object"]
                phase = obj.status.phase
                if time.time() - start_time > 1200:
                    w.stop()
                    raise RuntimeError(f"Kubernetes pod '{pod_name}' timed out after 1200 seconds.")
                if phase == "Running":
                    self.logger.info(f"Kubernetes pod '{pod_name}' is Running.")
                    w.stop()
                    break
                if phase in ["Failed", "Succeeded", "Unknown"]:
                    w.stop()
                    raise RuntimeError(
                        f"Kubernetes pod '{pod_name}' entered terminal phase '{phase}'."
                    )
            self.container = pod
        except Exception as e:
            self.logger.error(f"Error waiting for pod to start: {e}")
            try:
                pod_status = self.client.read_namespaced_pod(
                    name=pod_name, namespace=DEFAULT_NAMESPACE, _request_timeout=60,
                )
                if pod_status.status.phase == "Running":
                    self.logger.info(f"Pod '{pod_name}' is running (verified after watch error)")
                    self.container = pod_status
                else:
                    self.logger.warning(f"Pod '{pod_name}' is in state {pod_status.status.phase}")
                    raise RuntimeError(f"Pod '{pod_name}' failed to reach Running state: {pod_status.status.phase}")
            except Exception as status_error:
                self.logger.error(f"Failed to check pod status after watch error: {status_error}")
                raise RuntimeError(f"Failed to verify pod status: {status_error}")

    def start_container(
        self, docker_image: str, command: str, ctr_name: str, **docker_kwargs
    ):
        """Start or reuse a container."""
        try:
            if self.backend == "docker":
                containers = self.client.containers.list(
                    all=True, filters={"name": ctr_name}
                )
                if containers:
                    self.container = containers[0]
                    if self.container.status != "running":
                        self.container.start()
                else:
                    self.container = self.client.containers.run(
                        docker_image,
                        command,
                        name=ctr_name,
                        detach=True,
                        tty=True,
                        stdin_open=True,
                        **docker_kwargs,
                    )
            elif self.backend == "kubernetes":
                self._start_kubernetes_pod(
                    docker_image, command, ctr_name, **docker_kwargs
                )
        except Exception as e:
            print("Container start error:", repr(e))
            self.stop_container()
            return

    def _stop_kubernetes_pod(self):
        """Stop and delete Kubernetes pod."""
        try:
            self.client.delete_namespaced_pod(
                name=self.container_name,
                namespace=DEFAULT_NAMESPACE,
                body=kubernetes.client.V1DeleteOptions(grace_period_seconds=0),
                _request_timeout=60,
            )

            w = watch.Watch()
            stream = w.stream(
                self.client.list_namespaced_pod,
                namespace=DEFAULT_NAMESPACE,
                field_selector=f"metadata.name={self.container_name}",
                timeout_seconds=60,
            )

            deletion_confirmed = False
            for event in stream:
                if event["type"] == "DELETED":
                    self.logger.info(f"Kubernetes pod {self.container_name} deleted.")
                    deletion_confirmed = True
                    w.stop()
                    break
            
            if not deletion_confirmed:
                try:
                    self.client.read_namespaced_pod(
                        name=self.container_name, namespace=DEFAULT_NAMESPACE
                    )
                    self.logger.warning(
                        f"Watch timed out but pod {self.container_name} still exists. Forcing deletion."
                    )
                    self.client.delete_namespaced_pod(
                        name=self.container_name,
                        namespace=DEFAULT_NAMESPACE,
                        body=kubernetes.client.V1DeleteOptions(
                            grace_period_seconds=0,
                            force=True
                        ),
                    )
                except kubernetes.client.rest.ApiException as e:
                    if e.status == 404:
                        self.logger.info(f"Confirmed pod {self.container_name} is deleted.")
                    else:
                        self.logger.error(f"Error checking pod status after timeout: {e}")
        except kubernetes.client.rest.ApiException as e:
            if e.status == 404:
                self.logger.info(
                    f"Kubernetes pod '{self.container_name}' not found, likely already deleted."
                )
            else:
                self.logger.error(
                    f"Error deleting Kubernetes pod '{self.container_name}': {e}"
                )
                raise e

    def stop_container(self):
        """Stop the running container."""
        try:
            if self.container:
                if self.backend == "docker":
                    self.container.stop()
                    self.container.remove()
                elif self.backend == "kubernetes":
                    self._stop_kubernetes_pod()
        except Exception as e:
            print("Container stop/delete error:", repr(e))

    def _run_kubernetes(
        self,
        code: str,
        timeout: int = CMD_TIMEOUT,
        args: str = "",
        workdir: str = "",
    ) -> Tuple[str, str]:
        """Kubernetes-specific method to execute code in the pod."""
        command = ""
        if workdir:
            command += f"cd {workdir} && "
        command += f"timeout {timeout} {code} {args}"
        full_command = ["/bin/sh", "-c", command]
        try:
            def execute_command():
                resp = stream(
                    self.client.connect_get_namespaced_pod_exec,
                    self.container_name,
                    DEFAULT_NAMESPACE,
                    command=full_command,
                    stderr=True,
                    stdin=False,
                    stdout=True,
                    tty=False,
                    _preload_content=False,
                )
                combined_chunks = []
                stdout_chunks = []
                stderr_chunks = []
                while resp.is_open():
                    resp.update(timeout=1)
                    if resp.peek_stdout():
                        chunk = resp.read_stdout()
                        stdout_chunks.append(chunk)
                        combined_chunks.append(chunk)
                    if resp.peek_stderr():
                        chunk = resp.read_stderr()
                        stderr_chunks.append(chunk)
                        combined_chunks.append(chunk)
                resp.close()
                exit_code = resp.returncode
                combined_output = "".join(combined_chunks)
                return combined_output, exit_code

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(execute_command)
                combined_output, exit_code = future.result(timeout=timeout + 5)

            output = combined_output

            if exit_code is None:
                self.logger.error("Kubernetes exec: Exit code not found.")
                return output, "-1"

            if exit_code == 124:
                self.logger.error(f"Internal Timeout via 'timeout' command: {timeout}s")
                return f"The command took too long to execute (>{timeout}s)", "-1"

            if exit_code != 0:
                self.logger.error(
                    f"Kubernetes exec Error: Exit code {exit_code}\nError Message: {output}"
                )
                return output, f"Error: Exit code {exit_code}"

            output = re.sub(r"\x1b\[[0-9;]*m|\r", "", output)
            return output, str(exit_code)
        except concurrent.futures.TimeoutError:
            self.logger.error(f"Kubernetes exec Overall Timeout: {timeout + 5}s")
            return f"The command took too long to execute (>{timeout}s)", "-1"
        except client.ApiException as e:
            self.logger.error(f"Kubernetes API Error during exec: {e}")
            return f"Error executing command in pod: {repr(e)}", "-1"
        except Exception as e:
            self.logger.error(f"Unexpected error during Kubernetes exec: {repr(e)}")
            return f"Error: {repr(e)}", "-1"

    def run(
        self,
        code: str,
        timeout: int = CMD_TIMEOUT,
        args: str = "",
        workdir=None,
        type: str = None,
    ) -> Tuple[str, str]:
        """Execute command in container (Docker or Kubernetes)."""
        exec_code = code
        exec_workdir = self.repo_path if workdir is None else workdir

        if self.backend == "kubernetes":
            return self._run_kubernetes(exec_code, timeout, args, workdir=exec_workdir)

        command = f"timeout {timeout} {exec_code} {args}"
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(
                    self.container.exec_run,
                    cmd=["/bin/sh", "-c", command],
                    workdir=exec_workdir,
                    stdout=True,
                    stderr=True,
                    environment={"PATH": DOCKER_PATH},
                )
                exec_result = future.result(timeout=timeout + 5)

            output = exec_result.output.decode("utf-8", errors="replace")
            error_code = exec_result.exit_code

            if error_code == 124:
                self.logger.error(f"Internal Timeout: {timeout}s")
                return f"The command took too long to execute (>{timeout}s)", "-1"

            if error_code != 0:
                self.logger.error(
                    f"Error: Exit code {error_code} \nError Message: {output}"
                )
                return output, f"Error: Exit code {error_code}"

            output = re.sub(r"\x1b\[[0-9;]*m|\r", "", output)
            return output, str(error_code)

        except concurrent.futures.TimeoutError:
            self.logger.error(f"Timeout: {timeout}s")
            return f"The command took too long to execute (>{timeout}s)", "-1"
        except Exception as e:
            return f"Error: {repr(e)}", "-1"

    def demux_run(
        self, code: str, timeout: int = CMD_TIMEOUT, args: str = "", workdir=None
    ) -> Tuple[str, str, str]:
        """Execute with separate stdout/stderr streams (Docker only)."""
        command = f"timeout {timeout} {code} {args}"
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(
                    self.container.exec_run,
                    cmd=command,
                    workdir=self.repo_path if workdir is None else workdir,
                    demux=True,
                    environment={"PATH": DOCKER_PATH},
                )
                exec_result = future.result(timeout=timeout + 5)

            output_data, error_data = exec_result.output
            error_code = exec_result.exit_code

            stdout = (
                output_data.decode("utf-8", errors="replace") if output_data else ""
            )
            stderr = error_data.decode("utf-8", errors="replace") if error_data else ""

            if error_code != 0:
                self.logger.error(
                    f"Error: Exit code {error_code} \nStdout Message: {stdout}, \nError Message: {stderr}"
                )
                return stdout, stderr, f"Error: Exit code {error_code}"

            return stdout, stderr, str(error_code)
        except Exception as e:
            return f"Error: {repr(e)}", f"Error: {repr(e)}", "-1"

    def _copy_to_container_kubernetes(self, src_path: str, dest_path: str):
        """Copy file into Kubernetes pod using tar over exec."""
        dest_dir = os.path.dirname(dest_path)
        tar_stream = io.BytesIO()
        with tarfile.open(fileobj=tar_stream, mode="w") as tar:
            tar.add(src_path, arcname=os.path.basename(dest_path))
        tar_stream.seek(0)

        max_retries = 5
        retry_delay = 5
        for attempt in range(max_retries):
            try:
                exec_command = ["tar", "xmf", "-", "-C", dest_dir]
                resp = stream(
                    self.client.connect_get_namespaced_pod_exec,
                    self.container_name,
                    DEFAULT_NAMESPACE,
                    command=exec_command,
                    stderr=True,
                    stdin=True,
                    stdout=True,
                    tty=False,
                    _preload_content=False,
                )
                resp.write_stdin(tar_stream.read())
                resp.close()
                break
            except Exception as e:
                if attempt < max_retries - 1:
                    self.logger.warning(f"Copy to container failed (attempt {attempt+1}/{max_retries}): {str(e)}")
                    time.sleep(retry_delay)
                    retry_delay *= 2
                    retry_delay = min(retry_delay, 60)
                    tar_stream.seek(0)
                else:
                    self.logger.error(f"Copy to container failed after {max_retries} attempts: {str(e)}")
                    raise

    def copy_to_container(self, src_path: str, dest_path: str):
        """Copy file/directory into container (Docker or Kubernetes)."""
        if self.backend == "docker":
            tar_stream = io.BytesIO()
            with tarfile.open(fileobj=tar_stream, mode="w") as tar:
                tar.add(src_path, arcname=os.path.basename(dest_path))
            tar_stream.seek(0)
            self.container.put_archive(os.path.dirname(dest_path), tar_stream.read())
        else:
            return self._copy_to_container_kubernetes(src_path, dest_path)

    def close(self):
        """Clean up and close."""
        self.stop_container()
        if self.backend == "docker":
            self.client.close()

    def reset(self):
        """Reset environment by restarting container."""
        self.stop_container()
        self.start_container(
            self.docker_image, self.command, self.container_name, **self.docker_kwargs
        )
