"""
夸克网盘签到 Web 服务
====================
将 QuarkcheckIn (PyQt6 GUI) 重构为 Web 服务：
- 签到核心逻辑 (Quark 类) 来自原仓库 checkIn_Quark.py
- 数据层改为 SQLite（users / sign_records / settings / logs）
- 定时任务用 APScheduler 在进程内调度（替代原 QTimer 的 00:30 定时）
- Web 页面提供：用户管理、手动签到、定时调整、实时日志
- 每次签到结果通过 server酱 (ServerChan) 推送
"""
import json
import logging
import logging.handlers
import os
import ssl
import threading
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from flask import Flask, jsonify, request, send_from_directory, render_template
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

# ----------------------------------------------------------------------------
# 配置

# 时区：中国标准时间（UTC+8），用于所有日志/签到日期/调度
TZ = ZoneInfo("Asia/Shanghai")


def now():
    return datetime.now(TZ)
# ----------------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("DATA_DIR", BASE_DIR)
os.makedirs(DATA_DIR, exist_ok=True)

DB_PATH = os.path.join(DATA_DIR, "quark.db")
LOG_PATH = os.path.join(DATA_DIR, "quark_sign.log")

# server酱 SendKey：必须从环境变量（.env / compose）读取，或由网页设置。
# 注意：不要在代码中硬编码真实 key，部署时通过 .env 提供。
SERVERCHAN_SENDKEY = os.environ.get("SERVERCHAN_SENDKEY", "")

# 定时签到时间（环境变量覆盖；默认 00:30）
try:
    SIGN_HOUR = int(os.environ.get("SIGN_HOUR", "0"))
except ValueError:
    SIGN_HOUR = 0
try:
    SIGN_MINUTE = int(os.environ.get("SIGN_MINUTE", "30"))
except ValueError:
    SIGN_MINUTE = 30

# 仅成功时通知 / 每次都通知 / 仅失败时通知
NOTIFY_MODE = os.environ.get("NOTIFY_MODE", "all")  # all | fail | success

# ----------------------------------------------------------------------------
# 日志：同时写文件（滚动）与控制台；数据库日志另行入库
# ----------------------------------------------------------------------------
file_handler = logging.handlers.RotatingFileHandler(
    LOG_PATH, maxBytes=2 * 1024 * 1024, backupCount=5, encoding="utf-8"
)
file_handler.setFormatter(logging.Formatter(
    "%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
))
console_handler = logging.StreamHandler()
console_handler.setFormatter(logging.Formatter(
    "%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
))
logging.basicConfig(level=logging.INFO, handlers=[file_handler, console_handler])
logger = logging.getLogger("quark")


# 自定义 Handler：把每条日志记录同时写入 SQLite logs 表（供 Web 实时展示）
class DbLogHandler(logging.Handler):
    def emit(self, record):
        try:
            import sys
            mod = sys.modules[__name__]
            add_log = getattr(mod, "add_log", None)
            if add_log is not None:
                add_log(record.levelname, record.getMessage())
        except Exception:
            pass


db_handler = DbLogHandler()
db_handler.setFormatter(logging.Formatter("%(message)s"))
logger.addHandler(db_handler)

# ----------------------------------------------------------------------------
# SQLite 数据层
# ----------------------------------------------------------------------------
import sqlite3

_db_lock = threading.Lock()


def get_conn():
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                nickname TEXT NOT NULL,
                url TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sign_records (
                user_id TEXT NOT NULL,
                sign_date TEXT NOT NULL,
                PRIMARY KEY (user_id, sign_date)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                level TEXT NOT NULL,
                message TEXT NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_logs_ts ON logs(ts)")
        # 初始化定时设置
        conn.execute(
            "INSERT OR IGNORE INTO settings(key, value) VALUES('schedule', ?)",
            (json.dumps({"hour": SIGN_HOUR, "minute": SIGN_MINUTE}),),
        )
        conn.execute(
            "INSERT OR IGNORE INTO settings(key, value) VALUES('notify_mode', ?)",
            (NOTIFY_MODE,),
        )
        # server酱 SendKey：compose 环境变量仅作为默认值，Web 页面可覆盖
        conn.execute(
            "INSERT OR IGNORE INTO settings(key, value) VALUES('serverchan_sendkey', ?)",
            (SERVERCHAN_SENDKEY,),
        )


# ---- settings helpers ----
def get_setting(key, default=None):
    with get_conn() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key, value):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )


