"""Tool schemas, strict argument validation, and quoted Bash command builders."""
import json
import shlex


def string(description, default=None, allow_empty=False):
    schema = {"type": "string", "description": description, "minLength": 0 if allow_empty else 1}
    if default is not None:
        schema["default"] = default
    return schema


def boolean(description, default=False):
    return {"type": "boolean", "description": description, "default": default}


def integer(description, default, maximum):
    return {"type": "integer", "description": description, "default": default,
            "minimum": 1, "maximum": maximum}


def tool(name, description, properties):
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": properties,
                       "required": [key for key, value in properties.items() if "default" not in value],
                       "additionalProperties": False}}}


PATH = string("Literal file or directory path, like a quoted Bash argument. Relative to cwd. Do not add shell quoting; no glob or variable expansion.")
CONTENT = string("Exact text, like Bash printf '%s' with a quoted argument. No shell expansion or automatic newline.", allow_empty=True)
TOOLS = [
    tool("bash", "Run arbitrary Bash in the current directory. Files persist, but shell variables and cd inside a command do not. Use the cd tool to change directory across calls.",
         {"command": string("Bash command or script.")}),
    tool("cd", "Change the persistent physical working directory (cd -P) to an existing directory inside the workspace.", {"path": PATH}),
    tool("pwd", "Print the current working directory.", {}),
    tool("ls", "List a directory, including hidden entries, in long format.", {"path": string("Directory to list.", ".")}),
    tool("cat", "Read a text file; long output is truncated by the environment.", {"path": PATH}),
    tool("head", "Read the first N lines of a file.", {"path": PATH, "lines": integer("Number of lines.", 20, 10000)}),
    tool("tail", "Read the last N lines of a file.", {"path": PATH, "lines": integer("Number of lines.", 20, 10000)}),
    tool("write_file", "Create or overwrite a text file. Parent directory must exist.", {"path": PATH, "content": CONTENT}),
    tool("append_file", "Append exact text to a file, creating it if absent. Parent directory must exist.", {"path": PATH, "content": CONTENT}),
    tool("mkdir", "Create a directory; optionally create missing parents.", {"path": PATH, "parents": boolean("Create missing parents and tolerate existing directories.", True)}),
    tool("touch", "Create an empty file if absent, or update timestamps without changing existing content.", {"path": PATH}),
    tool("cp", "Copy a file, or a directory with recursive=true. May overwrite existing destination files; an existing destination directory receives the source basename.",
         {"source": PATH, "destination": PATH, "recursive": boolean("Copy directories recursively.")}),
    tool("mv", "Move or rename a file/directory. May overwrite destination files; an existing destination directory receives the source basename. Does not update the persistent cwd.",
         {"source": PATH, "destination": PATH}),
    tool("rm", "Delete a file. Directories require recursive=true. Missing paths return a nonzero exit code.",
         {"path": PATH, "recursive": boolean("Remove a directory and its contents.")}),
    tool("grep", "Search a single text file for a literal string, returning matching lines with line numbers. Exit code 1 means no matches, 2 indicates an error.",
         {"path": PATH, "pattern": string("Literal search string, not a regular expression.", allow_empty=True),
          "ignore_case": boolean("Use case-insensitive matching.")}),
    tool("find", "Find regular files under a directory by a filename glob; does not follow symlink directories.",
         {"path": string("Starting directory.", "."), "pattern": string("Filename glob such as *.txt.", "*"),
          "max_depth": integer("Maximum depth relative to starting directory.", 5, 20)}),
]
SCHEMAS = {entry["function"]["name"]: entry["function"]["parameters"] for entry in TOOLS}


def validate_arguments(name, raw):
    if name not in SCHEMAS:
        raise ValueError(f"Unknown tool: {name}")
    schema = SCHEMAS[name]
    args = json.loads(raw)
    if not isinstance(args, dict) or set(args) - set(schema["properties"]) or set(schema["required"]) - set(args):
        raise ValueError(f"Invalid argument names for {name}")
    result = {}
    for key, spec in schema["properties"].items():
        value = args[key] if key in args else spec["default"]
        expected = {"string": str, "boolean": bool, "integer": int}[spec["type"]]
        if type(value) is not expected:
            raise ValueError(f"{key} must be {spec['type']}")
        if isinstance(value, str) and (len(value) < spec.get("minLength", 0) or "\x00" in value):
            raise ValueError(f"{key} is empty or contains a NUL character")
        if expected is int and not spec["minimum"] <= value <= spec["maximum"]:
            raise ValueError(f"{key} must be between {spec['minimum']} and {spec['maximum']}")
        result[key] = value
    return result


def quote_path(path):
    # Prefix relative paths so names like '-rf' cannot become command options.
    return shlex.quote(path if path.startswith(("/", "./", "../")) or path in (".", "..") else "./" + path)


def command_for(name, args):
    path = quote_path(args["path"]) if "path" in args else None
    if name == "bash":
        return args["command"]
    if name == "pwd":
        return "pwd -P"
    if name == "ls":
        return f"ls -la {path}"
    if name in ("cat", "touch"):
        return f"{name} {path}"
    if name in ("head", "tail"):
        return f"{name} -n {args['lines']} {path}"
    if name in ("write_file", "append_file"):
        redirect = ">" if name == "write_file" else ">>"
        return f"printf '%s' {shlex.quote(args['content'])} {redirect} {path}"
    if name == "mkdir":
        return f"mkdir {'-p ' if args['parents'] else ''}{path}"
    if name in ("cp", "mv"):
        flags = "-R " if name == "cp" and args["recursive"] else ""
        return f"{name} {flags}{quote_path(args['source'])} {quote_path(args['destination'])}"
    if name == "rm":
        return f"rm {'-r ' if args['recursive'] else ''}{path}"
    if name == "grep":
        return f"grep -n -F {'-i ' if args['ignore_case'] else ''}-e {shlex.quote(args['pattern'])} {path}"
    if name == "find":
        return f"find {path} -maxdepth {args['max_depth']} -type f -name {shlex.quote(args['pattern'])}"
    raise ValueError(f"No command builder for {name}")


def execute_tool(env, call):
    try:
        function = call["function"]
        name = function["name"]
        args = validate_arguments(name, function["arguments"])
        return env.cd(args["path"]) if name == "cd" else env.bash(command_for(name, args))
    except (ValueError, TypeError, KeyError, OSError, RuntimeError) as exc:
        return {"error": f"{type(exc).__name__}: {exc}", "cwd": env.cwd,
                "environment_closed": env.closed}
