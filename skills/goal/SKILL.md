---
name: goal
description: SWE-2 Max 目标模式 —— 设定持久完成条件，独立评估器确认达成前不允许停止（Claude Code /goal 同构）
argument-hint: "<完成条件> | clear | status"
triggers:
  - user
---

【goal-mode 指令】用户调用了 /goal。按其后参数处理：

- **带了完成条件** → 在项目根目录写入 `.devin/goal.md`：

  ```
  ---
  status: active
  verify: <可选：整体验收命令，如 npm test；不确定就留空>
  verify_timeout: <可选：verify 秒数，默认 300>
  check_timeout: <可选：每条 [check:] 秒数，默认 120>
  eval_timeout: <可选：评估器秒数，默认 30>
  eval_model: <可选：评估器模型，默认 claude-haiku-4-5-20251001>
  strict: <可选：true 时评估器不可用也不放行（fail-closed），默认 false>
  max_blocks: 8
  ---

  # 目标

  <完成条件：一个可判定的终态，必要时列验收清单>
  ```

  完成条件的写法（对齐 Claude /goal 的约束）：写成**能从执行证据判定的形式**——
  「`pytest -q` 退出码为 0」可以判，「代码更优雅」没法判。条件 ≤4000 字符。

  验收清单判定标签（写在条目行尾，决定谁来判它）：
  - `- [ ] 描述 [check: <命令>]` —— 钩子亲自执行该命令，退出码 0 才算过；agent 的勾不作数
  - `- [ ] 描述 [human]` —— 只能用户人工验收（用户回复「人工验收通过」后由钩子标记）
  - `- [ ] 描述` —— 无标签：agent 自证，须勾 `[x]` 并附「—— 证据」

  ⚠️ 硬性要求：每个目标至少要有一种**客观验收手段**（`[check:]` / `verify` / `[human]` 任一）。
  纯自证目标无法完成——停止时会被打回要求补一条。写目标时就该想好「怎么证明它成了」。

  写完回复「目标模式已开启」，然后立即开始朝条件工作——条件本身就是指令，不要停下来问用户要做什么。

- **无参数** → 从当前对话提炼完成条件写入上述文件；提炼不出就反问用户想要什么终态。
- **off / clear / stop / reset / none / cancel / 退出** → 把 `.devin/goal.md` frontmatter 的 `status` 改为 `off`。
- **status** → 读取 `.devin/goal.md`，汇报 status、评估次数与目标内容。

完成约定：认为条件达成时，先自行验证，再创建非空 `.devin/goal.done` 写入完成证据（做了什么、怎么验证的）。

停止时的验收管线：
1. `goal.done` 必须存在且非空
2. **结构门**：目标须有 ≥1 种客观验收手段（`[check:]` / `verify` / `[human]`），纯自证不放行
3. 无标签条目须 `[x]` +「—— 证据」齐全
4. `[check:]` 条目由钩子**亲自执行**命令（失败照打回，改勾不改结果）
5. `verify` 命令须 exit 0
6. `[human]` 条目须有用户「人工验收通过」的钩子指纹
7. **独立评估器终审**（有 `ANTHROPIC_API_KEY` 时）：小模型读取 完成条件 + goal.done 声明 + 清单状态 + 真实工具调用记录（`.devin/.goal-state/trace.log`，PostToolUse 自动攒的证据轨），返回三态之一：
   - `ok` → status=done，目标完成
   - 未达成+原因 → 打回继续（打回格式 `[条件]: 原因`）
   - `impossible` → status=blocked + 自动写 blocker.md，交付用户
   
   评估器故障（无 key/超时/JSON 畸形）**fail-open**：不阻塞、不计数、目标保留；连续挂 3 次自动置 paused 并写卡点。
   设 `strict: true` 改为 fail-closed：评估器缺席时打回（计次），适合高硬度场景。

**任何打回都消耗 `max_blocks` 计数**（默认 8）；撞顶 → 自动写 blocker.md + status=paused 放行，交给用户处理。

出口：确实不可能/需用户拍板时，写清 `.devin/blocker.md`（目标/卡点/已尝试/需要），`status` 改 `blocked` 即交付——卡点为空会被退回 active。同一失败连续 3 次打回会附"换方案"提醒。所有状态变更记入 `.devin/history.log`。

恢复：会话开始/上下文压缩后自动重注入目标；paused/blocked 有遗留 blocker.md 时会提醒处理，处理后把 status 改回 `active` 继续。

环境变量 `DEVIN_GOAL_OFF=1` 是全静默急停开关。
