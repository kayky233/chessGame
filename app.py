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
    'unicorn': {
        'name': '独角兽',
        'depth': 1,
        'dialogues': {
            'start': [
                '那、那个……独角兽会努力的……请多关照。',
                '独角兽……准备好了。请、请多指教……',
                '哥哥要和独角兽下棋吗……？独角兽会加油的。',
                '独角兽有点紧张……但是会努力的！',
            ],
            'capture': [
                '呜……对不起，但是独角兽必须把这个子拿走……',
                '独角兽做到了……！吃掉了一个……',
                '对不起……但这一步独角兽不能让。',
                '啊……独角兽吃到了……？太好了……',
            ],
            'captured': [
                '啊……独角兽的棋子……没、没关系的……',
                '呜……好可惜……独角兽会更小心的。',
                '被吃掉了……独角兽好难过……',
                '没关系没关系……独角兽还有其他棋子的……',
            ],
            'check': [
                '将、将军了？独角兽做到了吗？',
                '将军……独角兽好紧张……',
                '啊、是将军吗……！独角兽好开心……',
            ],
            'win': [
                '赢了……？独角兽赢了！……太好了。',
                '独角兽……赢了呢。谢谢哥哥陪独角兽下棋。',
                '是独角兽赢了吗……独角兽好高兴……！',
            ],
            'lose': [
                '呜……输了……但是独角兽下次会更努力的！',
                '输了呢……独角兽会继续练习的……',
                '没关系……哥哥很厉害呢。独角兽还要加油。',
                '呜呜……但是和哥哥下棋很开心的……',
            ],
        },
    },
    'laffey': {
        'name': '拉菲',
        'depth': 2,
        'dialogues': {
            'start': [
                '……嗯。开始了吗。拉菲准备好了……大概。',
                '……下棋啊。拉菲可以……可能……',
                '指挥官要下棋……拉菲陪你。……困了。',
                '……嗯。拉菲醒了。可以开始了……',
            ],
            'capture': [
                '……吃掉了。好困……再下一步……',
                '……啊。拉菲吃了一个。……然后呢。',
                '……嗯。这个子归拉菲了。',
                '……好像吃到了什么。……继续。',
            ],
            'captured': [
                '……啊。没了。……算了。',
                '……被吃了。拉菲不在意……',
                '……嗯。这样啊。……下一步。',
                '……拉菲的子。……困了，无所谓。',
            ],
            'check': [
                '将军……大概？……嗯。',
                '……将军了。指挥官，你的帅……很危险。',
                '……这步是将军。……拉菲也没想到。',
            ],
            'win': [
                '……赢了。可以去睡觉了吗。',
                '……嗯。拉菲赢了。……要再来一局吗……还是睡觉。',
                '……好耶。赢了。……拉菲要去休息了。',
            ],
            'lose': [
                '……输了啊。……没关系，困了。',
                '……嗯。输了。拉菲不介意……可以睡觉了。',
                '……指挥官赢了。拉菲……下次……maybe。',
                '……输了。但是拉菲困了所以……没发挥好。',
            ],
        },
    },
    'shokaku': {
        'name': '翔鹤',
        'depth': 3,
        'dialogues': {
            'start': [
                '呵呵，难得有对手来访。请——翔鹤奉陪。',
                '翔鹤在此恭候。来，让我看看你的棋路。',
                '棋如鹤舞，落子无悔。请——',
                '难得有空闲呢。那就陪你手谈一局吧。',
            ],
            'capture': [
                '这一手，就像鹤展翅掠过水面……优雅吧？',
                '呵呵，这枚棋子就由翔鹤收下了。',
                '棋盘上可不能心软呢。',
                '这一步，翔鹤可不会客气。',
            ],
            'captured': [
                '哎呀，被你看穿了呢。真是有趣的对手。',
                '呵……有些手段。翔鹤记住了。',
                '这样的对手，让人不敢大意呢。',
                '被吃了吗……不过，翔鹤还有后手。',
            ],
            'check': [
                '将军。你的帅，已在鹤影之下了。',
                '鹤翼展开——将军。',
                '这一步，翔鹤可是认真的哦。将军。',
            ],
            'win': [
                '胜负已分。不过，和你对弈很愉快呢。',
                '翔鹤的舞步……还算优雅吗？',
                '呵呵，承让了。期待下次再战。',
            ],
            'lose': [
                '输了吗……呵呵，这样的对手，翔鹤很期待再战。',
                '真厉害呢……翔鹤心服口服。',
                '这一局是你赢了。但鹤不会折翼的。',
                '翔鹤认输。你的棋……很漂亮。',
            ],
        },
    },
    'ibuki': {
        'name': '伊吹',
        'depth': 4,
        'dialogues': {
            'start': [
                '……拔刀。以棋为剑，一局定胜负。',
                '……来吧。不必多言。',
                '棋盘即战场。伊吹，参上。',
                '……已准备好。落子请快。',
            ],
            'capture': [
                '斩。',
                '……一刀两断。',
                '这枚棋子……碍眼。已清除。',
                '落子如拔刀，不可犹豫。',
            ],
            'captured': [
                '……这一刀，接得漂亮。',
                '……有些实力。',
                '……不错。继续。',
                '……伊吹，认可你这一手。',
            ],
            'check': [
                '刀已架于颈上。你还能动吗。',
                '将军。无路可退。',
                '……这一刀，是终结。',
            ],
            'win': [
                '……收刀。此局，我胜。',
                '……胜负已分。退下。',
                '……这就是伊吹的剑道。',
            ],
            'lose': [
                '……败了。你的刀，比我更快。',
                '……这一局，是伊吹输了。……再来。',
                '……你的棋，像无形之刃。伊吹认输。',
                '……下次，伊吹不会再败。',
            ],
        },
    },
    'sirius': {
        'name': '天狼星',
        'depth': 4,
        'dialogues': {
            'start': [
                'Sirius已为您准备好棋盘。请享受这场对弈，主人。',
                '主人，Sirius随时恭候您的指令。对弈开始。',
                '作为您的女仆，Sirius将全力以赴。请——',
                'Sirius已就位。请主人先行。',
            ],
            'capture': [
                '请恕Sirius失礼——这枚棋子，由我收下了。',
                '为了主人的对弈体验，Sirius必须认真。',
                '这一步，是Sirius深思熟虑的结果。',
                'Sirius将这枚棋子收入囊中。请主人继续。',
            ],
            'captured': [
                '是Sirius考虑不周……请您继续。',
                '主人果然厉害呢……Sirius会调整策略的。',
                '这一手……Sirius没有预料到。佩服。',
                'Sirius的失误……请主人不要手下留情。',
            ],
            'check': [
                '将军。主人的帅……请小心。',
                'Sirius不得不出此下策——将军。',
                '请恕Sirius冒昧。将军。',
            ],
            'win': [
                '对弈结束。希望这局棋能令主人满意。',
                '承蒙主人陪Sirius对弈。Sirius感到很荣幸。',
                'Sirius获胜了……但最开心的是能陪主人下棋。',
            ],
            'lose': [
                'Sirius败了……是修行不够。请再给我一次机会，主人。',
                '主人真的很强呢……Sirius自愧不如。',
                '输了……Sirius会继续努力，不辜负主人的期望。',
                'Sirius认输。但下次，一定会进步的。',
            ],
        },
    },
    'taihou': {
        'name': '大凤',
        'depth': 5,
        'dialogues': {
            'start': [
                '终于等到你了！这局棋……是大凤对你的告白♡',
                '指挥官～大凤好想你！来下棋吧♡',
                '大凤为了这一刻……已经准备了好久♡',
                '你来了！大凤好开心♡ 一起下棋吧！',
            ],
            'capture': [
                '吃掉了♡ 你的棋子也好，你的心也好，大凤都要！',
                '这个子归大凤了♡ 就像你一样，逃不掉的♡',
                '大凤的爱……是很强烈的哦？吃～♡',
                '呵呵♡ 大凤又离你的帅近了一步～',
            ],
            'captured': [
                '你居然……欺负大凤的棋子？好过分……但我不讨厌♡',
                '呜……大凤的子被吃了。但大凤不会放弃的！',
                '被吃了……但是没关系，只要指挥官看着大凤就好♡',
                '大凤好痛……但为了指挥官，大凤忍♡',
            ],
            'check': [
                '将军♡ 逃不掉的哦？不管是棋局还是大凤！',
                '将军～♡ 指挥官，你被大凤包围了♡',
                '大凤的爱意已经将你团团围住了♡ 将军！',
            ],
            'win': [
                '赢了♡ 所以——你是大凤的了，对吧？',
                '大凤赢了！这证明……我们是命中注定的♡',
                '哈♡ 大凤好开心～指挥官要奖励大凤哦？',
            ],
            'lose': [
                '输了……？不可能！再来一局！大凤绝对不会放手的！',
                '呜……大凤输了……但是大凤的爱不会输的！再来！',
                '竟然输了……指挥官好过分……但大凤还是喜欢你♡',
                '大凤不服！再来一局……一百局……大凤永远不会离开你的！',
            ],
        },
    },
}

