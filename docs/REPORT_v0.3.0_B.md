# foreman-plane v0.3.0 (B) — attempt 维度与契约 fixture 实施报告

**增量范围**：B1~B4 四项。B 批次只补 attempt 维度、验收归属、契约 fixture 与第二个演练脚本；
租约 / 心跳 / reclaim / checkpoint / 退避重试（R1~R6，A 批次）见
[`docs/REPORT_v0.3.0.md`](REPORT_v0.3.0.md)。本报告不重写代码，只记「目标 / 实现落点 /
对应测试名 / 实测数字」。

**证据口径**：文中每个数字都能在 `docs/failure_drill_sample_output.txt`、
`docs/artifact_gate_drill_sample_output.txt` 或 unittest 输出里对上。

**实测基线**：`python3 -m unittest discover -s tests` → `Ran 144 tests, OK`（10 个测试文件
共 144 个 `test_` 方法，其中本批次新增 `test_attempts.py` 22 + `test_contract_fixtures.py` 13
= 35 个）；`bash scripts/failure_drill.sh` 退出码 0（样例输出 143 行）；
`bash scripts/artifact_gate_drill.sh` 退出码 0（样例输出 194 行）。

---

## B1 — attempt：把「一次执行」变成一个可寻址的工作单元

### 目标

1. 每次派单都是一个 **attempt**：独立 id、独立 epoch、独立产物目录，两次尝试的产物不混。
2. 恢复时能从台账**推断**当前 attempt 的状态，而不是猜。
3. **显式重试**（retry）= 主动开一个新 attempt，与 reclaim（被动标记 stale）职责分开。
4. 语义写死：**at-least-once + 幂等键**，**不承诺 exactly-once**。

### 实现落点

- `ledger.py:200` `attempt_id_for()` → attempt id = `{task_id}-a{seq:02d}`（路径安全，可直接当目录名）；
  `ledger.py:525` `next_attempt_id()` → 返回 `(attempt_id, epoch)`，epoch 从 1 递增。
- `runner.py:471-482` 派单时分配 attempt 并写 `attempt` 事件（`attempt_id` / `epoch` /
  `previous_attempt_id` / `attempt_dir` / 租约）；`runner.py:907` `_attempt_dir()` →
  `<workdir>/attempts/<attempt_id>/`（尽力创建）；`runner.py:260-263` 执行体环境变量
  `FOREMAN_ATTEMPT_ID` / `FOREMAN_ATTEMPT_DIR`；`runner.py:184-195` 可选占位符 `{attempt_dir}`
  （**旧模板不含占位符照旧工作**）。
- `ledger.py:154` `infer_attempt_state()` → 三态 + 两个终态的可执行判定（见下表）；
  `ledger.py:530` `attempts()` → 逐 attempt 记 id / epoch / 终态 / 验收实际读到的产物。
- `runner.py:923` `_superseded()` → 判定本 attempt 的回执是否已过期（取消 / 被新 attempt 取代）；
  `runner.py:968` `Dispatcher.retry()` → 显式重试（新 attempt + 重新执行 + 验收本次产物）。
- `cli.py:86` / `cli.py:90` / `cli.py:293` → `attempts` / `retry` 两个新子命令。

### 三态推断（`ledger.py:154-198`，规则可执行，不是启发式）

| 折叠状态 | 附加条件 | attempt 状态 |
| --- | --- | --- |
| `done` | — | `finished` |
| `cancelled` | 无迟到回执 / 有迟到回执 | `finished` / `outcome_unknown`（结束未知） |
| `blocked` | — | `not_started`（从未进平面） |
| `stale` | — | `interrupted`（已中断） |
| `dispatched` / `running` | 租约活着 / 租约过期 / 完全无租约（0.2.x 老台账） | `still_running`（仍在运行）/ `interrupted`（已中断）/ `outcome_unknown`（结束未知） |

老台账**永不**被当成失败：0.2.x 台账没有 `attempt_id` / `epoch`，折叠时按 legacy 记
`attempt_aware=false`，状态报 `outcome_unknown`。

### 语义（写进契约，不含糊）

- **at-least-once**：一个 attempt 至少执行一次。崩溃后重派是新 attempt，**旧 attempt 可能已产生
  副作用**，plane 不回滚、不去重执行。
