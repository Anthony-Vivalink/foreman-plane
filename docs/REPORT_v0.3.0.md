# foreman-plane v0.3.0 — 故障恢复与可观测（Failure & Recovery）实施报告

> 日期：2026-11-23 ｜ 主题：R1~R6 ｜ 测试：109 项全绿（原有 78 + 新增 31）
>
> 本次只改文件、只跑测试：**没有**改版本号、没有 commit、没有 push、没有打 tag、
> 没有动 `.github/workflows/`，也没有碰 `foreman-plane/` 以外的任何目录。
> 发布动作由维护者统一做。

---

## 1. 改了什么（R1~R6 逐条对应到文件与函数）

### R1 租约与心跳（lease + heartbeat）

| 文件 | 函数 / 位置 | 做了什么 |
| --- | --- | --- |
| `src/foreman_plane/config.py` | `DEFAULT_LEASE_S`、`ENV_LEASE` | 默认租约 900 秒；新增环境变量 `FOREMAN_LEASE` |
| `src/foreman_plane/config.py` | `ForemanConfig.lease_s` | 新配置项（带默认值，老代码构造不受影响） |
| `src/foreman_plane/config.py` | `load_settings()`、`_resolve_lease()`、`load_config()` | 取值顺序：`FOREMAN_LEASE` > `agents.json` 顶层 `lease_s` > 900 |
| `src/foreman_plane/config.py` | `describe()` | 输出里增加 `lease_s`（只增不改） |
| `src/foreman_plane/ledger.py` | `utcnow()` / `iso()` / `parse_timestamp()` | 时间工具：台账只存 UTC ISO8601，`Z` 后缀也认，绝不抛异常 |
| `src/foreman_plane/ledger.py` | `lease_remaining_s()` / `lease_expired()` / `task_age_s()` / `annotate_state()` | 租约剩余秒数、是否过期、任务年龄 |
| `src/foreman_plane/ledger.py` | `Ledger.states()` | 新事件 kind `heartbeat` 的折叠分支：只推租约截止时间，**不改任务状态**；新增状态字段 `lease_expires_at` / `lease_s` / `last_heartbeat_at` |
| `src/foreman_plane/runner.py` | `Dispatcher.dispatch()` | 派单成功时把 `lease_expires_at` / `lease_s` 写进 `dispatch` 与 `running` 事件 |
| `src/foreman_plane/runner.py` | `Dispatcher.heartbeat()` | 续租；只有 `running`/`dispatched` 的任务能续，其它状态回 `task_not_running`；未知任务号抛 `UnknownTaskError` 且不写事件 |
| `src/foreman_plane/cli.py` | `foreman-plane heartbeat <task_id>` | CLI 子命令（支持 `--json`） |

判定口径（已写进 README / SPEC §3.4 / PROTOCOL §2.2）：

* `status=blocked` → **永远可重派**（0.1.0 起的规矩，不能破）
* `status=stale` → **可重派**
* `status=running` 且租约未过期 → **不可重派**（reason=`duplicate_task`，附租约剩余秒数）

### R2 陈旧任务回收（reclaim）

| 文件 | 函数 / 位置 | 做了什么 |
| --- | --- | --- |
| `src/foreman_plane/ledger.py` | 新事件 kind `stale` 的折叠分支 | 状态变 `stale`，`recoveries` 计数 +1；append-only，历史不改写 |
| `src/foreman_plane/ledger.py` | `Ledger.is_already_dispatched(task_id, now=None)` | **语义从「有没有 dispatch 事件」改为「按当前状态判定是否可重派」**（见下表） |
| `src/foreman_plane/runner.py` | `Dispatcher.reclaim(task_id=None, dry_run=False, now=None)` | 扫描台账，把「租约已过期且仍在 running/dispatched」的任务写成 `stale`；返回结构化回执 `marked` / `active` / `skipped`（各带数量） |
| `src/foreman_plane/runner.py` | `Dispatcher.dispatch()` | 租约过期却没先 reclaim 就直接再派时，**先补一条 `stale` 再写 `dispatch`**，台账能说清「为什么它跑了第二遍」 |
| `src/foreman_plane/cli.py` | `foreman-plane reclaim [--task-id X] [--dry-run] [--now ISO8601]` | CLI 子命令；`--now` 用 `parse_timestamp()` 解析，解析不了退出码 1 |

