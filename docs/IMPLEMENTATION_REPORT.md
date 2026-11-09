# foreman-plane v0.1.0 — 实现报告

实现范围：`foreman-plane/` 目录内的完整 Python 包（零第三方依赖，Python 3.10 标准库）。
权威规格：`docs/SPEC.md`（13 节逐条落实，未发明规格外功能）。

任务号：`T-20261109-20` · 验收产物：本文件。

---

## 1. 文件清单

```
foreman-plane/
├── pyproject.toml            # setuptools / name="foreman-plane" / version=0.1.0 / requires-python>=3.10 / dependencies=[]
├── README.md                 # 英文主 README
├── README.zh.md              # 中文 README（信息等价重写，非机翻）
├── LICENSE                   # MIT, 2026 Foreman Plane contributors
├── CHANGELOG.md              # ## 0.1.0 首发条目
├── .gitignore
├── .github/workflows/ci.yml  # 矩阵 3.10/3.11/3.12，跑 python -m unittest discover -s tests -v
├── docs/
│   ├── SPEC.md               # 权威规格（原有，未改动）
│   ├── PROTOCOL.md           # 任务卡契约 + 事件模型 + MCP 工具签名 + 验收判据语法
│   └── IMPLEMENTATION_REPORT.md
├── examples/task_card.md     # 完整任务卡示例（四标记 + 判据 + 说明）
├── scripts/publish.sh        # 先跑测试再构建；凭据只走环境变量，脚本内无任何凭据字面量
├── src/foreman/
│   ├── __init__.py           # __version__ = "0.1.0"
│   ├── config.py             # FOREMAN_HOME 解析、agents.json 合并、FOREMAN_TIMEOUT/AGENT/ACTOR
│   ├── contract.py           # 四标记解析 + 严格清洗 + unparsed 检测 + 验收串拆分
│   ├── ledger.py             # append-only JSONL + 状态折叠 + 幂等 + cancel/replan
│   ├── gate.py               # 两级依赖闸门 + DFS 环检测
│   ├── acceptance.py         # min_bytes / sha256 / contains / exit_code 判据
│   ├── runner.py             # Runner（claude/codex/dsh/generic/自定义）+ Dispatcher（派单编排）
│   ├── mcp_server.py         # MCP over stdio：JSON-RPC 2.0 + NDJSON 帧
│   ├── cli.py                # foreman 命令行
│   └── errors.py             # 异常类型（UnknownTaskError 等）
└── tests/
    ├── test_contract.py      # 12 例
    ├── test_ledger.py        # 11 例
    ├── test_gate.py          # 12 例
    ├── test_acceptance.py    # 10 例
    ├── test_runner.py        #  8 例
    ├── test_mcp_server.py    #  8 例（真起子进程）
    └── test_cli.py           #  8 例（真起子进程）
```

> 说明：为不新增规格 §1 之外的模块，派单编排逻辑（契约→闸门→台账→执行→验收）放在
> `runner.py` 的 `Dispatcher` 类里，CLI 与 MCP 共用同一份实现，不存在两套行为。

---

## 2. 规格逐节落实对照

| 规格 | 落实位置 | 要点 |
| --- | --- | --- |
| §0 铁律 | 全包 | 零依赖；无绝对路径常量（`grep -rn '"/root\|/home' src/` 无命中）；无宿主模块 import；闸门失败即硬失败；不静默降级；台账只追加；Python ≥ 3.10 |
| §1 目录结构 | 上表 | 文件清单与规格逐项一致（无多余、无缺失） |
| §2 任务卡契约 | `contract.py` | 四标记；全角/半角/大小写/冒号容错；任务号 `datetime.date()` 真实日历校验；路径 token 锚定正则；`depends_on_unparsed` / `needs_api_unparsed` / `acceptance_unparsed`；自引用与环 |
| §3 台账 | `ledger.py` | `${FOREMAN_HOME}/ledger.jsonl`；`open(...,"a")`+`write`+`flush`+`os.fsync`；6 类事件；状态折叠；幂等；`UnknownTaskError` 不追加事件 |
| §4 闸门 | `gate.py` | 6 个阻塞原因码 + 1 个成功态；判定顺序"第一个命中即返回"；软依赖只置 `needs_api_pending`；DFS 环检测 |
| §5 产物验收 | `acceptance.py` | `path::key=value` 语法；4 类判据；AND 语义；`artifact_missing`；未知判据 fail closed |
| §6 执行体适配 | `runner.py` | claude/codex/dsh/generic + `agents.json` 自定义；`{task}`/`{workdir}`；命令不存在 → 127；超时 → 124（默认 900，`FOREMAN_TIMEOUT` 可覆盖） |
| §7 MCP 服务 | `mcp_server.py` | JSON-RPC 2.0 + NDJSON；initialize / notifications / tools/list / tools/call / resources/list / prompts/list；未知方法 -32601；stdout 只有 JSON，日志走 stderr |
| §8 CLI | `cli.py` | dispatch / status / ledger(list,show,ready,blocked,cancel,replan) / verify / mcp / config；全命令支持 `--json`；退出码 0/2/3/1 |
| §9 配置 | `config.py` | `FOREMAN_HOME` → `$HOME/.foreman` → `<cwd>/.foreman`；**HOME 未设置不崩**（回落 cwd）；`agents.json` 可选且容错 |
| §10 测试 | `tests/` | stdlib `unittest`，全部 PASS（见下） |
| §11 文档与打包 | 双 README / PROTOCOL / examples / scripts / CI / pyproject / CHANGELOG | 齐备；`scripts/publish.sh` 先测后建，凭据只走 `TWINE_USERNAME/TWINE_PASSWORD` 环境变量 |
| §12 交付要求 | 本目录 | 未 git init / commit / push；未 pip install；未改动 `foreman-plane/` 以外任何文件 |
| §13 默认取向 | 全包 | 拿不准→阻塞；拿不准→写事件；异常→结构化 reason；路径用 `pathlib`/`os.path` |

