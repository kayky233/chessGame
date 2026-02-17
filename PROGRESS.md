# 开发进度（持续更新）

更新时间：2026-02-08

## 当前阶段：体验优化（P0）

- [x] 皮肤系统（已完成）
- [x] 难度分级（UI + 记忆 + 传参）
- [x] 走棋/吃子动画
- [x] 残局模式基础结构（仅规划/入口）
- [x] 残局入口与列表面板（UI壳）
- [x] 残局载入/限步/通关判定（基础版）
- [x] 残局通关记录存储与展示
- [x] 残局关卡解锁规则（按顺序通关解锁）
- [x] 通关后提示进入下一关
- [x] 残局模式禁用悔棋
- [x] 新增残局关卡（10关示例）
- [x] 扩展残局关卡（30关基础版）
- [x] 残局提示文案（列表展示 + 开局提示）

## 进度日志

- 2026-02-08：新增进度维护文件，开始进行体验优化阶段
- 2026-02-08：完成难度分级（UI + 记忆 + AI 深度传参）
- 2026-02-08：完成走棋与吃子动画（移动弹跳 + 吃子脉冲）
- 2026-02-08：完成残局模式基础规划（数据结构/流程/入口）
- 2026-02-08：新增残局入口按钮与列表面板（样例数据渲染）
- 2026-02-08：完成残局逻辑（载入关卡/限步/通关判定）
- 2026-02-08：完成残局通关记录（localStorage + 列表展示）
- 2026-02-08：完成残局顺序解锁规则
- 2026-02-08：新增通关后下一关提示与残局悔棋限制
- 2026-02-08：扩展残局列表为 10 关示例
- 2026-02-08：扩展残局列表为 30 关基础版
- 2026-02-08：新增残局提示文案与列表展示

## 残局模式基础规划（草案）

### 目标
- 提供可重复挑战的“短局练习”，提升留存与成就感

### 数据结构（前端 JSON）
- `PUZZLES = [{ id, name, stars, maxMoves, board, side, hint }]`
- `side`: 先手（'r' 或 'b'）
- `maxMoves`: 限步将杀

### 流程
1. 从“残局”入口进入列表
2. 选择关卡 -> 初始化棋盘/步数
3. 走棋计步：超步数失败；将死成功
4. 通关记录存储到 `localStorage`（解锁/星级）

### UI 入口
- 在主界面控制栏新增“残局”按钮（后续实现）
- 列表页可筛选难度星级

---

## v2.0 角色体系企划：红颜博弈

### 核心定位
从"象棋工具"转型为"棋盘伴侣"。用高辨识度的女性角色人设包装 AI 难度分级，让用户因为"想看角色说什么"而持续对弈。类似 Galgame 的交互体验。

### 角色阵容

| 代号 | 棋力 | 性格关键词 | 视觉风格 | 说话风格 |
|------|------|-----------|---------|---------|
| **青鸾** | 简单 (depth=2) | 傲娇、活泼、嘴硬心软 | 古风青衣、双马尾、修仙小师妹 | 元气、口嫌体正直 |
| **银霜** | 中等 (depth=3) | 双重人格、慵懒/冷酷 | 仙侠银发、白衣、清冷感 | 古风、上位者从容 |
| **枢**   | 困难 (depth=4) | 绝对理性、数据化、毒舌 | 赛博制服、全息投影、冷色调 | 科幻术语、机械感 |

### 台词库设计（Mock 先行，后期可接 LLM）

每角色 6 类场景台词：
1. **开局** — 进入对局时的开场白
2. **吃子** — AI 吃掉玩家棋子时
3. **被吃** — 玩家吃掉 AI 棋子时
4. **将军** — AI 将军时
5. **胜利** — AI 获胜时
6. **失败** — 玩家获胜时

每类至少 3-5 句，随机抽取，避免重复感。

### 前端 UI 改造（Galgame 布局）

```
+-------------------------------------------------------+
| [角色立绘 (左侧半身)]         [棋盘区域 (右侧)]        |
|                                                       |
|  银霜正在注视着你...           (楚河汉界)              |
|                                                       |
| +---------------------------+                         |
| | 对话气泡                   |                         |
| | "小家伙，这步棋走偏了。"    |                         |
| +---------------------------+                         |
+-------------------------------------------------------+
```

