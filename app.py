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

from flask import Flask, request, jsonify, render_template, Response, stream_with_context, session
from flask_cors import CORS
from engine import XiangqiEngine
import time
import threading
import os
import random
import json
import sqlite3
import re
import hashlib
import hmac
import secrets

try:
    import redis  # type: ignore
except Exception:  # pragma: no cover
    redis = None

app = Flask(__name__)
CORS(app)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'change-this-in-production')
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = os.environ.get('SESSION_COOKIE_SECURE', '0') == '1'


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
# Presence / Online Users (Redis preferred, memory fallback)
# ================================================================
HEARTBEAT_TIMEOUT = int(os.environ.get('HEARTBEAT_TIMEOUT_SEC', '10'))
PRESENCE_REDIS_URL = (
    os.environ.get('PRESENCE_REDIS_URL')
    or os.environ.get('REDIS_URL')
    or ''
).strip()
PRESENCE_REDIS_KEY = os.environ.get('PRESENCE_REDIS_KEY', 'chess:presence:online')


class LocalPresenceStore:
    """In-process fallback presence store (not cross-worker accurate)."""

    def __init__(self, timeout_sec):
        self.timeout_sec = timeout_sec
        self.users = {}  # uid -> last_active_timestamp
        self.lock = threading.Lock()

    def heartbeat(self, uid, now_ts):
        with self.lock:
            if uid:
                self.users[uid] = now_ts

            stale_ids = [
                user_id for user_id, last_seen in self.users.items()
                if now_ts - last_seen > self.timeout_sec
            ]
            for user_id in stale_ids:
                del self.users[user_id]
            return len(self.users)


class RedisPresenceStore:
    """Cross-worker accurate presence store backed by Redis sorted-set."""

    def __init__(self, client, key, timeout_sec):
        self.client = client
        self.key = key
        self.timeout_sec = timeout_sec
        self.ttl_sec = max(timeout_sec * 3, 30)

    def heartbeat(self, uid, now_ts):
        cutoff = now_ts - self.timeout_sec
        pipe = self.client.pipeline(transaction=True)
        if uid:
            pipe.zadd(self.key, {uid: now_ts})
        pipe.zremrangebyscore(self.key, 0, cutoff)
        pipe.zcard(self.key)
        pipe.expire(self.key, self.ttl_sec)
        results = pipe.execute()
        return int(results[-2])  # zcard result (works with/without zadd)


presence_store = LocalPresenceStore(HEARTBEAT_TIMEOUT)
presence_store_mode = 'memory'
presence_fallback_store = LocalPresenceStore(HEARTBEAT_TIMEOUT)

if PRESENCE_REDIS_URL:
    if redis is None:
        app.logger.warning(
            'Redis URL configured but redis package is unavailable, fallback to memory presence.'
        )
    else:
        try:
            redis_client = redis.Redis.from_url(
                PRESENCE_REDIS_URL,
                decode_responses=True,
                socket_connect_timeout=1.5,
                socket_timeout=1.5,
                retry_on_timeout=False,
                health_check_interval=30,
            )
            redis_client.ping()
            presence_store = RedisPresenceStore(
                redis_client,
                PRESENCE_REDIS_KEY,
                HEARTBEAT_TIMEOUT,
            )
            presence_store_mode = 'redis'
            app.logger.info('Presence store initialized: redis key=%s', PRESENCE_REDIS_KEY)
        except Exception as exc:
            app.logger.warning(
                'Failed to init Redis presence store, fallback to memory: %s',
                exc,
            )


# ================================================================
# Match History / Leaderboard (SQLite)
# ================================================================
MATCH_DB_PATH = os.environ.get(
    'MATCH_DB_PATH',
    os.path.join(os.path.dirname(__file__), 'data', 'match_stats.db'),
)
MATCH_HISTORY_LIMIT_DEFAULT = 20
MATCH_HISTORY_LIMIT_MAX = 100
LEADERBOARD_LIMIT_DEFAULT = 20
LEADERBOARD_LIMIT_MAX = 100
USERNAME_RE = re.compile(r'^[a-zA-Z0-9_]{3,24}$')
DISPLAY_NAME_MAX_LEN = 24
PASSWORD_MIN_LEN = 6
PASSWORD_MAX_LEN = 72
PASSWORD_HASH_ITERATIONS = 260000