---

## 3. 测试输出摘要

命令（规格 §12 第 2 条指定）：

```bash
cd /root/Documents/deepseek-harness/default-workspace/foreman-plane
python3 -m unittest discover -s tests -v
```

结果：**Ran 78 tests — OK**（退出码 0，无 error / failure / skip）。

```
test_acceptance ......... 10 ok
test_cli ................ 11 ok
test_contract ........... 14 ok
test_gate ............... 12 ok
test_ledger ............. 13 ok
test_mcp_server .........  8 ok
test_runner .............. 10 ok
------------------------------------------------------
Ran 78 tests in 2.28s

OK
```

> v0.2.0 更新：包名从 `foreman` 改为 `foreman_plane`，命令行从 `foreman` 改为
> `foreman-plane`（别名 `gongtou`），新增 2 例（usage 名字随实际调用的命令名变化），
> 其余用例原样保留、全部重跑通过。

覆盖要点（规格 §10 逐条对应）：

| 要求 | 落点 |
| --- | --- |
| 四标记正常解析；全角/半角/大小写变体 | `test_contract` 四例 |
| `depends_on` 有标记但无合法 token → `depends_on_unparsed=True` | `test_depends_on_marker_without_legal_token_is_unparsed` |
| `needs_api` 路径含中文 → `needs_api_unparsed=True` | `test_needs_api_path_with_chinese_is_unparsed` |
| `验收` 路径含空格 → `acceptance_unparsed=True` | `test_acceptance_path_with_space_is_unparsed` |
| 非法日历日期（20260230）不认 | `test_invalid_calendar_date_is_not_a_task_id` |
| 缺标记 → unparsed 全 False | `test_missing_markers_leave_all_unparsed_flags_false` |
| append 后能读回；dispatched→running→done 折叠 | `test_ledger` 三例 |
| cancelled 覆盖 | `test_cancelled_overrides_previous_state` |
| done verdict=fail → 依赖不满足 | `test_done_with_fail_is_not_a_satisfied_dependency` |
| cancel 不存在的 id 抛 `UnknownTaskError` 且不追加事件 | `test_cancel_unknown_task_raises_and_appends_nothing` |
| 重复 dispatch 被拒且不产生第二条 dispatch | `test_duplicate_dispatch_is_rejected_without_second_dispatch`（断言 dispatch 事件数 == 1、执行体只被调用 1 次） |
| 六个 BLOCK 原因各一例 + 全 pass 放行 + 软依赖缺失只置 pending | `test_gate` 12 例 |
| min_bytes / sha256 / contains / exit_code / 文件缺失 | `test_acceptance` 10 例 |
| 假适配器：exit_code 透传、stdout 捕获、命令不存在→127、超时→124 | `test_runner` 8 例（用临时脚本，不依赖 claude/codex/dsh 是否安装） |
| MCP 子进程：initialize / tools/list / tools/call；**depends_on 指向不存在任务 → blocked 且台账只有 blocked 没有 dispatch** | `test_mcp_server` 8 例（真起 `python -m foreman_plane.mcp_server`，NDJSON 喂帧，断言台账事件序列恰好 `["blocked"]`） |
| `foreman-plane status --json` 可解析；dispatch 阻塞退出码 2 | `test_cli` 8 例 |