关键 UI 组件：
- **立绘区**：左侧固定宽度，显示当前角色半身像（静态 PNG）
- **对话气泡**：立绘下方或棋盘下方，显示角色台词
- **角色选择**：替代原有"难度选择"下拉框，改为角色卡片选择
- **表情差分**：每角色 3 张图（普通/开心/生气），根据场景切换

### 后端改造

在 `/ai-move` 返回值中增加 `dialogue` 字段：
```json
{
  "status": "ok",
  "move": {"from": [0,4], "to": [1,4]},
  "dialogue": "这枚棋子灵气已尽，本宫收下了。",
  "event": "capture"
}
```

实现方式：
- Phase 1：后端台词库（Python dict + random.choice），零成本
- Phase 2（可选）：接入 LLM API（如 DeepSeek/通义千问），传入角色 System Prompt

### 美术资源策略

| 阶段 | 方案 | 成本 |
|------|------|------|
| 开发期 | 占位图（网络素材/截图抠图） | 免费 |
| 上线期 | 开源立绘 / Booth.pm 购买授权 | 低 |
| 进阶期 | Stable Diffusion + LoRA 自生成 | 免费 |

### 开发任务拆解

- [x] Phase 1：台词库 + 后端返回 dialogue
- [x] Phase 2：角色选择 UI + Galgame 布局
- [x] Phase 3：Galgame 布局改造 + 表情差分
- [x] Phase 4：LLM 接入（DeepSeek API + 流式输出）
- [x] Phase 5：角色体系替换为碧蓝航线6角色 + Live2D 集成

### Phase 5 详情（2026-02-08）

**6角色阵容替换：**

| 代号 | 角色名 | 棋力(depth) | 性格关键词 |
|------|--------|------------|-----------|
| unicorn | 独角兽 | 1 | 害羞、胆小、努力 |
| laffey | 拉菲 | 2 | 慵懒、迷糊、偶尔灵光 |
| shokaku | 翔鹤 | 3 | 温婉、优雅、鹤舞 |
| ibuki | 伊吹 | 4 | 沉默、果断、剑客 |
| sirius | 天狼星 | 4 | 优雅女仆、忠诚 |
| taihou | 大凤 | 5 | 病娇、热情、极强 |

**Live2D 集成（可切换开/关）：**
- 使用 Live2D Cubism 3 SDK (CDN)
- 模型源：imuncle/live2d GitHub Pages
- 前端 checkbox 切换 SVG/Live2D 模式
- 状态通过 localStorage 保持

**LLM 模型切换：**
- 环境变量 LLM_API_KEY 控制是否启用
- 前端查询 /llm-status 动态判断
- 启用时：流式 SSE 输出 LLM 台词
- 禁用时：回退到本地台词库
 