- **幂等键**：`(task_id, attempt_id)`。同一 attempt 内的重复投递由台账幂等判定挡住；跨 attempt
  的重复副作用要由**执行体自己**保证幂等（明确接受的边界，见「已知限制」）。
- **不承诺 exactly-once**。原因写在代码里（`runner.py:540-542`）：worker 还在跑时任务被取消、
  或新 attempt 接过，旧 attempt 的回执会迟到——这种竞态无法靠 plane 消除，只能**不把它算成
  当前 attempt 的成功**。

### 对应测试（`tests/test_attempts.py`，22 个）

`test_dispatch_allocates_an_attempt` / `test_a_second_dispatch_allocates_a_new_attempt` /
`test_the_artefacts_of_two_attempts_do_not_mix` / `test_the_attempt_state_is_classified` /
`test_status_reports_the_attempt_state` / `test_a_legacy_ledger_is_not_misread` /
`test_a_legacy_running_task_is_unknown_and_never_failed` /
`test_a_legacy_ledger_keeps_the_0_2_x_done_wins_behaviour` /
`test_retry_runs_the_body_again_in_a_new_attempt` / `test_retry_is_not_reclaim` /
`test_retry_refuses_while_the_attempt_still_holds_a_lease` /
`test_retry_without_a_card_reports_card_unavailable` / `test_retry_of_an_unknown_task_raises` /
`test_cli_attempts_and_retry` / `test_cli_attempts_of_an_unknown_task_exits_with_1`

### 实测数字
- attempt id 形态：`T-20261123-51-a01` → `T-20261123-51-a02`（演练实跑，epoch 1 → 2）。
- 产物目录 `<workdir>/attempts/<attempt_id>/`，两个 attempt 各一份，互不覆盖。
- 演练中 A 的 attempt 列表 = `T-20261123-51-a01, T-20261123-51-a02`（2 个）；租约剩余 900.0 秒。

---

## B2 — 验收只认本次 attempt 的产物

### 目标

1. 验收读到的产物必须来自**本次 attempt**，不能是上一次留下的。
2. 验收实际读到的**路径 + sha256 + 结果**要落台账。
3. 取消之后迟到的回执，**不得**被当成当前 attempt 的成功。
4. `runs/` 下的执行日志，同一秒跑两次不能互相覆盖。

### 实现落点

- `acceptance.py:67` `resolve_target()` → 相对路径**先在 attempt 目录找**，找不到才回落 base
  目录（旧卡 / 旧模板仍可用）；`acceptance.py:92` `verify()` → 读文件 → 算 sha256（`:136`）
  → 判 4 个键；`runner.py:528-535` 验收时传入本次的 `attempt_dir`。
- `runner.py:543-566` `done` 事件落 `acceptance_path` / `acceptance_sha256` / `acceptance_size` /
  `acceptance_checks` / `attempt_id` / `epoch` / `attempt_dir` / `superseded` / `superseded_reason`；
  `runner.py:576-586` 回执若已 superseded：写进台账但**不作为当前 attempt 的成功**
  （`verdict` 置空、`ok=false`）。
- `runner.py:923-950` `_superseded()` → 两种原因：`cancelled_during_run` /
  `superseded_by_new_attempt`；`ledger.py:397` 折叠时把迟到回执计入 `late_receipts`，不改当前状态。
- `runner.py:307-345` `_write_log()` → 日志名 `{task_id}-{YYYYmmdd-HHMMSS}.log`；**同名再加数字
  后缀** `-2` / `-3`…，用 `"x"` 独占创建，日志永不丢；`ledger.py:530` `attempts()` 每个 attempt
  记下验收实际读到的产物路径。

### 对应测试（`tests/test_attempts.py`）

`test_the_acceptance_records_path_and_sha256` /
`test_a_stale_artefact_of_a_previous_attempt_is_not_accepted` /
`test_without_an_attempt_artefact_the_base_directory_is_the_fallback` /
`test_a_cancelled_task_ignores_a_late_receipt` /
`test_a_receipt_of_a_superseded_attempt_is_ignored` /
`test_two_runs_in_the_same_second_keep_both_logs` /
`test_the_log_name_carries_the_task_and_the_timestamp` /
`test_the_artefacts_of_two_attempts_do_not_mix`

### 实测数字（引自 `docs/artifact_gate_drill_sample_output.txt` 第 9、10 节与总结）

