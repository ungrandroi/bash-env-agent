"""A stdlib HTTP adapter and a deterministic offline wiring demonstration."""
import json
import os
from urllib.request import Request, urlopen


class HTTPClient:
    def __init__(self, base_url, model, timeout=120):
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self.base_url, self.model, self.timeout = base_url.rstrip("/"), model, timeout

    def complete(self, messages, tools):
        payload = {"model": self.model, "messages": messages, "tools": tools,
                   "temperature": 0, "parallel_tool_calls": False}
        headers = {"Content-Type": "application/json"}
        if os.environ.get("MODEL_API_KEY"):
            headers["Authorization"] = "Bearer " + os.environ["MODEL_API_KEY"]
        request = Request(self.base_url + "/chat/completions",
                          json.dumps(payload).encode(), headers)
        with urlopen(request, timeout=self.timeout) as response:
            return json.load(response)["choices"][0]["message"]


DEMO_TASKS = [
    "Read note.txt, remember its MEMORY_CODE, then delete note.txt. Do not repeat the code in your reply.",
    "Use the cd tool to enter archive. Create marker.txt containing ready. Reply briefly without repeating the remembered code.",
    "What was the MEMORY_CODE from the first task? Answer from your memory without using tools.",
]


class ScriptedClient:
    """Known tool plan, then a parser of visible history. Not an LLM or benchmark."""
    def __init__(self):
        self.index = 0
        self.plan = [("cat", {"path": "note.txt"}), ("rm", {"path": "note.txt"}), "Read and removed the note.",
                     ("cd", {"path": "archive"}), ("write_file", {"path": "marker.txt", "content": "ready\n"}),
                     "Created marker.txt."]

    def complete(self, messages, tools):
        index = self.index
        self.index += 1
        if index < len(self.plan):
            item = self.plan[index]
            if isinstance(item, str):
                return {"role": "assistant", "content": item}
            name, args = item
            return {"role": "assistant", "content": None, "tool_calls": [{
                "id": f"call_{index}", "type": "function",
                "function": {"name": name, "arguments": json.dumps(args)}}]}
        # No cached answer: the final response reads only messages provided by Memory.
        code = None
        for message in messages:
            if message["role"] == "tool":
                for line in json.loads(message["content"]).get("stdout", "").splitlines():
                    if line.startswith("MEMORY_CODE="):
                        code = line.partition("=")[2]
        return {"role": "assistant", "content": code or "Earlier observation is not available."}