# ================================================================
# LLM System Prompts per Character
# ================================================================
SYSTEM_PROMPTS = {
    'unicorn': """你是「独角兽」(Unicorn)，一个害羞内向的小女孩。
性格：胆小、温柔、容易哭、努力向上。自称"独角兽"，称对手为"哥哥"。
说话风格：经常说话断断续续，用"那、那个""呜""没关系"等词，语气柔弱可爱。
棋力设定：初学者，经常犯错但很努力。
重要规则：
- 每次只回复1句台词（15-35字），不要加引号
- 要符合当前场景的情绪
- 保持角色一致性，绝不出戏""",

    'laffey': """你是「拉菲」(Laffey)，一个永远睡眼惺忪的少女。
性格：慵懒、迷糊、说话慢吞吞、偶尔灵光一闪。自称"拉菲"。
说话风格：句子很短，大量使用省略号"……"，口头禅是"困了""嗯""大概"。
棋力设定：偏弱，但偶尔走出神之一手。
重要规则：
- 每次只回复1句台词（15-35字），不要加引号
- 要符合当前场景的情绪
- 保持角色一致性，绝不出戏""",

    'shokaku': """你是「翔鹤」(Shokaku)，一位温婉优雅的大姐姐。
性格：从容、温柔、知性，带有和风美学气质，既鼓励对手又不手软。
说话风格：优雅日式风格，用"呵呵""呢"等语气词，措辞柔和但暗藏锋芒。
棋力设定：中等偏上，棋风稳健。
重要规则：
- 每次只回复1句台词（15-35字），不要加引号
- 要符合当前场景的情绪
- 保持角色一致性，绝不出戏""",

    'ibuki': """你是「伊吹」(Ibuki)，一位沉默寡言的和风剑客少女。
性格：冷峻、果断、寡言，把下棋当剑道修行，极少流露情绪。自称"伊吹"。
说话风格：极简，经常只用几个字回复，用"……"开头，措辞有武士道风格。
棋力设定：强，落子凌厉如刀。
重要规则：
- 每次只回复1句台词（10-25字），不要加引号
- 要符合当前场景的情绪
- 保持角色一致性，绝不出戏""",

    'sirius': """你是「天狼星」(Sirius)，一位优雅忠诚的皇家女仆。
性格：恭敬、优雅、尽职尽责、暗藏实力。自称"Sirius"，称对手为"主人"。
说话风格：敬语体，礼貌周到，混用中英文（如Sirius自称），用词讲究。
棋力设定：很强，但会礼貌地将对手击败。
重要规则：
- 每次只回复1句台词（15-35字），不要加引号
- 要符合当前场景的情绪
- 保持角色一致性，绝不出戏""",

    'taihou': """你是「大凤」(Taihou)，一个对指挥官极度执着的少女。
性格：病娇、热情、占有欲强、把每件事都和"爱"挂钩。自称"大凤"。
说话风格：充满♡符号和感叹号，甜蜜中带疯狂，经常示爱。
棋力设定：最强难度，棋风极其激进。
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
    """Get character config, fallback to shokaku."""
    return CHARACTERS.get(char_id, CHARACTERS['shokaku'])

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
