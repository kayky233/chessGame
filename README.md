# 中国象棋 — Galgame 风格 AI 对弈

> 一个融合了 **Galgame 角色体验** 和 **AI 象棋引擎** 的 Web 游戏。  
> 6 位碧蓝航线角色 × Minimax Alpha-Beta AI × Live2D × LLM 动态对话 × Cinema Mode 固定比例布局

**在线体验**：http://8.137.86.5

---

## 目录

- [项目概览](#项目概览)
- [功能清单](#功能清单)
- [技术架构](#技术架构)
- [项目结构](#项目结构)
- [快速开始](#快速开始)
- [生产部署](#生产部署)
- [AI 引擎详解](#ai-引擎详解)
- [角色系统](#角色系统)
- [LLM 对话集成](#llm-对话集成)
- [付费解锁系统](#付费解锁系统)
- [前端架构](#前端架构)
- [API 文档](#api-文档)
- [配置参考](#配置参考)

---

## 项目概览

这不是一个普通的象棋程序。它将传统中国象棋与日式 Galgame 视觉小说体验相结合：

- **左侧**：角色立绘（SVG 全身像 / Live2D 动态模型）
- **右侧**：棋盘 + 棋子
- **底部**：Visual Novel 风格对话框，角色实时评论棋局
- **整体**：Cinema Mode 16:9 固定比例舞台，黑边 letterbox，像看电影一样

每个角色代表不同的 AI 难度（depth 1-5），拥有独立的性格、配色主题、台词库和 LLM System Prompt。

---

## 功能清单

### 核心玩法
- 完整的中国象棋规则引擎（车马炮兵将士相，含蹩马脚、塞象眼、将帅对面等）
- Minimax + Alpha-Beta 剪枝 AI，搜索深度 1-5 可调
- 走棋动画（弹跳移动 + 吃子脉冲特效）
- 悔棋功能（Make/Unmake 回溯）
- 残局挑战模式（30 关，限步将杀，顺序解锁）

### 角色系统（Galgame）
- 6 位角色，每位有独立人设、配色、台词库
- SVG 全身立绘（头发、裙摆、手臂、发饰、呼吸粒子动画）
- Live2D Cubism 3 动态模型（CDN 加载，可开关切换）
- 6 类场景台词（开局/吃子/被吃/将军/胜利/失败），每类 3-5 句
- 角色选择面板（卡片式 UI，星级/描述/预览）

### LLM 动态对话
- 集成 DeepSeek API（可替换为任意 OpenAI 兼容接口）
- 每角色独立 System Prompt，保持人设一致性
- SSE 流式输出，打字机效果实时显示
- 自动降级：LLM 不可用时回退到本地台词库

### 付费解锁（Freemium）
- 免费角色：伊吹、独角兽、拉菲
- 付费角色：翔鹤、天狼星、大凤（激活码解锁）
- 管理员上帝模式（密钥一键全解锁）
- localStorage 持久化解锁状态

### 皮肤系统
- 多套棋盘皮肤（怀旧经典、水墨丹青、赛博朋克等）
- 胜局解锁机制
- 每套皮肤包含完整 CSS 变量主题

### UI/UX
- **Cinema Mode**：16:9 固定比例舞台 + 黑边 letterbox
- 竖屏自动切换 9:16（手机适配）
- JS 动态计算棋盘缩放比例（`resizeStage()`）
- 角色主题色自动切换（背景渐变、强调色、辉光色）

---

## 技术架构

```
┌─────────────────────────────────────────────────────┐
│                    Frontend                          │
│  Single HTML file (templates/index.html)             │
│  ┌──────────┐ ┌──────────┐ ┌────────────────────┐   │
│  │ CSS      │ │ SVG Art  │ │ JavaScript          │   │
│  │ Cinema   │ │ Full-body│ │ ├ Board renderer    │   │
│  │ Mode     │ │ portraits│ │ ├ Move validation   │   │
│  │ 16:9     │ │ + Live2D │ │ ├ AI communication  │   │
│  │ Letterbox│ │ CDN      │ │ ├ Dialogue system   │   │
│  └──────────┘ └──────────┘ │ ├ Puzzle engine     │   │
│                            │ ├ Skin system       │   │
│                            │ └ Freemium/unlock   │   │
│                            └────────────────────┘   │
└──────────────────────┬──────────────────────────────┘
                       │ HTTP / SSE
┌──────────────────────▼──────────────────────────────┐
│                    Backend                           │
│  Flask + Gunicorn (sync workers)                     │
│  ┌──────────────┐ ┌───────────┐ ┌────────────────┐  │
│  │ engine.py    │ │ app.py    │ │ LLM Proxy      │  │
│  │ Minimax +    │ │ REST API  │ │ DeepSeek API   │  │
│  │ Alpha-Beta   │ │ Rate limit│ │ SSE streaming  │  │
│  │ PST tables   │ │ Metrics   │ │ Fallback       │  │
│  └──────────────┘ └───────────┘ └────────────────┘  │
└─────────────────────────────────────────────────────┘
```

| 层 | 技术 | 说明 |
|---|---|---|
| 前端 | HTML5 + CSS3 + Vanilla JS | 零依赖，无框架，单文件 |
| 立绘 | SVG（动态生成） + Live2D Cubism 3 SDK | SVG 全身像 ≈ 120行 JS；Live2D 按需异步加载 |
| 后端 | Python Flask | 无状态设计，每请求完整棋盘传入 |
| AI | Minimax + Alpha-Beta + PST | 纯 Python，depth 4 响应 < 2s |
| 部署 | Gunicorn (sync) + systemd | 2核 VPS，5 workers |
| LLM | DeepSeek / OpenAI 兼容 | 可选，环境变量配置 |

---

## 项目结构

```
chessGame/
├── app.py               # Flask 后端：路由、AI调用、台词、LLM代理、限流、监控
├── engine.py            # 象棋AI引擎：Minimax + Alpha-Beta + PST评估
├── templates/
│   └── index.html       # 前端全部代码（HTML + CSS + JS，≈4000行）
├── gunicorn.conf.py     # Gunicorn 生产配置（workers、timeout、端口）
├── requirements.txt     # Python 依赖（flask, flask-cors, gunicorn）
├── start.sh             # 启动脚本
├── benchmark.py         # AI 性能基准测试
├── stress_test.py       # 并发压力测试
├── PROGRESS.md          # 开发进度日志
├── .gitignore
└── README.md            # 本文件
```

---

## 快速开始

### 环境要求
- Python 3.8+
- pip

### 本地开发

```bash
# 克隆项目
git clone https://github.com/kayky233/chessGame.git
cd chessGame

# 安装依赖
pip install -r requirements.txt

# 启动开发服务器
python app.py
# => 访问 http://localhost:5000
```

### 可选：启用 LLM 对话

```bash
# 设置环境变量（DeepSeek 为例）
export LLM_API_KEY="sk-your-deepseek-key"
export LLM_API_URL="https://api.deepseek.com/chat/completions"
export LLM_MODEL="deepseek-chat"

python app.py
```

---

## 生产部署

项目已部署在阿里云 ECS（2核 / 1.8GB RAM），使用 Gunicorn + systemd。

### Gunicorn 配置要点 (`gunicorn.conf.py`)

```python
workers = 2 * CPU_CORES + 1      # 5 workers on 2-core
worker_class = 'sync'             # CPU 密集型 AI 用同步 worker
bind = '0.0.0.0:80'
timeout = 30                      # AI depth=5 最多 30 秒
max_requests = 2000               # Worker 回收防内存泄漏
max_requests_jitter = 200         # 错开重启避免雪崩
preload_app = True                # COW 节省内存
```

### systemd 服务

```ini
[Unit]
Description=Xiangqi Chess Game
After=network.target

[Service]
WorkingDirectory=/opt/chessGame
ExecStart=/usr/bin/gunicorn -c gunicorn.conf.py app:app
Restart=always
Environment="LLM_API_KEY=your-key-here"

[Install]
WantedBy=multi-user.target
```

### 部署流程

```bash
ssh root@your-server
cd /opt/chessGame
git pull origin main
systemctl restart xiangqi
systemctl status xiangqi
```

---

## AI 引擎详解

`engine.py` 实现了一个完整的中国象棋 AI。

### 算法

| 技术 | 说明 |
|------|------|
| **Minimax** | 博弈树搜索，交替模拟双方最优决策 |
| **Alpha-Beta 剪枝** | 剪掉不可能改变结果的分支，大幅减少搜索量 |
| **Negamax** | Minimax 的简化写法（取负翻转视角） |
| **Make/Unmake** | 走棋时修改棋盘原地回溯，零拷贝，零 `deepcopy` |
| **Move Ordering** | 优先搜索吃子走法，提升剪枝效率 |

### 评估函数

- **材料值**：将10000、车600、炮285、马270、士/相120、兵30
- **Piece-Square Tables (PST)**：每种棋子在不同位置有位置加分
  - 车占中路加分、马在中心加分、兵过河后价值飙升
  - 分别为马、车、炮、兵/卒定义了 10×9 PST 矩阵

### 搜索深度与角色对应

| 角色 | depth | 节点数量级 | 响应时间 |
|------|-------|-----------|---------|
| 独角兽 | 1 | ~200 | < 50ms |
| 拉菲 | 2 | ~5,000 | < 200ms |
| 翔鹤 | 3 | ~50,000 | < 500ms |
| 伊吹/天狼星 | 4 | ~500,000 | < 2s |
| 大凤 | 5 | ~5,000,000 | < 15s |

---

## 角色系统

### 角色阵容

| 角色 | 棋力 | 性格 | 主题色 | 类型 |
|------|------|------|--------|------|
| **独角兽** | ★☆☆☆☆ | 害羞内向，抱着玩偶 | 紫色 (#CE93D8) | 免费 |
| **拉菲** | ★★☆☆☆ | 慵懒迷糊，偶尔灵光 | 暖红 (#EF9A9A) | 免费 |
| **翔鹤** | ★★★☆☆ | 温婉大方，鹤舞优雅 | 橙暖 (#FFAB91) | 付费 |
| **伊吹** | ★★★★☆ | 沉默剑客，果断如刀 | 冰蓝 (#81D4FA) | 免费 |
| **天狼星** | ★★★★☆ | 皇家女仆，优雅忠诚 | 蓝色 (#64B5F6) | 付费 |
| **大凤** | ★★★★★ | 病娇热情，绝不手软♡ | 红色 (#FF5252) | 付费 |

### SVG 立绘

每个角色的 SVG 立绘由 `drawCharPortrait()` 函数动态生成（≈120 行），包含：

- 飘逸长发（背部流发 + 刘海 + 侧发）
- 全身比例（头/颈/上身/手臂/衣袖/裙摆/褶皱）
- 大动漫眼（高光层 + 反光点 + 睫毛线）
- 3 种表情差分：普通 / 开心 / 生气
- 发光粒子动画（`<animate>` 呼吸闪烁）
- 发饰/腰带等装饰件
- 角色专属配色（hair/skin/dress/eye/accent）

### Live2D 集成

- **SDK**: Live2D Cubism 3（CDN 异步加载，不阻塞首屏渲染）
- **模型源**: [imuncle/live2d](https://imuncle.github.io/live2d/live2d_3/)
- **渲染**: PIXI.js + Live2D Cubism Framework
- **切换**: 角色选择面板中 checkbox 开关，状态通过 localStorage 保持
- 当 Live2D 开启时，SVG 立绘自动隐藏

---

## LLM 对话集成

### 架构

```
前端                    后端                     LLM API
  │                      │                         │
  ├─POST /dialogue──────>├─(非流式) req.post()────>│
  │<─────JSON { dialogue }│<──────completion────────│
  │                      │                         │
  ├─POST /dialogue-stream>├─(流式) SSE stream──────>│
  │<─────SSE chunks──────│<──────stream chunks─────│
  │   打字机效果逐字显示   │                         │
```

### System Prompt 设计

每个角色拥有独立的 System Prompt（位于 `app.py` 的 `SYSTEM_PROMPTS` 字典），包含：

1. **角色身份**：名字、代号、自称方式
2. **性格描述**：3-5 个关键词
3. **说话风格**：口头禅、标点习惯、句式特征
4. **棋力设定**：让 LLM 理解角色的强弱
5. **格式约束**：15-35 字，不加引号，符合情绪

### 降级策略

```
LLM_API_KEY 有值？
  ├─ Yes → 调用 LLM API
  │         ├─ 成功 → 返回 LLM 生成台词
  │         └─ 失败 → 降级到本地台词库
  └─ No  → 直接使用本地台词库（零延迟）
```

---

## 付费解锁系统

### 数据结构

```javascript
const FREE_CHARS  = new Set(['ibuki', 'unicorn', 'laffey']);
const ADMIN_KEY   = 'WYLNB-2026';
```

### 激活码格式

| 输入 | 效果 |
|------|------|
| `ILOVE-CHESS` | **买断全部**：¥10 解锁所有付费角色 |
| `WYLNB-2026` | **上帝模式**：全部解锁 + DEV 标识（开发者专用） |

购买链接：[爱发电 - kayky](https://afdian.com/a/kayky)

### 持久化

| Key | 值 | 说明 |
|-----|---|------|
| `xiangqi_unlocks` | `["ibuki","unicorn","laffey","taihou"]` | 已解锁角色列表 |
| `xiangqi_admin` | `"true"` | 管理员标识 |
| `xiangqi_profile` | `{wins, skin, difficulty, characterId, ...}` | 游戏存档 |

### 重置

浏览器控制台执行：
```javascript
resetGameData()  // 清除所有 localStorage 并刷新
```

---

## 前端架构

### Cinema Mode（固定比例舞台）

```css
#game-stage {
    aspect-ratio: 16 / 9;
    width: min(100vw, calc(100vh * 16 / 9));
    height: min(100vh, calc(100vw * 9 / 16));
}
```

- PC：16:9 黄金比例，超出部分显示黑边
- 手机竖屏：自动切换 9:16（`@media max-aspect-ratio: 1/1`）
- 棋盘缩放：JS `resizeStage()` 动态计算 `--board-scale` CSS 变量

### 图层结构

```
Z=0   #game-stage::before   背景渐变（角色主题色）
Z=0   #game-stage::after    暗角 Vignette 遮罩
Z=10  .char-panel            角色立绘（SVG / Live2D）
Z=20  .game-panel            棋盘 + 控件
Z=50  .dialogue-bubble       对话气泡
Z=60  h1 / .toolbar-top      标题 / 工具栏
```

### 棋盘渲染

- 64px 格子，9×10 交叉点布局（576×640px）
- Canvas-free：纯 HTML `<div>` + CSS Grid 实现
- 棋子用 CSS `border-radius: 50%` + `box-shadow` + 渐变绘制
- 走棋动画：CSS `transition` + JS `requestAnimationFrame`

---

## API 文档

### `POST /ai-move`

请求 AI 下一步棋。

**Request:**
```json
{
    "board": [["b_che","b_ma",...], ...],  // 10×9 矩阵
    "character": "taihou",                  // 可选，角色ID
    "depth": 4                              // 可选，搜索深度(1-6)
}
```

**Response:**
```json
{
    "status": "ok",
    "move": {"from": [0,0], "to": [2,0]},
    "time": 1.234,
    "nodes": 523461,
    "event": "capture",
    "dialogue": "吃掉了♡ 你的棋子也好，你的心也好，大凤都要！"
}
```

### `POST /dialogue-stream`

SSE 流式对话（LLM 驱动）。

**Request:**
```json
{"character": "taihou", "event": "capture"}
```

**Response:** `text/event-stream`
```
data: {"t":"吃","done":false}
data: {"t":"掉","done":false}
data: {"t":"了♡","done":false}
data: {"t":"","done":true}
```

### `GET /stats`

服务器运行指标。

```json
{
    "worker_pid": 12345,
    "uptime_seconds": 3600,
    "total_requests": 500,
    "total_ai_requests": 200,
    "active_ai_requests": 1,
    "avg_ai_time": 0.856,
    "ai_throughput_per_min": 3.3
}
```

### `GET /health`
健康检查。返回 `{"status": "healthy"}`。

### `GET /llm-status`
LLM 启用状态。返回 `{"enabled": true, "model": "deepseek-chat"}`。

---

## 配置参考

### 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `LLM_API_KEY` | *(空)* | LLM API 密钥，留空则禁用 LLM |
| `LLM_API_URL` | `https://api.deepseek.com/chat/completions` | API 端点 |
| `LLM_MODEL` | `deepseek-chat` | 模型名称 |

### 限流配置

| 参数 | 值 | 说明 |
|------|---|------|
| 每 IP 请求上限 | 10 次 / 10 秒 | 防止滥用 |
| AI 超时 | 30 秒 | Gunicorn worker timeout |
| 搜索深度上限 | 6 | 硬限制，防止 CPU 打满 |

---

## 许可

本项目为个人学习项目。  
Live2D 模型来源：[imuncle/live2d](https://github.com/niconi233/live2d_demo)，模型版权归原作者所有。  
碧蓝航线角色版权归 Manjuu/Yongshi/Yostar 所有，本项目仅用于学习交流。

---

## 2026-02 Ops Notes

For production stability and global online-count accuracy:

- `REDIS_URL` (or `PRESENCE_REDIS_URL`): enable Redis-backed heartbeat counting across workers.
- `PRESENCE_REDIS_KEY` (default: `chess:presence:online`): Redis sorted-set key for presence.
- `HEARTBEAT_TIMEOUT_SEC` (default: `10`): heartbeat offline timeout.
- `AI_MOVE_TIME_LIMIT_SEC` (default: `8`): hard wall-clock limit for one AI move search.
- `AI_MOVE_DEPTH5_LIMIT_SEC` (default: `3.5`): stricter cap for depth-5 mode to improve responsiveness.
- `AI_MOVE_LLM_DIALOGUE` (default: `0`): when `0`, `/ai-move` uses local dialogue only (faster, no external API wait).

If users report "one move hangs for minutes", check:

1. `/stats` -> `active_ai_requests` and `ai_move_time_limit_sec`.
2. Gunicorn worker count/timeout settings.
3. Whether LLM streaming calls are saturating sync workers.
