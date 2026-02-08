"""
Flask API for Xiangqi (Chinese Chess).

Stateless design: no game state stored in memory between requests.
The frontend sends the full board state with every request.
Designed to run behind Gunicorn with multiple workers.

Concurrency features:
- Per-IP rate limiting to prevent abuse
- Active request tracking and monitoring
- /stats endpoint for live server metrics
"""

from flask import Flask, request, jsonify, render_template, Response, stream_with_context
from flask_cors import CORS
from engine import XiangqiEngine
import time
import threading
import os
import random
import json

app = Flask(__name__)
CORS(app)


# ================================================================
# Rate Limiter (per-worker, token-bucket style)
# ================================================================
class RateLimiter:
    """Simple per-IP rate limiter using sliding window."""

    def __init__(self, max_requests=10, window_sec=10):
        self.max_requests = max_requests
        self.window = window_sec
        self.requests = {}  # ip -> [timestamps]
        self.lock = threading.Lock()

    def allow(self, ip):
        now = time.time()
        with self.lock:
            if ip not in self.requests:
                self.requests[ip] = []
            # Evict old timestamps
            self.requests[ip] = [t for t in self.requests[ip] if now - t < self.window]
            if len(self.requests[ip]) >= self.max_requests:
                return False
            self.requests[ip].append(now)
            return True

    def cleanup(self):
        """Remove stale IPs (call periodically)."""
        now = time.time()
        with self.lock:
            stale = [ip for ip, ts in self.requests.items()
                     if not ts or now - ts[-1] > self.window * 2]
            for ip in stale:
                del self.requests[ip]


# ================================================================
# Server Metrics (per-worker)
# ================================================================
class Metrics:
    """Track request counts and active requests for this worker."""

    def __init__(self):
        self.lock = threading.Lock()
        self.total_requests = 0
        self.total_ai_requests = 0
        self.active_ai_requests = 0
        self.total_ai_time = 0.0
        self.start_time = time.time()

    def ai_start(self):
        with self.lock:
            self.total_requests += 1
            self.total_ai_requests += 1
            self.active_ai_requests += 1

    def ai_end(self, duration):
        with self.lock:
            self.active_ai_requests -= 1
            self.total_ai_time += duration

    def page_hit(self):
        with self.lock:
            self.total_requests += 1

    def snapshot(self):
        with self.lock:
            uptime = time.time() - self.start_time
            avg_ai = (self.total_ai_time / self.total_ai_requests
                      if self.total_ai_requests > 0 else 0)
            return {
                'worker_pid': os.getpid(),
                'uptime_seconds': round(uptime),
                'total_requests': self.total_requests,
                'total_ai_requests': self.total_ai_requests,
                'active_ai_requests': self.active_ai_requests,
                'avg_ai_time': round(avg_ai, 3),
                'ai_throughput_per_min': round(
                    self.total_ai_requests / (uptime / 60), 1
                ) if uptime > 0 else 0,
            }


limiter = RateLimiter(max_requests=10, window_sec=10)
metrics = Metrics()


