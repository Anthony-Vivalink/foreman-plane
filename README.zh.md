# foreman-plane

**部署在 agent harness 之外的任务治理平面。**

foreman-plane（下称"工头"）是**在 agent 之上**的那一层，而不是塞进 agent 里面的一层。
Claude Code、Codex、DeepSeek Harness 或者任意 CLI agent 都只是可插拔的"执行体"；
真正由工头掌管的是 agent 会话管不了的那几件事：派单、依赖闸门、客观产物验收、
以及一条只追加的审计台账。

```
        ┌───────────────────────────────────────────┐
        │            foreman-plane（工头）          │
        │  契约解析 → 闸门 → 台账 → 产物验收        │
        └───┬───────────────┬───────────────┬───────┘
            │ MCP/stdio     │ MCP/stdio     │ MCP/stdio
       ┌────▼─────┐    ┌────▼─────┐    ┌────▼─────┐
       │ Claude   │    │  Codex   │    │   DSH    │      ← 执行体
       └──────────┘    └──────────┘    └──────────┘
```

* **跨会话** —— 台账比任何一段对话都活得久。
* **跨实例** —— 多个 agent 共用同一张任务图。
* **跨天** —— 今天派出去的单，明天任何一个执行体都能接着干。
* **在 harness 之外** —— 工头就是一个普通进程，用它不需要给任何 agent 框架打补丁。

零第三方依赖，Python ≥ 3.10，只用标准库。

---

## 快速开始

```bash
# 1. 直接从代码目录跑起来
cd foreman-plane
export PYTHONPATH="$PWD/src"
export FOREMAN_HOME="$PWD/.foreman"

# 2. 声明一个执行体
cat > "$FOREMAN_HOME/agents.json" <<'JSON'
{"agents": {"mybot": {"command": "mybot run --prompt {task}"}}}
JSON

# 3. 派一张任务卡
cat > card.md <<'MD'
【id】T-20261109-20
【depends_on】T-20261109-01
【验收】reports/report.md::min_bytes=200
写一份报告。
MD

python3 -m foreman.cli dispatch --file card.md --agent mybot --json
```

`python -m foreman.cli` 和安装后的 `foreman` 命令等价。

---

## 任务卡

任务卡就是一段纯文本，四个标记可以出现在任何位置：

| 标记            | 含义                                                        |
| --------------- | ----------------------------------------------------------- |
| `【id】`         | 任务号，形如 `T-YYYYMMDD-NN`                                |
| `【depends_on】` | **硬依赖** —— 上游必须已终态、已通过、已验收                 |
| `【needs_api】`  | **软依赖** —— 相关产物就位即可开工，缺了只提示不阻塞         |
| `【验收】`       | 验收产物路径，外加可选的客观判据                             |

```text
【id】T-20261109-20
【depends_on】T-20261109-01
【needs_api】T-20261109-01#/var/lib/foreman/api/order_book.json
【验收】reports/order_book_20261109.md::min_bytes=200::contains=结论

请读取 order_book.json，产出分析报告并给出结论小节。
```

标记写法很宽容（`【id】`、`[id]`、`ID:`、全角空格和全角冒号都认），但**依赖解析不出来
绝不静默丢弃**：这种单子会被 `dependency_unparsed` 直接拒掉。

---

## 工头到底保证什么

### 硬闸门 —— 任务绝不可能提前开跑

带 `【depends_on】` 的单子，只有当每个上游都终态、`verdict=pass`、产物验收也过，
才会被放行。否则工头只写一条 `blocked` 事件就停下——被拦下的单子**不会有**
`dispatch` 事件，台账里不会出现"幽灵派单"。

```text
【depends_on】T-20261109-01  →  上游 done 且 verdict=pass  →  放行执行
                             →  上游还在 running             →  blocked(dependency_pending)
                             →  上游查无此单                 →  blocked(dependency_unknown)
                             →  上游 verdict=fail            →  blocked(dependency_failed)
                             →  依赖标记解析不出来           →  blocked(dependency_unparsed)
```

### 软闸门 —— 产物没到位也允许开工

`【needs_api】` 标的是"要就位但不要求完工"的产物。缺了就记 `needs_api_pending`，
任务照跑不误。

### 客观验收 —— 产物说话，不看模型自述

```text
【验收】reports/report.md::min_bytes=200::sha256=<hex>::contains=结论
```