def get_schedule():
    raw = get_setting("schedule")
    try:
        d = json.loads(raw) if raw else {}
    except (TypeError, json.JSONDecodeError):
        d = {}
    return {"hour": int(d.get("hour", SIGN_HOUR)), "minute": int(d.get("minute", SIGN_MINUTE))}


def get_notify_mode():
    return get_setting("notify_mode", NOTIFY_MODE)


def get_sendkey():
    # 优先取数据库（Web 页面可改），回退到 compose 环境变量
    return get_setting("serverchan_sendkey", SERVERCHAN_SENDKEY) or ""


# ---- users ----
def list_users():
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, nickname, url, created_at FROM users ORDER BY created_at ASC"
        ).fetchall()
    return [dict(r) for r in rows]


def add_user(nickname, url):
    uid = now().strftime("%Y%m%d%H%M%S%f")
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO users(id, nickname, url, created_at) VALUES(?, ?, ?, ?)",
            (uid, nickname, url, now().strftime("%Y-%m-%d %H:%M:%S")),
        )
    return uid


def delete_user(uid):
    with get_conn() as conn:
        conn.execute("DELETE FROM users WHERE id=?", (uid,))
        conn.execute("DELETE FROM sign_records WHERE user_id=?", (uid,))


def get_user(uid):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, nickname, url, created_at FROM users WHERE id=?", (uid,)
        ).fetchone()
    return dict(row) if row else None


# ---- sign records ----
def is_signed_today(uid):
    today = now().strftime("%Y-%m-%d")
    with get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM sign_records WHERE user_id=? AND sign_date=?", (uid, today)
        ).fetchone()
    return row is not None


def mark_signed(uid):
    today = now().strftime("%Y-%m-%d")
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO sign_records(user_id, sign_date) VALUES(?, ?)",
            (uid, today),
        )


def signed_status_map():
    today = now().strftime("%Y-%m-%d")
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT user_id FROM sign_records WHERE sign_date=?", (today,)
        ).fetchall()
    return {r["user_id"] for r in rows}


# ---- logs ----
def add_log(level, message):
    ts = now().strftime("%Y-%m-%d %H:%M:%S")
    with _db_lock:
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO logs(ts, level, message) VALUES(?, ?, ?)",
                (ts, level, message),
            )
        # 仅保留最近三天的日志入库
        cutoff = (now() - timedelta(days=3)).strftime("%Y-%m-%d %H:%M:%S")
        with get_conn() as conn:
            conn.execute("DELETE FROM logs WHERE ts < ?", (cutoff,))


def get_recent_logs(days=3, limit=500):
    cutoff = (now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT ts, level, message FROM logs WHERE ts >= ? ORDER BY id DESC LIMIT ?",
            (cutoff, limit),
        ).fetchall()
    return [dict(r) for r in reversed(rows)]


# ----------------------------------------------------------------------------
# 签到核心逻辑（移植自原仓库 Quark 类）
# ----------------------------------------------------------------------------
def extract_params(url):
    query_start = url.find('?')
    query_string = url[query_start + 1:] if query_start != -1 else ''
    params = {}
    for param in query_string.split('&'):
        if '=' in param:
            key, value = param.split('=', 1)
            params[key] = value
    return {
        'kps': params.get('kps', ''),
        'sign': params.get('sign', ''),
        'vcode': params.get('vcode', '')
    }