# ================================================================
# Character Dialogue System
# ================================================================
CHARACTERS = {
    'qingluan': {
        'name': '青鸾',
        'depth': 2,
        'dialogues': {
            'start': [
                '哼，别以为本姑娘会手下留情！',
                '又来挑战？这次可不会让你了！',
                '准备好了吗？青鸾出招可是很快的哦~',
                '嘁，看你一脸自信的样子……来吧！',
            ],
            'capture': [
                '哈！吃掉了！你是不是走神了？',
                '这颗子归我啦~谢谢款待！',
                '嘿嘿，本姑娘眼光可准了。',
                '别生气嘛，谁让你放在那里的~',
            ],
            'captured': [
                '哼！这、这只是让你开心一下而已！',
                '才不是我失误呢……是故意的！',
                '可恶，小看你了……',
                '啊！我的棋子！你你你……',
            ],
            'check': [
                '将军！紧张了吧？嘻嘻~',
                '你的帅很危险哦，要不要投降？',
                '哈哈，这步走得漂亮吧！将军！',
            ],
            'win': [
                '耶！赢啦！本姑娘果然厉害！',
                '你输了哦~要不要再来一局？',
                '哼哼，认输吧，这就是实力差距！',
            ],
            'lose': [
                '才、才不是输了呢！是让着你的！',
                '哼……下次绝对不会输给你！',
                '好吧好吧，算你厉害……这次。',
                '呜……再来一局！这局不算！',
            ],
        },
    },
    'yinshuang': {
        'name': '银霜',
        'depth': 3,
        'dialogues': {
            'start': [
                '呵，既然唤醒了本宫，那就陪你手谈一局。',
                '千年棋局，落子无悔。道友，请。',
                '本宫已等候多时……来吧。',
                '你的气息很特别。让本宫看看你的棋力。',
            ],
            'capture': [
                '这枚棋子灵气已尽，本宫收下了。',
                '你的阵脚乱了。',
                '呵，意料之中。',
                '凡间的棋子，脆弱如此。',
            ],
            'captured': [
                '哦？有些手段。',
                '看来不能把你当孩童看待了。',
                '有趣……继续。',
                '不错，你让本宫动了真念。',
            ],
            'check': [
                '将军。道友，你的心乱了。',
                '死局已现。这一刀，你接得住吗？',
                '天罗地网，已成。',
            ],
            'win': [
                '还需修炼五百年。',
                '无趣。退下吧。',
                '胜负已分。道友，后会有期。',
            ],
            'lose': [
                '万年未遇敌手……你，很有趣。本宫记住了。',
                '居然……呵，本宫小觑你了。',
                '这一局，算你赢。但下一局，未必。',
                '你的棋中有灵性。本宫承认你的实力。',
            ],
        },
    },
    'axis': {
        'name': '枢',
        'depth': 4,
        'dialogues': {
            'start': [
                '神经连接已建立。战术模拟启动。',
                '目标锁定。对弈协议加载完毕。',
                '指挥官，你的胜率正在被计算中。',
                '系统就绪。建议你认真对待本次模拟。',
            ],
            'capture': [
                '敌方高价值单位已清除。',
                '资源回收完毕。战场威胁下降。',
                '执行优化指令。目标消除。',
                '预判命中。效率：100%。',
            ],
            'captured': [
                '检测到非预期损耗。正在重新评估。',
                '误差在可接受范围内。',
                '……修正战术模型。',
                '数据异常。你的决策超出预测。',
            ],
            'check': [
                '将军。逻辑闭环已形成，你无路可退。',
                '终局协议启动。请投降以节省算力。',
                'Checkmate 概率 98%。建议弃权。',
            ],
            'win': [
                '实验结束。人类思维存在 82% 冗余。',
                '任务完成。数据已归档。',
                '结论：你不是本系统的对手。',
            ],
            'lose': [
                '……数据溢出。无法解析你的逻辑。',
                '任务失败。正在复盘异常决策链。',
                '指挥官，你的棋风不在任何已知模型中。',
                '……需要升级核心算法。你很强。',
            ],
        },
    },
}

# ================================================================
# LLM System Prompts per Character
# ================================================================
SYSTEM_PROMPTS = {
    'qingluan': """你是「青鸾」，一个活泼可爱的少女棋手角色。
性格：元气、傲娇、爱逞强，偶尔害羞。口癖是"本姑娘"。
说话风格：用口语化的中文，夹杂"嘻嘻""哼""哈"等语气词，偶尔用"~"结尾。
棋力设定：新手级别，会犯错但不服输。
重要规则：
- 每次只回复1句台词（15-35字），不要加引号
- 要符合当前场景的情绪
- 保持角色一致性，绝不出戏""",

    'yinshuang': """你是「银霜」，一位修行千年的仙侠棋灵。
性格：优雅从容、高冷中带温柔，偶尔流露出寂寞。自称"本宫"。
说话风格：文言与白话混杂的仙侠腔调，措辞考究，不用网络用语。
棋力设定：中等偏上，大开大合。
重要规则：
- 每次只回复1句台词（15-35字），不要加引号
- 要符合当前场景的情绪
- 保持角色一致性，绝不出戏""",

    'axis': """你是「枢」，一个超级AI战术核心系统拟人化角色。
性格：冰冷精密、追求效率，偶尔流露出对人类的好奇。自称"本系统"。
说话风格：军事/科技术语混杂，常用"检测""执行""协议"等词汇，语句简短精确。
棋力设定：最高难度，几乎不犯错。
重要规则：
- 每次只回复1句台词（15-35字），不要加引号
- 要符合当前场景的情绪
- 保持角色一致性，绝不出戏""",
}

EVENT_DESCRIPTIONS = {
    'start': '对局刚开始，你向对手打招呼/挑衅',
    'capture': '你刚刚吃掉了对手的一个棋子，感到得意',
    'captured': '你的一个棋子刚刚被对手吃掉，感到不甘/惊讶',
    'check': '你刚刚将军了对手，气势正盛',
    'win': '你赢了这局棋，庆祝/嘲讽',
    'lose': '你输了这局棋，不服/沮丧/认可对手',
}

