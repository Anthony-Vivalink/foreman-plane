# 分歧台账 —— 本机 foreman-tasks 与发布的 foreman-plane

> 本文件记录**两条代码线之间的关系**，以及「本机改动要不要回灌进包」的判据。
> 写给维护者（也写给未来的我）：每次改完本机那套，先回来查一次这张表。
>
> 最后更新：2026-11-13

---

## 0. 为什么有这份文件

`foreman-plane`（PyPI + GitHub 发布版）和本机的 `/root/foreman-tasks/`（实际在跑的那套）
**不是同一份代码，但共用同一套数据契约**。

- **foreman-plane** = 通用治理平面的**发布版**。给外部用户装到他们自己的机器上用，
  零第三方依赖、Python 标准库实现。执行体由用户自己定义（`agents.json`）。
- **/root/foreman-tasks/** = 本机的**先行实现 + 实际运行时**。用 shell 写的专用 agent，
  47 条 cron 直接驱动，平时跑的就是它。**本机没有 pip 安装这个包** —— 这是
  分发定位的不同，不是「无人使用」。

两条线共享：台账格式、任务卡契约、验收判据语法、状态目录布局。
两条线各自拥有：执行体、verdict 语义、专用 agent、编排方式。

**风险**：共享面如果只在一侧改了，另一侧迟早读不懂对方写的东西。
本文件的作用就是：**把「共享面」列死**，让每次改动都能立刻判断该不该发版。

---

## 1. 共享面 —— 改这里必须同步两侧

| 共享的东西 | 包在哪 | 本机在哪 | 改动的后果 |
| --- | --- | --- | --- |
| **台账事件格式** | `src/foreman_plane/ledger.py`（`ts`/`event`/`task_id`/`actor`/`detail`，UTC ISO） | `/root/foreman-tasks/home/ledger.jsonl` | 包的 `ledger.py` 改字段 → 本机读台账的代码全部要跟着改 |
| **任务卡契约** | `src/foreman_plane/contract.py`（`[id]` / `[depends_on]` / `[needs_api]` / `[acceptance]`，半角为 canonical，全角 `【】` 也认） | `/root/foreman-tasks/cards/*.md.tpl` | 契约改标记名或语义 → 本机 6 张卡模板要重写 |
| **验收判据键集** | `src/foreman_plane/acceptance.py`：**只有 4 个键** —— `min_bytes`、`sha256`、`contains`、`exit_code` | 同上，卡里的 `::` 串 | **本机卡片只能用这 4 个键**。新增键必须先进包，否则包判 `acceptance_unknown_criterion` → 记 fail |
| **状态目录布局** | `FOREMAN_HOME` / `$HOME/.foreman`：`agents.json` + `ledger.jsonl` + `runs/` | `/root/foreman-tasks/home/`：同名同构 | 布局改名 → 本机所有绝对路径要改 |
| **任务号形状** | `T-YYYYMMDD-NN` | 同 | 形状放宽/收紧 → 两侧都要验 |

**同步动作**：共享面改了 → 本机侧改完 → **发一个 patch 版本**（`0.2.x`），
并在下面 §4 的案例区追加一行。

---

## 2. 本机专有面 —— 不进包

这些是本机的业务层，包是通用工具，**故意不收**：

- **6 个专用 agent**：`selfcheck` / `cronwatch` / `stockhealth` / `reversalab` / `kbscan` / `reconcile`
- **四态 verdict**：`green` / `amber` / `red` / `error`（包只有二态 `pass` / `fail`）
- **artifacts 约定**：`home/artifacts/<agent>-<日期>.json`，一次运行一份、永不覆盖
- **47 条 cron 编排**：`run_daily.sh <agent>` 驱动
- **治理控制层**：`collect_evidence.py` / `apply_control.py` / `control_plan.md` / `control_actions.json`
  （裁决轮：继续 / 暂停 / 取消 / 重规划四态）

**本机怎么把四态塞进包的二态**：卡里写
`acceptance=...::exit_code=0::contains="verdict": "green"`。
于是「执行层成功但体检是黄项」= exit_code 0 + acceptance 不满足 = 包记 **fail**，
而 `exit_code` 与 `acceptance` 在台账里可分别查到 —— 语义没有丢。
**这个映射是本机的设计，不要因为「包只有二态」就去改包来迁就本机。**

---

## 3. 判据：本机这次改动，要不要回灌进包？

```
这次改动落在哪一面？
│
├─ 共享面（§1）──► 要回灌。改完本机 → 发 patch 版本 → 在 §4 追加一行
│                    （同时检查：包里有没有别人在用这个键/字段）
│
├─ 本机专有面（§2）──► 不回灌。包是通用工具，收业务层会变成又一个平台
│                      （发布定位写得很清楚：个人工具级，不是企业级平台）
│
└─ 拿不准 ──► 问一句：「这个 bug 的**根因类别**在包里有没有对应物？」
               有 → 大概率要回灌；没有 → 不回灌。
               ⚠️ 注意区分「根因类别」和「具体症状」——
               今天这个案例就是「症状没有、根因类别有」。
```

**还有一条横向判据**（不管落在哪一面）：

> 凡是**枚举 / case 表**里可能漏项的地方，一律用「**显式枚举 + 未知即报错**」，
> 不要用「字符串 case 表 + 兜底分支」。
>
> 包已经这么做了：验收遇到未知键 → 记 `acceptance_unknown_criterion` 并判 fail，
> 而不是静默放行。本机踩过的坑正是反面（见 §4 案例 1）。

---

## 4. 案例区

### 案例 1 —— 2026-11-13 reconcile verdict case 漏项（**不回灌**）

**现象**：`foreman-tasks/agents/run_task.sh` 的 verdict 映射表漏了 `reconcile:0` 这一支，
导致 reconcile 正常退出（rc=0，零漏跑）落到兜底 `*)` 被判 `error`，
真漏跑（rc=1）反而判 `red` —— **好=坏反转**，连挂 11-11、11-12 两天假失败。

**为什么不回灌**：包里的 verdict 是 `runner.py::_verdict()`，**逐条 `if` 判断，没有 case 表**，
结构上不可能漏分支；而且包的执行体由用户自定义，包根本不认识 `reconcile` 这个 agent。
→ 落在 §2 本机专有面。

**但根因类别有对应物，必须记住**：
- 包的同类风险点是**验收未知键**（`acceptance.py` 的 `else` 分支）。包已处理得当
  → 显式判 fail + 写明 `acceptance_unknown_criterion`，不会静默放行，也不会好坏反转。
- 本机的坑是同一类（枚举漏项 → 好坏反转），只是发生在四态映射表上。

**顺带确认的一条硬约束**：本机 6 张卡片目前只用了 `exit_code` 和 `contains`，
都在包的 4 键安全集内。**以后给本机卡片加新判据键，必须先 backport 进包**，
否则跑起来会静默记 fail。

**修法留档**：判定段抽成 `foreman_verdict()` 函数 + `source` 守卫，
让 `test_verdict_case.sh` 能直接驱动生产代码做回归（PASS=12），
另加 `test_reconcile_table.py`（PASS=7）。
**测试用 source 生产脚本 + 调它自己的判定函数驱动，不是文本 grep 也不是逻辑复刻**
——这条方法以后写回归都照抄。

### 案例 2 —— 2026-11-12 collect_evidence 假差异（**不回灌**）

`collect_evidence.py` 把 registry 的 `command` 字段整行（含 schedule）拿去跟 crontab 比，
产生 6 对假差异。修的是本机采集侧，**包里没有 registry 这个概念** → 不回灌。

---

## 5. 版本台账

| 日期 | 包版本 | 事件 |
| --- | --- | --- |
| 2026-11-09 | 0.1.0 | 首发 |
| 2026-11-09 | 0.1.1 | 部署章节（两份 README）+ 诊断性修复 |
| 2026-11-09 | 0.1.2 | 发布说明 + 两处诊断性修复 |
| 2026-11-12 | 0.2.0 | 改名 `foreman-plane`（别名 `gongtou`）、包名 `foreman_plane`、scope 章节 |
| 2026-11-13 | — | 本文件建立。**共享面未变，无新版本** |
