#!/usr/bin/env python3
"""Render the README diagrams as real images.

The ASCII art in the READMEs stays as the text fallback; these images are
the pretty version.  Deterministic output: same source -> same bytes.

Requires Graphviz (``dot``).  Run:

    python3 scripts/render_diagrams.py

Writes ``docs/images/*.dot``, ``*.png`` and ``*.svg``.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "images"

FONT = "Noto Sans CJK SC"

COMMON = f"""
  graph [bgcolor="#FFFFFF", fontname="{FONT}", dpi=160, margin=0.12, nodesep=0.45, ranksep=0.55];
  node  [shape=box, style="rounded,filled", fillcolor="#FFFFFF", color="#5B4CF0",
         penwidth=1.6, fontname="{FONT}", fontsize=13, fontcolor="#1F2430", margin="0.22,0.14"];
  edge  [fontname="{FONT}", fontsize=11, fontcolor="#3C4A6B", color="#8E9BD6", penwidth=1.4,
         arrowsize=0.8];
"""


def architecture(lang: str) -> str:
    if lang == "zh":
        bodies = "Claude Code\\nCodex\\nDeepSeek Harness\\n任意 CLI agent"
        bodies_title = "执行体（可插拔）"
        plane = "工头 foreman-plane\\n—————————————\\n派单\\n依赖闸门\\n产物验收\\n只追加台账"
        edge_in = "送来任务卡"
        edge_out = "执行这张卡"
        ledger = "台账文件\\nappend-only\\nJSON Lines"
        ledger_edge = "追加一行"
        caption = "MCP / stdio — JSON-RPC 2.0"
    else:
        bodies = "Claude Code\\nCodex\\nDeepSeek Harness\\nany CLI agent"
        bodies_title = "execution bodies (pluggable)"
        plane = "foreman-plane\\n—————————————\\ndispatch\\ndependency gates\\nobjective acceptance\\nappend-only ledger"
        edge_in = "hand in a task card"
        edge_out = "run the card"
        ledger = "ledger file\\nappend-only\\nJSON Lines"
        ledger_edge = "append one line"
        caption = "MCP / stdio — JSON-RPC 2.0"

    return f"""digraph architecture {{
{COMMON}
  rankdir=LR;
  labelloc="t";
  label=<<b>{caption}</b>>;
  fontsize=12;
  fontcolor="#6B7A99";

  subgraph cluster_bodies {{
    label="{bodies_title}";
    fontname="{FONT}";
    fontsize=13;
    fontcolor="#3C4A6B";
    color="#C8D0F0";
    penwidth=1.4;
    style="rounded";
    margin=14;
    bodies [label="{bodies}"];
  }}

  plane [label="{plane}", fillcolor="#5B4CF0", fontcolor="#FFFFFF", penwidth=2.0];

  ledger [label="{ledger}", fillcolor="#F4F6FB", color="#9AA6C8"];

  bodies -> plane [label="{edge_in}"];
  plane -> bodies [label="{edge_out}"];
  plane -> ledger [label="{ledger_edge}"];
}}
"""


def pipeline(lang: str) -> str:
    if lang == "zh":
        card = "任务卡\\n纯文本"
        parse = "解析\\n契约"
        gate = "闸门\\n硬依赖都终态、通过、已验收？"
        blocked = "blocked\\n台账记一笔\\n不执行、不写 dispatch"
        run = "执行\\n调用执行体"
        accept = "验收\\n按产物判据核"
        ledger = "台账\\n追加一行 JSON"
        cap = "一张任务卡怎么跑"
    else:
        card = "task card\\nplain text"
        parse = "parse\\ncontract"
        gate = "gate\\nhard dependencies all\\nterminal, passed, accepted?"
        blocked = "blocked\\none ledger line\\nnothing runs, no dispatch"
        run = "run\\ncall the execution body"
        accept = "accept\\ncheck the artefact"
        ledger = "ledger\\nappend one JSON line"
        cap = "how one task card runs"

    return f"""digraph pipeline {{
{COMMON}
  rankdir=TB;
  labelloc="t";
  label=<<b>{cap}</b>>;
  fontsize=13;
  fontcolor="#6B7A99";

  card    [label="{card}", fillcolor="#F4F6FB", color="#9AA6C8"];
  parse   [label="{parse}"];
  gate    [label="{gate}", fillcolor="#EEF2FF"];
  blocked [label="{blocked}", fillcolor="#FFF4E5", color="#E0A458", fontcolor="#8A5A18"];
  run     [label="{run}"];
  accept  [label="{accept}"];
  ledger  [label="{ledger}", fillcolor="#EEF2FF"];

  card -> parse -> gate -> run -> accept -> ledger;
  gate -> blocked [label="no", fontcolor="#8A5A18"];
  blocked -> ledger [label="blocked event", style="dashed", color="#E0A458", fontcolor="#8A5A18"];
}}
"""


def deployment(lang: str) -> str:
    if lang == "zh":
        cli = "命令行\\n人工或脚本"
        mcp = "MCP 客户端\\nClaude Code / Codex / DSH"
        sched = "外部调度\\ncron / CI"
        deciders = "谁来决定派什么"
        plane = "工头 foreman-plane\\n—————————————\\n解析 → 闸门 → 执行 → 验收"
        home = "FOREMAN_HOME\\nledger.jsonl 只追加台账\\nagents.json 执行体\\nruns/ 运行日志"
        bodies = "执行体（可插拔）\\nclaude -p {task}\\ncodex exec {task}\\ndsh headless {task}\\n自定义命令"
        arte = "产物文件\\nmin_bytes / sha256\\ncontains / exit_code"
        e_card = "送来任务卡"
        e_tool = "foreman_dispatch"
        e_sched = "定时喂卡"
        e_run = "执行这张卡"
        e_write = "写出文件"
        e_accept = "按产物判据验收"
        e_ledger = "追加事件"
        cap = "部署：三个入口 · 一个内核 · 一本台账"
    else:
        cli = "CLI\\nhuman or script"
        mcp = "MCP client\\nClaude Code / Codex / DSH"
        sched = "external scheduler\\ncron / CI"
        deciders = "who decides what runs"
        plane = "foreman-plane\\n—————————————\\nparse → gate → run → accept"
        home = "FOREMAN_HOME\\nledger.jsonl append-only\\nagents.json bodies\\nruns/ run logs"
        bodies = "execution bodies (pluggable)\\nclaude -p {task}\\ncodex exec {task}\\ndsh headless {task}\\ncustom command"
        arte = "artefact files\\nmin_bytes / sha256\\ncontains / exit_code"
        e_card = "hand in a card"
        e_tool = "foreman_dispatch"
        e_sched = "feeds cards on a schedule"
        e_run = "run the card"
        e_write = "writes files"
        e_accept = "acceptance checks"
        e_ledger = "append events"
        cap = "deployment — three entry points, one core, one ledger"

    return f"""digraph deployment {{
{COMMON}
  rankdir=LR;
  labelloc="t";
  label=<<b>{cap}</b>>;
  fontsize=12;
  fontcolor="#6B7A99";

  subgraph cluster_deciders {{
    label="{deciders}";
    fontname="{FONT}";
    fontsize=13;
    fontcolor="#3C4A6B";
    color="#C8D0F0";
    penwidth=1.4;
    style="rounded";
    margin=14;
    cli   [label="{cli}", fillcolor="#F4F6FB", color="#9AA6C8"];
    mcp   [label="{mcp}", fillcolor="#F4F6FB", color="#9AA6C8"];
    sched [label="{sched}", fillcolor="#F4F6FB", color="#9AA6C8"];
  }}

  plane  [label="{plane}", fillcolor="#5B4CF0", fontcolor="#FFFFFF", penwidth=2.0];
  home   [label="{home}", fillcolor="#EEF2FF"];
  bodies [label="{bodies}"];
  arte   [label="{arte}", fillcolor="#FFF4E5", color="#E0A458", fontcolor="#8A5A18"];

  cli   -> plane [label="{e_card}"];
  mcp   -> plane [label="{e_tool}"];
  sched -> plane [label="{e_sched}"];
  plane -> bodies [label="{e_run}"];
  bodies -> arte  [label="{e_write}"];
  plane  -> arte  [label="{e_accept}", style="dashed", color="#E0A458", fontcolor="#8A5A18"];
  plane  -> home  [label="{e_ledger}"];
}}
"""


GRAPHS = {
    "architecture": architecture,
    "pipeline": pipeline,
    "deployment": deployment,
}


def render(dot_file: pathlib.Path, out_base: pathlib.Path) -> list[pathlib.Path]:
    written = []
    for fmt in ("png", "svg"):
        target = out_base.with_name(out_base.name + f".{fmt}")
        proc = subprocess.run(
            ["dot", f"-T{fmt}", str(dot_file), "-o", str(target)],
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            print(f"  !! dot failed for {dot_file.name} -> {fmt}\n{proc.stderr}", file=sys.stderr)
            continue
        written.append(target)
    return written


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, builder in GRAPHS.items():
        for lang in ("en", "zh"):
            source = OUT / f"{name}.{lang}.dot"
            source.write_text(builder(lang), encoding="utf-8")
            written = render(source, OUT / f"{name}.{lang}")
            for path in written:
                print(f"  {path.relative_to(ROOT)}  {path.stat().st_size} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
