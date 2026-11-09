<div align="center">

# 工头 foreman-plane

### 让任务治理跑在 agent 外面

[![PyPI 版本](https://img.shields.io/pypi/v/foreman-plane?style=flat-square&color=5B4CF0)](https://pypi.org/project/foreman-plane/)
[![下载量](https://badgen.net/pypi/dm/foreman-plane?label=downloads&color=4E9BF1)](https://pypi.org/project/foreman-plane/)
![Protocol](https://img.shields.io/badge/Protocol-MCP-blue)
![Python](https://img.shields.io/badge/Python-%3E%3D3.10-blue)
![许可证](https://img.shields.io/badge/License-MIT-yellow.svg)

[English](README.md) · **简体中文**

</div>

## 一句话

**工头是一层跑在 agent harness 之外的治理平面。** DeepSeek Harness、Claude Code、
Codex 或者任意命令行 agent 都只是可插拔的「执行体」；真正被工头掌管的是会话管不了
的那几件事：派单、依赖闸门、客观产物验收，以及一条只追加的审计台账。

> **agent 忘了的承诺不算承诺。** 通过与否只认磁盘上的产物，所以判据活得比产出它的
> 那段会话更久。

![执行体通过 MCP/stdio 与工头对话](https://raw.githubusercontent.com/Anthony-Vivalink/foreman-plane/main/docs/images/architecture.zh.png)

```text
┌──────────────────┐        ┌────────────────────┐
│ 执行体（可插拔） │        │ 工头 foreman-plane │
│ Claude Code      │ ─────▶ │ 派单               │
│ Codex            │        │ 依赖闸门           │
│ DeepSeek Harness │ ◀───── │ 产物验收           │
│ 任意 CLI agent   │        │ 只追加台账         │
└──────────────────┘        └────────────────────┘
    MCP / stdio —— JSON-RPC 2.0，一行一个 JSON 对象
```

* **跨会话** —— 台账比任何一段对话都活得久。
* **跨实例** —— 多个 agent 共用同一张任务图。
* **跨天** —— 今天派出去的单，明天任何一个执行体都能接着干。
* **不侵入** —— 工头就是一个普通进程，用它不需要给任何 agent 框架打补丁。

零第三方依赖，Python ≥ 3.10，只用标准库。

## 为什么需要工头

| 能力 | 带来的变化 |
| --- | --- |
| **两级依赖闸门** | 带 `[depends_on]` 的单子，上游没终态、没通过、没验收就开不了工；`[needs_api]` 是软闸门，产物没就位只记一笔，任务照跑。 |
| **客观产物验收** | 通过与否由产物算出来——`min_bytes`、`sha256`、`contains`、`exit_code`。嘴上说写了报告、盘上没文件，照样判不通过。 |
| **无幽灵派单** | 被闸门拦下的单子只写 `blocked` 事件、不写 `dispatch` 事件，台账里不会出现没跑过的派单。 |
| **只追加台账** | 每个决策一行 JSON，历史不改写、不删除，能查询、能 diff、能被一个不是大模型的东西审计。 |
| **执行体可插拔** | 工头不选模型，也不关心哪条命令跑这张单；换条命令，治理逻辑照旧。 |
| **依赖解析失败即致命** | 依赖标记解析不出来就拒绝派单，绝不静默丢掉。 |

## 装上之后是什么体验

> 你：`这批报告先跑完，跑完再让下游合并`
>
> 工头：拆成三张单。下游那张挂着 `[depends_on]`，上游文件没落地，下游连跑都不跑。
> 全部跑完再对着产物核一遍，缺字段就是 `fail`，台账里写清楚缺在哪个文件。

## 安装

```bash
pip install foreman-plane
```

或者从源码目录直接跑：

```bash
cd foreman-plane
export PYTHONPATH="$PWD/src"
export FOREMAN_HOME="$PWD/.foreman"
```

### 接进 DSH

工头自带 MCP 服务，不用写一行插件代码。把 profile 补丁指向这个 stdio 服务就能用：

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

### 接进 Claude Code、Codex 或任意 MCP 客户端

```json
{
  "mcpServers": {
    "foreman": {
      "command": "python3",
      "args": ["-m", "foreman.mcp_server"],
      "env": { "FOREMAN_HOME": "/var/lib/foreman" }
    }
  }
}
```

## 任务卡契约

任务卡就是一段纯文本，四个标记可以出现在任何位置：

| 标记 | 含义 |
| --- | --- |
| `[id]` | 任务号，形如 `T-YYYYMMDD-NN` |
| `[depends_on]` | **硬依赖**：上游必须已终态、已通过、已验收 |
| `[needs_api]` | **软依赖**：相关产物就位即可开工，不要求完工 |
| `[acceptance]` | 验收产物路径，外加可选的客观判据 |

```text
[id] T-20261109-20
[depends_on] T-20261109-01
[needs_api] T-20261109-01#/var/lib/foreman/api/order_book.json
[acceptance] reports/order_book_20261109.md::min_bytes=200::contains=结论

请读取 order_book.json，产出分析报告并给出结论小节。
```

上面这种半角方括号是规范写法。全角写法 `【id】`、冒号写法 `id:`、全角空格和全角冒号
也都认。但**绝不静默丢弃**——依赖标记解析不出来，这张单会被 `dependency_unparsed`
直接拒掉。

## 工头保证什么

### 一张单怎么跑

![一张任务卡在工头里怎么跑](https://raw.githubusercontent.com/Anthony-Vivalink/foreman-plane/main/docs/images/pipeline.zh.png)

```text
  任务卡
    │
    ├─ 解析 ──▶ 契约：id / 硬依赖 / 软依赖 / 验收判据
    │
    ├─ 闸门 ──▶ 硬依赖是否全部终态、通过、已验收？
    │              ├─ 否 ──▶ blocked：台账记一笔，不执行，也不写 dispatch
    │              └─ 是
    │                   ▼
    ├─ 执行 ──▶ 调用执行体：Claude Code / Codex / DSH / 任意命令
    │
    ├─ 验收 ──▶ 按产物判据核：min_bytes / sha256 / contains / exit_code
    │
    └─ 台账 ──▶ 追加一行 JSON：dispatch / running / done / blocked / …
```

### 硬闸门 —— 任务绝不可能提前开跑

带 `[depends_on]` 的单子，只有当每个上游都终态、`verdict=pass`、产物验收也过，
才会被放行。否则工头只写一条 `blocked` 事件就停下——被拦下的单子**不会有**
`dispatch` 事件，台账里不会出现幽灵派单。

```text
[depends_on] T-20261109-01  →  上游 done 且 verdict=pass  →  放行执行
                             →  上游还在 running             →  blocked(dependency_pending)
                             →  上游查无此单                 →  blocked(dependency_unknown)
                             →  上游 verdict=fail            →  blocked(dependency_failed)
                             →  依赖标记解析不出来           →  blocked(dependency_unparsed)
```

### 软闸门 —— 产物没到位也允许开工

`[needs_api]` 标的是「要就位但不要求完工」的产物。缺了就记 `needs_api_pending`，
任务照跑不误。

### 客观验收 —— 产物说话，不看模型自述

```text
[acceptance] reports/report.md::min_bytes=200::sha256=<hex>::contains=结论
```

支持的判据：`min_bytes`、`sha256`、`contains`、`exit_code`。多个判据是「与」的关系，
相对路径按工作目录解析，遇到不认识的判据一律判不通过——宁可严，不许松。

### 只追加的审计台账

每一个决策都是 `${FOREMAN_HOME}/ledger.jsonl` 里的一行 JSON：

```json
{"ts":"2026-11-09T09:41:02.113456+00:00","event":"dispatch","task_id":"T-20261109-20","actor":"foreman","depends_on":["T-20261109-01"],"needs_api":[],"acceptance":"reports/report.md::min_bytes=200","needs_api_pending":false,"detail":{}}
```

事件类型：`dispatch`、`running`、`blocked`、`done`、`cancelled`、`replan`。
历史只追加，不改写、不删除。

## MCP 工具

| 工具 | 用途 |
| --- | --- |
| `foreman_dispatch` | 派单：解析契约 → 过闸门 → 执行 → 验收 |
| `foreman_status` | 查某个任务或全局的台账状态 |
| `foreman_cancel` | 取消任务，不存在的任务号会被拒绝 |

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

## 配置

| 变量 | 含义 |
| --- | --- |
| `FOREMAN_HOME` | 工头的主目录：台账、运行日志、agents.json |
| `FOREMAN_TIMEOUT` | 执行超时秒数，默认 900 |
| `FOREMAN_AGENT` | 默认执行体，默认 `generic` |
| `FOREMAN_ACTOR` | 台账里记录的 actor，默认 `foreman` |

`FOREMAN_HOME` 的解析顺序：环境变量 → `$HOME/.foreman` → `<cwd>/.foreman`。
`HOME` 没设也不会崩。

执行体写在 `${FOREMAN_HOME}/agents.json` 里，一条命令一个名字：

```json
{"agents": {"mybot": {"command": "mybot run --prompt {task}"}}}
```

## 开发

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

规格原文见 [`docs/SPEC.md`](docs/SPEC.md)，协议细节见 [`docs/PROTOCOL.md`](docs/PROTOCOL.md)。

## 许可证

MIT，见 [`LICENSE`](LICENSE)。
