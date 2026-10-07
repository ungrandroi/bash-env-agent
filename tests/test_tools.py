import json
import os
import unittest
from bash_agent.environment import BashEnvironment
from bash_agent.tools import execute_tool


class ToolTests(unittest.TestCase):
    backend = "local"

    def setUp(self):
        self.env = BashEnvironment(self.backend).__enter__()
        self.addCleanup(self.env.close)

    def run_tool(self, name, **args):
        return execute_tool(self.env, {"function": {"name": name, "arguments": json.dumps(args)}})

    def ok(self, name, **args):
        result = self.run_tool(name, **args)
        self.assertNotIn("error", result, result)
        self.assertEqual(result.get("exit_code", 0), 0, result)
        return result

    def test_navigation_and_listing(self):
        self.assertEqual(self.ok("pwd")["stdout"].strip(), self.env.root)
        self.ok("mkdir", path="a/b")
        self.ok("touch", path="a/.hidden")
        self.assertIn(".hidden", self.ok("ls", path="a")["stdout"])
        self.ok("cd", path="a/b")
        self.assertTrue(self.ok("pwd")["stdout"].strip().endswith("/a/b"))
        self.ok("write_file", path="local.txt", content="here")
        self.assertEqual(self.ok("cat", path="local.txt")["stdout"], "here")
        self.assertNotEqual(self.run_tool("mkdir", path="missing/child", parents=False)["exit_code"], 0)

    def test_text_read_write_and_line_selection(self):
        self.ok("write_file", path="text", content="one\ntwo\n")
        self.ok("append_file", path="text", content="three\n")
        self.assertEqual(self.ok("head", path="text", lines=2)["stdout"], "one\ntwo\n")
        self.assertEqual(self.ok("tail", path="text", lines=1)["stdout"], "three\n")
        self.ok("touch", path="text")
        self.assertEqual(self.ok("cat", path="text")["stdout"], "one\ntwo\nthree\n")
        self.ok("write_file", path="text", content="")
        self.assertEqual(self.ok("cat", path="text")["stdout"], "")
        self.ok("append_file", path="new", content="no trailing newline")
        self.assertEqual(self.ok("cat", path="new")["stdout"], "no trailing newline")

    def test_copy_move_and_recursive_delete(self):
        self.ok("mkdir", path="source/sub")
        self.ok("write_file", path="source/sub/data", content="payload")
        self.ok("cp", source="source/sub/data", destination="copy")
        self.ok("mv", source="copy", destination="renamed")
        self.assertNotEqual(self.run_tool("cat", path="copy")["exit_code"], 0)
        self.assertEqual(self.ok("cat", path="renamed")["stdout"], "payload")
        self.assertNotEqual(self.run_tool("cp", source="source", destination="no-copy")["exit_code"], 0)
        self.ok("cp", source="source", destination="tree", recursive=True)
        self.assertEqual(self.ok("cat", path="tree/sub/data")["stdout"], "payload")
        self.assertNotEqual(self.run_tool("rm", path="tree")["exit_code"], 0)
        self.ok("rm", path="tree", recursive=True)
        self.ok("rm", path="renamed")
        self.assertNotEqual(self.run_tool("cat", path="tree/sub/data")["exit_code"], 0)
        self.assertNotEqual(self.run_tool("rm", path="renamed")["exit_code"], 0)

    def test_literal_search_and_find_depth(self):
        self.ok("write_file", path="a.txt", content="Alpha\na.b\naxb\n-option\n")
        self.assertEqual(self.ok("grep", path="a.txt", pattern="a.b")["stdout"], "2:a.b\n")
        self.assertEqual(self.ok("grep", path="a.txt", pattern="alpha", ignore_case=True)["stdout"], "1:Alpha\n")
        self.assertEqual(self.ok("grep", path="a.txt", pattern="-option")["stdout"], "4:-option\n")
        self.assertEqual(self.run_tool("grep", path="a.txt", pattern="absent")["exit_code"], 1)
        self.assertEqual(self.run_tool("grep", path="missing", pattern="absent")["exit_code"], 2)
        self.ok("mkdir", path="deep/sub")
        self.ok("touch", path="deep/sub/nested.txt")
        self.ok("touch", path="skip.csv")
        shallow = set(self.ok("find", pattern="*.txt", max_depth=1)["stdout"].splitlines())
        self.assertEqual(shallow, {"./a.txt", "./note.txt"})
        deep = self.ok("find", pattern="*.txt")["stdout"]
        self.assertIn("./deep/sub/nested.txt", deep)
        self.assertNotIn("skip.csv", deep)

    def test_shell_metacharacters_are_literal(self):
        filename = "- strange ' $(touch INJECTED); `touch ALSO`"
        content = "你好\n$(touch CONTENT_ATTACK) `touch BACKTICK` ' \" %s \\n"
        self.ok("write_file", path=filename, content=content)
        self.assertEqual(self.ok("cat", path=filename)["stdout"], content)
        self.ok("cp", source=filename, destination="-copy")
        self.ok("mv", source="-copy", destination="-renamed")
        self.assertEqual(self.ok("cat", path="-renamed")["stdout"], content)
        self.ok("rm", path="-renamed")
        for path in ("INJECTED", "ALSO", "CONTENT_ATTACK", "BACKTICK"):
            self.assertNotEqual(self.run_tool("cat", path=path)["exit_code"], 0)

    def test_invalid_arguments_never_change_files(self):
        for name, args in [
            ("head", {"path": "note.txt", "lines": True}),
            ("tail", {"path": "note.txt", "lines": 0}),
            ("find", {"max_depth": 21}), ("find", {"pattern": "\x00"}),
            ("rm", {"path": "note.txt", "recursive": "false"}),
            ("write_file", {"path": "note.txt", "content": None}),
            ("touch", {"path": "note.txt", "extra": True}),
            ("cp", {"source": "note.txt"}), ("pwd", {"path": "."}),
        ]:
            with self.subTest(name=name, args=args):
                self.assertIn("error", self.run_tool(name, **args))
        self.assertEqual(self.ok("cat", path="note.txt")["stdout"], "MEMORY_CODE=teal-47\n")


@unittest.skipUnless(os.environ.get("RUN_DOCKER_TESTS") == "1", "Docker integration is opt-in")
class DockerToolTests(ToolTests):
    backend = "docker"