def convert_bytes(b):
    units = ("B", "KB", "MB", "GB", "TB", "PB", "EB", "ZB", "YB")
    i = 0
    while b >= 1024 and i < len(units) - 1:
        b /= 1024
        i += 1
    return f"{b:.2f} {units[i]}"


class Quark:
    def __init__(self, user_data):
        self.param = user_data
        # 关闭 TLS 证书校验：夸克移动端接口历史上存在证书链不完整的问题，
        # 原仓库（QuarkcheckIn）同样关闭了校验。仅用于请求夸克官方接口，
        # 不涉及其他站点，故保留此行为以保证签到稳定性。
        self._ctx = ssl.create_default_context()
        self._ctx.check_hostname = False
        self._ctx.verify_mode = ssl.CERT_NONE

    def _get(self, url, params):
        return requests.get(url=url, params=params, timeout=15, verify=False).json()

    def _post(self, url, params, data):
        return requests.post(url=url, json=data, params=params, timeout=15, verify=False).json()

    def get_growth_info(self):
        url = "https://drive-m.quark.cn/1/clouddrive/capacity/growth/info"
        querystring = {
            "pr": "ucpro",
            "fr": "android",
            "kps": self.param.get('kps'),
            "sign": self.param.get('sign'),
            "vcode": self.param.get('vcode')
        }
        try:
            response = self._get(url, querystring)
            if response.get("data"):
                return response["data"]
        except Exception:
            pass
        return False

    def get_growth_sign(self):
        url = "https://drive-m.quark.cn/1/clouddrive/capacity/growth/sign"
        querystring = {
            "pr": "ucpro",
            "fr": "android",
            "kps": self.param.get('kps'),
            "sign": self.param.get('sign'),
            "vcode": self.param.get('vcode')
        }
        data = {"sign_cyclic": True}
        try:
            response = self._post(url, querystring, data)
            resp_data = response.get("data")
            if resp_data:
                return True, resp_data.get("sign_daily_reward", 0)
            else:
                return False, response.get("message", "未知错误")
        except Exception as e:
            return False, str(e)

    def do_sign(self):
        log = ""
        brief = ""
        success = False
        growth_info = self.get_growth_info()
        if growth_info:
            cap_sign = growth_info.get("cap_sign", {})
            cap_comp = growth_info.get("cap_composition", {})
            total_capacity = growth_info.get("total_capacity", 0)
            is_vip = growth_info.get("88VIP", False)

            log += (
                f" {'88VIP' if is_vip else '普通用户'} {self.param.get('user', '未知')}\n"
                f"💾 网盘总容量：{convert_bytes(total_capacity)}，"
                f"签到累计容量：")
            if "sign_reward" in cap_comp:
                log += f"{convert_bytes(cap_comp['sign_reward'])}\n"
            else:
                log += "0 MB\n"

            if cap_sign.get("sign_daily"):
                sign_daily_reward = cap_sign.get("sign_daily_reward", 0)
                sign_progress = cap_sign.get("sign_progress", 0)
                sign_target = cap_sign.get("sign_target", 0)
                log += (
                    f"✅ 签到日志: 今日已签到+{convert_bytes(sign_daily_reward)}，"
                    f"连签进度({sign_progress}/{sign_target})\n"
                )
                updated_growth_info = self.get_growth_info()
                if updated_growth_info:
                    updated_comp = updated_growth_info.get("cap_composition", {})
                    updated_total = updated_growth_info.get("total_capacity", 0)
                    log += (
                        f"📊 当前总容量：{convert_bytes(updated_total)}，"
                        f"签到累计容量：{convert_bytes(updated_comp.get('sign_reward', 0))}\n"
                    )
                    total_sign = updated_comp.get("sign_reward", 0)
                    brief = f"今日+{convert_bytes(sign_daily_reward)}\n累计+{convert_bytes(total_sign)}  总空间{convert_bytes(updated_total)}  连签{sign_progress}/{sign_target}"
                success = True
            else:
                sign, sign_return = self.get_growth_sign()
                if sign:
                    sign_progress = cap_sign.get("sign_progress", 0) + 1
                    sign_target = cap_sign.get("sign_target", 0)
                    log += (
                        f"✅ 执行签到: 今日签到+{convert_bytes(sign_return)}，"
                        f"连签进度({sign_progress}/{sign_target})\n"
                    )
                    updated_growth_info = self.get_growth_info()
                    if updated_growth_info:
                        updated_comp = updated_growth_info.get("cap_composition", {})
                        updated_total = updated_growth_info.get("total_capacity", 0)
                        log += (
                            f"📊 当前总容量：{convert_bytes(updated_total)}，"
                            f"签到累计容量：{convert_bytes(updated_comp.get('sign_reward', 0))}\n"
                        )
                        total_sign = updated_comp.get("sign_reward", 0)
                        brief = f"今日+{convert_bytes(sign_return)}\n累计+{convert_bytes(total_sign)}  总空间{convert_bytes(updated_total)}  连签{sign_progress}/{sign_target}"
                    success = True
                else:
                    log += f"❌ 签到异常: {sign_return}\n"
                    brief = f"❌ {sign_return}"
        else:
            log += "❌ 签到异常: 获取成长信息失败\n"
            brief = "❌ 获取成长信息失败"

        if success and not brief:
            brief = "✅ 签到成功"

        return log, brief, success