| 项 | 值 |
| --- | --- |
| attempt 1 的产物 sha256 | `440ec0b3694e1fb7903efe625f793963598e9a5a577bf8717f444407bde85edd`（内容 `PARTIAL`，**未**被采纳） |
| attempt 2 的产物 sha256 | `a4bccf5c13ef5ff8311e59b34d492bc7072a55298723601a6a9c127053aee8fc`（内容 `COMPLETE`，**被**采纳） |
| decoy（陈旧但 COMPLETE）的 sha256 | `d95f956db65b68c1834316a8e9a7d487f3f6581b9af1af1edc20c07f678cdbf1`（**未**被采纳） |
| 被验收的路径 | `<tmp>/attempts/T-20261123-51-a02/report.json`（9 字节） |
| 台账里的 `done` 事件数 / `late_receipts` | 1 / 0 |

---

## B3 — 契约从「文档」变成「测试」

### 目标

共享契约（台账事件 / 任务卡 / 验收单）以前只写在文档里，两边实现各写各的。现在：**公开包实现**
与**本机 shell 实现**跑**同一组脱敏 fixture**，并补上崩溃恢复与 JSONL 读取的回归。

### 实现落点

- `tests/fixtures/`（8 个文件）：`manifest.json` / `contract.json` / `task_card_basic.json` /
  `acceptance_sheet.json` / `report.json` / `ledger_attempt.jsonl` / `ledger_legacy.jsonl` /
  `ledger_corrupt.jsonl`。
- `tests/contract_probe.py` = 公开实现（`foreman_plane`）的探针；
  `scripts/shell_impl/contract_shell_impl.sh` = 本机 shell 实现的**同一接口**，用来交叉比对；
  `scripts/contract_fixture_check.sh` = 交叉跑两侧 + 比对结论；
  `tests/test_contract_fixtures.py`（13 个测试）= 把上面这些变成 unittest。

fixture 清单（`tests/fixtures/manifest.json` 声明，版本 `1.0`，参考时间
`2026-11-24T00:00:00+00:00`）：`card` → `task_card_basic.json`；`ledger_attempt` →
`ledger_attempt.jsonl`；`ledger_legacy` → `ledger_legacy.jsonl`；`ledger_corrupt` →
`ledger_corrupt.jsonl`；`acceptance` → `acceptance_sheet.json`；`artifact` → `report.json`。
三个台账 fixture 各覆盖一种形状：`ledger_attempt`（带 attempt 维度的新台账）、`ledger_legacy`
（0.2.x 老台账，无 attempt 字段）、`ledger_corrupt`（截断半行 + 非 JSON 行 + 未知事件 kind
`quantum_flux`）。

### 对应测试（`tests/test_contract_fixtures.py`，13 个）

`test_the_fixtures_exist` / `test_the_public_implementation_matches_the_contract` /
`test_the_shell_implementation_matches_the_contract` /
`test_both_implementations_agree_with_each_other` /
`test_the_fixture_directory_holds_no_host_path` / `test_the_contract_check_script_passes` /
`test_a_dead_worker_leaves_an_interrupted_attempt` /
`test_a_recovered_task_carries_its_steps_and_its_last_attempt` /
`test_a_late_receipt_does_not_close_a_cancelled_task` / `test_a_half_line_is_ignored` /
`test_an_unknown_event_kind_is_ignored_safely` / `test_the_reader_survives_a_truncated_ledger` /
`test_a_ledger_without_a_file_is_empty_and_safe`

### 实测数字

`bash scripts/contract_fixture_check.sh` 实跑输出 4 行 `ok`：

```
ok   public implementation matches the contract
ok   shell implementation matches the contract
ok   both implementations agree with each other
contract OK
```

回归覆盖：崩溃恢复（死 worker → interrupted attempt、恢复后带上已完成步骤与最后一次 attempt）、
JSONL 读取（半行、非 JSON、未知 kind、截断文件、文件不存在）5 类。

---

## B4 — 第二个演练脚本：artifact_gate_drill.sh

### 目标

把 B1+B2 的故事**跑出来**看：A 产出文件 → 写 done 前被打断 → 重启后 B 仍不写文件 → 核查或显式
重试 A → **只认本次通过验收的产物**，B 才放行。

### 实现落点

- `scripts/artifact_gate_drill.sh` —— 一条命令跑完整条链，不联网、只用 `/tmp`。
- `docs/artifact_gate_drill_sample_output.txt` —— 实跑输出（194 行）。

