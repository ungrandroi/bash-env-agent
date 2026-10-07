# Bash Env Agent

A small Bash agent with 16 tools and replaceable memory. Python 3.10+, Linux/POSIX, no Python runtime dependencies.

## Quick start

Run the offline demo with trusted, scripted commands:

```bash
python -m bash_agent --backend local --memory identity --output runs/full.json
python -m bash_agent --backend local --memory recent --output runs/recent.json
```

The demo reads a code, deletes the file, performs another task, then recalls the code. It demonstrates memory inputs, not LLM performance. Exact model inputs and tool results are saved as JSON.

For a real model, use Docker and a Chat Completions endpoint that supports tool calls:

```bash
docker pull bash:5.2
python -m bash_agent --backend docker --client http \
  --base-url http://127.0.0.1:11434/v1 --model YOUR_MODEL \
  --memory identity --output runs/model.json
```

Optional authentication: `MODEL_API_KEY`. Add repeated `--task '...'` arguments for custom user turns. Use a new output filename for each run.

## Tools and Bash behavior

`bash`, `cd`, `pwd`, `ls`, `cat`, `head`, `tail`, `write_file`, `append_file`, `mkdir`, `touch`, `cp`, `mv`, `rm`, `grep`, `find`.

Dedicated tools invoke real commands with quoted arguments. For example, `cat` with `{"path": "a b.txt"}` behaves like `cat -- 'a b.txt'`. Pass the actual filename, without extra shell quotes. Spaces, quotes, `$`, and backticks in filenames or text stay literal.

- `write_file` / `append_file` use `printf '%s'` with `>` / `>>`; no newline is added.
- `grep` uses `grep -n -F` (literal matching); `find.pattern` accepts filename globs.
- Use `bash` for variable expansion, wildcards, pipelines, or shell scripts.
- Files persist. Each `bash` call starts a fresh shell; variables and internal `cd` do not persist. Use the `cd` tool for persistent directory changes; it uses physical paths (`cd -P`).
- Command failures retain their exit codes and stderr. Output is truncated at 12,000 bytes per stream; command timeout closes the environment.

Local mode executes with your user permissions and is **not a sandbox**. Docker uses a temporary workspace, no host mounts, and no network. Available utilities and flags depend on the selected image or local installation.

## Code and memory

| File | Responsibility |
|---|---|
| `environment.py` | Bash execution and workspace lifecycle |
| `tools.py` | Tool schemas, validation, and command construction |
| `agent.py` | User → model → tool loop |
| `memory.py` | `reset()`, `update(message)`, `render()` |
| `client.py` | HTTP client and offline demo |

`memory.render()` is the only source of model messages. `identity` keeps all messages; `recent` sends the last N user turns (`--recent-turns N`), preserving complete tool exchanges. It retains the full history internally.

To replace memory, implement the three methods and pass `memory=YourMemory()` to `run_agent`, or use `--memory your_module:YourMemory` for a class with a no-argument constructor.

## Tests

```bash
python -m unittest discover -s tests -v
RUN_DOCKER_TESTS=1 python -m unittest discover -s tests -v
```

Docker tests require a running daemon and the image above. GitHub Actions runs both local and Docker tests.