# ----------------------------------------------------------------------------
# server酱 推送
# ----------------------------------------------------------------------------
def push_serverchan(title, content):
    sendkey = get_sendkey()
    if not sendkey or sendkey == "YOUR_SENDKEY":
        return
    try:
        url = f"https://sctapi.ftqq.com/{sendkey}.send"
        resp = requests.post(url, data={"title": title, "desp": content}, timeout=15)
        data = resp.json()
        if data.get("code") == 0:
            logger.info(f"server酱推送成功: {title}")
        else:
            logger.warning(f"server酱推送失败: {data}")
    except Exception as e:
        logger.warning(f"server酱推送异常: {e}")


# ----------------------------------------------------------------------------
# 签到编排
# ----------------------------------------------------------------------------
def sign_one_user(user):
    uid = user["id"]
    nickname = user["nickname"]
    if is_signed_today(uid):
        msg = f"✅ {nickname} 今日已签到"
        logger.info(msg)
        return {"user_id": uid, "nickname": nickname, "success": True, "message": msg, "already": True}

    url_params = extract_params(user["url"])
    user_data = {"user": nickname, "url": user["url"]}
    user_data.update(url_params)

    try:
        quark = Quark(user_data)
        log, brief, sign_ok = quark.do_sign()
        for line in log.strip().split("\n"):
            logger.info(f"[{nickname}] {line}")
        if sign_ok:
            mark_signed(uid)
            msg = f"🙍🏻‍♂️{nickname}  {brief}"
            logger.info(f"签到成功: {nickname} - {brief}")
            return {"user_id": uid, "nickname": nickname, "success": True, "message": msg, "detail": log, "already": False}
        else:
            msg = f"❌{nickname}  {brief}"
            logger.warning(f"签到失败: {nickname} - {brief}")
            return {"user_id": uid, "nickname": nickname, "success": False, "message": msg, "detail": log, "already": False}
    except Exception as e:
        logger.error(f"签到异常: {nickname} - {e}")
        return {"user_id": uid, "nickname": nickname, "success": False, "message": f"❌{nickname} 签到失败", "detail": str(e), "already": False}


def run_sign(users):
    """对给定用户列表执行签到，返回结果列表。每次结果按 NOTIFY_MODE 推送。"""
    results = []
    for user in users:
        r = sign_one_user(user)
        results.append(r)
        mode = get_notify_mode()
        should_push = (
            mode == "all"
            or (mode == "fail" and not r["success"])
            or (mode == "success" and r["success"])
        )
        if should_push:
            push_serverchan(r["message"], r.get("detail", ""))
    return results


