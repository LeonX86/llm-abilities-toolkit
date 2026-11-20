#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CheapV2ray / SSPanel-UIM 每日自动签到（青龙面板适配）

流程：随机延迟 -> 登录 -> 记录签到前流量 -> 签到 -> 记录签到后流量 -> 计算获得流量
      -> 打印青龙日志 -> 调用青龙 notify.py 推送飞书（或其它已配置渠道）

接口（已在本站实测）：
    POST {base}/auth/login       表单 email / passwd      -> {"ret":1,"msg":"登录成功"}
    POST {base}/user/checkin     需登录 Cookie            -> {"ret":1/0,"msg":"..."}
    GET  {base}/user             用户首页，含 Hi, 用户名 与剩余流量

通知格式（一行一个账号，整行不超过 14 个中文字 = 28 半角格）：
    成功签到 2/2 个V2ray账号
    ✅ 账号A               +0MB
    ✅ 账号B               +704MB

环境变量（账号任选一种）：
1) 多账号，用中文或英文分号分隔，每个账号是「邮箱|密码」：
   CV2R_ACCOUNTS="user1@example.com|密码A；user2@example.com|密码B"
2) 单账号：
   CV2R_EMAIL / CV2R_PASSWORD

可选：
   CV2R_DELAY_MAX=10    每个账号开始前的随机延迟上限（秒），默认 0~10 秒
   CV2R_NOTIFY=路径     notify.py 路径（import 失败时的子进程回退用）
   CV2R_NO_NOTIFY=1     跳过通知推送
   CV2R_INSECURE=1      强制跳过证书校验
   CV2R_TIMEOUT=20      单次请求超时秒数
   CV2R_URL=地址        登录地址（代码里不再内置）

