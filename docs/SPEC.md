# foreman-plane v0.1.0 — 实现规格（唯一权威）

> 工头（Foreman）：部署在 agent harness **之外**的任务治理平面。DSH / Claude Code / Codex / 任意 CLI agent 都是它的接入端。
> 本文件是实现的唯一权威来源。**不要发明本文件没写的功能**；有歧义时按本文件的「默认取向」段落选。

## 0. 铁律（违反即验收失败）

1. **零第三方依赖**。只用 Python 3.10 标准库。测试用 `unittest`（不用 pytest），MCP 用手写 JSON-RPC（不用 mcp SDK）。
2. **纯标准库、零宿主耦合**：代码里不得出现任何绝对路径常量、不得 import 任何宿主模块、不得读 `/root/...`。所有路径来自配置。
3. **闸门失败必须是硬失败**：依赖不满足时**绝不执行任务**、绝不留 dispatch 记录。
4. **不得静默降级**：解析不出合法依赖 token 时，必须显式标记 `depends_on_unparsed` / `needs_api_unparsed`，不许当成"没有依赖"。
5. **append-only**：台账只追加，不改写、不删除历史事件。
6. Python ≥ 3.10（无 `tomllib`，用 JSON 配置；无 `match` 以外的新语法依赖）。

## 1. 目录结构（必须一致）

```
foreman-plane/
├── pyproject.toml            # setuptools, name="foreman-plane", requires-python=">=3.10", 无依赖
├── README.md                 # 英文主 README
├── README.zh.md              # 中文 README（内容与英文等价，非机翻）
├── LICENSE                   # MIT, 2026 Foreman Plane contributors
├── CHANGELOG.md
├── .gitignore
├── .github/workflows/ci.yml  # python 3.10/3.11/3.12 跑 python -m unittest discover -s tests
├── docs/
│   ├── SPEC.md               # 本文件
│   └── PROTOCOL.md           # 任务卡契约 + 事件模型 + MCP 工具说明（人类可读）
├── examples/
│   └── task_card.md          # 完整任务卡示例（含四个标记 + 验收判据）
├── scripts/
│   └── publish.sh            # 构建 + 校验（不内含任何 token；凭据走环境变量）
├── src/foreman/
│   ├── __init__.py           # __version__ = "0.1.0"
│   ├── config.py             # FOREMAN_HOME 解析、agents.json 载入、默认值
│   ├── contract.py           # 任务卡契约解析（四个标记 + 严格清洗 + unparsed 检测）
│   ├── ledger.py             # append-only JSONL 台账 + 状态折叠 + 裁决事件
│   ├── gate.py               # 两级依赖闸门判定
│   ├── acceptance.py         # 产物验收判据
│   ├── runner.py             # 执行体适配器（claude/codex/dsh/generic + 自定义）
│   ├── mcp_server.py         # MCP over stdio（JSON-RPC 2.0，NDJSON 帧）
│   ├── cli.py                # foreman 命令行
│   └── errors.py             # 异常类型
└── tests/
    ├── test_contract.py
    ├── test_ledger.py
    ├── test_gate.py
    ├── test_acceptance.py
    ├── test_runner.py
    ├── test_mcp_server.py
    └── test_cli.py
```

## 2. 任务卡契约（contract.py）

### 2.1 四个标记

可在任务文本任意位置出现，大小写不敏感，**全角/半角空格与冒号容错**：

| 标记 | 语义 | 产出字段 |
|---|---|---|
| `[id]` | 任务号，形如 `T-YYYYMMDD-NN` | `task_id` |
| `[depends_on]` | **硬依赖**：上游须终态且 `verdict=pass` | `depends_on: list[str]` |
| `[needs_api]` | **软依赖**：产物就位即可开工 | `needs_api: list[str]` |
| `[acceptance]` | 验收产物路径（可带判据） | `acceptance: str` |

正则骨架（全角/半角均匹配）：
- 规范写法为半角方括号：`[id]` / `[depends_on]` / `[needs_api]` / `[acceptance]`
- 方括号形式标记名：`[【\[]?\s*(id|depends_on|needs_api|验收|acceptance)\s*[】\]]?\s*[:：]?\s*`
- 冒号形式标记名：`(?<![A-Za-z0-9_"'\[{])(id|depends_on|needs_api|验收)\s*[:：]\s*`
  （`acceptance` 只在方括号形式里认，避免英文散文 `acceptance criteria:` 被误判成标记）
