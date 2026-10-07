# Bash Agent Memory

一个用于理解和修改 Agent Memory 的最小 Python 项目：真实 Bash 环境、工具定义、Agent 输入与执行循环、可替换的记忆模块。Python 3.10+，运行代码仅使用标准库；不依赖 BFCL、MiniGrid、模型权重或其他项目目录。

```text
用户任务 → Memory.update(user)
               ↓
         Memory.render() → 实际模型输入 → 模型响应
               ↑                            ↓
         Memory.update(tool) ← Bash 环境 ← 工具调用
```

模型每次通过 `complete(messages, tools)` 接收输入。**`memory.render()` 是唯一的消息输入来源**，Agent 循环不会偷偷追加旧历史。每次请求的实际输入、模型输出、工具结果和工作目录都记录在 JSON 中。

## 快速运行

本地离线示例只执行仓库里预先写好的命令，不需要模型、API key 或 Docker：

```bash
python -m bash_agent --backend local --memory identity --output runs/full.json
python -m bash_agent --backend local --memory recent --output runs/recent.json
python -m unittest discover -s tests -v
```

示例包含三个用户回合：

1. 读取 `note.txt` 中的代码并删除文件，不在回复中重复代码。
2. 进入 `archive`，创建 `marker.txt`。
3. 不使用工具，从已有历史回答第一轮的代码。

完整历史会回答 `teal-47`；只保留最近一轮时，会回答 `Earlier observation is not available.`。示例客户端只是固定工具计划加一个读取可见历史的解析器，**用于演示输入如何变化，不是 LLM 评测结果**。

输出目录会自动创建；已有结果文件不会被覆盖。每次运行都创建新的临时工作区，结束后删除。日志中的工作目录记录的是运行时位置，运行结束后不再存在。

## 四个主要部分

| 文件 | 修改什么 |
|---|---|
| `bash_agent/environment.py` | 工作区初始文件、命令执行、超时与环境生命周期 |
| `bash_agent/tools.py` | 工具 JSON schema、参数检查和执行分发 |
| `bash_agent/agent.py` | 系统提示、用户回合、输入构造、模型与工具循环 |
| `bash_agent/memory.py` | 历史的存储、筛选、摘要或检索策略 |

`client.py` 提供 HTTP 客户端和离线示例；`__main__.py` 提供命令行入口。建议按上表顺序读代码。

### 环境与工具

每个 episode 初始化 `note.txt` 和空的 `archive/`。暴露 16 个工具，全部经过同一个参数校验与执行入口：

| 工具 | 参数 | 用途 |
|---|---|---|
| `bash` | `command` | 通用 Bash 命令、管道和脚本 |
| `cd` | `path` | 改变跨调用保留的工作目录 |
| `pwd` | 无 | 查看当前目录 |
| `ls` | `path="."` | 显示目录详情与隐藏文件 |
| `cat` | `path` | 读取文本文件 |
| `head` / `tail` | `path`, `lines=20` | 查看开头 / 结尾若干行 |
| `write_file` | `path`, `content` | 创建或覆盖文本文件 |
| `append_file` | `path`, `content` | 追加文本，不存在则创建 |
| `mkdir` | `path`, `parents=true` | 创建目录 |
| `touch` | `path` | 创建空文件或更新时间戳，保留原内容 |
| `cp` | `source`, `destination`, `recursive=false` | 复制文件或目录 |
| `mv` | `source`, `destination` | 移动或重命名 |
| `rm` | `path`, `recursive=false` | 删除文件；递归删除目录需显式开启 |
| `grep` | `path`, `pattern`, `ignore_case=false` | 按字面字符串搜索单个文件，返回行号 |
| `find` | `path="."`, `pattern="*"`, `max_depth=5` | 按文件名通配符查找普通文件 |

调用示意：