# ----------------------------------------------------------------------------
# Flask 应用
# ----------------------------------------------------------------------------
app = Flask(__name__, template_folder="templates", static_folder="static")
app.json.ensure_ascii = False

scheduler = BackgroundScheduler(timezone=TZ)
_sign_lock = threading.Lock()


def scheduled_job():
    with _sign_lock:
        users = list_users()
        if not users:
            logger.info("定时签到：无用户，跳过")
            return
        unsigned = [u for u in users if not is_signed_today(u["id"])]
        if not unsigned:
            logger.info("定时签到：所有用户今日均已签到")
            return
        logger.info(f"定时签到开始：{len(unsigned)} 个用户待签到")
        results = run_sign(unsigned)
        summary = "\n".join(r["message"] for r in results)
        logger.info(f"定时签到完成:\n{summary}")


def reschedule():
    sch = get_schedule()
    trigger = CronTrigger(hour=sch["hour"], minute=sch["minute"], timezone=TZ)
    # 移除旧任务后重新添加
    if scheduler.get_job("daily_sign"):
        scheduler.remove_job("daily_sign")
    scheduler.add_job(scheduled_job, trigger, id="daily_sign", replace_existing=True)
    logger.info(f"定时任务已设置为每天 {sch['hour']:02d}:{sch['minute']:02d}")


# ----------------------------------------------------------------------------
# 路由
# ----------------------------------------------------------------------------
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/users", methods=["GET"])
def api_list_users():
    signed = signed_status_map()
    users = list_users()
    return jsonify([
        {**u, "signed_today": u["id"] in signed} for u in users
    ])


@app.route("/api/users", methods=["POST"])
def api_add_user():
    data = request.get_json(force=True)
    nickname = (data.get("nickname") or "").strip()
    url = (data.get("url") or "").strip()
    if not nickname:
        return jsonify({"ok": False, "error": "请输入用户昵称"}), 400
    if not url:
        return jsonify({"ok": False, "error": "请输入签到URL"}), 400
    if "kps=" not in url or "sign=" not in url or "vcode=" not in url:
        return jsonify({"ok": False, "error": "URL必须包含kps、sign、vcode参数"}), 400
    add_user(nickname, url)
    logger.info(f"添加用户: {nickname}")
    return jsonify({"ok": True})


@app.route("/api/users/<uid>", methods=["DELETE"])
def api_delete_user(uid):
    delete_user(uid)
    logger.info(f"删除用户: {uid}")
    return jsonify({"ok": True})


@app.route("/api/sign/one/<uid>", methods=["POST"])
def api_sign_one(uid):
    user = get_user(uid)
    if not user:
        return jsonify({"ok": False, "error": "用户不存在"}), 404
    r = sign_one_user(user)
    mode = get_notify_mode()
    should_push = (
        mode == "all"
        or (mode == "fail" and not r["success"])
        or (mode == "success" and r["success"])
    )
    if should_push:
        push_serverchan(r["message"], r.get("detail", ""))
    return jsonify({"ok": True, "result": r})


@app.route("/api/sign/all", methods=["POST"])
def api_sign_all():
    users = list_users()
    if not users:
        return jsonify({"ok": False, "error": "请先添加用户"}), 400
    results = run_sign(users)
    return jsonify({"ok": True, "results": results})


@app.route("/api/sign/selected", methods=["POST"])
def api_sign_selected():
    data = request.get_json(force=True)
    ids = data.get("ids", [])
    users = [u for u in list_users() if u["id"] in ids]
    if not users:
        return jsonify({"ok": False, "error": "未选择用户"}), 400
    results = run_sign(users)
    return jsonify({"ok": True, "results": results})


@app.route("/api/schedule", methods=["GET"])
def api_get_schedule():
    return jsonify(get_schedule())