- 全角写法 `【id】` / `【验收】` 与冒号写法 `id:` / `验收:` 继续兼容
- 任务号：`T-\d{8}-\d{2,}`（`\d{8}` 必须是真日期：年 2000-2099，月 1-12，日 1-31；用 `datetime.date()` 校验真实性）
- 依赖 id：同上任务号正则；`needs_api` 允许 `id#path` 前缀形式

### 2.2 严格清洗与 unparsed 检测（本项目最重要的防呆点）

- **任务号**：只取完整匹配的 `T-\d{8}-\d{2,}`；被中文说明污染（如 `T-20261109-01（等 A 完成）`）→ 取到的是 `T-20261109-01`，**合法**；括号部分忽略。
- **`needs_api` 路径**：**必须锚定结尾**。只接受形如 `/abs/path`、`rel/path`、`~/path`、`name#path` 的 token；路径中不得含中文标点/空格分隔的说明文字。路径 token 正则：
  `^[A-Za-z0-9_\-./~]{1,200}(?:#[A-Za-z0-9_\-./~]{1,200})?$`
  → 含中文或含空格 → **判为非法 token**。
- **`验收` 路径**：同上正则（可带 `::key=value` 判据后缀，见 §4）。
- **unparsed 语义**（硬要求）：
  - 标记**存在**但提取出 **0 个**合法 token → `depends_on_unparsed=True`（当是 `depends_on`）/ `needs_api_unparsed=True`（当是 `needs_api`）/ `acceptance_unparsed=True`（当是 `验收`）。
  - 标记**不存在** → 对应 unparsed 标志为 False（是"没有"，不是"解析失败"）。
  - `depends_on_unparsed=True` → **调用方必须拒绝派单**（gate 返回 BLOCK，reason=`dependency_unparsed`）。
  - `needs_api_unparsed=True` / `acceptance_unparsed=True` → **不阻塞**，但必须在回执与台账里点名标记。
- **自引用与环**：同一张卡 `depends_on` 含自己的 task_id → BLOCK（reason=`dependency_cycle`）。

### 2.3 契约对象

```python
@dataclass
class TaskCard:
    raw: str
    task_id: str | None
    depends_on: list[str]
    needs_api: list[str]            # 每项形如 "path" 或 "id#path"
    acceptance: str | None          # 原始验收串（可能含 ::判据）
    depends_on_unparsed: bool
    needs_api_unparsed: bool
    acceptance_unparsed: bool
```

`parse_task_card(text) -> TaskCard`。**不得抛异常**——解析失败也要返回对象并带 unparsed 标志。

## 3. 台账（ledger.py）

### 3.1 存储

- 文件：`${FOREMAN_HOME}/ledger.jsonl`，默认 `FOREMAN_HOME` 解析顺序：
  1. 环境变量 `FOREMAN_HOME`
  2. `$HOME/.foreman`（**若 HOME 未设置则跳过，绝不崩**）
  3. `<cwd>/.foreman`
- 每行一个 JSON 对象；**只 append**。写入用 `open(..., "a", encoding="utf-8")` + 单次 `write(line)` + `flush()` + `os.fsync()`。
- 事件公共字段：`ts`（ISO8601 带时区）、`event`、`task_id`、`actor`、`detail`（dict，可空）。

### 3.2 事件类型

| event | 触发时机 | 关键字段 |
|---|---|---|
| `dispatch` | **闸门放行之后**、真正要执行之前 | `depends_on`、`needs_api`、`acceptance`、`needs_api_pending` |
| `running` | 执行体启动 | `agent` |
| `blocked` | 闸门拦下 | `reason`、`missing`（list） |
| `done` | 执行体结束 | `verdict`（`pass`/`fail`）、`acceptance_ok`（bool/null）、`exit_code` |
| `cancelled` | 人工取消 | `reason` |
| `replan` | 人工重规划 | `reason`、`new_task_id`（可选） |

**顺序铁律**：`blocked` 事件在闸门判定后立即写；**闸门 BLOCK 时绝不能有 `dispatch` 事件**（无幽灵派单）。

### 3.3 状态折叠