def _safe_int(value, default, min_v=None, max_v=None):
    try:
        out = int(value)
    except (TypeError, ValueError):
        out = default
    if min_v is not None:
        out = max(min_v, out)
    if max_v is not None:
        out = min(max_v, out)
    return out


def _normalize_uid(raw_uid):
    if raw_uid is None:
        return ''
    return str(raw_uid).strip()[:64]


def _normalize_player_name(raw_name, uid):
    name = str(raw_name or '').strip()
    if not name:
        short = uid[-6:] if uid else 'guest'
        name = f'Guest-{short}'
    return name[:24]


def _normalize_result(raw_result):
    result = str(raw_result or '').strip().lower()
    return result if result in {'win', 'lose', 'draw'} else ''


def _normalize_username(raw_username):
    username = str(raw_username or '').strip().lower()
    if not USERNAME_RE.match(username):
        return ''
    return username


def _normalize_display_name(raw_name, fallback=''):
    name = str(raw_name or '').strip()
    if not name:
        name = str(fallback or '').strip()
    return name[:DISPLAY_NAME_MAX_LEN]


def _hash_password(password):
    salt = secrets.token_bytes(16)
    hashed = hashlib.pbkdf2_hmac(
        'sha256',
        password.encode('utf-8'),
        salt,
        PASSWORD_HASH_ITERATIONS,
    )
    return f'pbkdf2_sha256${PASSWORD_HASH_ITERATIONS}${salt.hex()}${hashed.hex()}'


def _verify_password(password, password_hash):
    try:
        algo, rounds, salt_hex, hash_hex = str(password_hash).split('$', 3)
        if algo != 'pbkdf2_sha256':
            return False
        rounds_int = int(rounds)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
    except Exception:
        return False

    actual = hashlib.pbkdf2_hmac(
        'sha256',
        password.encode('utf-8'),
        salt,
        rounds_int,
    )
    return hmac.compare_digest(actual, expected)


def _session_player_id(user_id):
    return f'user:{int(user_id)}'


def _get_session_user(conn):
    user_id = session.get('auth_user_id')
    if not user_id:
        return None
    try:
        user_id_int = int(user_id)
    except (TypeError, ValueError):
        session.pop('auth_user_id', None)
        return None
    row = conn.execute(
        'SELECT id, username, display_name FROM users WHERE id = ?',
        (user_id_int,),
    ).fetchone()
    if row is None:
        session.pop('auth_user_id', None)
    return row