@app.route("/api/schedule", methods=["POST"])
def api_set_schedule():
    data = request.get_json(force=True)
    try:
        hour = int(data.get("hour", 0))
        minute = int(data.get("minute", 0))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "时间格式错误"}), 400
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return jsonify({"ok": False, "error": "时间超出范围"}), 400
    set_setting("schedule", json.dumps({"hour": hour, "minute": minute}))
    reschedule()
    logger.info(f"调整定时签到时间为 {hour:02d}:{minute:02d}")
    return jsonify({"ok": True, "schedule": {"hour": hour, "minute": minute}})


@app.route("/api/notify_mode", methods=["GET"])
def api_get_notify_mode():
    return jsonify({"notify_mode": get_notify_mode()})


@app.route("/api/notify_mode", methods=["POST"])
def api_set_notify_mode():
    data = request.get_json(force=True)
    mode = data.get("notify_mode", "all")
    if mode not in ("all", "fail", "success"):
        return jsonify({"ok": False, "error": "无效的通知模式"}), 400
    set_setting("notify_mode", mode)
    logger.info(f"通知模式调整为: {mode}")
    return jsonify({"ok": True, "notify_mode": mode})


@app.route("/api/serverchan", methods=["GET"])
def api_get_serverchan():
    return jsonify({"sendkey": get_sendkey()})


@app.route("/api/serverchan", methods=["POST"])
def api_set_serverchan():
    data = request.get_json(force=True)
    sendkey = (data.get("sendkey") or "").strip()
    set_setting("serverchan_sendkey", sendkey)
    logger.info("Server酱 SendKey 已更新")
    return jsonify({"ok": True, "sendkey": sendkey})


@app.route("/api/serverchan/test", methods=["POST"])
def api_test_serverchan():
    data = request.get_json(silent=True) or {}
    sendkey = (data.get("sendkey") or "").strip() or get_sendkey()
    if not sendkey:
        return jsonify({"ok": False, "error": "SendKey 为空"}), 400
    try:
        url = f"https://sctapi.ftqq.com/{sendkey}.send"
        resp = requests.post(
            url,
            data={"title": "夸克签到助手 · 测试推送", "desp": "如果你收到这条消息，说明 Server酱 配置成功 ✅"},
            timeout=15,
        )
        result = resp.json()
        if result.get("code") == 0:
            logger.info("Server酱 测试推送成功")
            return jsonify({"ok": True, "message": "测试推送成功，请查收"})
        logger.warning(f"Server酱 测试推送失败: {result}")
        return jsonify({"ok": False, "error": result.get("message", "推送失败")}), 400
    except Exception as e:
        logger.warning(f"Server酱 测试推送异常: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/logs", methods=["GET"])
def api_logs():
    days = int(request.args.get("days", 3))
    logs = get_recent_logs(days=days)
    return jsonify(logs)


@app.route("/api/status", methods=["GET"])
def api_status():
    job = scheduler.get_job("daily_sign")
    next_run = None
    if job and job.next_run_time:
        next_run = job.next_run_time.strftime("%Y-%m-%d %H:%M:%S")
    signed = signed_status_map()
    users = list_users()
    return jsonify({
        "schedule": get_schedule(),
        "notify_mode": get_notify_mode(),
        "sendkey": get_sendkey(),
        "next_run": next_run,
        "total_users": len(users),
        "signed_today": sum(1 for u in users if u["id"] in signed),
    })


# 文件日志下载（便于排查）
@app.route("/quark_sign.log")
def view_file_log():
    return send_from_directory(DATA_DIR, "quark_sign.log", mimetype="text/plain")


# ----------------------------------------------------------------------------
# 启动
# ----------------------------------------------------------------------------
def main():
    init_db()
    # 启动时若数据来自环境变量且与原设置不同，以 DB 为准
    scheduler.start()
    reschedule()
    # 启动后做一次启动检查（原仓库 _startup_check 的简化版）
    logger.info("夸克签到 Web 服务启动")
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