支持的判据：`min_bytes`、`sha256`、`contains`、`exit_code`。多个判据是"与"的关系，
相对路径按工作目录解析，遇到不认识的判据一律判不通过（宁可严，不许松）。

### 只追加的审计台账

每一个决策都是 `${FOREMAN_HOME}/ledger.jsonl` 里的一行 JSON：

```json
{"ts":"2026-11-09T09:41:02.113456+00:00","event":"dispatch","task_id":"T-20261109-20","actor":"foreman","depends_on":["T-20261109-01"],"needs_api":[],"acceptance":"reports/report.md::min_bytes=200","needs_api_pending":false,"detail":{}}
```

事件类型：`dispatch`、`running`、`blocked`、`done`、`cancelled`、`replan`。
历史只追加，不改写、不删除。

---

## MCP 接入

工头自带 MCP 服务，stdio 上跑 JSON-RPC 2.0，用 NDJSON 分帧（一行一个 JSON 对象）。
任何 MCP 客户端指过来就能用：

```bash
python3 -m foreman.mcp_server
```

如果你的 harness 用补丁文件加载 MCP 服务，条目大致长这样（具体字段名以你用的
MCP 客户端为准，工头这边只固定了 stdio 上的 JSON-RPC 握手）：

```yaml
# ~/.dsh/profiles/headless/cordis.patch.yml
mcp:
  servers:
    - name: foreman
      command: python3
      args:
        - -m
        - foreman.mcp_server
      env:
        FOREMAN_HOME: /var/lib/foreman
```

对外暴露三个工具：

| 工具                | 用途                                                    |
| ------------------- | ------------------------------------------------------- |
| `foreman_dispatch`  | 派单（解析契约 → 过闸门 → 执行 → 验收）                |
| `foreman_status`    | 查某个任务或全局的台账状态                              |
| `foreman_cancel`    | 取消任务（不存在的任务号会被拒绝）                       |

---

## 命令行

```
foreman dispatch --file TASK.md [--agent NAME] [--workdir DIR] [--force] [--json]
foreman status   [--task-id ID] [--json]
foreman ledger   list | show | ready | blocked | cancel | replan
foreman verify   --acceptance "<验收串>"
foreman mcp
foreman config
```

退出码：`0` 成功 · `2` 被闸门拦下 · `3` 产物验收不通过 · `1` 其它错误。

---

## 配置

| 变量               | 含义                                          |
| ------------------ | --------------------------------------------- |
| `FOREMAN_HOME`     | 工头的主目录（台账、运行日志、agents.json）    |
| `FOREMAN_TIMEOUT`  | 执行超时秒数（默认 900）                       |
| `FOREMAN_AGENT`    | 默认执行体（默认 `generic`）                   |
| `FOREMAN_ACTOR`    | 台账里记录的 actor（默认 `foreman`）           |

`FOREMAN_HOME` 的解析顺序：环境变量 → `$HOME/.foreman` → `<cwd>/.foreman`。
`HOME` 没设也不会崩。

---

## 和其它做法的差别

* **对"agent 自己会记得"** —— 会话一断，依赖就没人管了。闸门是独立进程、有自己的
  持久状态，跨会话、跨 agent、跨天都还在。
* **对"agent 自己会汇报"** —— 结论由磁盘上的产物算出来（`min_bytes`、`sha256`、
  `contains`、`exit_code`），不是从对话里的一句话提取的。嘴上说写了报告、
  盘上没文件的单子，照样判不通过。
* **对"搞一个大一统 agent"** —— 工头不选模型，也不关心哪条命令跑这张单。
  执行体就是可替换的命令，治理逻辑只有一份。
* **对"上工作流引擎"** —— 这里没有 DAG 调度器、没有后台 worker、没有定时器。
  工头 = 一本台账 + 一道闸门：它只决定"这张单能不能开工"，"怎么干"交给接进去的执行体。
* **对"拿聊天记录当状态"** —— 台账是只追加、机器可读的，能被查询、能 diff、
  能被一个不是大模型的东西审计。

---

## 开发

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

规格原文见 [`docs/SPEC.md`](docs/SPEC.md)，协议细节见 [`docs/PROTOCOL.md`](docs/PROTOCOL.md)。

## 许可证

MIT，见 [`LICENSE`](LICENSE)。
