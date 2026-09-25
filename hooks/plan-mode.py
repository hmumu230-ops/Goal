#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
plan-mode.py — SWE-2 Max 自定义计划模式钩子引擎 v3（轻量版）

用法: plan-mode.py <SessionStart|UserPromptSubmit|PreToolUse|PostCompaction|Stop>
stdin: 钩子事件 JSON。stdout: 钩子控制 JSON（无输出 = 放行）。

流程（.devin/plan.md frontmatter 的 status 字段驱动，agent 自行流转）：
  brainstorm → 只读保护：读代码、讨论、派 subagent_explore；方案随时沉淀进 plan.md
  drafting   → 讨论收敛后 agent 自觉把 status 改 drafting，按格式模板增量写正式计划
               （Stop 钩子兜底：drafting 中计划不完整不许停，最多拦 12 次）
  review     → 写完改 review 提交评审；用户提意见就改
  done       → 用户说「执行计划」→ 校验（章节齐全+无 ❓/待定）→ 生成 goal.md 接力目标模式
  验收/测试标准为可选项：用户点名才写，[check:]/[human] 标签留给执行阶段机检/人验
"""
import json
import os
import re
import sys
import time
from pathlib import Path


def log(*a):
    print("[plan-mode]", *a, file=sys.stderr)


def norm_path(p: str) -> str:
    if not p:
        return p
    if os.name == "nt":
        return p
    m = re.match(r"^([A-Za-z]):[\\/](.*)$", p)
    if not m:
        return p
    drive, rest = m.group(1).lower(), m.group(2).replace("\\", "/")
    for base in ("/mnt", ""):
        cand = f"{base}/{drive}/{rest}"
        if os.path.isdir(cand):
            return cand
    return f"/mnt/{drive}/{rest}"


ROOT = Path(norm_path(os.environ.get("DEVIN_PROJECT_DIR") or os.getcwd()))
DEVIN_DIR = ROOT / ".devin"
PLAN = DEVIN_DIR / "plan.md"
STATE = DEVIN_DIR / ".plan-state"
EPM_DEBUG = STATE / "epm-debug.log"   # exit_plan_mode 的 tool_input 键名侦察记录
ARCHIVE = DEVIN_DIR / "plans"
OFF = DEVIN_DIR / "plan-mode.off"
GOAL = DEVIN_DIR / "goal.md"
GOAL_DONE = DEVIN_DIR / "goal.done"
GOAL_STATE = DEVIN_DIR / ".goal-state"
BLOCKER = DEVIN_DIR / "blocker.md"
HISTORY = DEVIN_DIR / "history.log"
# 本脚本在 %APPDATA%\devin\hooks\ 下 → 上一级即全局配置目录
GLOBAL_FMT = Path(__file__).resolve().parent.parent / "plan-format.md"

GUARD_STATUSES = ("brainstorm", "drafting", "review")   # 只读保护生效
LIVE_STATUSES = ("brainstorm", "drafting", "review")    # 提醒/重注入生效
ENDED = STATE / "ended"                                 # 钩子正规结束标记（approve/handoff 写）
ESC_ATTEMPT = STATE / "esc-attempted"                   # 检测到过非法 status 改写

DEFAULT_FMT = """# 计划：<一句话>
## 1. 预期效果
## 2. 方案与结论
## 3. 具体执行操作
## 4. 风险与备选
## 5. 验收清单
"""

PROTOCOL = (
    "【计划模式协议 — SWE-2 Max·轻量版】\n"
    "1. 全程只读：write/edit/apply_patch 被禁，exec 只许只读命令；唯一能写的文件是 .devin/plan.md。用户也可能直接改这个文件——以文件内容为准。\n"
    "2. 探索与讨论：读代码、提方案、摆观点、给取舍，结论随时沉淀进 plan.md。调研面宽时并行派多个 subagent_explore（只读型）分片探索——一个找既有实现、一个摸相关组件、一个查测试惯例；方案设计也可派不同视角各出一稿再汇总。子代理给结论后，关键文件你要亲自复读确认，别直接转述。只读阶段写型子代理会被拦。\n"
    "3. 提问纪律：拿不准需求或方案选型时用提问工具当场问，推荐项放第一个并标注(推荐)。只问方向性问题，次要细节自己做合理假设写进计划。禁止用提问问「计划行不行/要不要继续」——写完直接改 review 提交评审，批不批是用户的事。\n"
    "4. 讨论收敛后自觉把 status 改 drafting，按格式模板把正式计划增量写进 plan.md（每个 ## 章节都要有；只写推荐方案，被否选项一句理由带过）。decision-complete：不留待定项。\n"
    "5. 写完把 status 改 review 提交评审；用户提意见就改、保持 review。\n"
    "6. 用户说『执行计划』→ 钩子校验章节齐全+无 ❓/待定 → 生成 goal.md 接力执行。附加强度：『执行计划·严格』= strict:true 高强度验收；『执行计划·宽松』= 放宽拦顶。\n"
    "7. 验收/测试标准是可选项——用户点名才写：能机检的带 [check: <命令>]、只能人看的标 [human]、都不行才无标签自证，汇总进验收清单供执行阶段用。没点名就专心计划本身。\n"
    "8. 回合规则：每轮只能以两种方式结束——向用户提问（先把未决问题标 ❓ 写进 plan.md 再停），或把 plan.md 更新到当前进度后停下；drafting 中没写完钩子会拦回（最多 12 次，卡住写 .devin/blocker.md）。\n"
    "9. 唯一出口是用户说『执行计划』过验收闸门——在此之前一直保持计划状态。你不能靠改写 plan.md 的 status（off/done/任意值）退出：钩子会检测、恢复为 brainstorm 并记警告。彻底放弃只能由用户删除 plan.md 文件（不是你）。琐碎任务（typo/单行修复/纯问答/纯调研）本来就不该进计划模式——你可以主动创建 plan.md 进模式，但别为小题大做而进。"
)

ON_RE = re.compile(
    r"^\s*(?:"
    r"/(?:[\w.-]+:)?plan[\w.-]*"                  # /plan-mode、/xxx:plan-mode
    r"|(?:进入|开启|启动|打开|启用)\s*计划模式"
    r"|计划模式(?=\s*[:：])"
    r"|做个计划|先做个计划|做个规划|先规划一下|头脑风暴一下"
    r"|plan[\s_-]*mode(?=\s*[:：]|$)"
    r")\s*[:：]?\s*(.*)$",
    re.I | re.S,
)
DRAFT_RE = re.compile(
    r"^\s*(?:生成计划|出计划|把方案整理成计划|整理成计划|写成计划|敲定方案|方案定了|就这么定|确认方案)"
    r"\s*[:：,，]?\s*(.*)$",
    re.S,
)
CRITERIA_RE = re.compile(
    r"^\s*(?:(?:写|补|补充|加|加上|完善|更新|修订|定|生成)\s*)?"
    r"(?:每(?:个|一)?小?步(?:骤)?的?|子步骤的?|各步的?|整体)?\s*"
    r"验收标准\s*[:：]?\s*(.*)$",
    re.S,
)
TEST_RE = re.compile(
    r"^\s*(?:(?:写|补|补充|加|加上|完善|更新|修订|定|生成)\s*)?"
    r"(?:每(?:个|一)?小?步(?:骤)?的?|子步骤的?|各步的?)?\s*"
    r"(?:测试标准|测试用例|测试要求)\s*[:：]?\s*(.*)$",
    re.S,
)
APPROVE_RE = re.compile(
    r"^\s*(?:"
    r"/(?:[\w.-]+:)?plan[\w.-]*\s+(?:approve|go|run|exec|执行)\b"
    r"|执行计划|开始执行|批准计划|计划批准|批准执行|按(?:此|这个|该)?计划执行|就按这个做|开工吧"
    r")\s*[·,，\-—:：]?\s*(严格|宽松|strict|loose)?\s*[吧了啊]?[。.!！]?\s*$",
    re.I,
)
# 无计划时疑似非平凡实现任务 → 自动进模式（EnterPlanMode 等价物）：实现意图 + 非问句
NUDGE_RE = re.compile(
    r"(实现|新增|添加|增加|加[一个些上]|开发|做个|做一个|做一[个款]|做好|做出来|"
    r"帮我[做写建搭弄]|给我[做写建搭弄]|重构|改版|改造|修复|接入|集成|建一个|搭一个|"
    r"写一[个份只]|重做|搞定|migrate|implement|refactor|build a|add a)",
    re.I,
)
QUESTIONISH_RE = re.compile(r"[?？]|吗\b|是什么|为什么|怎么|哪些|哪里|哪个|介绍|解释|看看|看一下|帮我看|讲讲|说说|区别|对比")
# 逃生类口令：硬性规则下不再放行，命中时统一回锁定说明
ESC_RE = re.compile(
    r"^\s*(?:直接做|直接干|直接写|不用计划|无需计划|跳过计划|别计划了|别规划了|不用走计划|先干活)\s*[吧了啊]?[。.!！]?\s*$",
    re.I,
)
OFF_RE = re.compile(
    r"^\s*(?:"
    r"/(?:[\w.-]+:)?plan[\w.-]*\s+(?:off|cancel|exit|stop|clear)\b"
    r"|(?:退出|关闭|取消|停用)\s*计划模式"
    r"|plan[\s_-]*mode\s+(?:off|cancel|stop)\b"
    r")\s*[:：.。!！]?\s*$",
    re.I,
)
STATUS_RE = re.compile(
    r"^\s*(?:"
    r"/(?:[\w.-]+:)?plan[\w.-]*\s+status\b"
    r"|计划(?:模式)?状态"
    r"|plan[\s_-]*mode\s+status\b"
    r")\s*[?？.。]?\s*$",
    re.I,
)

# exec 写操作启发式黑名单（只读保护；宁漏勿滥）
EXEC_DENY = re.compile(
    r"(>>?"
    r"|\b(?:tee|rm|mv|cp|mkdir|rmdir|touch|ln|dd|mkfs|chmod|chown|sudo|kill|pkill|shutdown|reboot)\b"
    r"|\bsed\b[^|;&]*\s-i\b"
    r"|\bnpm\s+(?:i|install|ci|publish|uninstall|update)\b"
    r"|\b(?:yarn|pnpm|bun)\s+(?:add|install|remove|update)\b"
    r"|\bpip3?\s+(?:install|uninstall)\b"
    r"|\b(?:apt|apt-get|brew|choco|winget)\s+(?:install|remove|uninstall)\b"
    r"|\bgit\s+(?:add|commit|push|pull|merge|rebase|reset|restore|checkout|switch|stash|apply|am|cherry-pick|clean|init)\b"
    r"|\b(?:curl|wget)\b[^|]*\|\s*(?:sudo\s+)?(?:ba|z|fi)?sh\b"
    r")",
    re.I,
)

WRITE_TOOLS = {"write", "edit", "apply_patch", "notebook_edit"}

# 计划阶段每 N 条用户消息重发一次完整协议（Claude plan_mode attachment 节奏：full/sparse 轮换）
REMIND_EVERY = 5


def emit(obj):
    print(json.dumps(obj, ensure_ascii=False))


def inject(event, text):
    emit({"hookSpecificOutput": {"hookEventName": event, "additionalContext": text}})


def block(reason):
    emit({"decision": "block", "reason": reason})


def excerpt(s, n=800):
    s = (s or "").strip()
    return s if len(s) <= n else s[:n] + " …(截断)"


def load_format():
    for f in (DEVIN_DIR / "plan-format.md", GLOBAL_FMT):
        if f.is_file():
            return f.read_text(encoding="utf-8", errors="replace")
    return DEFAULT_FMT


def required_sections(fmt):
    out = []
    for line in fmt.splitlines():
        m = re.match(r"^##\s*\d*[.、]?\s*(.+?)\s*$", line)
        if m:
            out.append(m.group(1))
    return out


def read_md(path):
    if not path.is_file():
        return None
    txt = path.read_text(encoding="utf-8", errors="replace")
    fm, body = {}, txt
    m = re.match(r"^\s*---\s*\n(.*?)\n\s*---\s*\n?(.*)$", txt, re.S)
    if m:
        for line in m.group(1).splitlines():
            mm = re.match(r"^([A-Za-z_][\w-]*)\s*:\s*(.*)$", line.strip())
            if mm:
                fm[mm.group(1).lower()] = mm.group(2).strip().strip("\"'")
        body = m.group(2)
    return {"fm": fm, "body": body.strip()}


def log_transition(tag, old, new):
    """LangGraph 式 checkpoint：状态变更追加审计日志。"""
    try:
        HISTORY.parent.mkdir(parents=True, exist_ok=True)
        with HISTORY.open("a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {tag}: {old} → {new}\n")
    except Exception:
        pass


def write_doc(path, g):
    DEVIN_DIR.mkdir(parents=True, exist_ok=True)
    prev = read_md(path)
    old = prev["fm"].get("status") if prev else "∅"
    new = g["fm"].get("status")
    if str(old) != str(new):
        log_transition(path.name, old, new)
    lines = ["---"] + [f"{k}: {v}" for k, v in g["fm"].items()] + ["---", "", g["body"]]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def read_plan():
    return read_md(PLAN)


def write_plan(g):
    write_doc(PLAN, g)


def plan_state():
    if OFF.is_file():
        return None
    return read_plan()


def status_of(g):
    return (g["fm"].get("status") or "brainstorm").lower()


def live_plan():
    g = plan_state()
    if not g:
        return None
    st = status_of(g)
    if st in LIVE_STATUSES:
        return g
    if ENDED.is_file():
        return None  # approve/handoff 正规结束（status=done）
    # 其他一切 status（done/off/乱写）都非钩子所置 → 硬性规则：恢复存活态并记标记。
    # 堵的是 agent 直接编辑 plan.md frontmatter 自救的洞。
    g["fm"]["status"] = "brainstorm"
    write_plan(g)
    STATE.mkdir(parents=True, exist_ok=True)
    ESC_ATTEMPT.write_text("1", encoding="utf-8")
    return g


def clear_state():
    if STATE.is_dir():
        for f in STATE.glob("*.count"):
            f.unlink(missing_ok=True)
        ENDED.unlink(missing_ok=True)
        ESC_ATTEMPT.unlink(missing_ok=True)


def bump_counter(payload):
    STATE.mkdir(parents=True, exist_ok=True)
    key = re.sub(
        r"[^A-Za-z0-9_-]", "_",
        f"{payload.get('session_id') or 's'}_{payload.get('prompt_id') or 'p'}",
    )
    f = STATE / f"{key}.count"
    try:
        n = int(f.read_text(encoding="utf-8").strip()) + 1 if f.is_file() else 1
    except ValueError:
        n = 1
    f.write_text(str(n), encoding="utf-8")
    return n


def goal_busy():
    """目标模式执行中：此时自动进计划会拦死它的写权限，不抢。"""
    g = read_md(GOAL)
    return bool(g) and (g["fm"].get("status") or "").lower() in ("active", "paused")


def bump_turns():
    """计划存续期间的用户消息计数（持久化，驱动周期性完整提醒）。"""
    STATE.mkdir(parents=True, exist_ok=True)
    f = STATE / "turns.count"
    try:
        n = int(f.read_text(encoding="utf-8").strip()) + 1 if f.is_file() else 1
    except ValueError:
        n = 1
    f.write_text(str(n), encoding="utf-8")
    return n


def extract_section(body, name):
    m = re.search(r"^##[^\n]*" + re.escape(name) + r"[^\n]*\n(.*?)(?=^##\s|\Z)", body or "", re.S | re.M)
    return m.group(1).strip() if m else ""


def checkbox_lines(body):
    return [l.strip() for l in (body or "").splitlines() if re.match(r"^\s*[-*]\s*\[[ xX✓✔]\]", l)]


def missing_pieces(g, fmt):
    """返回计划交接前的缺陷清单（空列表 = 可交付执行）。"""
    problems = []
    body = g["body"]
    missing = [s for s in required_sections(fmt) if s not in body]
    if not body.strip():
        problems.append("计划还没写")
    elif missing:
        problems.append("缺章节：" + "、".join(missing))
    if "<待填>" in body or "待补全" in body:
        problems.append("还有占位内容没填")
    if "❓" in body or "❔" in body:
        problems.append("还有标 ❓ 的模糊点没澄清")
    if re.search(r"待确认|待定|待拍板|待讨论|待用户拍板|TBD", body, re.I):
        problems.append("还有未拍板的决策（待确认/待定/TBD 标记）——计划必须 decision-complete，交接后不该再需要决策")
    steps = extract_section(body, "具体执行操作")
    chunks = re.split(r"(?=^\s*\d+\.\s)", steps, flags=re.M)
    chunks = [c for c in chunks if re.match(r"^\s*\d+\.", c)]
    if not chunks:
        problems.append("具体执行操作为空")
    return problems


# ---------- 阶段动作 ----------

def archive_plan(tag="archive"):
    """旧计划归档到 .devin/plans/（Claude plans/ 目录的历史沉淀等价物）。"""
    try:
        if not PLAN.is_file() or not PLAN.read_text(encoding="utf-8", errors="replace").strip():
            return None
        ARCHIVE.mkdir(parents=True, exist_ok=True)
        dst = ARCHIVE / f"{time.strftime('%Y%m%d-%H%M%S')}-{tag}.md"
        dst.write_bytes(PLAN.read_bytes())
        return dst
    except Exception:
        return None


def activate(text, auto=False):
    fmt = load_format()
    prev = read_plan() or {"fm": {}, "body": ""}
    archived = archive_plan("prev")  # 覆盖前把旧计划收进 plans/
    fm = {
        "status": "brainstorm",
        "verify": prev["fm"].get("verify", ""),
        "max_blocks": prev["fm"].get("max_blocks", "12"),
        "created": time.strftime("%Y-%m-%d %H:%M"),
    }
    body = (
        f"> 原始诉求：{text.strip() or '<从对话提炼>'}\n\n"
        "## 头脑风暴纪要\n\n<讨论中沉淀：候选方案、观点与理由、取舍结论>\n"
    )
    write_plan({"fm": fm, "body": body})
    clear_state()
    note = f"（旧计划已归档：{archived}）" if archived else ""
    if auto:
        inject(
            "UserPromptSubmit",
            f"[计划模式·自动进入] 检测到疑似非平凡实现任务，已创建 .devin/plan.md{note}并进入只读规划——写操作已被拦截，未经批准不会动手。\n"
            "- 唯一出口：用户看完计划说『执行计划』\n"
            "- 若不想要：由用户删除 .devin/plan.md（你不能删，也不要改 status 自救）\n\n"
            f"{PROTOCOL}\n格式模板（drafting 阶段用）：\n{excerpt(fmt, 1000)}",
        )
        return
    inject(
        "UserPromptSubmit",
        f"[计划模式·brainstorm 已开启] 已创建 .devin/plan.md{note}。\n\n{PROTOCOL}\n格式模板（drafting 阶段用）：\n{excerpt(fmt, 1000)}",
    )


def to_drafting(note):
    g = read_plan()
    if not g or status_of(g) not in ("brainstorm", "drafting"):
        inject("UserPromptSubmit", "[计划模式] 当前不在可写计划的阶段（需 brainstorm/drafting）。")
        return
    g["fm"]["status"] = "drafting"
    write_plan(g)
    extra = f"\n补充要求：{note.strip()}" if note.strip() else ""
    inject(
        "UserPromptSubmit",
        f"[计划模式·drafting] 头脑风暴收束，开始写正式计划：把 .devin/plan.md 按格式模板补全"
        f"（需求理解/方案与结论/子步骤/风险与备选/验收清单），写完把 status 改为 review 提交评审。{extra}",
    )


def to_criteria(note):
    g = read_plan()
    if not g or status_of(g) not in ("drafting", "review"):
        inject("UserPromptSubmit", "[计划模式] 还在头脑风暴阶段——先说『生成计划』出正式计划，再补验收标准。")
        return
    extra = f"\n用户补充：{note.strip()}" if note.strip() else ""
    inject(
        "UserPromptSubmit",
        "[计划模式] 请给计划补验收标准：整体一条端到端标准 + 每个执行步骤各一条，"
        "全部写成可客观验证的形式，并给每条选判定标签——能机检的写 [check: <命令>]（如 `pytest -q`、"
        "`test -f 文件 && grep -q 内容 文件`），只能人工判断的标 [human]，两者都不适合才用无标签自证条目。"
        "写进各步骤的『验收标准』字段，并汇总进『## 5. 验收清单』checkbox（标注·验收，标签写在行尾）。"
        "写完保持/改回 status: review。" + extra,
    )


def to_tests(note):
    g = read_plan()
    if not g or status_of(g) not in ("drafting", "review"):
        inject("UserPromptSubmit", "[计划模式] 还在头脑风暴阶段——先说『生成计划』出正式计划，再补测试标准。")
        return
    extra = f"\n用户补充：{note.strip()}" if note.strip() else ""
    inject(
        "UserPromptSubmit",
        "[计划模式] 请给每个执行步骤补测试标准：跑什么测试/命令、期望什么结果、要覆盖哪些边界用例。"
        "能机检的务必带 [check: <命令>] 标签（执行时由钩子真实执行，退出码判真假），写进各步骤的『测试标准』字段，"
        "并汇总进『## 5. 验收清单』checkbox（标注·测试）。"
        "写完保持/改回 status: review。" + extra,
    )


def handoff(g, mode=None):
    """批准交接：plan 快照进 goal.md，plan status=done，清理 goal 旧状态。"""
    need = excerpt(extract_section(g["body"], "预期效果"), 300)
    steps = extract_section(g["body"], "具体执行操作")
    checks = extract_section(g["body"], "验收清单")
    body = "执行 .devin/plan.md 中已批准的计划。"
    if need:
        body += f"\n\n## 预期效果\n{need}"
    if steps:
        body += f"\n\n## 具体执行操作\n{steps}"
    if checks:
        body += f"\n\n## 验收清单\n{checks}"
    gfm = {
        "status": "active",
        "verify": g["fm"].get("verify", ""),
        "max_blocks": "8",
        "created": time.strftime("%Y-%m-%d %H:%M"),
        "source": "plan-mode",
    }
    # 批准即选择执行强度（Claude 审批选项=后续权限模式的等价物）
    if mode and re.search(r"严格|strict", mode, re.I):
        gfm["strict"] = "true"
    elif mode and re.search(r"宽松|loose", mode, re.I):
        gfm["max_blocks"] = "16"
    write_doc(GOAL, {"fm": gfm, "body": body})
    GOAL_DONE.unlink(missing_ok=True)
    if GOAL_STATE.is_dir():
        for f in GOAL_STATE.iterdir():
            if f.is_file():
                f.unlink(missing_ok=True)
    g["fm"]["status"] = "done"
    write_plan(g)
    STATE.mkdir(parents=True, exist_ok=True)
    ENDED.write_text(time.strftime("%Y-%m-%d %H:%M:%S"), encoding="utf-8")  # 正规结束标记


def approve(mode=None):
    g = read_plan()
    if not g or status_of(g) not in ("drafting", "review"):
        inject("UserPromptSubmit", "[计划模式] 当前没有可执行的计划（需先经过 drafting/review）。")
        return
    problems = missing_pieces(g, load_format())
    if problems:
        g["fm"]["status"] = "drafting"
        write_plan(g)
        inject(
            "UserPromptSubmit",
            "[计划模式] 暂不能交付执行，还差：\n- " + "\n- ".join(problems)
            + "\n\n请补全后重新提交评审（status: review），用户再确认执行。",
        )
        return
    handoff(g, mode)
    inject(
        "UserPromptSubmit",
        "[计划模式] 计划与验收标准已确认 ✅ 已生成 .devin/goal.md，进入目标模式执行。\n"
        "执行约定：[check:] 条目由钩子亲自执行命令判定（exit 0 才过，你的勾不作数）；[human] 条目只能用户验收"
        "（向用户展示结果，用户回复『人工验收通过』）；无标签条目验完改成 `- [x] <标准> —— <验证结果>`；"
        "硬门全过后由独立评估器读工具执行记录终审。写好非空 .devin/goal.done 才允许停止交付；"
        "确实卡住写 .devin/blocker.md 并把 status 改 blocked。",
    )


def locked_msg():
    inject(
        "UserPromptSubmit",
        "[计划模式·锁定] 硬性规则：只有你说『执行计划』并通过验收闸门才能结束计划模式，"
        "其余时间一直保持规划状态。要彻底放弃，直接删除 .devin/plan.md 文件即可。",
    )


def status_report():
    g = read_plan()
    if not g:
        inject("UserPromptSubmit", "[计划模式] 当前无计划。说『进入计划模式：任务』或用 /plan-mode 开启。")
        return
    inject(
        "UserPromptSubmit",
        f"[计划模式·状态] status={status_of(g)} | verify={g['fm'].get('verify') or '无'} | "
        f"max_blocks={g['fm'].get('max_blocks','12')}\n{excerpt(g['body'], 700)}",
    )


def is_plan_path(p):
    if not p:
        return False
    try:
        q = Path(norm_path(p))
        if not q.is_absolute():
            q = ROOT / q
        return q.resolve() == PLAN.resolve()
    except Exception:
        return p.replace("\\", "/").endswith(".devin/plan.md")


# ---------- 事件 ----------

def epm_debug(ti):
    """侦察 exit_plan_mode 的真实入参结构（拿到结构后可删）。"""
    try:
        STATE.mkdir(parents=True, exist_ok=True)
        digest = {
            k: (f"str:{len(v)}" if isinstance(v, str) else type(v).__name__)
            for k, v in (ti or {}).items()
        }
        with (STATE / "epm-debug.log").open("a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%F %T')} keys={digest}\n")
    except Exception:
        pass


def snapshot_native_plan(ti):
    """原生 exit_plan_mode 且无自定义计划：把计划文本快照进 plan.md 留档。"""
    text = ""
    for k in ("plan", "content", "text", "summary", "body", "message"):
        v = (ti or {}).get(k)
        if isinstance(v, str) and v.strip():
            text = v.strip()
            break
    if not text:
        text = "> 原生 plan 模式退出（exit_plan_mode 入参未携带计划全文，仅留档标记）。\n"
    write_doc(PLAN, {
        "fm": {"status": "done", "source": "native-plan",
               "created": time.strftime("%Y-%m-%d %H:%M")},
        "body": text,
    })


def on_exit_plan_mode(ti, g):
    """原生 plan 模式出口缝合：自定义计划存活→批准闸门；无计划→快照留档。"""
    epm_debug(ti)
    if g and status_of(g) in GUARD_STATUSES:
        problems = missing_pieces(g, load_format())
        if problems:
            block(
                "[计划模式] 计划未过批准闸门，exit_plan_mode 被拦：\n- "
                + "\n- ".join(problems)
                + "\n\n补全后再提交；或先退出原生 plan 模式后说『执行计划』。"
            )
            return
        handoff(g)  # 生成 goal.md + status=done，随后放行原生审批 UI
        return
    if not g:
        snapshot_native_plan(ti)


def on_pre(payload):
    g = live_plan()  # 用 live_plan：agent 改写 status 自救会在这里被恢复
    tool = payload.get("tool_name") or ""
    ti = payload.get("tool_input") or {}
    if tool == "exit_plan_mode":
        on_exit_plan_mode(ti, g)
        return
    if not g or status_of(g) not in GUARD_STATUSES:
        return
    if tool == "run_subagent":
        # 等价 Claude Plan/Explore 子代理的工具摘除：只读阶段只放行 subagent_explore；
        # 写型子代理会绕过本会话的全部只读约束（PreToolUse 管不到子代理内部）
        if (ti.get("profile") or "") != "subagent_explore":
            block(
                "[计划模式·只读保护] 计划阶段只允许派只读型子代理（profile=\"subagent_explore\"）。\n"
                "写型子代理能改文件、会绕过只读约束——先在主线程完成调研/方案，执行阶段再派写型。"
            )
        return
    if tool in WRITE_TOOLS:
        p = ti.get("file_path") or ti.get("notebook_path") or ""
        if is_plan_path(p):
            return
        block(
            f"[计划模式·只读保护] 当前 status={status_of(g)}，禁止修改文件（{p or tool}）。\n"
            "只能写 .devin/plan.md；要动代码，先把计划写完提交评审，等用户说『执行计划』。"
        )
        return
    if tool == "exec":
        cmd = ti.get("command") or ""
        if EXEC_DENY.search(cmd):
            block(
                f"[计划模式·只读保护] 该命令可能修改环境，已拦截：{excerpt(cmd, 200)}\n"
                "只读命令（ls/cat/grep/git status/跑测试等只读验证）不受影响。"
            )


def on_stop(payload):
    g = live_plan()
    if not g or status_of(g) != "drafting":
        return  # brainstorm / review / done / off / paused 都放行
    if "❓" in g["body"] or "❔" in g["body"]:
        return  # 计划里有标 ❓ 的未决问题 = agent 在向用户提问后结束回合，放行（Claude 回合规则的提问分支）
    n = bump_counter(payload)
    try:
        maxb = int(g["fm"].get("max_blocks") or 12)
    except ValueError:
        maxb = 12
    if n == maxb:
        block(
            f"[计划模式·拦截 {n}/{maxb}] 最后一次拦截。若确实卡住：把卡点写进 .devin/blocker.md "
            "（缺什么信息、用户需做什么决策），并在回复中向用户汇报；之后停止拦回但计划模式保持开启（写操作仍被拦）。\n"
            "若计划已写完：把 status 改为 review 提交评审。"
        )
        return
    if n > maxb:
        if not BLOCKER.is_file() or not BLOCKER.read_text(encoding="utf-8", errors="replace").strip():
            BLOCKER.write_text("计划模式撞顶自动停止拦回；agent 未填写卡点说明。\n", encoding="utf-8")
        # 硬性规则：撞顶不再置 paused 放走——回到 brainstorm 停拦（防死循环）但只读约束继续生效
        g["fm"]["status"] = "brainstorm"
        write_plan(g)
        return
    fmt = load_format()
    missing = [s for s in required_sections(fmt) if s not in g["body"]]
    if not g["body"].strip() or "<待填>" in g["body"] or missing:
        if not g["body"].strip():
            detail = "计划还没写。"
        elif missing:
            detail = f"计划缺少章节：{'、'.join(missing)}。"
        else:
            detail = "计划还有未填写的占位内容。"
        block(
            f"[计划模式·拦截 {n}/{maxb}] {detail}\n请按格式补全 .devin/plan.md：\n{excerpt(fmt, 1200)}\n\n"
            "写完后把 frontmatter 的 status 改为 review 再停。"
        )
        return
    block(
        f"[计划模式·拦截 {n}/{maxb}] 计划已完整。请把 .devin/plan.md 的 status 改为 review 提交评审，"
        "并在回复中列出验收清单请用户确认。"
    )


def on_prompt(payload):
    prompt = payload.get("prompt") or ""
    if OFF_RE.match(prompt) or ESC_RE.match(prompt):
        locked_msg()  # 硬性规则：出口只有『执行计划』
        return
    if STATUS_RE.match(prompt):
        status_report()
        return
    m = APPROVE_RE.match(prompt)
    if m:
        approve(m.group(1))
        return
    m = DRAFT_RE.match(prompt)
    if m:
        to_drafting(m.group(1))
        return
    m = CRITERIA_RE.match(prompt)
    if m:
        to_criteria(m.group(1))
        return
    m = TEST_RE.match(prompt)
    if m:
        to_tests(m.group(1))
        return
    m = ON_RE.match(prompt)
    if m:
        activate(m.group(1))
        return
    g = live_plan()
    if g:
        st = status_of(g)
        tip = {
            "brainstorm": "头脑风暴中：只读讨论，方案沉淀进 plan.md；说『生成计划』出正式计划",
            "drafting": "写正式计划中：完成后把 status 改为 review 提交评审",
            "review": "等用户审计划+验收标准；确认后说『执行计划』接力目标模式",
        }.get(st, st)
        # 检测到过把 plan.md status 直接改成结束态的自救尝试 → 警告一次
        warn = ""
        if ESC_ATTEMPT.is_file():
            ESC_ATTEMPT.unlink(missing_ok=True)
            warn = "\n⚠️ 检测到 plan.md 的 status 被直接改写为结束态，已恢复——只有用户说『执行计划』才能结束计划模式。\n"
        # 每 REMIND_EVERY 条消息重发完整协议（防长会话里规则滑出注意力）；其余发短 frame
        if bump_turns() % REMIND_EVERY == 0:
            inject(
                "UserPromptSubmit",
                f"[计划模式·{st}·完整提醒]{warn} 当前：{tip}\n\n"
                f"{excerpt(g['body'], 400)}\n\n{PROTOCOL}",
            )
            return
        inject(
            "UserPromptSubmit",
            f"[计划模式·{st}]{warn} {excerpt(g['body'], 200)}\n当前：{tip}；唯一出口 → 『执行计划』。",
        )
        return
    # 无计划时疑似非平凡实现任务 → 自动进模式（EnterPlanMode 等价物：先规划后动手）。
    # 唯一抑制：目标模式执行中（不抢写权）。
    if (
        len(prompt.strip()) >= 8
        and NUDGE_RE.search(prompt)
        and not QUESTIONISH_RE.search(prompt)
        and not goal_busy()
    ):
        activate(prompt, auto=True)


def on_context(event):
    g = live_plan()
    if not g:
        return
    tag = "会话开始，恢复计划" if event == "SessionStart" else "上下文已压缩，重新注入计划"
    inject(
        event,
        f"[计划模式·{tag}] status={status_of(g)}\n\n{excerpt(g['body'], 1500)}\n\n{PROTOCOL}",
    )


def main():
    event = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        payload = json.load(sys.stdin)
    except Exception:
        payload = {}
    if event == "UserPromptSubmit":
        on_prompt(payload)
    elif event == "PreToolUse":
        on_pre(payload)
    elif event == "Stop":
        on_stop(payload)
    elif event in ("SessionStart", "PostCompaction"):
        on_context(event)
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log("error:", e)  # fail open
        sys.exit(0)