按 `task_id` 顺序折叠事件得到当前状态：

- 终态：`done`（verdict pass/fail）、`cancelled`
- 非终态：`dispatched`（有 dispatch 无 running）、`running`、`blocked`
- `done` 的 verdict 由最后一个 `done` 事件的 `verdict` 决定；**没有 `done` 的任务不算 pass**。
- 同一 task_id 多次 `done` → 取最后一次（允许重规划后重跑）。

**依赖满足判定**：上游 task_id 的当前状态是终态 `done` 且其 `verdict == "pass"` 且 `acceptance_ok is not False` → 满足。

### 3.4 幂等与去重

- `dispatch` 前检查：该 task_id 是否已有 **`dispatch` 事件** → 拒绝重复派单，reason=`duplicate_task`，**不写 dispatch**。
- **`blocked` 不计入重复判定**：被闸门拦下的任务**从未进入平面**，必须在其依赖满足后可再次派单（否则上游任务永远无法解锁它，任务图死锁）。
- 允许 `--force` 覆盖（CLI 有，API 也留参数）。

### 3.5 查询 API

```python
class Ledger:
    def append(self, event: str, task_id: str | None = None, **fields) -> dict
    def events(self, task_id: str | None = None) -> list[dict]
    def states(self) -> dict[str, dict]          # task_id -> 折叠状态
    def state_of(self, task_id: str) -> dict | None
    def is_dependency_satisfied(self, dep_id: str) -> bool
    def cancel(self, task_id: str, reason: str = "") -> dict
    def replan(self, task_id: str, reason: str = "", new_task_id: str | None = None) -> dict
```
`cancel`/`replan` 对**台账里不存在的 task_id** → 抛 `UnknownTaskError`，**不追加任何事件**（防幽灵任务）。

## 4. 闸门（gate.py）

```python
@dataclass
class GateDecision:
    allowed: bool
    reason: str | None            # 阻塞原因码
    missing: list[str]            # 未满足的依赖 id
    needs_api_pending: bool
    notes: list[str]              # 人类可读说明
```

判定顺序（**第一个命中即返回**）：
1. `task_id` 缺失 → BLOCK `task_id_missing`
2. `depends_on_unparsed` → BLOCK `dependency_unparsed`
3. 自引用 / 依赖成环 → BLOCK `dependency_cycle`
4. 逐个硬依赖：
   - 台账里查不到 → BLOCK `dependency_unknown`
   - 非终态 → BLOCK `dependency_pending`
   - 终态但 `verdict != "pass"` → BLOCK `dependency_failed`
   - `acceptance_ok is False` → BLOCK `dependency_unaccepted`
5. 软依赖 `needs_api`：逐条检查路径是否存在（`id#path` 取 path 部分）；任一缺失 → `needs_api_pending=True`（**不阻塞**）
6. 全部通过 → allowed=True

**环检测**：对 `depends_on` 做 DFS，检测 `task_id → dep → ... → task_id`。

## 5. 产物验收（acceptance.py）

验收串语法：`path` 或 `path::key=value`（多个判据用 `::` 连接，例：`out/report.md::min_bytes=100::sha256=abc`）。

支持的判据：
- `min_bytes=N`（文件字节数 ≥ N）
- `sha256=<hex>`（文件内容 sha256 等于该值）
- `exit_code=N`（执行体退出码等于 N）
- `contains=<substring>`（文件内容包含子串）

`verify(acceptance: str | None, exit_code: int | None = None, base_dir: Path | None = None) -> AcceptanceResult`

```python
@dataclass
class AcceptanceResult:
    ok: bool
    path: str | None
    checks: list[dict]     # 每条判据 {name, expected, actual, ok}
    reason: str | None
```
- `acceptance` 为 None → `ok=True`（无验收要求），`reason=None`。
- 路径相对 `base_dir`（默认 cwd）解析；不存在 → ok=False, reason=`artifact_missing`。
- 判据之间是 AND。

## 6. 执行体适配（runner.py）

```python
@dataclass
class RunResult:
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool = False
    duration_s: float = 0.0
```

内置适配器（**都是 harness 之上的通用接入端，不依赖 DSH**）：
| name | 命令 |
|---|---|
| `claude` | `claude -p <task>` |
| `codex` | `codex exec <task>` |
| `dsh` | `dsh headless <task>` |
| `generic` | 由 `agents.json` 里 `generic.command` 模板决定，如 `"myagent --task {task}"` |