## 2026-02-17 Network and Online Update 
- Added backend /api/heartbeat with in-memory active-user tracking and timeout cleanup. 
- Added AI move response headers: Cache-Control, X-AI-Time, X-Worker-Pid, plus request-start logging. 
- Added online badge UI and heartbeat polling loop in templates/index.html. 
- Added fetch retry helper with timeout/backoff and wired AI move request retries. 
- Updated Gunicorn timeout to 120s in gunicorn.conf.py. 
- Validation: python -m py_compile app.py passed. 
- Validation: Flask test client heartbeat calls returned online_count 1 then 2. 
- Validation: Playwright client run produced no new console error artifact.
- Revalidation: post-localization Playwright smoke run passed and online_count remained stable at 1.
## 2026-02-17 Redis Presence + Long Wait Investigation
- Switched online heartbeat to Redis-backed presence store (`REDIS_URL`/`PRESENCE_REDIS_URL`) with sorted-set cleanup and memory fallback.
- Added AI search wall-clock cap via `AI_MOVE_TIME_LIMIT_SEC` in engine and `/ai-move`.
- Added optional switch `AI_MOVE_LLM_DIALOGUE` (default off) so `/ai-move` no longer blocks on external LLM by default.
- Exposed runtime indicators in `/stats`: `presence_mode`, `ai_move_time_limit_sec`, `ai_move_llm_dialogue`.
- Validation: `python -m py_compile app.py engine.py` passed.
- Validation: heartbeat endpoint returned mode and count successfully.
- Validation: forced tiny time limit triggered `search_timed_out=True` and returned move quickly.
- Validation: develop-web-game Playwright loop re-run after backend changes; no new `errors-*.json` emitted.
- Investigation: depth=5 search can hit high node counts; without wall-clock guard this can create long-tail latency.
- Investigation: `/ai-move` previously could block on external LLM call; now default path is local dialogue only.
- Tuning: added `AI_MOVE_DEPTH5_LIMIT_SEC` (default 4s) so depth>=5 responses cap latency harder than global limit.
- Validation: depth=5 `/ai-move` now returns around 4.0s with `search_timed_out=True` under heavy search.
## 2026-02-17 UX Iteration (AI Wait Experience)
- Frontend: added AI thinking timer (`AI˼���� (Xs)`) to reduce perceived freeze.
- Frontend: added stale-response guard via request token; old AI responses no longer overwrite a new game/puzzle/undo state.
- Frontend: when backend returns `search_timed_out`, info bar now shows `�����ü��پ���`.
- Backend tuning: reduced default `AI_MOVE_DEPTH5_LIMIT_SEC` from 4.0 to 3.5 for faster depth-5 response.
- Validation: depth=5 `/ai-move` returned in ~3.5s with timeout flag on complex search.
- Validation: Playwright loop re-run with no new `errors-*.json`; full-page screenshot manually inspected.
## 2026-02-17 UX Iteration (Match History + Leaderboard)
- Frontend: completed rank panel interactions (open/close, tabs, refresh, nickname save, Enter-key save).
- Frontend: added safe HTML escaping for rendered history/leaderboard fields to avoid name injection issues.
- Frontend: implemented per-match lifecycle fields (`matchReported`, `matchStartTs`) and duration helper.
- Frontend: wired result reporting on free-mode game end paths:
  - player wins by capturing `b_jiang`
  - player wins when AI has no legal move
  - player loses when AI captures `r_shuai` or delivers checkmate
- Frontend: ensured puzzle mode does not report rank data and resets match-tracking state on puzzle start.
- Backend: SQLite endpoints already in place for `/api/match-result`, `/api/match-history`, `/api/leaderboard`; confirmed live with HTTP tests.
- Validation: `python -m py_compile app.py engine.py` passed.
- Validation: Flask test client + HTTP local checks confirmed insert/history/leaderboard responses return `200` and expected payloads.
- Validation: develop-web-game Playwright client re-run produced no `errors-*.json` artifacts.
## 2026-02-17 UX Iteration (Account Registration + Logged-in Ranking)
- Backend: added session-based auth (`/api/auth/register`, `/api/auth/login`, `/api/auth/logout`, `/api/auth/me`, `/api/auth/display-name`).
- Backend: added `users` table with PBKDF2 password hash storage and login state via Flask session cookie.
- Backend: extended `match_results` with `user_id` + `is_registered` and migration-safe column creation.
- Backend: `/api/match-result` now auto-binds to logged-in account when session exists; guest records still supported via `uid`.
- Backend: `/api/match-history` now supports logged-in fetch without `uid`; guest mode remains backward-compatible.
- Backend: `/api/leaderboard` now defaults to `registered_only=1` to avoid guest IDs polluting rankings.
- Frontend: rank panel now shows account state (guest/logged-in), adds login/register entry, and logout action.
- Frontend: added dedicated auth modal with login/register tabs and submit handling.
- Frontend: nickname save now updates server-side display name when logged in; guest mode still stores locally.
- Frontend: leaderboard tab now prompts login for guests and shows explicit guidance.
- Validation: `python -m py_compile app.py engine.py` passed.
- Validation: Flask test client covered register -> me -> rename -> match submit -> history -> leaderboard -> logout flow.
- Validation: develop-web-game Playwright smoke run completed with no new `errors-*.json` artifacts.