```json
{"name": "bash", "arguments": {"command": "cat note.txt | head -n 1"}}
{"name": "cd", "arguments": {"path": "archive"}}
{"name": "write_file", "arguments": {"path": "result.txt", "content": "hello\n"}}
{"name": "grep", "arguments": {"path": "result.txt", "pattern": "hello"}}
{"name": "find", "arguments": {"pattern": "*.txt", "max_depth": 3}}
```

这是参数的示意写法；实际 Chat Completions 的 `function.arguments` 是 JSON 字符串。

`bash` 支持真实的管道、重定向及文件操作，返回 `stdout / stderr / exit_code / timed_out / output_truncated / cwd`。`cd` 更新跨调用保留的工作目录。其余专用工具也通过真实 Bash 执行，并返回相同的执行结果字段。

专用工具的路径和文本按字面值处理，不展开 `~`、`$VAR`、`$(...)` 或通配符；`find.pattern` 单独支持文件名通配符。需要 shell 展开时使用 `bash`。`write_file` / `append_file` 不自动添加换行，父目录必须存在；`cp` / `mv` 遵循普通命令的目标目录语义，可能覆盖已有目标文件。`grep` 使用固定字符串匹配，不是正则表达式，退出码 1 表示没有匹配、2 表示执行错误；`find` 默认不跟随符号链接目录。

`head` / `tail` 行数范围为 1–10000，`find` 深度范围为 1–20。错误类型、缺少参数和多余参数都在执行之前被拒绝。参数转义防止专用工具的参数意外执行代码，但不构成本地模式的文件系统沙箱。

**文件和 `cd` 工具设置的目录会保留；每次 `bash` 调用都是新 shell。** 因此 `bash("cd archive && ls")` 只影响这一次命令；跨调用改变目录要用 `cd` 工具。shell 变量、函数等也不跨调用保留。

默认单条命令超时 5 秒，stdout 和 stderr 各返回最多 12,000 字节。截断只限制返回文本，执行期间的输出暂存到临时文件。命令超时会关闭整个环境；普通非零退出码和参数错误会作为工具结果返回，允许 Agent 在下一步修正。

### 替换 Memory

所有实现遵循三个方法：

```python
class Memory:
    def reset(self): ...             # 新 episode 时清空状态
    def update(self, message): ...   # 接收 system / user / assistant / tool 消息
    def render(self): ...            # 构造下一次真正发送给模型的消息列表
```

自定义实现应返回独立的消息副本，并保留有效的 assistant-tool 配对。不要直接截取最后几个消息，否则可能保留工具结果却删掉对应调用。压缩历史时，可把已结束回合的摘要放到一条消息中，保留当前回合完整的工具交互。

内置两种方式：

- `IdentityMemory`：保留所有消息。
- `RecentTurnsMemory(turns=N)`：模型仅看到系统说明和最近 N 个用户回合，当前回合内的工具调用及结果完整保留。它仍在内部存储全部历史，是输入窗口对照，不是存储容量优化。

在 Python 中注入自己的 Memory：

```python
from bash_agent.agent import run_agent
from bash_agent.client import DEMO_TASKS, ScriptedClient
from bash_agent.environment import BashEnvironment
from bash_agent.memory import RecentTurnsMemory

with BashEnvironment(backend="local") as env:
    trace = run_agent(
        DEMO_TASKS,
        env,
        ScriptedClient(),
        memory=RecentTurnsMemory(turns=2),
    )
```

CLI 也可以加载自定义类。例如在项目根目录创建 `my_memory.py`：

```python
from bash_agent.memory import RecentTurnsMemory

class MyMemory(RecentTurnsMemory):
    def __init__(self):
        super().__init__(turns=2)
```

```bash
python -m bash_agent --backend local \
  --memory my_memory:MyMemory --output runs/custom.json
```

该类必须可以无参数实例化。后续可以直接实现 `Memory` 接口，替换为摘要、结构化状态或检索记忆，不需要修改工具或 Agent 循环。

## 接入真实模型