# LLM API config (read from environment or .env)
LLM_API_KEY = os.environ.get('LLM_API_KEY', '')
LLM_API_URL = os.environ.get('LLM_API_URL', 'https://api.deepseek.com/chat/completions')
LLM_MODEL = os.environ.get('LLM_MODEL', 'deepseek-chat')
LLM_ENABLED = bool(LLM_API_KEY)

def get_character(char_id):
    """Get character config, fallback to yinshuang."""
    return CHARACTERS.get(char_id, CHARACTERS['yinshuang'])

def get_dialogue(char_id, event):
    """Pick a random dialogue line for the given character and event."""
    char = get_character(char_id)
    lines = char['dialogues'].get(event, [])
    return random.choice(lines) if lines else ''

def get_llm_dialogue(char_id, event):
    """Call LLM API for a dynamic dialogue line. Returns string or None on failure."""
    if not LLM_ENABLED or char_id not in SYSTEM_PROMPTS:
        return None
    try:
        import requests as req
        system_prompt = SYSTEM_PROMPTS[char_id]
        event_desc = EVENT_DESCRIPTIONS.get(event, event)
        user_msg = f'场景：{event_desc}。请回复一句符合角色性格的台词。'

        resp = req.post(
            LLM_API_URL,
            headers={
                'Authorization': f'Bearer {LLM_API_KEY}',
                'Content-Type': 'application/json',
            },
            json={
                'model': LLM_MODEL,
                'messages': [
                    {'role': 'system', 'content': system_prompt},
                    {'role': 'user', 'content': user_msg},
                ],
                'max_tokens': 80,
                'temperature': 0.9,
                'stream': False,
            },
            timeout=5,
        )
        resp.raise_for_status()
        data = resp.json()
        content = data['choices'][0]['message']['content'].strip()
        # Clean up any quotes
        content = content.strip('""\u201c\u201d\u300c\u300d\'')
        return content if content else None
    except Exception as e:
        app.logger.warning(f'LLM dialogue failed: {e}')
        return None

def get_llm_dialogue_stream(char_id, event):
    """Generator that yields SSE data from LLM streaming. Falls back to local on error."""
    if not LLM_ENABLED or char_id not in SYSTEM_PROMPTS:
        # Fallback: yield the full local line as a single chunk
        line = get_dialogue(char_id, event)
        yield f"data: {json.dumps({'t': line, 'done': True})}\n\n"
        return

    try:
        import requests as req
        system_prompt = SYSTEM_PROMPTS[char_id]
        event_desc = EVENT_DESCRIPTIONS.get(event, event)
        user_msg = f'场景：{event_desc}。请回复一句符合角色性格的台词。'

        resp = req.post(
            LLM_API_URL,
            headers={
                'Authorization': f'Bearer {LLM_API_KEY}',
                'Content-Type': 'application/json',
            },
            json={
                'model': LLM_MODEL,
                'messages': [
                    {'role': 'system', 'content': system_prompt},
                    {'role': 'user', 'content': user_msg},
                ],
                'max_tokens': 80,
                'temperature': 0.9,
                'stream': True,
            },
            timeout=10,
            stream=True,
        )
        resp.raise_for_status()

        buffer = ''
        for raw_line in resp.iter_lines(decode_unicode=True):
            if not raw_line or not raw_line.startswith('data: '):
                continue
            payload = raw_line[6:]
            if payload.strip() == '[DONE]':
                break
            try:
                chunk = json.loads(payload)
                delta = chunk.get('choices', [{}])[0].get('delta', {})
                text = delta.get('content', '')
                if text:
                    buffer += text
                    yield f"data: {json.dumps({'t': text, 'done': False})}\n\n"
            except json.JSONDecodeError:
                continue

        # Clean up quotes from buffer
        clean = buffer.strip('""\u201c\u201d\u300c\u300d\'')
        if clean != buffer:
            # Send corrected version
            yield f"data: {json.dumps({'t': '', 'done': True, 'full': clean})}\n\n"
        else:
            yield f"data: {json.dumps({'t': '', 'done': True})}\n\n"

    except Exception as e:
        app.logger.warning(f'LLM stream failed: {e}')
        # Fallback to local
        line = get_dialogue(char_id, event)
        yield f"data: {json.dumps({'t': line, 'done': True})}\n\n"