### 演练的 11 步（与样例输出一一对应）

1. 新建平面 + 两张卡：A（产 `report.json`）、B（依赖 A，产 `report_b.json`），外加一个
   **decoy**：内容 COMPLETE 但陈旧的 `report.json`。
2. 派 A：worker 写下产物到 `attempts/T-20261123-51-a01/report.json`，内容 `PARTIAL`。
3. `kill -9` 掉 dispatcher —— 台账没有 A 的 `done` 事件。
4. 看台账：`status=running`，`attempt_id=T-20261123-51-a01`，`epoch=1`，
   `attempt_state=still_running`，`late_receipts=0`。
5. 重启平面 → 闸门**拒绝 B**：`exit code: 2`，`reason: dependency_pending`，
   `missing: ["T-20261123-51"]`，备注 `dependency T-20261123-51 is still running` → **B 没写任何文件**。
6. 复查 A：租约还活着 → attempt 仍是 `still_running`。
7. 租约过期后 reclaim：把时间前拨 1 小时（`lease_remaining_s = -2700.299`）→ A 变 `stale`，
   `attempt_state` 变 `interrupted`。
8. **显式重试 A**：`retry_mode=explicit`，新 attempt `T-20261123-51-a02`，`epoch=2`，
   `previous_attempt_id=T-20261123-51-a01`，产物目录换成 `attempts/T-20261123-51-a02/`。
9. 验收真正读到的东西：`attempts/T-20261123-51-a02/report.json`，
   sha256 `a4bccf5c13ef5ff8311e59b34d492bc7072a55298723601a6a9c127053aee8fc`，`done events: 1`。
10. 磁盘实况：`a01/report.json` = `PARTIAL`，`a02/report.json` = `COMPLETE`，
    decoy = `COMPLETE` 但**陈旧** → 没被采纳。
11. 这时 B 才跑：产出 `attempts/T-20261123-52-a01/report_b.json`。

### 结论（演练收尾原话）
`drill OK — the interrupted attempt was not accepted, the retried attempt was, and only then B ran.`
—— 中断的 attempt 没被验收，重试的 attempt 被验收，B 在这之后才跑。

---

## 已知限制

1. **at-least-once 的语义边界**。plane 保证「一个 attempt 至少跑一次」，**不保证**副作用只发生
   一次，也**不承诺 exactly-once**。跨 attempt 的重复副作用必须由执行体自己幂等（幂等键 =
   `(task_id, attempt_id)`；attempt 目录隔离是它最省事的落地方式）。plane 只保证一件事：
   **迟到的回执不会被算成当前 attempt 的成功**。
2. **租约误杀长任务**。默认租约 900 秒；跑得更久的任务必须自己发心跳，否则租约过期 → reclaim
   标 `stale` → 若 worker 其实还活着，就会出现两个 attempt 并行写。处置：长任务开 `heartbeat`；
   即便被误标 stale，attempt 目录隔离保证两次 attempt 的产物不混，验收只认最后一次。
3. **fixture 脱敏强度**。fixture 用占位路径，**不含本机绝对路径**
   （`test_the_fixture_directory_holds_no_host_path` 守着这条）。但覆盖面有限：
   `ledger_corrupt.jsonl` 只覆盖 4 类损坏（截断半行 / 非 JSON 行 / 未知 kind / 正常事件混在其中），
   不等于对任意损坏都健壮；真实世界的台账形状仍可能撞到未覆盖的情形。
4. **老台账不猜**。0.2.x 台账没有 attempt 字段，plane 只报 `outcome_unknown`，不会替它推断
   「成功还是失败」——这是刻意的保守。

---

## 交付清单（本批次改动）

- `docs/REPORT_v0.3.0_B.md`（本报告）；`README.md` / `README_ZH.md`：各加 3 节 ——
  *Attempts — the unit of work* / *Explicit retry vs reclaim* /
  *The acceptance only accepts the current attempt*
- `CHANGELOG.md`：0.3.0 段落补 B1~B4 条目
- `docs/SPEC.md`：第 14 节补 attempt 维度与验收归属
- `docs/PROTOCOL.md`：事件表补 `attempt` kind 与 attempt 字段
- `DIVERGENCE.md`：共享面表补「attempt 维度与幂等键」一行，并注明本机专有面不回灌