默认环境后端是 Docker；需要本机 Docker 服务可用，且预先下载镜像：

```bash
docker pull bash:5.2

# 先验证容器环境，不调用模型
python -m bash_agent --backend docker --output runs/docker-demo.json

# 再接入支持工具调用的 Chat Completions 服务
python -m bash_agent --backend docker --client http \
  --base-url http://127.0.0.1:11434/v1 --model YOUR_MODEL \
  --memory identity --request-timeout 120 \
  --output runs/model-full.json
```

不传 `--task` 时使用上面的三轮任务。也可重复传入 `--task` 自定义连续用户回合，例如：

```bash
python -m bash_agent --backend docker --client http \
  --base-url http://127.0.0.1:11434/v1 --model YOUR_MODEL \
  --task 'Create archive/result.txt containing hello.' \
  --task 'Enter archive using the cd tool, then read result.txt.' \
  --memory recent --recent-turns 2 --output runs/custom-tasks.json
```

HTTP 服务需支持 `tools`、`temperature=0` 和 `parallel_tool_calls=false`；可选鉴权通过 `MODEL_API_KEY` 环境变量传入。模型由宿主机上的 Python 客户端调用，Bash 容器不需要网络。客户端不会自动重试，模型每个响应最多调用一个工具，每个用户回合最多进行 8 次模型请求，可用 `--max-steps` 调整。文本回复表示该回合结束，**不代表任务正确性已经被评估器验证**。

Docker 环境不挂载宿主目录、不向容器传入 API key、禁用网络，使用只读根文件系统和临时工作区，并限制内存、CPU 和进程数。每个 episode 使用独立容器；正常退出、异常退出或命令超时时清理该容器。用于可复现实验时，可用 `--image` 指定已下载的固定镜像 digest；默认标签不是不可变版本。

`--backend local` 只是在临时目录里执行，**不是安全沙箱**，命令仍有当前用户权限，能够访问工作区外的文件。它用于可信的离线示例和开发调试；运行模型生成的 Bash 时使用 Docker。当前实现面向 Linux/POSIX，不支持原生 Windows shell。

## 日志与测试

`runs/*.json` 记录：

- `steps[].messages_sent_to_model`：该次模型请求的精确消息列表。
- `steps[].available_tools / model_output / tool_result`：工具定义、模型响应和执行结果。
- `steps[].cwd_before / cwd_after`：工具执行前后的持久工作目录。
- `turns[].status / answer`：回合状态和最终回复。
- `final_context`：最后一次 Memory 渲染结果；不保证包含被窗口策略隐藏的旧回合，旧输入仍可在 `steps` 中检查。

`completed` 表示模型给出最终回复；`step_limit` 表示模型调用次数用尽；`request_error`、`protocol_error`、`environment_closed` 分别表示请求异常、响应格式错误、环境已关闭。出现未完成回合时停止后续用户任务、保存日志并返回非零退出码。

```bash
python -m unittest discover -s tests -v
# 需要 Docker 服务和已下载的 bash:5.2 镜像
RUN_DOCKER_TESTS=1 python -m unittest discover -s tests -v
```

本地验证覆盖全部 16 个工具、真实 Bash、文件持久化、工作目录、环境清理、超时、返回码、输出截断、Memory 替换、工具消息配对、非法参数、特殊文件名与文本的转义，以及 HTTP 请求格式。Docker 集成测试默认跳过，GitHub Actions 中单独执行；打包这台机器的 Docker 服务未启动，因此尚未在这里验证容器路径，也未运行真实 LLM。

仓库仅需这些源码、测试、README、`pyproject.toml`、`.gitignore` 和 `.github/workflows/tests.yml`。无需上传 `runs/`、虚拟环境、模型文件或 `.env`。从仓库根目录可直接 `python -m bash_agent`；也可在自己的虚拟环境中 `python -m pip install -e .` 后使用 `bash-agent` 命令。
