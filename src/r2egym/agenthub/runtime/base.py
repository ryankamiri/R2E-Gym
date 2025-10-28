import os
import json
import datetime
import hashlib
import tempfile
import uuid
import re
from abc import ABC, abstractmethod
from typing import Optional, Tuple, Dict, Any

from r2egym.agenthub.utils.log import get_logger
from r2egym.commit_models.diff_classes import ParsedCommit
from r2egym.agenthub.trajectory.swebench_utils import make_test_spec
from r2egym.repo_analysis.execution_log_parser import parse_log_fn, decolor_dict_keys
from r2egym.agenthub import SUPPORTED_REPOS, SKIP_FILES, SKIP_FILES_NEW, CMD_TIMEOUT
from r2egym.swesmith.utils import get_test_command

from swebench.harness.constants import (
    APPLY_PATCH_FAIL,
    END_TEST_OUTPUT,
    FAIL_TO_FAIL,
    FAIL_TO_PASS,
    KEY_INSTANCE_ID,
    KEY_PREDICTION,
    MAP_REPO_VERSION_TO_SPECS,
    PASS_TO_FAIL,
    PASS_TO_PASS,
    RESET_FAILED,
    START_TEST_OUTPUT,
    TESTS_ERROR,
    TESTS_TIMEOUT,
    EvalType,
    ResolvedStatus,
    TestStatus,
)
from swebench.harness.test_spec.test_spec import TestSpec
from swebench.harness.log_parsers import MAP_REPO_TO_PARSER, get_eval_type
from swebench.harness.grading import get_eval_tests_report, get_resolution_status


DOCKER_PATH = "/root/.venv/bin:/root/.local/bin:/root/.cargo/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"