退出码：0 正常（含"今日已签到"），1 有账号失败或配置缺失。
"""

import json
import os
import random
import re
import subprocess
import sys
import time
import unicodedata
from urllib.parse import urlparse

try:  # 青龙面板里可能没装 requests
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
except ImportError as exc:  # pragma: no cover
    print(f"缺少依赖 {exc}，请先执行：pip install requests", flush=True)
    raise SystemExit(1)

# 登录地址不写死在仓库里，运行时通过 CV2R_URL 环境变量提供
BASE_URL = ""

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
FLOW_UNITS = {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3, "TB": 1024**4, "PB": 1024**5}

# 状态图标：绿色 ✅ 新签到成功 / 蓝色 ☑️ 今日已签到 / 红色 ❌ 失败
ICON_OK = "✅"
ICON_ALREADY = "☑️"
ICON_FAIL = "❌"

# 通知每行最多 14 个中文字 = 28 个半角格
MAX_LINE_UNITS = 28
# 每个账号开始前的随机延迟上限（秒）
ACCOUNT_DELAY_MAX = 10.0


def log(msg=""):
    print(msg, flush=True)


# ---------------------------------------------------------------- 显示宽度工具


def _display_units(text):
    """中文/全角算 2 格，其余算 1 格。"""
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text)


def _truncate_units(text, limit):
    if _display_units(text) <= limit:
        return text
    budget = limit - 1  # 给「…」留一格
    out = []
    used = 0
    for ch in text:
        width = 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
        if used + width > budget:
            break
        out.append(ch)
        used += width
    return "".join(out) + "…"


def _pad_units(text, width):
    return text + " " * max(0, width - _display_units(text))


def _pad_left(text, width):
    return " " * max(0, width - _display_units(text)) + text


# ---------------------------------------------------------------- HTTP


def build_session(timeout, insecure):
    if insecure:
        from urllib3.exceptions import InsecureRequestWarning

        requests.packages.urllib3.disable_warnings(InsecureRequestWarning)
    s = requests.Session()
    s.headers.update(
        {
            "User-Agent": UA,
            "Accept": "application/json, text/javascript, text/html, */*; q=0.01",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "X-Requested-With": "XMLHttpRequest",
        }
    )
    retry_kwargs = dict(
        total=3,
        connect=3,
        read=3,
        backoff_factor=1.5,
        status_forcelist=(500, 502, 503, 504),
    )
    try:
        retry = Retry(allowed_methods=frozenset(["GET", "POST"]), **retry_kwargs)
    except TypeError:  # urllib3 < 1.26
        retry = Retry(method_whitelist=frozenset(["GET", "POST"]), **retry_kwargs)
    s.mount("https://", HTTPAdapter(max_retries=retry))
    s.mount("http://", HTTPAdapter(max_retries=retry))
    s.timeout = timeout
    s.verify = not insecure
    return s


def parse_traffic(html):
    """返回 (剩余流量原文, 今日已用原文, 剩余字节数)。"""
    left = used = None
    m = re.search(
        r"<h4>\s*剩余流量\s*</h4>(.{0,400}?)<span[^>]*class=\"counter\"[^>]*>\s*([\d.]+)\s*</span>\s*([KMGTP]?B)",
        html,
        re.S | re.I,
    )
    if m:
        left = f"{m.group(2)} {m.group(3).upper()}"
    else:
        mm = re.search(r"剩余流量[\s\S]{0,400}?([\d.]+)\s*([KMGTP]?B)", html, re.I)
        if mm:
            left = f"{mm.group(1)} {mm.group(2).upper()}"
    m = re.search(r"今日已用[:：]\s*([\d.]+\s*[KMGTP]?B)", html, re.I)
    if m:
        used = re.sub(r"\s+", "", m.group(1)).upper()

    left_bytes = None
    if left:
        mm = re.match(r"([\d.]+)\s*([KMGTP]?B)", left, re.I)
        if mm:
            left_bytes = float(mm.group(1)) * FLOW_UNITS.get(mm.group(2).upper(), 1)
    return left, used, left_bytes


def parse_username(html):
    """从用户首页的 “Hi, 用户名” 提取账号显示名，取不到返回 None。"""
    m = re.search(r"Hi,\s*([^<]{1,40}?)</div>", html)
    if m:
        name = m.group(1).strip().strip(",，!")
        if name:
            return name
    return None


def parse_json(resp):
    try:
        data = resp.json()
        return data if isinstance(data, dict) else None
    except ValueError:
        return None


def human_gain(size):
    """紧凑流量文案：1.49 GB / 704 MB / 512 KB / 0 B。"""
    if not size:
        return "0 B"
    if size >= FLOW_UNITS["GB"]:
        return f"{size / FLOW_UNITS['GB']:.2f} GB"
    if size >= FLOW_UNITS["MB"]:
        return f"{size / FLOW_UNITS['MB']:.0f} MB"
    if size >= FLOW_UNITS["KB"]:
        return f"{size / FLOW_UNITS['KB']:.0f} KB"
    return f"{int(size)} B"


def flow_bytes(text):
    """把 "704 MB" 这样的文本换算成字节。"""
    m = re.match(r"([\d.]+)\s*([KMGTP]?B)", text or "", re.I)
    if not m:
        return 0.0
    return float(m.group(1)) * FLOW_UNITS.get(m.group(2).upper(), 1)


def login(s, base, email, password):
    url = base.rstrip("/") + "/auth/login"
    resp = s.post(
        url,
        data={"email": email, "passwd": password, "code": ""},
        headers={"Referer": url},
        timeout=s.timeout,
        allow_redirects=True,
    )
    data = parse_json(resp)
    if data is None:
        return False, f"登录返回非 JSON（HTTP {resp.status_code}），可能是站点异常或被 WAF 拦截"
    if int(data.get("ret", 0)) == 1:
        return True, data.get("msg", "登录成功")
    msg = str(data.get("msg", "登录失败"))
    if "两步" in msg or "2fa" in msg.lower() or "验证码" in msg:
        msg += "（账号启用了两步验证，脚本无法自动完成）"
    return False, msg


def checkin(s, base):
    url = base.rstrip("/") + "/user/checkin"
    resp = s.post(url, headers={"Referer": base.rstrip("/") + "/user"}, timeout=s.timeout)
    data = parse_json(resp)
    if data is None:
        return None, f"签到返回非 JSON（HTTP {resp.status_code}）"
    return data, None


def fetch_user_page(s, base):
    resp = s.get(base.rstrip("/") + "/user", timeout=s.timeout)
    if "/auth/login" in resp.url:
        return None
    return resp.text


def extract_gain(msg):
    """从签到返回文案中提取流量，例如 “你获得了 704 MB流量”。"""
    if not msg:
        return None
    m = re.search(r"([\d.]+)\s*([KMGTP]?B)", msg, re.I)
    return f"{m.group(1)} {m.group(2).upper()}" if m else None


# ---------------------------------------------------------------- 单账号流程


def run_account(base, email, password, timeout, insecure):
    """执行单个账号并返回结构化结果，供日志与通知分别渲染。"""
    r = {
        "base": base,
        "email": email,
        "name": email.split("@")[0],
        "ok": False,
        "login_ok": False,
        "login_msg": "",
        "before_left": None,
        "before_used": None,
        "after_left": None,
        "after_used": None,
        "status": "failed",  # success / already / failed
        "gain": None,
        "api_gain": None,
        "msg": "",
        "error": None,
    }

    s = build_session(timeout, insecure)
    ok, msg = login(s, base, email, password)
    r["login_ok"], r["login_msg"] = ok, msg
    log(f"[登录{ICON_OK if ok else ICON_FAIL}] {'成功' if ok else '失败'}：{msg}")
    if not ok:
        r["error"] = msg
        return r

    home = fetch_user_page(s, base)
    if home is None:
        r["error"] = "登录态无效，无法打开用户首页"
        log(f"[检查{ICON_FAIL}] 登录态无效，无法打开用户首页")
        return r

    name = parse_username(home)
    if name:
        r["name"] = name
    r["before_left"], r["before_used"], before_bytes = parse_traffic(home)
    log(f"[签到前] 剩余流量 {r['before_left'] or '未解析'}；今日已用 {r['before_used'] or '未解析'}")

    data, err = checkin(s, base)
    if err:
        r["error"] = err
        log(f"[签到{ICON_FAIL}] 失败：{err}")
        return r

    ret = int(data.get("ret", 0))
    raw_msg = str(data.get("msg", ""))
    r["msg"] = raw_msg
    already = "已经签到" in raw_msg or "已签到" in raw_msg

    if already or (ret == 0 and "签到" in raw_msg):
        r["status"] = "already"
        log(f"[签到{ICON_ALREADY}] {raw_msg or '今日已签到'}")
    elif ret == 1:
        r["status"] = "success"
        r["api_gain"] = extract_gain(raw_msg)
        r["gain"] = r["api_gain"]
        log(f"[签到{ICON_OK}] {raw_msg or '签到成功'}")
    else:
        r["status"] = "failed"
        r["error"] = raw_msg or f"签到接口返回 ret={ret}"
        log(f"[签到{ICON_FAIL}] 失败：{r['error']}")

    after_home = fetch_user_page(s, base)
    if after_home:
        r["after_left"], r["after_used"], after_bytes = parse_traffic(after_home)
        log(f"[签到后] 剩余流量 {r['after_left'] or '未解析'}；今日已用 {r['after_used'] or '未解析'}")
        if r["status"] == "success":
            diff = None
            if before_bytes is not None and after_bytes is not None:
                diff = after_bytes - before_bytes
            if diff is not None and diff > 0:
                # 通知里统一显示实际到账，接口文案只留在日志核对
                r["gain"] = human_gain(diff)
                log(f"[本次获得] {r['gain']}（剩余流量差值；接口文案 {r['api_gain'] or '无'}）")
            elif r["api_gain"]:
                log(f"[本次获得] {r['api_gain']}（页面差值 "
                    f"{human_gain(diff) if diff is not None else '未知'}，差值不可用，退回接口文案）")

    r["ok"] = r["status"] in ("success", "already")
    log(f"[账号结果{ICON_OK if r['ok'] else ICON_FAIL}] {r['name']} "
        f"{'正常' if r['ok'] else '失败'}")
    return r


# ---------------------------------------------------------------- 通知格式


def _account_label(r):
    return r["name"] or r["email"].split("@")[0]


def _status_token(r):
    """一行里的状态列：成功显示到账流量，已签到显示“已签”，失败显示短原因。"""
    if r["status"] == "success":
        gain = (r["gain"] or "").replace(" ", "")
        return f"+{gain}" if gain else "+成功"
    if r["status"] == "already":
        return "已签"
    if not r["login_ok"]:
        return "登录失败"
    return "签到失败"


def format_compact(results):
    """紧凑格式：标题 + 每账号一行，单行不超过 28 半角格。"""
    total = len(results)
    ok = sum(1 for r in results if r["ok"])
    title = f"成功签到 {ok}/{total} 个V2ray账号"

    tokens = [_status_token(r) for r in results]
    needed = max((_display_units(t) for t in tokens), default=0)
    status_width = min(10, max(needed, 4))
    name_width = MAX_LINE_UNITS - 2 - 1 - 2 - status_width

    lines = []
    for r, token in zip(results, tokens):
        icon = ICON_OK if r["status"] == "success" else (
            ICON_ALREADY if r["status"] == "already" else ICON_FAIL
        )
        name = _truncate_units(_account_label(r), name_width)
        status = _truncate_units(token, status_width)
        lines.append(f"{icon} {_pad_units(name, name_width)}  {_pad_left(status, status_width)}")

    return title, "\n".join(lines)


# ---------------------------------------------------------------- 通知发送


def find_notify():
    """定位青龙 notify.py：环境变量优先，其次常见路径。"""
    candidates = []
    env_path = os.environ.get("CV2R_NOTIFY", "").strip()
    if env_path:
        candidates.append(env_path)
    candidates += [
        "/ql/scripts/notify.py",
        "/ql/data/scripts/notify.py",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "notify.py"),
    ]
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    return None


def _load_ql_notify():
    """按青龙标准方式 import notify 拿到 send()；失败返回 None。

    青龙 notify.py 会在正文后追加一条随机一言，这里在 import 前把 HITOKOTO 关掉，
    只影响当前进程，不改动面板里的 notify.py。
    """
    os.environ["HITOKOTO"] = "false"
    for path in (
        os.path.dirname(os.path.abspath(__file__)),
        "/ql/scripts",
        "/ql/data/scripts",
    ):
        if path and os.path.isdir(path) and path not in sys.path:
            sys.path.append(path)
    try:
        import notify as ql_notify  # noqa: WPS433
    except Exception as exc:  # noqa: BLE001
        log(f"[通知] import notify 失败：{type(exc).__name__}: {exc}")
        return None
    try:
        if getattr(ql_notify, "push_config", None) is not None:
            ql_notify.push_config["HITOKOTO"] = False
    except Exception:  # noqa: BLE001
        pass
    return ql_notify if hasattr(ql_notify, "send") else None


def send_notify(title, content):
    if os.environ.get("CV2R_NO_NOTIFY", "").strip().lower() in ("1", "true", "yes", "on"):
        log("[通知] 已通过 CV2R_NO_NOTIFY 关闭，跳过推送")
        return False

    mod = _load_ql_notify()
    if mod is not None:
        try:
            mod.send(title, content)
            log(f"[通知{ICON_OK}] 已通过青龙 notify.send 推送")
            return True
        except Exception as exc:  # noqa: BLE001
            log(f"[通知{ICON_FAIL}] notify.send 异常：{type(exc).__name__}: {exc}")

    notify_path = find_notify()
    if not notify_path:
        log("[通知] 未找到可用的 notify.py（可用 CV2R_NOTIFY 指定路径），跳过推送")
        return False

    try:
        child_env = dict(os.environ)
        child_env["HITOKOTO"] = "false"
        proc = subprocess.run(
            [sys.executable, notify_path, title, content],
            capture_output=True,
            text=True,
            timeout=90,
            env=child_env,
        )
    except Exception as exc:  # noqa: BLE001
        log(f"[通知{ICON_FAIL}] 调用 notify.py 出错：{type(exc).__name__}: {exc}")
        return False

    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    if out:
        log(f"[通知] {out}")
    if err:
        log(f"[通知] {err}")
    if proc.returncode == 0:
        log(f"[通知{ICON_OK}] 已调用 notify.py 推送：{notify_path}")
        return True
    log(f"[通知{ICON_FAIL}] notify.py 退出码 {proc.returncode}")
    return False


# ---------------------------------------------------------------- 配置读取


def load_accounts():
    """账号格式：邮箱|密码，多账号用 ；或 ; 分隔。登录地址由 CV2R_URL 指定。"""
    raw = (os.environ.get("CV2R_ACCOUNTS") or os.environ.get("CHEAPV2RAY_ACCOUNTS") or "").strip()

    accounts = []
    if raw:
        for chunk in re.split(r"[;；]", raw):
            chunk = chunk.strip()
            if not chunk or chunk.startswith("#"):
                continue
            if "|" not in chunk:
                log(f"[配置] 跳过无法解析的账号：{chunk[:40]}")
                continue
            email, password = chunk.split("|", 1)
            email, password = email.strip(), password.strip()
            if not email or not password:
                log(f"[配置] 跳过缺少邮箱或密码的账号：{chunk[:40]}")
                continue
            accounts.append((email, password))

    if not accounts:
        email = os.environ.get("CV2R_EMAIL", "").strip()
        password = os.environ.get("CV2R_PASSWORD", "").strip()
        if email and password:
            accounts.append((email, password))

    return accounts


def main():
    timeout = float(os.environ.get("CV2R_TIMEOUT", "20") or 20)
    insecure = os.environ.get("CV2R_INSECURE", "").strip().lower() in ("1", "true", "yes", "on")
    try:
        delay_max = float(os.environ.get("CV2R_DELAY_MAX", str(ACCOUNT_DELAY_MAX)) or 0)
    except ValueError:
        delay_max = ACCOUNT_DELAY_MAX
    delay_max = max(0.0, delay_max)

    base = (os.environ.get("CV2R_URL", "").strip() or BASE_URL).rstrip("/")
    if urlparse(base).scheme not in ("http", "https"):
        log(f"[配置] 登录地址缺少 http(s) 前缀：{base}")
        return 1

    accounts = load_accounts()
    if not accounts:
        log('未找到账号配置：请设置环境变量 CV2R_ACCOUNTS="邮箱|密码；邮箱|密码"')
        return 1

    log(f"共 {len(accounts)} 个账号，登录地址 {base}")
    log(f"开始时间：{time.strftime('%Y-%m-%d %H:%M:%S')}")

    results = []
    for idx, (email, password) in enumerate(accounts, 1):
        delay = random.uniform(0, delay_max) if delay_max > 0 else 0.0
        log("")
        log(f"---- [{idx}/{len(accounts)}] {email}（随机延迟 {delay:.3f} 秒）----")
        if delay > 0:
            time.sleep(delay)
        try:
            results.append(run_account(base, email, password, timeout, insecure))
        except requests.exceptions.SSLError:
            if insecure:
                log(f"[错误{ICON_FAIL}] SSL 校验失败，且已设置 CV2R_INSECURE，请检查网络中间设备")
                results.append(_failed_result(base, email, "SSL 校验失败"))
            else:
                log("[提示] SSL 校验失败，正在以跳过校验方式重试…")
                try:
                    results.append(run_account(base, email, password, timeout, True))
                except Exception as exc:  # noqa: BLE001
                    log(f"[错误{ICON_FAIL}] {type(exc).__name__}: {exc}")
                    results.append(_failed_result(base, email, str(exc)))
        except Exception as exc:  # noqa: BLE001
            log(f"[错误{ICON_FAIL}] {type(exc).__name__}: {exc}")
            results.append(_failed_result(base, email, str(exc)))

    title, content = format_compact(results)
    log("")
    log(title)
    log(content)
    send_notify(title, content)

    return 0 if all(r["ok"] for r in results) else 1


def _failed_result(base, email, error):
    return {
        "base": base,
        "email": email,
        "name": email.split("@")[0],
        "ok": False,
        "login_ok": False,
        "login_msg": error,
        "before_left": None,
        "before_used": None,
        "after_left": None,
        "after_used": None,
        "status": "failed",
        "gain": None,
        "msg": "",
        "error": error,
    }


if __name__ == "__main__":
    sys.exit(main())