- 模板占位符：`{task}`（必填）、`{workdir}`（可选）。
- 命令不存在 → `RunResult(exit_code=127, stderr="agent not found: <cmd>")`，**不抛异常**。
- 超时：默认 900 秒（`FOREMAN_TIMEOUT` 可覆盖），超时 → `timed_out=True`，exit_code=124。
- `agents.json`（`${FOREMAN_HOME}/agents.json`，**可选**，不存在则用内置默认）：
  ```json
  {"agents": {"mybot": {"command": "mybot run --prompt {task}", "timeout_s": 600}}}
  ```
- 通过 `FOREMAN_AGENT` 环境变量指定本次使用的 agent 名，默认 `generic`。

## 7. MCP 服务（mcp_server.py）

**协议**：JSON-RPC 2.0，**NDJSON 帧**（一行一个 JSON 对象，stdin/stdout），stderr 写日志（**stdout 只能有 JSON**）。

必须支持的方法：
- `initialize` → `{"protocolVersion": "2024-11-05", "capabilities": {"tools": {}}, "serverInfo": {"name": "foreman", "version": "0.1.0"}}`
- `notifications/initialized` → 不回复（notification 无 id）
- `tools/list` → 三个工具（见下）
- `tools/call` → 执行工具，返回 `{"content": [{"type": "text", "text": "<json>"}], "isError": bool}`
- `resources/list` / `prompts/list` → 返回空列表
- 未知方法 → JSON-RPC error `-32601`

工具：
1. `foreman_dispatch(task: str, agent?: str, workdir?: str, force?: bool)`
   - 全流程：解析契约 → 幂等/重复检查 → 闸门 → (BLOCK: 写 `blocked` 事件并返回 `{"ok":false,"blocked":true,"reason":...,"missing":[...]}`，**不执行**) → (放行: 写 `dispatch` → 执行 → 写 `running`/`done` → 产物验收 → 返回回执 `{"ok":bool,"task_id":...,"verdict":...,"acceptance_ok":...,"needs_api_pending":bool,"exit_code":...,"duration_s":...}`)
   - **`task` 文本里带 `[depends_on]` 指向不存在的任务 → 必须返回 blocked，且 runner 完全没被调用**（这条要能被测试断言）
2. `foreman_status(task_id?: str)` → 台账状态摘要
3. `foreman_cancel(task_id: str, reason?: str)` → 取消（未知 id → error）

## 8. CLI（cli.py）

命令（`python -m foreman.cli` 或控制台脚本 `foreman`）：
- `foreman dispatch --file TASK.md [--agent NAME] [--workdir DIR] [--force] [--json]`
- `foreman status [--task-id ID] [--json]`
- `foreman ledger list|show|ready|blocked|cancel|replan`（子命令形式）
- `foreman verify --acceptance "<验收串>"`
- `foreman mcp`（启动 MCP stdio 服务）
- `foreman config`（打印当前生效配置：FOREMAN_HOME、ledger 路径、可用 agent 名）

所有命令支持 `--json`。退出码：0 成功 / 2 闸门阻塞 / 3 验收失败 / 1 其它错误。

## 9. 配置（config.py）

```python
@dataclass
class ForemanConfig:
    home: Path
    ledger_path: Path
    runs_dir: Path
    agents: dict[str, dict]
    timeout_s: int
```
- `FOREMAN_HOME` 解析见 §3.1；`FOREMAN_TIMEOUT` 覆盖超时；`FOREMAN_AGENT` 覆盖默认 agent。
- **HOME 未设置时不得抛异常**（容器化的 CI 环境常见这种情况）。

## 10. 测试（tests/，stdlib unittest）

**必须覆盖，且必须全绿**：