def _get_match_db_conn():
    conn = sqlite3.connect(MATCH_DB_PATH, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA synchronous=NORMAL')
    return conn


def _ensure_column(conn, table, column, ddl):
    cols = conn.execute(f'PRAGMA table_info({table})').fetchall()
    if any(col['name'] == column for col in cols):
        return
    conn.execute(f'ALTER TABLE {table} ADD COLUMN {ddl}')


def init_match_db():
    db_dir = os.path.dirname(MATCH_DB_PATH)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)

    conn = _get_match_db_conn()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                display_name TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                created_ts REAL NOT NULL,
                last_login_ts REAL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS match_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                player_id TEXT NOT NULL,
                player_name TEXT NOT NULL,
                user_id INTEGER,
                is_registered INTEGER NOT NULL DEFAULT 0,
                result TEXT NOT NULL,
                mode TEXT NOT NULL DEFAULT 'free',
                difficulty TEXT,
                character_id TEXT,
                duration_sec REAL,
                nodes INTEGER,
                search_timed_out INTEGER NOT NULL DEFAULT 0,
                created_ts REAL NOT NULL
            )
            """
        )
        _ensure_column(conn, 'match_results', 'user_id', 'user_id INTEGER')
        _ensure_column(
            conn,
            'match_results',
            'is_registered',
            'is_registered INTEGER NOT NULL DEFAULT 0',
        )
        conn.execute(
            'CREATE INDEX IF NOT EXISTS idx_match_player_time ON match_results (player_id, created_ts DESC)'
        )
        conn.execute(
            'CREATE INDEX IF NOT EXISTS idx_match_time ON match_results (created_ts DESC)'
        )
        conn.execute(
            'CREATE INDEX IF NOT EXISTS idx_match_registered ON match_results (is_registered, created_ts DESC)'
        )
        conn.execute(
            'CREATE INDEX IF NOT EXISTS idx_match_user ON match_results (user_id, created_ts DESC)'
        )
        conn.execute(
            'CREATE UNIQUE INDEX IF NOT EXISTS idx_users_username ON users (username)'
        )
        conn.commit()
    finally:
        conn.close()


init_match_db()


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
AI_MOVE_LLM_DIALOGUE = os.environ.get('AI_MOVE_LLM_DIALOGUE', '').strip().lower() in {
    '1', 'true', 'yes', 'on'
}

try:
    AI_MOVE_TIME_LIMIT_SEC = float(os.environ.get('AI_MOVE_TIME_LIMIT_SEC', '8'))
except ValueError:
    AI_MOVE_TIME_LIMIT_SEC = 8.0
if AI_MOVE_TIME_LIMIT_SEC <= 0:
    AI_MOVE_TIME_LIMIT_SEC = None

try:
    AI_MOVE_DEPTH5_LIMIT_SEC = float(os.environ.get('AI_MOVE_DEPTH5_LIMIT_SEC', '3.5'))
except ValueError:
    AI_MOVE_DEPTH5_LIMIT_SEC = 3.5
if AI_MOVE_DEPTH5_LIMIT_SEC <= 0:
    AI_MOVE_DEPTH5_LIMIT_SEC = None

def get_character(char_id):
    """Get character config, fallback to shokaku."""
    return CHARACTERS.get(char_id, CHARACTERS['shokaku'])

def get_dialogue(char_id, event):
    """Pick a random dialogue line for the given character and event."""
    char = get_character(char_id)
    lines = char['dialogues'].get(event, [])
    return random.choice(lines) if lines else ''


def get_ai_move_dialogue(char_id, event):
    """Fast dialogue path for /ai-move: local by default, LLM optional."""
    if not char_id:
        return ''
    if AI_MOVE_LLM_DIALOGUE and LLM_ENABLED:
        return get_llm_dialogue(char_id, event) or get_dialogue(char_id, event)
    return get_dialogue(char_id, event)

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


@app.route('/api/heartbeat', methods=['POST'])
def heartbeat():
    """Update online heartbeat and return online count."""
    data = request.get_json(silent=True) or {}
    raw_uid = data.get('uid')
    uid = str(raw_uid).strip()[:64] if raw_uid is not None else ''
    now = time.time()

    try:
        online_count = presence_store.heartbeat(uid, now)
    except Exception as exc:
        app.logger.warning(
            'Heartbeat presence error (%s), fallback to local memory: %s',
            presence_store_mode,
            exc,
        )
        online_count = presence_fallback_store.heartbeat(uid, now)

    return jsonify({
        'status': 'ok',
        'online_count': online_count,
        'mode': presence_store_mode,
    })


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
    move_time_limit = AI_MOVE_TIME_LIMIT_SEC
    if depth >= 5 and AI_MOVE_DEPTH5_LIMIT_SEC is not None:
        if move_time_limit is None:
            move_time_limit = AI_MOVE_DEPTH5_LIMIT_SEC
        else:
            move_time_limit = min(move_time_limit, AI_MOVE_DEPTH5_LIMIT_SEC)
    app.logger.info(
        'ai_move start ip=%s char=%s depth=%s time_limit=%s pid=%s',
        client_ip,
        char_id or '-',
        depth,
        f'{move_time_limit:.2f}s' if move_time_limit else 'unlimited',
        os.getpid(),
    )

    metrics.ai_start()
    t0 = time.time()
    try:
        engine = XiangqiEngine(depth=depth, time_limit_sec=move_time_limit)
        move = engine.get_best_move(board)
    finally:
        dt = time.time() - t0
        metrics.ai_end(dt)

    if move is None:
        response = jsonify({
            'status': 'ok',
            'move': None,
            'message': 'No valid moves for AI',
            'time': round(dt, 3),
            'search_timed_out': engine.timed_out,
            'event': 'lose',
            'dialogue': get_ai_move_dialogue(char_id, 'lose'),
        })
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-AI-Time'] = str(round(dt, 3))
        response.headers['X-Worker-Pid'] = str(os.getpid())
        return response

    fr, fc, tr, tc = move
    captured = board[tr][tc] if board[tr][tc] else None
    event = 'capture' if captured else 'move'

    response = jsonify({
        'status': 'ok',
        'move': {
            'from': [fr, fc],
            'to': [tr, tc]
        },
        'time': round(dt, 3),
        'nodes': engine.nodes,
        'search_timed_out': engine.timed_out,
        'event': event,
        'dialogue': (get_ai_move_dialogue(char_id, event) if char_id and event != 'move' else ''),
    })
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-AI-Time'] = str(round(dt, 3))
    response.headers['X-Worker-Pid'] = str(os.getpid())
    return response


def _serialize_user(row):
    if row is None:
        return None
    return {
        'id': int(row['id']),
        'username': row['username'],
        'display_name': row['display_name'],
    }


@app.route('/api/auth/me')
def auth_me():
    conn = _get_match_db_conn()
    try:
        user = _get_session_user(conn)
        return jsonify({
            'status': 'ok',
            'logged_in': bool(user),
            'user': _serialize_user(user),
        })
    finally:
        conn.close()


@app.route('/api/auth/register', methods=['POST'])
def auth_register():
    data = request.get_json(silent=True) or {}
    username = _normalize_username(data.get('username'))
    password = str(data.get('password') or '')
    display_name = _normalize_display_name(data.get('display_name'), fallback=username)

    if not username:
        return jsonify({'status': 'error', 'message': 'Invalid username (3-24 chars: letters, numbers, _)'}), 400
    if len(password) < PASSWORD_MIN_LEN or len(password) > PASSWORD_MAX_LEN:
        return jsonify({'status': 'error', 'message': f'Password must be {PASSWORD_MIN_LEN}-{PASSWORD_MAX_LEN} chars'}), 400
    if not display_name:
        return jsonify({'status': 'error', 'message': 'Display name is required'}), 400

    now_ts = time.time()
    password_hash = _hash_password(password)
    conn = _get_match_db_conn()
    try:
        exists = conn.execute(
            'SELECT id FROM users WHERE username = ?',
            (username,),
        ).fetchone()
        if exists is not None:
            return jsonify({'status': 'error', 'message': 'Username already exists'}), 409

        try:
            cur = conn.execute(
                """
                INSERT INTO users (username, display_name, password_hash, created_ts, last_login_ts)
                VALUES (?, ?, ?, ?, ?)
                """,
                (username, display_name, password_hash, now_ts, now_ts),
            )
        except sqlite3.IntegrityError:
            return jsonify({'status': 'error', 'message': 'Username already exists'}), 409
        conn.commit()
        user_id = int(cur.lastrowid)
        user_row = conn.execute(
            'SELECT id, username, display_name FROM users WHERE id = ?',
            (user_id,),
        ).fetchone()
    finally:
        conn.close()

    session['auth_user_id'] = user_id
    return jsonify({'status': 'ok', 'logged_in': True, 'user': _serialize_user(user_row)})


@app.route('/api/auth/login', methods=['POST'])
def auth_login():
    data = request.get_json(silent=True) or {}
    username = _normalize_username(data.get('username'))
    password = str(data.get('password') or '')
    if not username or not password:
        return jsonify({'status': 'error', 'message': 'Username and password are required'}), 400

    conn = _get_match_db_conn()
    try:
        row = conn.execute(
            """
            SELECT id, username, display_name, password_hash
            FROM users
            WHERE username = ?
            """,
            (username,),
        ).fetchone()
        if row is None or not _verify_password(password, row['password_hash']):
            return jsonify({'status': 'error', 'message': 'Invalid username or password'}), 401

        now_ts = time.time()
        conn.execute(
            'UPDATE users SET last_login_ts = ? WHERE id = ?',
            (now_ts, int(row['id'])),
        )
        conn.commit()
        user_row = conn.execute(
            'SELECT id, username, display_name FROM users WHERE id = ?',
            (int(row['id']),),
        ).fetchone()
    finally:
        conn.close()

    session['auth_user_id'] = int(user_row['id'])
    return jsonify({'status': 'ok', 'logged_in': True, 'user': _serialize_user(user_row)})


@app.route('/api/auth/logout', methods=['POST'])
def auth_logout():
    session.pop('auth_user_id', None)
    return jsonify({'status': 'ok', 'logged_in': False})


@app.route('/api/auth/display-name', methods=['POST'])
def auth_update_display_name():
    data = request.get_json(silent=True) or {}
    new_name = _normalize_display_name(data.get('display_name'))
    if not new_name:
        return jsonify({'status': 'error', 'message': 'Display name is required'}), 400

    conn = _get_match_db_conn()
    try:
        user = _get_session_user(conn)
        if user is None:
            return jsonify({'status': 'error', 'message': 'Login required'}), 401
        conn.execute(
            'UPDATE users SET display_name = ? WHERE id = ?',
            (new_name, int(user['id'])),
        )
        conn.commit()
        user_row = conn.execute(
            'SELECT id, username, display_name FROM users WHERE id = ?',
            (int(user['id']),),
        ).fetchone()
    finally:
        conn.close()

    return jsonify({'status': 'ok', 'user': _serialize_user(user_row)})


@app.route('/api/match-result', methods=['POST'])
def match_result():
    """Record one completed match result for history/leaderboard."""
    data = request.get_json(silent=True) or {}

    result = _normalize_result(data.get('result'))
    if not result:
        return jsonify({'status': 'error', 'message': 'Invalid result'}), 400

    mode = str(data.get('mode') or 'free').strip().lower()
    if mode not in {'free', 'puzzle'}:
        mode = 'free'

    difficulty = str(data.get('difficulty') or '')[:32]
    character_id = str(data.get('character_id') or '')[:32]

    try:
        duration_sec = float(data.get('duration_sec')) if data.get('duration_sec') is not None else None
    except (TypeError, ValueError):
        duration_sec = None
    if duration_sec is not None:
        duration_sec = max(0.0, min(duration_sec, 36000.0))

    try:
        nodes = int(data.get('nodes')) if data.get('nodes') is not None else None
    except (TypeError, ValueError):
        nodes = None
    if nodes is not None:
        nodes = max(0, min(nodes, 1000000000))

    timed_out = 1 if bool(data.get('search_timed_out')) else 0
    created_ts = time.time()

    conn = _get_match_db_conn()
    try:
        auth_user = _get_session_user(conn)
        if auth_user is not None:
            uid = _session_player_id(auth_user['id'])
            player_name = _normalize_display_name(
                data.get('player_name'),
                fallback=auth_user['display_name'],
            )
            user_id = int(auth_user['id'])
            is_registered = 1
        else:
            uid = _normalize_uid(data.get('uid'))
            if not uid:
                return jsonify({'status': 'error', 'message': 'Missing uid'}), 400
            player_name = _normalize_player_name(data.get('player_name'), uid)
            user_id = None
            is_registered = 0

        cur = conn.execute(
            """
            INSERT INTO match_results (
                player_id, player_name, user_id, is_registered, result, mode, difficulty, character_id,
                duration_sec, nodes, search_timed_out, created_ts
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                uid,
                player_name,
                user_id,
                is_registered,
                result,
                mode,
                difficulty,
                character_id,
                duration_sec,
                nodes,
                timed_out,
                created_ts,
            ),
        )
        conn.commit()
        row_id = int(cur.lastrowid)
    finally:
        conn.close()

    return jsonify({'status': 'ok', 'id': row_id})