新的再派单口径：

| 当前状态 | 能否再派 | reason |
| --- | --- | --- |
| `blocked` | 能 | ——（从没进过平面，规矩不变） |
| `stale` | 能 | —— |
| `running` / `dispatched`，租约未过期 | 不能 | `duplicate_task` |
| `running` / `dispatched`，租约已过期 | 能 | ——（默认 worker 已死） |
| `done` / `cancelled` | 不能 | `duplicate_task`（`--force` 仍可强制绕过） |

### R3 步骤级 checkpoint（重派不重复执行已完成步骤）

| 文件 | 函数 / 位置 | 做了什么 |
| --- | --- | --- |
| `src/foreman_plane/ledger.py` | 新事件 kind `checkpoint` 的折叠分支 | 字段 `task_id` / `step` / `artifact` / `ts`；折叠出 `completed_steps`（去重有序）、`completed_steps_count`、`checkpoint_count`、`artifacts` |
| `src/foreman_plane/ledger.py` | `Ledger.completed_steps(task_id)` | 按时间顺序取该任务已 checkpoint 的步骤名（去重） |
| `src/foreman_plane/runner.py` | `Runner.command_for(..., completed_steps=None)` | 新增**可选**占位符 `{completed_steps}` → 紧凑 JSON 数组，如 `["a","b"]`；无 checkpoint 时是 `[]`。模板里没有这个占位符 → 行为与 0.2.1 完全一致 |
| `src/foreman_plane/runner.py` | `Runner.run(..., completed_steps=None)` | 把步骤集合透传给 `command_for` |
| `src/foreman_plane/runner.py` | `Dispatcher._runner_takes_completed_steps()` | 签名探测：老的自定义 runner 不认这个关键字也不炸（照旧工作，只是不拿步骤集合） |
| `src/foreman_plane/runner.py` | `Dispatcher.dispatch()` | 派单前取 `completed_steps`，写进 `dispatch` 事件；重派时交给执行体；回执新增 `completed_steps` / `skipped_steps` / `resumed` |
| `src/foreman_plane/cli.py` | `foreman-plane checkpoint <task_id> --step NAME [--artifact PATH]` | CLI 子命令；未知任务号抛错且不写事件 |

### R4 可观测：`status` 增强

| 文件 | 函数 / 位置 | 做了什么 |
| --- | --- | --- |
| `src/foreman_plane/ledger.py` | `annotate_state()`、`task_age_s()` | 给每个任务补 `lease_remaining_s`、`age_s` |
| `src/foreman_plane/runner.py` | `Dispatcher.status()` | 每个任务都带：`status`、`lease_expires_at`、`lease_remaining_s`、`age_s`、`completed_steps` / `completed_steps_count`、`recoveries`、`retries`。**原有字段一个都没删、没改语义**；`--json` 同样只增不改 |
| `src/foreman_plane/cli.py` | `_cmd_status()` | 文本模式补同样的字段，并追加一个 `--- non-terminal ---` 小节（追加在原有输出之后） |

### R5 退避重试（backoff retry）

| 文件 | 函数 / 位置 | 做了什么 |
| --- | --- | --- |
| `src/foreman_plane/runner.py` | `RetryPolicy` / `RetryPolicy.from_spec()` | 读 `agents.json` 执行体里的 `retry` 块：`max_attempts`（**总次数**，含第一次；默认 1 = 不重试）、`base_delay_s`（默认 5.0）、`retry_on_exit_codes`（默认 `[124]`） |
| `src/foreman_plane/runner.py` | `Dispatcher.retry_policy()` | 取某个执行体的策略；没有 `retry` 块 → `None` → **不重试**（0.2.x 行为完全不变） |
| `src/foreman_plane/runner.py` | `Dispatcher._run_with_retries()` | 指数退避：第 n 次重试前等 `base_delay_s * 2 ** (n - 1)`；每次写一条 `retry` 事件（`attempt` / `delay_s` / `exit_code` / `max_attempts` / stderr 尾巴）并顺手续一次租约 |
| `src/foreman_plane/runner.py` | `Dispatcher.dispatch()` | 重试**不产生第二条 `dispatch` 事件**（幂等）；依赖图按 task_id 聚一个桶，同一任务的依赖只算一次 |
| `src/foreman_plane/ledger.py` | 新事件 kind `retry` 的折叠分支 | `retries` 计数 +1、`last_attempt` |

