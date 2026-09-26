# Goal

为 Devin CLI（SWE-2 Max）打造的 **Codex 式目标模式** + **自定义计划模式**——纯钩子（hooks）实现，全局生效，无需改 Devin 本体。

核心理念：**Agent 的自述永远只是线索，不是证据。** 目标达成与否由钩子亲自执行的命令、真实工具调用证据轨和独立小模型评估器共同判定。

## 架构

```
进入目标模式：<完成条件>          或   /goal <完成条件>
        │
        ▼
┌─ 激活 ──────────────────────────────────────────┐
│ .devin/goal.md = 完成条件 + 验收清单 + frontmatter│
└──────────────────────────────────────────────────┘
        │
        ▼  每次工具调用后（PostToolUse）
   .devin/.goal-state/trace.log ← 真实工具证据轨（400KB 保尾）
        │
        ▼  Agent 想停（Stop 事件）——六道闸
   blocked 卡点交付 → goal.done 声明 → 结构门（必须存在
   ≥1 种客观验收手段：[check:]/verify/[human]）→ 自证勾+证据
   → [check:] 钩子实跑命令 → verify 全局命令 → [human] 指纹
   → 独立评估器终审 {ok / not_met / impossible}
        │
   打回统一计次，撞顶（默认 8）→ paused + blocker.md 交还用户
```

计划模式：`进入计划模式` → 只读脑暴（写工具硬拦，仅放行 `subagent_explore`）→ `生成计划` → `执行计划` → 自动生成 `goal.md` 接力目标模式。原生 `/plan` 的 `exit_plan_mode` 出口也被缝合（快照留档 / 批准闸门接管）。

## 文件清单

| 文件 | 说明 |
|---|---|
| `hooks/goal-mode.py` | 目标模式引擎（~810 行，零依赖纯标准库） |
| `hooks/plan-mode.py` | 计划模式引擎（只读保护、批准闸门、原生出口缝合） |
| `plan-format.md` | 计划书格式模板 |
| `skills/goal/SKILL.md` | 目标模式触发/文档 |
| `skills/plan-mode/SKILL.md` | 计划模式触发/文档 |
| `config.example.json` | 钩子注册示例 |

## 安装

```bash
# 1. 钩子脚本与技能 → Devin 用户级目录
cp hooks/*.py            "%APPDATA%/devin/hooks/"        # Windows
cp skills/*/SKILL.md     "%APPDATA%/devin/skills/<name>/" # 对应 skills/goal、skills/plan-mode
cp plan-format.md        "%APPDATA%/devin/"

# Linux/macOS 对应 ~/.config/devin/

# 2. 把 config.example.json 里的 hooks 段合并进 %APPDATA%/devin/config.json
#    Windows 只需把 <WIN_USER> 换成你的用户名；Linux/macOS 不用改
#    ⚠️ 勿写 C:\ 形式路径——钩子跑在 WSL 里，会锁死输入框（见「排障」节）
```

事件注册矩阵：

| 事件 | goal-mode.py | plan-mode.py |
|---|---|---|
| SessionStart | ✓ 目标重注入 | ✓ 计划重注入 |
| UserPromptSubmit | ✓ 口令识别+进度帧 | ✓ 口令识别+阶段推进 |
| PreToolUse | — | ✓ 只读保护（write/exec/run_subagent/exit_plan_mode） |
| PostToolUse | ✓ 证据轨采集 | — |
| PostCompaction | ✓ 压缩后重注入 | ✓ |
| Stop | ✓ 验收管线 | ✓ drafting 拦截 |

## 用法

```
进入目标模式：把所有测试修绿        # 自然语言激活
/goal 完成订单页重构               # slash 激活
/goal status                      # 状态（含评估器在线/离线）
退出目标模式 / /goal off           # 退出
```

验收清单条目按标签分流判定：

```markdown
- [ ] 测试全绿 [check: pytest -q]     # 钩子亲自执行，exit 0 才过
- [ ] UI 符合设计稿 [human]           # 只能用户回「人工验收通过」
- [ ] README 已更新 —— 附了证据       # 无标签：agent 自证勾+证据
```

goal.md frontmatter 可调项：`verify`（整体验收命令）、`strict: true`（评估器缺席也打回，fail-closed）、`max_blocks`（默认 8）、`eval_model`、`eval_timeout`、`check_timeout`。

## 评估器

终审调用 Anthropic API（默认 `claude-haiku-4-5-20251001`，可用 `ANTHROPIC_SMALL_FAST_MODEL` 覆盖）：

- 需要 `ANTHROPIC_API_KEY`；`ANTHROPIC_BASE_URL` 可指向兼容端点
- 读：完成条件 + goal.done + 清单状态 + trace.log 尾部 8KB
- 返回 `{ok, reason, impossible?}`：ok→done，not_met→打回，impossible→blocked+卡点
- 故障 fail-open（不阻塞）；连挂 3 次 → paused；`strict:true` 改 fail-closed
- **没有 key 也能用**：硬门（[check:]/verify/[human]）照常生效

## 环境变量

| 变量 | 作用 |
|---|---|
| `DEVIN_GOAL_OFF=1` | 全静默急停开关 |
| `ANTHROPIC_API_KEY` | 评估器密钥 |
| `ANTHROPIC_BASE_URL` | 自定义评估端点 |
| `ANTHROPIC_SMALL_FAST_MODEL` | 评估器模型覆盖 |
| `DEVIN_PROJECT_DIR` | 项目根（Devin 自动注入，也可手动） |

## 排障 / Troubleshooting

以下全是实机踩出来的坑（Windows 侧实测结论）：

**每条消息都被「Prompt blocked」锁死** —— 最常见

钩子协议里**进程退出码 2 = block 决策**。Windows 下 Devin 经 **WSL** 执行钩子命令，如果写成 `python3 "C:\..."`（或 `C:/...`）形式，WSL python3 把 `C:` 当相对路径拼到项目目录下 → `can't open file` → exit 2 → 每条消息被 block，输入框锁死。修法：命令必须用 `/mnt/c/...` posix 路径——`config.example.json` 里的双路径自回退写法（`/mnt/c/... || $HOME/... || true`）已覆盖 Windows 与 POSIX 两种情况，改完**重开会话**生效。

**改了 config 没生效**

钩子注册在会话启动时读入、按会话缓存、**不热重载**——必须关掉重开会话。在旧会话里重发消息只会重演旧命令的错误。

**怎么确认钩子活着**

- 会话内敲 `/hooks` 看已加载钩子与命令来源
- 手动跑一遍：`echo '{"prompt":"hi"}' | python3 /mnt/c/Users/<你>/AppData/Roaming/devin/hooks/goal-mode.py UserPromptSubmit`——有 JSON 输出即健康（无存活目标时静默退出 0 也正常）
- `|| true` 兜底的代价是钩子崩溃时静默失效——排障第一步永远是手动跑命令，别看"没报错"就以为在跑

**`[check:]` 命令的 shell**

`[check:]` 命令与钩子同一执行环境（Windows=WSL bash）。跨 shell 写法推荐 `py -3 ... || python3 ...` 双落，但路径同理要用 `/mnt/c` 形式。

## 安全边界

- 钩子所有异常 fail-open，绝不打断会话
- `[check:]`/`verify` 命令以普通用户权限在项目根执行，无提权
- goal.done / blocker.md / trace.log / history.log 全在 `<项目>/.devin/` 下，可审计可删

## License

[MIT](LICENSE)