@app.route('/api/match-history')
def match_history():
    """Return recent match history for one player uid."""
    limit = _safe_int(
        request.args.get('limit'),
        MATCH_HISTORY_LIMIT_DEFAULT,
        min_v=1,
        max_v=MATCH_HISTORY_LIMIT_MAX,
    )

    conn = _get_match_db_conn()
    try:
        auth_user = _get_session_user(conn)
        if auth_user is not None:
            uid = _session_player_id(auth_user['id'])
            login_required = False
        else:
            uid = _normalize_uid(request.args.get('uid'))
            if not uid:
                return jsonify({'status': 'error', 'message': 'Missing uid (or login required)'}), 400
            login_required = True

        rows = conn.execute(
            """
            SELECT
                id, result, mode, difficulty, character_id, duration_sec,
                nodes, search_timed_out, created_ts
            FROM match_results
            WHERE player_id = ?
            ORDER BY created_ts DESC
            LIMIT ?
            """,
            (uid, limit),
        ).fetchall()
    finally:
        conn.close()

    matches = []
    for row in rows:
        matches.append({
            'id': int(row['id']),
            'result': row['result'],
            'mode': row['mode'],
            'difficulty': row['difficulty'] or '',
            'character_id': row['character_id'] or '',
            'duration_sec': row['duration_sec'],
            'nodes': row['nodes'],
            'search_timed_out': bool(row['search_timed_out']),
            'created_ts': row['created_ts'],
        })

    return jsonify({
        'status': 'ok',
        'uid': uid,
        'is_guest_uid': login_required,
        'count': len(matches),
        'matches': matches,
    })


