"""Real Bash, with Docker isolation or an explicit local debug backend."""
import os
import signal
import subprocess
import tempfile
import uuid
from pathlib import Path, PurePosixPath


class BashEnvironment:
    def __init__(self, backend="docker", image="bash:5.2", timeout=5, max_output=12000):
        if backend not in ("docker", "local") or timeout <= 0 or max_output < 1:
            raise ValueError("Invalid environment configuration")
        self.backend, self.image = backend, image
        self.timeout, self.max_output = timeout, max_output
        self.closed = True
        self.container = None
        self.temp = None

    def __enter__(self):
        try:
            if self.backend == "docker":
                self.container = "bash-agent-" + uuid.uuid4().hex[:12]
                # No host mounts, credentials, or container network. Image must exist.
                subprocess.run([
                    "docker", "run", "--pull=never", "--detach", "--rm",
                    "--name", self.container, "--network=none", "--read-only",
                    "--cap-drop=ALL", "--security-opt=no-new-privileges", "--user=65534:65534",
                    "--pids-limit=64", "--memory=128m", "--cpus=1",
                    "--tmpfs=/workspace:rw,nosuid,nodev,size=32m,mode=1777",
                    "--tmpfs=/tmp:rw,nosuid,nodev,size=16m,mode=1777",
                    "--workdir=/workspace", "--entrypoint=bash", self.image,
                    "--noprofile", "--norc", "-c", "while :; do sleep 3600; done",
                ], check=True, capture_output=True, text=True, timeout=30)
                self.root = "/workspace"
            else:
                self.temp = tempfile.TemporaryDirectory(prefix="bash-agent-")
                self.root = self.temp.name
            self.cwd = self.root
            self.closed = False
            # Fixtures live only in this episode's temporary workspace.
            result = self.bash("mkdir -p archive && printf 'MEMORY_CODE=teal-47\\n' > note.txt")
            if result["exit_code"] != 0 or result["timed_out"]:
                raise RuntimeError(f"Workspace initialization failed: {result}")
            return self
        except BaseException:
            self.close()
            raise

    def _execute(self, command):
        if self.closed:
            raise RuntimeError("Environment is closed")
        if self.backend == "docker":
            argv = ["docker", "exec", "--workdir", self.cwd, self.container,
                    "bash", "--noprofile", "--norc", "-c", command]
            kwargs = {}
        else:
            argv = ["bash", "--noprofile", "--norc", "-c", command]
            kwargs = {"cwd": self.cwd, "env": {"PATH": os.defpath, "LANG": "C.UTF-8"}}
        # Disk-backed capture avoids holding arbitrary command output in RAM.
        with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
            proc = subprocess.Popen(argv, stdout=out, stderr=err, start_new_session=True, **kwargs)
            timed_out = False
            try:
                proc.wait(timeout=self.timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
            finally:
                # Also clean up ordinary background children of a local command.
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                proc.wait()
                if timed_out:
                    # Killing docker exec alone would leave the command running.
                    self.close()
            sizes = [os.fstat(stream.fileno()).st_size for stream in (out, err)]
            out.seek(0)
            err.seek(0)
            return {"stdout": out.read(self.max_output).decode("utf-8", errors="replace"),
                    "stderr": err.read(self.max_output).decode("utf-8", errors="replace"),
                    "exit_code": proc.returncode, "timed_out": timed_out,
                    "output_truncated": any(size > self.max_output for size in sizes)}

    def bash(self, command):
        result = self._execute(command)
        return {**result, "cwd": self.cwd, "environment_closed": self.closed}

    def cd(self, path):
        # cd is an explicit persistent state change; each bash call is a fresh shell.
        if self.backend == "local":
            target = (Path(self.cwd) / path).resolve()
            if not target.is_relative_to(Path(self.root)) or not target.is_dir():
                return {"error": "Directory must exist inside the workspace", "cwd": self.cwd}
            self.cwd = str(target)
        else:
            import shlex
            result = self._execute("cd -- " + shlex.quote(path) + " && pwd -P")
            if result["timed_out"]:
                return {**result, "cwd": self.cwd, "environment_closed": self.closed}
            target = result["stdout"].strip()
            if result["exit_code"] != 0 or result["output_truncated"] or not PurePosixPath(target).is_relative_to(self.root):
                return {"error": "Directory must exist inside the workspace", "cwd": self.cwd}
            self.cwd = target
        return {"cwd": self.cwd}

    def close(self):
        self.closed = True
        if self.container:
            # Remove only the uniquely named container owned by this instance.
            subprocess.run(["docker", "rm", "--force", self.container],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
            self.container = None
        if self.temp:
            self.temp.cleanup()
            self.temp = None

    def __exit__(self, *exc):
        self.close()