### R6 故障演练 demo

| 文件 | 内容 |
| --- | --- |
| `scripts/failure_drill.sh` | 一条命令跑完整故事：假 agent（三步、每步一个 checkpoint）→ 派单 → 中途 `kill -9` → 展示卡在 running 与被拒的再派单 → `--now` 把时间拨快让租约过期 → `reclaim` 标 stale → 再派（带 `{completed_steps}`）→ 打印全过程台账 → 自检（做完的步骤只能执行一次、剩余步骤各执行一次、任务最终 done/pass）。`set -euo pipefail`，任何一步失败非零退出，临时目录在 `/tmp` 下且退出时清理，不联网、可重复运行 |
| `docs/failure_drill_sample_output.txt` | **实跑**输出（只把随机临时目录名替换成 `<tmp>` 占位符） |
| `docs/REPORT_v0.3.0.md` | 本文件 |

演练脚本里没有用任何假数据：worker 是真进程、真被 `kill -9`、台账是真的停在 `running`、
再派单是真的被拒、再派是真的跳过了已完成的步骤。

---

## 2. 向后兼容性说明

**没变的东西（逐条）**

| 契约 | 状态 |
| --- | --- |
| 台账记录格式（`ts`/`event`/`task_id`/`actor`/`detail`，UTC ISO8601） | 公共字段不变；新事件只是多几种 `event` 取值。个别**已有**事件上多了几个**可选**字段（`dispatch`/`running` 多 `lease_expires_at`/`lease_s`/`completed_steps`，`done` 多 `attempts`/`completed_steps`）——老读者用 `.get()` 取值，多出来的字段直接被忽略 |
| 任务卡契约标记（`[id]`/`[depends_on]`/`[needs_api]`/`[acceptance]`） | 不变，一个字没动 |
| MCP 工具名与签名（`foreman_dispatch` / `foreman_status` / `foreman_cancel`） | 不变；**没有加新工具**，payload 只增字段 |
| `FOREMAN_HOME` 布局（`agents.json` + `ledger.jsonl` + `runs/`） | 不变 |
| 验收判据键集（`min_bytes` / `sha256` / `contains` / `exit_code`） | 不变，一个键没加 |
| `{task}` 占位符必填 | 不变 |
| 退出码（0/2/3/1） | 不变；新子命令沿用同一套 |
| 老 ledger（0.2.x 写的） | 能读：没有租约字段的任务永远不会被自动回收（记 `no_lease`），不会误杀 |

**只增不改的东西**

* 新事件 kind：`heartbeat` / `stale` / `checkpoint` / `retry`。
  **老代码读到新事件必须安全忽略** —— 折叠是 `if/elif` 链，没有匹配分支就跳过，
  不抛异常（`tests/test_recovery.py::test_an_unknown_event_kind_does_not_break_the_fold` 守着这条）。
* 新状态字段：`lease_expires_at` / `lease_s` / `last_heartbeat_at` / `completed_steps` /
  `completed_steps_count` / `checkpoint_count` / `artifacts` / `recoveries` / `retries` /
  `last_attempt` / `first_ts` / `stale_reason` / `lease_remaining_s` / `age_s`。
* 新回执字段：`completed_steps` / `skipped_steps` / `resumed` / `attempts` / `retries` /
  `recovered` / `lease_expires_at` / `lease_remaining_s` / `status`（重复派单回执里）。
* 已有事件上新增的**可选**字段（老读者忽略即可）：`dispatch`/`running` 的
  `lease_expires_at` / `lease_s` / `completed_steps` / `recovered`，
  `done` 的 `attempts` / `completed_steps`。
* 新**可选**标记与配置：`{completed_steps}` 占位符（不写就照旧）、`lease_s` / `FOREMAN_LEASE`、
  执行体里的 `retry` 块（不写就不重试）。

**唯一一处语义变化（有意为之，且是本单要求的）**