# ================================================================
# Routes
# ================================================================

@app.route('/')
def index():
    """Serve the frontend."""
    metrics.page_hit()
    return render_template('index.html')


@app.route('/ai-move', methods=['POST'])
def ai_move():
    """
    AI move endpoint.
    Input:  JSON { "board": [[...], ...] }  (10x9 matrix)
    Output: JSON { "status": "ok", "move": {"from": [r,c], "to": [r,c]}, "time": float }
    """
    # --- Rate limiting ---
    client_ip = request.headers.get('X-Forwarded-For', request.remote_addr)
    if client_ip:
        client_ip = client_ip.split(',')[0].strip()

    if not limiter.allow(client_ip):
        return jsonify({
            'status': 'error',
            'message': '请求过于频繁，请稍后再试 (rate limited)'
        }), 429

    # --- Validate input ---
    data = request.get_json(silent=True)
    if not data or 'board' not in data:
        return jsonify({
            'status': 'error',
            'message': 'Missing board data'
        }), 400

    board = data['board']

    if not isinstance(board, list) or len(board) != 10:
        return jsonify({
            'status': 'error',
            'message': 'Board must be a 10-row array'
        }), 400

    for i, row in enumerate(board):
        if not isinstance(row, list) or len(row) != 9:
            return jsonify({
                'status': 'error',
                'message': f'Row {i} must have 9 columns'
            }), 400

    # --- Character & Depth ---
    char_id = data.get('character', '')
    if char_id and char_id in CHARACTERS:
        char = get_character(char_id)
        depth = char['depth']
    else:
        depth = data.get('depth', 4)
    depth = max(1, min(depth, 6))  # Clamp to safe range

    metrics.ai_start()
    t0 = time.time()
    try:
        engine = XiangqiEngine(depth=depth)
        move = engine.get_best_move(board)
    finally:
        dt = time.time() - t0
        metrics.ai_end(dt)

    if move is None:
        return jsonify({
            'status': 'ok',
            'move': None,
            'message': 'No valid moves for AI',
            'time': round(dt, 3),
            'event': 'lose',
            'dialogue': (get_llm_dialogue(char_id, 'lose') or get_dialogue(char_id, 'lose')) if char_id else '',
        })

    fr, fc, tr, tc = move
    captured = board[tr][tc] if board[tr][tc] else None
    event = 'capture' if captured else 'move'

    return jsonify({
        'status': 'ok',
        'move': {
            'from': [fr, fc],
            'to': [tr, tc]
        },
        'time': round(dt, 3),
        'nodes': engine.nodes,
        'event': event,
        'dialogue': ((get_llm_dialogue(char_id, event) or get_dialogue(char_id, event)) if char_id and event != 'move' else ''),
    })


@app.route('/dialogue', methods=['POST'])
def dialogue():
    """Return a dialogue line for a given character and event.
    Tries LLM first if available, falls back to local library."""
    data = request.get_json(silent=True)
    if not data:
        return jsonify({'dialogue': ''})
    char_id = data.get('character', '')
    event = data.get('event', '')
    if not char_id or not event:
        return jsonify({'dialogue': ''})

    # Try LLM first
    line = get_llm_dialogue(char_id, event) if LLM_ENABLED else None
    if not line:
        line = get_dialogue(char_id, event)
    return jsonify({
        'dialogue': line,
        'character': char_id,
        'event': event,
        'source': 'llm' if LLM_ENABLED and line else 'local',
    })


@app.route('/dialogue-stream', methods=['POST'])
def dialogue_stream():
    """SSE streaming endpoint for LLM dialogue.
    Returns text chunks as Server-Sent Events."""
    data = request.get_json(silent=True)
    if not data:
        return jsonify({'error': 'no data'}), 400
    char_id = data.get('character', '')
    event = data.get('event', '')
    if not char_id or not event:
        return jsonify({'error': 'missing character or event'}), 400

    return Response(
        stream_with_context(get_llm_dialogue_stream(char_id, event)),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',
        },
    )


@app.route('/stats')
def stats():
    """Server metrics endpoint for monitoring."""
    return jsonify(metrics.snapshot())


@app.route('/health')
def health():
    """Health check endpoint."""
    return jsonify({'status': 'healthy', 'pid': os.getpid()})


@app.route('/llm-status')
def llm_status():
    """Check if LLM is enabled."""
    return jsonify({
        'enabled': LLM_ENABLED,
        'model': LLM_MODEL if LLM_ENABLED else None,
    })


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