| 文件 | 必测用例 |
|---|---|
| test_contract.py | 四标记正常解析；全角/半角/大小写变体；`depends_on` 标记存在但无合法 token → `depends_on_unparsed=True`；`needs_api` 路径含中文 → `needs_api_unparsed=True`；`验收` 路径含空格 → `acceptance_unparsed=True`；任务号日期非法（如 20260230）→ 不认；缺标记 → unparsed 全 False |
| test_ledger.py | append 后能读回；状态折叠（dispatched→running→done pass）；cancelled 覆盖；`done` verdict=fail → 依赖不满足；**cancel 不存在的 id 抛 UnknownTaskError 且不追加事件**；重复 dispatch 被拒且**不产生第二条 dispatch** |
| test_gate.py | dependency_unknown / dependency_pending / dependency_failed / dependency_unaccepted / dependency_unparsed / dependency_cycle 六个 BLOCK 原因各一例；全部 pass 才放行；软依赖缺失 → allowed=True 且 needs_api_pending=True |
| test_acceptance.py | 存在且非空→ok；min_bytes；sha256；contains；exit_code；文件缺失→ok=False |
| test_runner.py | 用**假的适配器**（写一个临时脚本）验证：exit_code 透传、stdout 捕获、命令不存在→127、超时→124 |
| test_mcp_server.py | 起一个 MCP server 子进程（`sys.executable -m foreman.mcp_server`），用 NDJSON 喂 initialize/tools/list/tools/call；**关键用例**：dispatch 一个 depends_on 不存在的任务 → 返回 blocked、isError 或 ok=false、且台账里只有 `blocked` 没有 `dispatch` |
| test_cli.py | `foreman status --json` 可解析；`dispatch` 阻塞时退出码为 2 |

- 测试用 `tempfile.TemporaryDirectory()` 作 FOREMAN_HOME，**不得污染真实 HOME**。
- 测试不得依赖网络、不得依赖 claude/codex/dsh 是否安装。

## 11. 文档与打包

- `README.md`（英文）与 `README.zh.md`（中文）：必须包含
  - 一句话定位 + **「部署在 harness 之外」**的明确表述
  - 快速开始（3 步内能跑起来）、任务卡示例、MCP 接入示例（贴 `cordis.patch.yml` 的 `@deepseek-ai/dsh-mcp-client` 配置样例）
  - 与其它方案的差异（**不要点名任何现有项目**，用能力差异描述）
  - 中文版不是机翻，允许重写但信息必须等价
- `docs/PROTOCOL.md`：任务卡契约、事件模型、MCP 工具签名、验收判据语法
- `pyproject.toml`：`name="foreman-plane"`, `version="0.1.0"`, `license="MIT"`, `requires-python=">=3.10"`, `dependencies=[]`,
  `[project.scripts] foreman = "foreman.cli:main"`, `[tool.setuptools] package-dir = {"" = "src"}`
- `scripts/publish.sh`：`set -euo pipefail`；构建 wheel/sdist（`python -m build` 若不可用则退化为 `pip wheel`）；**必须先跑测试再构建**；上传用 `TWINE_USERNAME/TWINE_PASSWORD` 环境变量，脚本里不得出现任何凭据字面量。
- `CHANGELOG.md`：`## 0.1.0` 首次发布条目。
- `.github/workflows/ci.yml`：矩阵 3.10/3.11/3.12，`python -m unittest discover -s tests -v`。

## 12. 交付要求

1. 全部代码写在仓库根目录 `foreman-plane/` 下（包源码在 `src/foreman/`）。
2. **测试必须真的跑通**：`cd foreman-plane && python3 -m unittest discover -s tests -v` 全绿，把输出贴进 `docs/IMPLEMENTATION_REPORT.md`。
3. `docs/IMPLEMENTATION_REPORT.md` 写：文件清单、测试输出摘要、已知限制、v2 计划（cron/环境反写、过夜不炸、Web UI）。
4. **不要** git init / git commit / 任何 push / 任何上传（凭据没有，且由主脑统一做）。
5. **不要**改 `foreman-plane/` 以外的任何文件。
6. 不要 pip install 任何东西（环境无网/无依赖）；只用标准库。

## 13. 默认取向（有歧义时按此选）

- 拿不准要不要阻塞 → **阻塞**（闸门宁严勿松）。
- 拿不准要不要写事件 → **写**（append-only，宁可多留痕）。
- 拿不准异常怎么处理 → 转成结构化 `reason` 码返回，**不要抛栈给调用方**（CLI 层才 exit code）。
- 拿不准要不要写 Windows 兼容 → 写（`pathlib` + `os.path`，别硬编码 `/`）。