`Ledger.is_already_dispatched()` 从「有没有 `dispatch` 事件」改成「按当前状态判定」。
原因写在代码注释与 SPEC §3.4 里：老口径会让被 `kill -9` 打断的任务永远卡在 `running`，
只能人肉 `--force` 整卡重跑（已完成的步骤会重复执行）。

**三条不能破的旧规矩，守住了**

1. `blocked` 任务必须永远可重派 —— `is_already_dispatched()` 里 `blocked` 直接返回 `False`，
   并且有专门的回归测试（`test_blocked_task_stays_re_dispatchable`）。
2. 老 agent 模板必须照旧工作 —— `{completed_steps}` 是可选占位符；不带它的模板一字不改照旧能跑
   （`test_a_template_without_the_placeholder_still_works`）。
3. 已有 78 项测试全部保持绿 —— 本次**没有修改任何既有测试文件**，只新增了 `tests/test_recovery.py`。

---

## 3. 测试结果

命令：`python3 -m unittest discover -s tests -v`（仓库根目录执行）

```
Ran 109 tests in 2.767s

OK
```

* 原有测试：**78 项，全绿，文件一个字没改**
* 新增测试：**31 项**（`tests/test_recovery.py`），分 6 组：
  * `LeaseTests`（5）：租约落账、心跳续租且不改状态、未知任务心跳抛错不写事件、
    终态任务拒绝续租、`lease_s` 可配（环境变量与 agents.json 两条路）
  * `ReclaimTests`（6）：过期任务标 stale、`--dry-run` 不写、租约未过不动、
    非 running 任务跳过、未知任务号抛错、`--now` 生效
  * `RedispatchTests`（5）：租约未过期被拒（`duplicate_task`）、stale 可再派、
    **blocked 仍可再派（旧语义回归）**、done 仍受重复保护、租约过期可直接再派并补 `stale`
  * `CheckpointTests`（5）：checkpoint 落账、未知任务抛错不写事件、
    重派时 `{completed_steps}` 注入、旧模板照旧能用、无 checkpoint 时是 `[]`
  * `RetryTests`（3）：默认不重试、可重试退出码触发重试（且只写一条 `dispatch`）、
    `RetryPolicy` 默认值
  * `ObservabilityTests`（3）：`status` 新字段、**老字段仍在**、未知事件 kind 不炸
  * `RecoveryCliTests`（4）：CLI 的 heartbeat / checkpoint / reclaim / 错误 `--now` / 未知任务退出码 1

演练脚本另有一条自检链（见第 4 节末尾），任一断言不过脚本非零退出。

---

## 4. 故障演练的真实输出

下面是 `scripts/failure_drill.sh` 的**实跑输出**（唯一编辑：随机临时目录名换成 `<tmp>` 占位符；
完整版在 `docs/failure_drill_sample_output.txt`）。先说脚本干了什么，再贴真实输出。

### 4.1 演练脚本做了什么（对照代码）

1. `mktemp -d /tmp/foreman-drill.XXXXXX` 建临时目录（校验必须在 `/tmp` 下），退出时 `trap` 清理
2. 写一个假 worker（`fake_agent.py`）：三步，每步先写 `DRILL_AGENT_LOG` 再调 `foreman-plane checkpoint`，步间 `sleep`
3. `agents.json` 里注册执行体 `fake`，命令模板带 `{task}` 与 `{completed_steps}`
4. 后台起一次 `foreman-plane dispatch`，轮询台账直到出现第一条 `checkpoint`
5. `kill -9` 整个进程组（先确认 dispatcher 是自己的进程组组长，否则退回逐个 kill 子进程）
6. `status --task-id` 展示任务停在 `running`、租约还活着
7. 再派一次 → 必须被拒（退出码 2，`duplicate_task`）
8. `--now` 把时间拨到一小时后 → `reclaim` → 任务变 `stale`
9. 再派一次 → 必须成功，且执行体拿到 `{completed_steps}`，跳过 `step_a`
10. 打印全过程台账 + worker 日志 + 汇总，并做三条断言（做完的步骤只跑一次、剩余步骤各跑一次、最终 done/pass）

### 4.2 真实输出