@app.route('/api/leaderboard')
def leaderboard():
    """Return global leaderboard ranked by total wins."""
    limit = _safe_int(
        request.args.get('limit'),
        LEADERBOARD_LIMIT_DEFAULT,
        min_v=1,
        max_v=LEADERBOARD_LIMIT_MAX,
    )
    min_games = _safe_int(request.args.get('min_games'), 1, min_v=1, max_v=999999)
    registered_only = str(request.args.get('registered_only', '1')).strip().lower() not in {'0', 'false', 'no'}
    scope = str(request.args.get('scope') or 'all').strip().lower()
    mode = str(request.args.get('mode') or 'free').strip().lower()
    if mode not in {'free', 'puzzle', 'all'}:
        mode = 'free'

    min_ts = None
    if scope == '7d':
        min_ts = time.time() - 7 * 86400
    elif scope == '30d':
        min_ts = time.time() - 30 * 86400
    else:
        scope = 'all'

    where_clauses = ['1=1']
    params = []
    if min_ts is not None:
        where_clauses.append('created_ts >= ?')
        params.append(min_ts)
    if mode != 'all':
        where_clauses.append('mode = ?')
        params.append(mode)
    if registered_only:
        where_clauses.append('is_registered = 1')
    where_sql = ' AND '.join(where_clauses)

    query = f"""
        WITH agg AS (
            SELECT
                player_id,
                SUM(CASE WHEN result = 'win' THEN 1 ELSE 0 END) AS wins,
                SUM(CASE WHEN result = 'lose' THEN 1 ELSE 0 END) AS losses,
                SUM(CASE WHEN result = 'draw' THEN 1 ELSE 0 END) AS draws,
                COUNT(*) AS games,
                MAX(created_ts) AS last_played_ts
            FROM match_results
            WHERE {where_sql}
            GROUP BY player_id
        ),
        named AS (
            SELECT
                a.*,
                (
                    SELECT mr.player_name
                    FROM match_results mr
                    WHERE mr.player_id = a.player_id
                    ORDER BY mr.id DESC
                    LIMIT 1
                ) AS player_name
            FROM agg a
        )
        SELECT
            player_id,
            COALESCE(player_name, player_id) AS player_name,
            wins,
            losses,
            draws,
            games,
            ROUND((wins * 100.0) / games, 1) AS win_rate,
            last_played_ts
        FROM named
        WHERE games >= ?
        ORDER BY wins DESC, win_rate DESC, games DESC, last_played_ts DESC
        LIMIT ?
    """

    conn = _get_match_db_conn()
    try:
        rows = conn.execute(query, params + [min_games, limit]).fetchall()
    finally:
        conn.close()

    entries = []
    for i, row in enumerate(rows, start=1):
        entries.append({
            'rank': i,
            'player_id': row['player_id'],
            'player_name': row['player_name'],
            'wins': int(row['wins']),
            'losses': int(row['losses']),
            'draws': int(row['draws']),
            'games': int(row['games']),
            'win_rate': float(row['win_rate']) if row['win_rate'] is not None else 0.0,
            'last_played_ts': row['last_played_ts'],
        })

    return jsonify({
        'status': 'ok',
        'scope': scope,
        'mode': mode,
        'registered_only': registered_only,
        'min_games': min_games,
        'count': len(entries),
        'entries': entries,
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
    data = metrics.snapshot()
    data['presence_mode'] = presence_store_mode
    data['ai_move_time_limit_sec'] = AI_MOVE_TIME_LIMIT_SEC
    data['ai_move_depth5_limit_sec'] = AI_MOVE_DEPTH5_LIMIT_SEC
    data['ai_move_llm_dialogue'] = AI_MOVE_LLM_DIALOGUE
    return jsonify(data)


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
        'ai_move_llm_dialogue': AI_MOVE_LLM_DIALOGUE,
    })


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