补充的端到端冒烟（人工执行，非单测）：真起 MCP 子进程 →
`initialize` 返回 `serverInfo={"name":"foreman","version":"0.1.0"}`；
`tools/list` 返回三个工具；派一张 `depends_on` 指向不存在任务的卡 → 回执
`{"ok":false,"blocked":true,"reason":"dependency_unknown","missing":["T-20261109-99"]}`、
`isError=true`；台账事件序列为 `blocked`（无 `dispatch`）；换一个合法卡 →
`dispatch/running/done` 三条、verdict=pass、acceptance_ok=true；
CLI `dispatch` 阻塞时退出码 2。

---

## 4. 已知限制

1. **重复派单判定取的是"事件历史"口径。** 规格 §3.4 写的是"已有非终态事件
   （dispatched/running/blocked）"。若严格按"当前状态"理解，一个已经跑完
   （状态 done）的单子可以被无限重复派单，幂等就形同虚设。因此实现取
   "该 task_id 已经出现过 dispatch / running / blocked 事件"为重复派单。
   代价：被闸门拦下的单子要重试必须带 `--force` / `force:true`；另一条正路是
   `replan` 换一个新任务号。
2. **验收路径必须整段是合法 token。** 规格 §2.2 要求路径"锚定结尾"、含中文或空格
   即判非法，因此 `【验收】out/report.md 请务必写完` 会被判
   `acceptance_unparsed=True`（不阻塞，但会在回执和台账里被点名）。这是为了
   杜绝"悄悄修好路径、验收了另一个文件"。
3. **冒号形式的标记有上下文限制。** `depends_on:` 这类写法在 JSON 键名位置
   （`{"depends_on": []}`）不会被当成标记，以免任务正文里的 JSON 触发假阻塞；
   反过来，紧贴中文的 `依赖depends_on:` 仍会被识别。
4. **软依赖只查"存在性"**，不校验内容、不校验新鲜度；缺失只置
   `needs_api_pending`，不阻塞。
5. **执行是同步阻塞的**：`dispatch` 会一直等到执行体退出（默认 900 秒）。
   没有并发上限、没有后台 worker、没有"取消正在跑的任务"的能力
   （`cancel` 只改台账状态，不能打断进程）。
6. **单文件台账、单用户**。没有鉴权、没有多租户、没有并发写锁（append 本身是
   原子写，状态折叠会跳过读到的半截/坏行）。
7. **MCP 只暴露三个工具**，`resources/list` 与 `prompts/list` 返回空列表。
8. **没有 `--version`**，也没有自动生成任务号的能力（规格未要求；任务号缺失
   会被闸门以 `task_id_missing` 拦下）。
9. **verdict 规则**：验收串里含 `exit_code=` 判据时，以该判据为准；否则
   `exit_code != 0` 即判 fail。
10. **Windows 兼容只做到 `pathlib`/`os.path` 层面**，未在 Windows 上实测。

---

## 5. v2 计划

### 5.1 cron / 环境反写
把平面接到宿主的排期上，形成"到点自动派单 → 产物年龄巡检 → 缺件自动补派"
的闭环：新增 `--emit-cron`（把闸门/验收结论翻成排期动作）、暂停/恢复/取消三类
反写动作（每个动作进审计流水账、可一键回滚），以及"排期登记 + 期望产物"的
巡检器（断更任务当天判红，而不是等到下一次排期才发现）。所有反写都要先 dry-run
预览，且只允许动排期，不允许自动改业务配置。

### 5.2 过夜不炸
夜间无人值守的可靠性：执行体心跳与断线重连；断电/断网后可续跑的幂等执行
（可重放日志 + 分段检查点）；闸门失败与验收失败的分级重试与降级策略；
指纹去重，保证同一份问题清单在一个窗口内只推一次，不刷屏。

### 5.3 Web UI
把台账变成可点的看板：任务图（依赖关系可视化）、闸门原因与缺失依赖高亮、
产物在线预览、一键 `dispatch / cancel / replan / force`；读视图完全只读，
写操作必须二次确认并留下审计事件；面向"跨天、跨实例"的值班视图
（今天谁在跑、哪张单卡在哪一步）。

### 5.4 其它（优先级次之）
并发调度器与资源配额；MCP resources/prompts 的实质内容；鉴权与多租户台账
分区；任务重放（把同一张卡在新执行体上重跑并对比产物）；`verdict` 的多态化
（引入人工复核态）。