```text
foreman-plane v0.3.0 — failure drill, real output
=================================================

This is the verbatim output of `scripts/failure_drill.sh` on a machine with
bash, python3 (>= 3.10) and ps.  Nothing in the story is mocked: the worker
is a real process, it is really killed with `kill -9`, the ledger really
ends on "running" and the re-dispatch really skips the finished step.

Only one thing is edited: the random name of the throw-away temp directory
is replaced by the placeholder `<tmp>` — every run creates a new one.
Timestamps are the real ones of that run.


== 1. a fresh plane and a three step card ==
FOREMAN_HOME : <tmp>/home
agent        : fake (fake_agent.py, writes a checkpoint per step)
【id】T-20261123-01
三步任务：step_a → step_b → step_c。每完成一步写一次 checkpoint。

== 2. dispatch the card — the worker will be killed half way through ==
dispatcher pid: 16
first checkpoint seen — the worker is now sleeping in step_b

== 3. kill -9 the worker (the whole process group of the dispatcher) ==
worker killed — the ledger keeps the last event the worker wrote

== 4. the task is stuck in running: the lease is alive, a re-dispatch is refused ==
  status            : running
  lease_expires_at  : 2026-11-23T06:33:05.227931+00:00
  lease_remaining_s : 899.792
  age_s             : 0.208
  completed_steps   : ['step_a']
  recoveries        : 0
  retries           : 0
{
  "ok": false,
  "blocked": true,
  "dispatched": false,
  "reason": "duplicate_task",
  "task_id": "T-20261123-01",
  "missing": [],
  "needs_api_pending": false,
  "notes": [
    "task T-20261123-01 was already dispatched",
    "lease still held, 900s left"
  ],
  "status": "running",
  "lease_expires_at": "2026-11-23T06:33:05.227931+00:00",
  "lease_remaining_s": 899.736
}
exit code: 2 (2 = refused, the task still holds its lease)

== 5. let the lease expire, then reclaim the task ==
evaluating the leases at 2026-11-23T07:18:05.514835+00:00 (one hour ahead)
{
  "ok": true,
  "dry_run": false,
  "now": "2026-11-23T07:18:05.514835+00:00",
  "marked": [
    {
      "task_id": "T-20261123-01",
      "status": "running",
      "lease_expires_at": "2026-11-23T06:33:05.227931+00:00",
      "lease_remaining_s": -2700.287
    }
  ],
  "active": [],
  "skipped": [],
  "marked_count": 1,
  "active_count": 0,
  "skipped_count": 0
}

== 6. dispatch the task again — the finished step must not run twice ==
{
  "ok": true,
  "blocked": false,
  "dispatched": true,
  "task_id": "T-20261123-01",
  "verdict": "pass",
  "acceptance_ok": null,
  "acceptance_reason": null,
  "needs_api_pending": false,
  "exit_code": 0,
  "duration_s": 0.327,
  "timed_out": false,
  "stderr": "",
  "notes": [
    "re-dispatch: step_a already done — handed to the execution body as {completed_steps}"
  ],
  "completed_steps": [
    "step_a"
  ],
  "skipped_steps": [
    "step_a"
  ],
  "resumed": true,
  "attempts": 1,
  "retries": 0,
  "recovered": false,
  "lease_expires_at": "2026-11-23T06:33:05.617002+00:00",
  "lease_remaining_s": 900.0
}

== 7. the whole ledger ==
{"ts": "2026-11-23T06:18:05.228015+00:00", "event": "dispatch", "task_id": "T-20261123-01", "actor": "foreman", "depends_on": [], "needs_api": [], "acceptance": null, "needs_api_pending": false, "agent": "fake", "workdir": "<tmp>", "force": false, "lease_expires_at": "2026-11-23T06:33:05.227931+00:00", "lease_s": 900, "completed_steps": [], "recovered": false, "detail": {"depends_on": [], "needs_api": [], "acceptance": null, "needs_api_pending": false, "agent": "fake", "workdir": "<tmp>", "force": false, "lease_expires_at": "2026-11-23T06:33:05.227931+00:00", "lease_s": 900, "completed_steps": [], "recovered": false}}
{"ts": "2026-11-23T06:18:05.228098+00:00", "event": "running", "task_id": "T-20261123-01", "actor": "foreman", "agent": "fake", "lease_expires_at": "2026-11-23T06:33:05.227931+00:00", "lease_s": 900, "detail": {"agent": "fake", "lease_expires_at": "2026-11-23T06:33:05.227931+00:00", "lease_s": 900}}
{"ts": "2026-11-23T06:18:05.294980+00:00", "event": "checkpoint", "task_id": "T-20261123-01", "actor": "foreman", "step": "step_a", "artifact": null, "detail": {"step": "step_a", "artifact": null}}
{"ts": "2026-11-23T06:18:05.563285+00:00", "event": "stale", "task_id": "T-20261123-01", "actor": "foreman", "reason": "lease_expired", "lease_expires_at": "2026-11-23T06:33:05.227931+00:00", "previous_status": "running", "evaluated_at": "2026-11-23T07:18:05.514835+00:00", "detail": {"reason": "lease_expired", "lease_expires_at": "2026-11-23T06:33:05.227931+00:00", "previous_status": "running", "evaluated_at": "2026-11-23T07:18:05.514835+00:00"}}
{"ts": "2026-11-23T06:18:05.617214+00:00", "event": "dispatch", "task_id": "T-20261123-01", "actor": "foreman", "depends_on": [], "needs_api": [], "acceptance": null, "needs_api_pending": false, "agent": "fake", "workdir": "<tmp>", "force": false, "lease_expires_at": "2026-11-23T06:33:05.617002+00:00", "lease_s": 900, "completed_steps": ["step_a"], "recovered": false, "detail": {"depends_on": [], "needs_api": [], "acceptance": null, "needs_api_pending": false, "agent": "fake", "workdir": "<tmp>", "force": false, "lease_expires_at": "2026-11-23T06:33:05.617002+00:00", "lease_s": 900, "completed_steps": ["step_a"], "recovered": false}}
{"ts": "2026-11-23T06:18:05.617290+00:00", "event": "running", "task_id": "T-20261123-01", "actor": "foreman", "agent": "fake", "lease_expires_at": "2026-11-23T06:33:05.617002+00:00", "lease_s": 900, "detail": {"agent": "fake", "lease_expires_at": "2026-11-23T06:33:05.617002+00:00", "lease_s": 900}}
{"ts": "2026-11-23T06:18:05.682940+00:00", "event": "checkpoint", "task_id": "T-20261123-01", "actor": "foreman", "step": "step_b", "artifact": null, "detail": {"step": "step_b", "artifact": null}}
{"ts": "2026-11-23T06:18:05.934459+00:00", "event": "checkpoint", "task_id": "T-20261123-01", "actor": "foreman", "step": "step_c", "artifact": null, "detail": {"step": "step_c", "artifact": null}}
{"ts": "2026-11-23T06:18:05.944882+00:00", "event": "done", "task_id": "T-20261123-01", "actor": "foreman", "verdict": "pass", "acceptance_ok": null, "exit_code": 0, "duration_s": 0.327, "timed_out": false, "agent": "fake", "acceptance_reason": null, "stderr_tail": "", "attempts": 1, "completed_steps": ["step_a"], "detail": {"verdict": "pass", "acceptance_ok": null, "exit_code": 0, "duration_s": 0.327, "timed_out": false, "agent": "fake", "acceptance_reason": null, "stderr_tail": "", "attempts": 1, "completed_steps": ["step_a"]}}

== 8. what the worker actually did ==
RUN step_a
SKIP step_a (already checkpointed)
RUN step_b
RUN step_c
WORKER DONE

== summary ==
recoveries (stale events) : 1
steps handed back as done : step_a
step_a executed times     : 1  (1 = no duplicated work)
step_b executed times     : 1
step_c executed times     : 1
final status              : done pass

drill OK — the worker died, the task was reclaimed, the finished step was not repeated.
```

