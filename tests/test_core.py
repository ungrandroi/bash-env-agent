import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch
from bash_agent.agent import run_agent
from bash_agent.client import DEMO_TASKS, HTTPClient, ScriptedClient
from bash_agent.environment import BashEnvironment
from bash_agent.memory import IdentityMemory, RecentTurnsMemory
from bash_agent.tools import TOOLS, execute_tool


def call(name, args):
    return {"id": "t", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


class CoreTests(unittest.TestCase):
    def test_real_bash_cwd_files_cleanup(self):
        with BashEnvironment("local") as env:
            root = Path(env.root)
            self.assertEqual(env.bash("cat note.txt | cut -d= -f2")["stdout"], "teal-47\n")
            env.bash("cd archive && printf 'inside' > a.txt")
            self.assertEqual(env.cwd, str(root))
            env.cd("archive")
            self.assertEqual(env.bash("cat a.txt")["stdout"], "inside")
            env.bash("printf 'next' >> a.txt")
            self.assertEqual(env.bash("cat a.txt")["stdout"], "insidenext")
            self.assertIn("error", env.cd("../.."))
        self.assertFalse(root.exists())

    def test_independent_episodes(self):
        with BashEnvironment("local") as a, BashEnvironment("local") as b:
            a.bash("rm note.txt")
            self.assertNotEqual(a.root, b.root)
            self.assertEqual(b.bash("cat note.txt")["exit_code"], 0)

    def test_nonzero_stderr_and_output_limit(self):
        with BashEnvironment("local", max_output=20) as env:
            result = env.bash("printf 'problem' >&2; exit 7")
            self.assertEqual(result["exit_code"], 7)
            self.assertEqual(result["stderr"], "problem")
            self.assertFalse(env.closed)
            result = env.bash("printf '%100s' x")
            self.assertTrue(result["output_truncated"])
            self.assertEqual(len(result["stdout"]), 20)

    def test_timeout_closes_environment(self):
        with BashEnvironment("local", timeout=.2) as env:
            result = env.bash("sleep 10")
            self.assertTrue(result["timed_out"])
            self.assertTrue(result["environment_closed"])
            with self.assertRaises(RuntimeError):
                env.bash("pwd")

    def test_no_model_credentials_in_local_shell(self):
        with patch.dict(os.environ, {"MODEL_API_KEY": "unit-test-key"}):
            with BashEnvironment("local") as env:
                self.assertEqual(env.bash('printf "%s" "${MODEL_API_KEY-unset}"')["stdout"], "unset")

    def test_memory_changes_exact_input_and_preserves_tool_exchanges(self):
        traces = []
        for memory in (IdentityMemory(), RecentTurnsMemory()):
            with BashEnvironment("local") as env:
                traces.append(run_agent(DEMO_TASKS, env, ScriptedClient(), memory))
        full, recent = traces
        self.assertEqual(full["turns"][-1]["answer"], "teal-47")
        self.assertEqual(recent["turns"][-1]["answer"], "Earlier observation is not available.")
        self.assertEqual(len(recent["steps"][-1]["messages_sent_to_model"]), 2)
        self.assertNotIn("teal-47", json.dumps(recent["steps"][-1]["messages_sent_to_model"]))
        for trace in traces:
            for step in trace["steps"]:
                pending = []
                for message in step["messages_sent_to_model"]:
                    if message["role"] == "assistant":
                        pending.extend(c["id"] for c in message.get("tool_calls", []))
                    if message["role"] == "tool":
                        self.assertEqual(pending.pop(0), message["tool_call_id"])
                self.assertFalse(pending)

    def test_memory_copy_and_reset(self):
        memory = IdentityMemory()
        message = {"role": "user", "content": "original"}
        memory.update(message)
        message["content"] = "changed"
        rendered = memory.render()
        rendered[0]["content"] = "also changed"
        self.assertEqual(memory.render()[0]["content"], "original")
        memory.reset()
        self.assertEqual(memory.render(), [])

    def test_invalid_tool_arguments_do_not_execute(self):
        with BashEnvironment("local") as env:
            for tool in [call("missing", {}), call("bash", {"command": "rm note.txt", "extra": 1}),
                         call("bash", {"command": ["rm", "note.txt"]}), call("cd", {"path": ""})]:
                self.assertIn("error", execute_tool(env, tool))
            broken = call("bash", {})
            broken["function"]["arguments"] = "{broken"
            self.assertIn("error", execute_tool(env, broken))
            self.assertEqual(env.bash("cat note.txt")["exit_code"], 0)

    def test_limits_and_model_failures(self):
        class Client:
            def __init__(self, output=None, error=None):
                self.output, self.error = output, error
            def complete(self, messages, tools):
                if self.error:
                    raise self.error
                return self.output
        examples = [
            (Client(error=TimeoutError("HTTP timeout")), "request_error"),
            (Client({"role": "assistant", "content": None}), "protocol_error"),
            (Client({"role": "assistant", "tool_calls": [call("bash", {"command": "rm note.txt"})]*2}), "protocol_error"),
            (Client({"role": "assistant", "tool_calls": [call("bash", {"command": "pwd"})]}), "step_limit"),
        ]
        for client, status in examples:
            with self.subTest(status=status), BashEnvironment("local") as env:
                trace = run_agent(["first", "must not run"], env, client, max_steps=2)
                self.assertEqual(trace["turns"][0]["status"], status)
                self.assertEqual(len(trace["turns"]), 1)
                self.assertTrue(trace["steps"][0]["messages_sent_to_model"])
                self.assertEqual(env.bash("cat note.txt")["exit_code"], 0)

    def test_http_request_contract(self):
        expected = {"role": "assistant", "content": "ok"}
        with patch("bash_agent.client.urlopen") as open_url:
            response = open_url.return_value.__enter__.return_value
            response.read.return_value = json.dumps({"choices": [{"message": expected}]}).encode()
            result = HTTPClient("http://example.invalid/v1/", "test-model").complete(
                [{"role": "user", "content": "test"}], TOOLS)
            request = open_url.call_args.args[0]
            body = json.loads(request.data)
            self.assertEqual(request.full_url, "http://example.invalid/v1/chat/completions")
            self.assertEqual(body["tools"], TOOLS)
            self.assertFalse(body["parallel_tool_calls"])
            self.assertEqual(body["model"], "test-model")
            self.assertEqual(result, expected)


@unittest.skipUnless(os.environ.get("RUN_DOCKER_TESTS") == "1", "Docker integration is opt-in")
class DockerTests(unittest.TestCase):
    def test_real_container(self):
        with BashEnvironment("docker") as env:
            self.assertEqual(env.bash("cat note.txt")["stdout"], "MEMORY_CODE=teal-47\n")
            self.assertEqual(env.cd("archive")["cwd"], "/workspace/archive")
            self.assertEqual(env.bash("pwd")["stdout"], "/workspace/archive\n")
            self.assertNotEqual(env.bash("touch /cannot-write-root")["exit_code"], 0)
            self.assertIn("error", env.cd("/"))
        self.assertTrue(env.closed)

    def test_timeout_removes_container(self):
        with BashEnvironment("docker", timeout=2) as env:
            result = env.bash("sleep 30")
            self.assertTrue(result["timed_out"])
            self.assertTrue(env.closed)
            self.assertIsNone(env.container)


if __name__ == "__main__":
    unittest.main()