##############################################################################
# base runtime class
##############################################################################
class ExecutionEnvironment(ABC):
    """
    Abstract base class for execution environments (Docker, Kubernetes, Apptainer).
    Implements common functionality while leaving backend-specific operations abstract.
    """
    
    def __init__(
        self,
        ds,
        repo_path: str = "/testbed",
        alt_path: str = "/root",
        docker_image: str = None,
        command: str = "/bin/bash",
        logger=None,
        **kwargs
    ):
        # Validate dataset
        assert ds, f"Dataset not provided for image: {docker_image}"
        self.ds = ds
        
        # Parse dataset to get image name
        if "docker_image" in ds:
            image = ds["docker_image"]
        elif "image_name" in ds:
            image = ds["image_name"]
        else:
            raise ValueError("No image found in dataset")
        
        self.docker_image = docker_image if docker_image else image
        
        # Determine environment type
        self.swebench_verified = "swebench" in self.docker_image
        self.swesmith = "swesmith" in self.docker_image
        
        if self.swesmith:
            image_name = ds['image_name'].replace('__', '_1776_')
            self.docker_image = f'jyangballin/{image_name}:latest'
        
        # Setup paths and metadata
        self.repo_path = repo_path
        self.alt_path = alt_path
        self.command = command
        self.repo_name = (
            ds["repo"] if self.swebench_verified or self.swesmith
            else ds["repo_name"]
        )
        
        # Parse commit
        if not self.swesmith:
            commit_json = (
                ds["parsed_commit"] if self.swebench_verified
                else ds["parsed_commit_content"]
            )
            self.commit = ParsedCommit(**json.loads(commit_json))
        
        # Setup logger
        if logger is None:
            self.logger = get_logger(self.__class__.__name__)
        else:
            self.logger = logger
        
        # Create test spec for SWE-bench
        if self.swebench_verified:
            self.test_spec = make_test_spec(ds)
        
        # Store kwargs for reset
        self.init_kwargs = kwargs
        
        # Container state
        self.container = None
        self.container_name = self._get_container_name(self.docker_image)
    
    @staticmethod
    def _get_container_name(image_name: str) -> str:
        """Generate unique container name."""
        process_id = str(os.getpid())
        current_time = str(datetime.datetime.now())
        unique_string = current_time + process_id
        hash_object = hashlib.sha256(unique_string.encode())
        image_name_sanitized = image_name.replace("/", "-").replace(":", "-")
        return f"{image_name_sanitized}-{hash_object.hexdigest()[:10]}"
    
    # ========================================================================
    # Abstract methods - must be implemented by each backend
    # ========================================================================
    
    @abstractmethod
    def start_container(self, image: str, command: str, name: str, **kwargs):
        """Start/create container instance."""
        pass
    
    @abstractmethod
    def stop_container(self):
        """Stop the running container."""
        pass
    
    @abstractmethod
    def run(self, code: str, timeout: int = CMD_TIMEOUT, args: str = "", workdir=None, type: str = None) -> Tuple[str, str]:
        """Execute command in container."""
        pass
    
    @abstractmethod
    def demux_run(self, code: str, timeout: int = CMD_TIMEOUT, args: str = "", workdir=None) -> Tuple[str, str, str]:
        """Execute with separate stdout/stderr streams."""
        pass
    
    @abstractmethod
    def copy_to_container(self, src_path: str, dest_path: str):
        """Copy files into container."""
        pass
    
    @abstractmethod
    def close(self):
        """Cleanup and close."""
        pass
    
    # ========================================================================
    # Concrete methods - implemented in base class
    # ========================================================================
    
    def reset_swesmith_tests(self):
        f2p_files = list(set([x.split("::", 1)[0] for x in self.ds[FAIL_TO_PASS]]))
        p2p_files = list(set([x.split("::", 1)[0] for x in self.ds[PASS_TO_PASS]]))
        all_files = list(set(f2p_files + p2p_files))
        all_files = [f for f in all_files if 
             os.path.basename(f).startswith('test_') and os.path.basename(f).endswith('.py') or
             os.path.basename(f).endswith('_test.py')]
        commit_id = self.ds['base_commit']
        reset_command = (
            f'printf "%s\\n" {" ".join(all_files)} | '
            f'xargs -n1 -I{{}} git checkout {commit_id} -- "{{}}" 2>/dev/null'
        )
        self.run(reset_command)
    
    def setup_env_swesmith(self):
        try:
            commit_id = self.ds['base_commit']
            self.run("git fetch")
            self.run(f"git checkout {commit_id}")
            # Setup the run_test.sh script for subsequent testing.  
            test_command, _ = get_test_command(self.ds)
            eval_script_content = "\n".join(
                [
                    "#!/bin/bash",
                    "set -uxo pipefail",
                    "source /opt/miniconda3/bin/activate",
                    f"conda activate testbed",
                    f"cd testbed/",
                    f": '>>>>> Start Test Output'",
                    test_command,
                    f": '>>>>> End Test Output'",
                ]
            ) + "\n"
            
            with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.sh') as temp_file:
                temp_file.write(eval_script_content)
                temp_file.flush()
                temp_file_path = temp_file.name
            
            self.copy_to_container(temp_file_path, "/run_tests.sh")
            os.unlink(temp_file_path)
            
            self.run("chmod +x /run_tests.sh")
            self.run(f"ln -s /opt/miniconda3/envs/testbed /root/.venv")
            self.run('echo \'export PATH="/usr/local/bin:$PATH"\' >> ~/.bashrc')
            self.run("python -m pip install chardet")
        except Exception as e:
            self.logger.error(f"Error setting up environment: {repr(e)}")
    
    def setup_env_swebench(self):
        try:
            self.run("chmod +x /run_tests.sh")
            self.alt_path = "/"
            self.run(f"ln -s /opt/miniconda3/envs/testbed /root/.venv")
            self.run("python -m pip install chardet")
        except Exception as e:
            self.logger.error(f"Error setting up environment: {repr(e)} @ {self.docker_image}")
    
    def setup_env(self):
        if self.swebench_verified:
            return self.setup_env_swebench()
        elif self.swesmith:
            return self.setup_env_swesmith()
        
        try:
            self.run(f"ln -s {self.repo_path}/.venv {self.alt_path}/.venv")
            self.run(f"ln -s {self.repo_path}/.venv/bin/python {self.alt_path}/.local/bin/python")
            self.run(f"ln -s {self.repo_path}/.venv/bin/python {self.alt_path}/.local/bin/python3")
            self.run(f"find {self.repo_path}/.venv/bin -type f -executable -exec ln -sf {{}} {self.alt_path}/.local/bin/ \\;")
            self.run("uv pip install chardet")
            self.run("find . -name '*.pyc' -delete")
            self.run("find . -name '__pycache__' -exec rm -rf {} +")
            self.run("find /r2e_tests -name '*.pyc' -delete")
            self.run("find /r2e_tests -name '__pycache__' -exec rm -rf {} +")
            
            for skip_file in SKIP_FILES_NEW:
                self.run(f"mv {self.repo_path}/{skip_file} {self.alt_path}/{skip_file}")
            
            self.run(f"mv /r2e_tests {self.alt_path}/r2e_tests")
            self.run(f"ln -s {self.alt_path}/r2e_tests {self.repo_path}/r2e_tests")
        except Exception as e:
            self.logger.error(f"Error setting up environment: {repr(e)}")
    
    def get_task_instruction(self) -> str:
        try:
            content = self.ds["problem_statement"]
            return re.search(r"\[ISSUE\](.*)\[/ISSUE\]", content, re.DOTALL).group(1)
        except Exception as e:
            return self.ds["problem_statement"]
    
    @DeprecationWarning
    def read_file(self, rel_file_path: str) -> str:
        output, _ = self.run(f"cat /{self.alt_path}/{rel_file_path}")
        return output
    
    def run_tests(self, timeout: int = 300) -> Tuple[str, str]:
        output, error_code = self.run(f"bash {self.alt_path}/run_tests.sh", timeout=timeout)
        output = re.sub(r"\x1b\[[0-9;]*m|\r", "", output)
        return output, error_code
    
    def demux_run_tests(self) -> Tuple[str, str, str]:
        stdout, stderr, error_code = self.demux_run(f"bash {self.alt_path}/run_tests.sh")
        stdout = re.sub(r"\x1b\[[0-9;]*m|\r", "", stdout)
        stderr = re.sub(r"\x1b\[[0-9;]*m|\r", "", stderr)
        return stdout, stderr, error_code
    
    def checkout(self, commit_hash: str) -> Tuple[str, str]:
        output, error_code = self.run(f"git checkout {commit_hash}")
        return output, error_code
    
    def get_patch(self) -> str:
        output, _ = self.run("git add -A && git diff --cached")
        return output
    
    def create_file(self, file_path: str, content: str) -> Tuple[str, str]:
        uuid_ = uuid.uuid4()
        file_path_ = f"{file_path}_{uuid_}"
        file_path__ = os.path.join("/tmp", file_path_)
        with open(file_path__, "w") as f:
            f.write(content)
        self.copy_to_container(file_path__, f"/testbed/{file_path_}")
        self.run(f"mv /testbed/{file_path_} /{file_path}")
        return "", "0"
    
    def apply_patch(self, patch: str) -> Tuple[str, str]:
        uuid_ = uuid.uuid4()
        patch_path = f"{self.container_name}_{uuid_}.patch"
        patch_path = os.path.join("/tmp", patch_path)
        with open(patch_path, "w") as f:
            f.write(patch)
        self.copy_to_container(patch_path, f"/{patch_path}")
        output, error_code = self.run(f"git apply --whitespace=fix /{patch_path}")
        return output, error_code
    
    def reverse_patch(self, patch: str) -> Tuple[str, str]:
        patch_path = f"{self.container_name}_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.patch"
        patch_path = os.path.join("/tmp", patch_path)
        with open(patch_path, "w") as f:
            f.write(patch)
        self.copy_to_container(patch_path, f"/{patch_path}")
        output, error_code = self.run(f"git apply -R /{patch_path}")
        return output, error_code
    
    def get_logs_eval(self, test_spec: TestSpec, content: str) -> Tuple[Dict[str, str], bool]:
        repo = test_spec.repo
        version = test_spec.version
        log_parser = MAP_REPO_TO_PARSER[repo]
        test_cmd = MAP_REPO_VERSION_TO_SPECS[repo][version]["test_cmd"]
        if isinstance(test_cmd, list):
            test_cmd = test_cmd[-1]
        
        bad_codes = list(
            filter(
                lambda x: x in content,
                [APPLY_PATCH_FAIL, RESET_FAILED, TESTS_ERROR, TESTS_TIMEOUT],
            )
        )
        if bad_codes:
            self.logger.error(f"Bad code found in log: {bad_codes}")
            return {}, False
        
        content = content.split(test_cmd)[-1]
        self.logger.info(f"using swebench log_parser for repo: {repo}")
        return log_parser(content, test_spec), True
    
    def parse_logs(self, log_output: str) -> Dict:
        if self.swebench_verified:
            parsed_output, patch_apply_success = self.get_logs_eval(self.test_spec, log_output)
            return parsed_output
        else:
            return parse_log_fn(f"{self.repo_name}")(log_output)
    
    def _calculate_reward_swesmith(self, get_test_output=False, timeout: int = 300) -> float:
        self.reset_swesmith_tests()
        output, error_msg = self.run("/run_tests.sh", timeout=timeout)
        parse = self.parse_logs(output)
        
        fail2pass = [ ".".join(line.split("::")[1:]) for line in self.ds[FAIL_TO_PASS]]
        pass2pass = [ ".".join(line.split("::")[1:]) for line in self.ds[PASS_TO_PASS]]
        if not parse:
            return 0.0
        
        for test_name in fail2pass:
            if test_name not in parse:
                matching_key = next((k for k in parse.keys() if test_name in k), None)
                if matching_key is None:
                    return 0.0
                if parse[matching_key] != 'PASSED':
                    return 0.0
                test_name = matching_key
            if parse[test_name] != 'PASSED':
                return 0.0
        
        for test_name in pass2pass:
            if test_name not in parse:
                matching_key = next((k for k in parse.keys() if test_name in k), None)
                if matching_key is None:
                    return 0.0
                test_name = matching_key
            if parse[test_name] != 'PASSED':
                return 0.0
        return 1.0
    
    def _calculate_reward_swebench(self, get_test_output=False, timeout: int = 300):
        out, _ = self.run("/run_tests.sh", timeout=timeout)
        eval_status_map, found = self.get_logs_eval(self.test_spec, out)
        eval_ref = {
            KEY_INSTANCE_ID: self.test_spec.instance_id,
            FAIL_TO_PASS: self.test_spec.FAIL_TO_PASS,
            PASS_TO_PASS: self.test_spec.PASS_TO_PASS,
        }
        report = get_eval_tests_report(
            eval_status_map, eval_ref, eval_type=get_eval_type(self.test_spec)
        )
        success = get_resolution_status(report) == ResolvedStatus.FULL.value
        if get_test_output:
            return success, out
        return int(success)
    
    def _calculate_reward_r2e(self, get_test_output=False, timeout: int = 300) -> float:
        output, error_code = self.run_tests(timeout=timeout)
        parse = self.parse_logs(output)
        parse = decolor_dict_keys(parse)
        try:
            expected_json = self.ds["expected_output_json"]
        except Exception as e:
            expected_json = self.read_file("expected_test_output.json")
        
        expected: Dict = json.loads(expected_json)
        expected = decolor_dict_keys(expected)
        parse = {k.split(" - ")[0]: parse[k] for k in sorted(parse.keys())}
        expected = {k.split(" - ")[0]: expected[k] for k in sorted(expected.keys())}
        
        if len(parse) != len(expected):
            reward = 0.0
        else:
            match = True
            for k in parse.keys():
                if not k:
                    continue
                if k not in expected:
                    match = False
                    break
                if parse[k] != expected[k]:
                    match = False
                    break
            reward = 1.0 if match else 0.0
        
        if get_test_output:
            return reward, output
        return reward
    
    def _calculate_reward(self, get_test_output=False, timeout: int = 300):
        if self.swebench_verified:
            return self._calculate_reward_swebench(get_test_output=get_test_output, timeout=timeout)
        elif self.swesmith:
            return self._calculate_reward_swesmith(get_test_output=get_test_output, timeout=timeout)
        else:
            return self._calculate_reward_r2e(get_test_output=get_test_output, timeout=timeout)
    
    def reset(self):
        self.stop_container()
        self.start_container(
            self.docker_image, self.command, self.container_name, **self.init_kwargs
        )
    
    def run_swebv_regression(self, run_tests_regression: str = None, timeout: int = 300) -> str:
        if run_tests_regression is None:
            run_tests_regression = self.ds["run_tests_regression"]
        
        with tempfile.NamedTemporaryFile("w") as f:
            f.write(run_tests_regression)
            f.flush()
            self.copy_to_container(f.name, "/run_tests_regression.sh")
        
        self.run("chmod +x /run_tests_regression.sh")
        output, error_code = self.run("/run_tests_regression.sh", timeout=timeout)
        return output
    
    def start_new_branch(self, branch_name: str = "exp") -> Tuple[str, str]:
        self.run("git config --global user.email 'you@example.com'")
        self.run("git config --global user.name 'Your Name'")
        output, error_code = self.run("git rev-parse HEAD")
        self.current_commit = output.strip()
        return output, error_code
    
    def commit_after_step(self, step_idx: int) -> Tuple[str, str]:
        output, error_code = self.run("git add .")
        output, error_code = self.run(f"git commit -m '{step_idx}'")
        return output, error_code
    
    def undo_last_commit(self) -> Tuple[str, str]:
        output, error_code = self.run("git reset --hard HEAD~1")
        return output, error_code
    
    def get_current_commit_hash(self) -> str:
        output, _ = self.run("git rev-parse HEAD")
        return output.strip()
    
    def soft_git_reset(self) -> Tuple[str, str]:
        output, error_code = self.run(f"git reset --soft {self.current_commit}")
        return output, error_code