### 4.3 演练证明了三件事

* **能恢复**：worker 被 `kill -9` 之后，台账停在 `running` 不再是死局 —— 租约过期 → `reclaim` →
  `stale` → 再派成功，最终 `done / pass`。
* **不重复干活**：`step_a` 在两次运行里合计只执行了 **1 次**（第一次执行、第二次跳过），
  `step_b` / `step_c` 各执行 1 次。这是「已完成步骤被跳过」的可验证证据。
* **拒绝是对的**：租约还在的时候再派单确实被拒（退出码 2），不会把同一个任务跑两遍。

---

## 5. 已知限制与后续可做的事

### 已知限制

1. **没有自动看护**：`reclaim` 要人（或 cron）自己跑。平面里没有守护进程，也没有调度器。
2. **租约只是租约**：心跳要执行体自己发；执行体要是连 `checkpoint` 都不会调，
   那它就享受不到续跑（只能靠 `--force` 整卡重跑）。
3. **0.2.x 写的台账没有租约字段**：这类任务永远不会被自动回收（`reclaim` 记 `no_lease`），
   宁可漏回收也不误杀正在跑的任务。
4. **租约过期 = 假定 worker 死了**：一个跑了很久但没发心跳的 worker，可能在租约过期后被
   判定为可再派；真要严格，得让执行体在长步骤之间发心跳。
5. **重试只覆盖「同一次派单内的重跑」**：跨派单的续跑靠 checkpoint + 再派，不靠 retry。
6. **`retry` 不区分错误类型**：只按退出码判可重试，不做「这个错误重试也没用」的判断。
7. **MCP 面没长新工具**：`heartbeat` / `checkpoint` / `reclaim` 只有 CLI 入口；
   MCP 侧仍能通过 `foreman_status` 看到全部新字段。
8. **`--now` 是给演练与排障用的**：它只影响这一次回收判定，不会改台账里已写的时间戳。
9. **单机单文件**：一个平面目录、一份台账；没有多租户、没有 RBAC、没有高可用。

### 后续可做的事（按价值排序）

1. **`reclaim` 的定时化**：给一个 `foreman-plane sweep` 或一条 cron 示例，
   把「回收 + 重派」变成一条命令（先做 `--dry-run` 让人看一眼）。
2. **心跳协议**：给执行体一个统一的「我在干」的约定（长步骤之间发心跳），
   以及一个 `foreman-plane lease <id>` 的查询命令。
3. **步骤级产物**：把 checkpoint 的 `artifact` 真正用起来 —— 续跑时把上一步的产物路径也交给执行体。
4. **`retry` 的错误分类**：允许执行体声明「这个退出码重试也没用」，避免无谓的退避等待。
5. **可观测的落盘**：把 `lease_remaining_s` / `age_s` 这类字段做成一条 `status --watch` 的持续输出。
6. **MCP 新工具**：`foreman_heartbeat` / `foreman_reclaim`（要保持现有工具名与语义不变）。
7. **依赖图与重派的关系**：现在重派会写第二条 `dispatch` 事件，可以再想想要不要
   在台账里显式记一条 `redispatch` 事件，让「第几次、因为什么重跑」更清楚。

---

## 6. 交付清单（本次改动的文件）

```text
新增  scripts/failure_drill.sh                 故障演练（真 kill -9、真重派、真续跑）
新增  tests/test_recovery.py                   31 项新测试
新增  docs/REPORT_v0.3.0.md                    本报告
新增  docs/failure_drill_sample_output.txt     实跑输出
改    src/foreman_plane/config.py              租约配置（lease_s / FOREMAN_LEASE）
改    src/foreman_plane/ledger.py              新事件 kind、租约/时间工具、再派单口径、checkpoint 查询
改    src/foreman_plane/runner.py              租约落账、{completed_steps} 注入、退避重试、heartbeat/checkpoint/reclaim、status 增强
改    src/foreman_plane/cli.py                 heartbeat / checkpoint / reclaim 子命令、status 文本增强
改    README.md / README_ZH.md                 新增「Failure & Recovery / 故障恢复」章节
改    docs/SPEC.md                             §1/§3/§6/§8/§9 增补 + 新增 §14
改    docs/PROTOCOL.md                          事件模型、状态折叠、幂等口径、执行体模板
改    CHANGELOG.md                             0.3.0 条目（unreleased）
改    DIVERGENCE.md                            版本台账 +1 行（共享面变了 → 要发 patch 版本）
```

**未做**（按指令）：没有 commit、没有 push、没有打 tag、没有改版本号、
没有动 `.github/workflows/`、没有碰 `foreman-plane/` 以外的目录。
