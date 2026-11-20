#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MoliGate 青龙面板版（QingLong 2.x，单文件自包含）。

和 Docker 版的区别只有四点：

1. 一次运行只做一次「查流水 → 缺奖励就请求登录接口 → 复查流水」，不做常驻调度，
   什么时候跑由青龙的「定时规则」决定；
2. `MS_TOKEN` 可以用英文分号（`;`，中文 `；` 也认）分隔多个账号，一次运行依次处理，
   账号之间随机等 0~10 秒（`ACCOUNT_DELAY` 调上限），避免几个账号挤在同一秒打登录接口；
3. 通知优先复用 scripts 目录里的青龙 `notify.py`（装了它就自动用），
   找不到时回落到自带的 `FEISHU_WEBHOOK` 直推；多个账号合并成**一条**汇总消息，
   避免一个 cron 刷屏；
4. 同一天只要某个账号处理成功过，重复触发不会再为它请求登录接口，只补发一条
   「本次跳过」提醒（手动点「运行」也不会重复请求登录接口，除非加 `--force`）。

部署步骤、环境变量、定时规则见 docs/qinglong.md。
本文件不依赖同目录的 moligate 包，直接拷到 /ql/data/scripts 下即可运行。
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import inspect
import json
import logging
import os
import random
import re
import sys
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

try:
    import requests
except ImportError:  # 青龙里几乎不会发生；换机器时给一句能操作的提示
    print("缺少 requests 库：青龙面板 → 依赖管理 → python3 → 安装 requests")
    raise SystemExit(2)

log = logging.getLogger("moligate")

# ---------------------------------------------------------------- 常量

DEFAULT_BASE_URL = "https://modelscope.cn"

RULE_DAILY_ACTIVE = "daily_active"
RULE_ALIYUN_BIND = "aliyun_bindlogin"
DAILY_RULES = (RULE_DAILY_ACTIVE, RULE_ALIYUN_BIND)

STATUS_DONE = "done"
STATUS_CLAIMED = "claimed"
STATUS_INACTIVE = "inactive"
STATUS_FAILED = "failed"
STATUS_SKIPPED = "skipped"
SETTLED_STATUSES = (STATUS_DONE, STATUS_CLAIMED, STATUS_INACTIVE)

STATUS_LABEL = {
    STATUS_CLAIMED: "✅ ModelScope 登录成功，魔粒已到账",
    STATUS_DONE: "☑️ ModelScope 今日已领取",
    STATUS_INACTIVE: "⚠️ ModelScope 登录成功，但未检测到奖励",
    STATUS_FAILED: "❌ ModelScope 登录失败",
    STATUS_SKIPPED: "☑️ ModelScope 今日已处理，本次跳过",
}

# 单账号消息里用完整标题；多账号汇总是按行汇总，用短的
SHORT_LABEL = {
    STATUS_CLAIMED: "✅ 已领取",
    STATUS_DONE: "☑️ 今日已领",
    STATUS_INACTIVE: "⚠️ 登录了但没奖励",
    STATUS_FAILED: "❌ 失败",
    STATUS_SKIPPED: "☑️ 已处理，跳过",
}

DEFAULT_ACCOUNT_DELAY = 10

MAX_RUNS = 30

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)

_LIST_KEYS = ("transactions", "records", "list", "items", "rows", "data")
_DATA_KEYS = ("data", "Data")
_NICKNAME_KEYS = ("nickname", "user_nickname", "displayname", "username", "user_name", "name")
_AMOUNT_KEYS = ("amount", "total_amount", "change_amount", "value", "quantity", "num")
_TIME_KEYS = ("occurred_at", "gmt_created", "created_at", "create_time", "gmt_create", "time", "timestamp")


# ---------------------------------------------------------------- 异常与数据


class ConfigError(RuntimeError):
    """配置缺失或不合法。"""


class ModelScopeError(RuntimeError):
    """ModelScope 接口返回了非预期结果。"""


class AuthError(ModelScopeError):
    """Token 失效 / 被拒绝（401）。"""


class RiskControlError(ModelScopeError):
    """403：多半是风控，不代表 Token 失效。"""


class NotifyError(RuntimeError):
    """通知推送失败。"""


@dataclass(frozen=True)
class Txn:
    """一条魔粒流水。服务端按 (日期, 类型, rule_key, model_tier) 聚合，次数记在 count 上。"""

    id: str
    type: str
    rule: str
    amount: float
    occurred_at: Any = None
    expire_at: Any = None
    count: int = 1

    @property
    def is_earn(self) -> bool:
        return "EARN" in self.type

    @property
    def is_spend(self) -> bool:
        return "SPEND" in self.type

    @property
    def is_expire(self) -> bool:
        return "EXPIRE" in self.type

    @property
    def is_daily(self) -> bool:
        rule = self.rule.lower()
        return self.is_earn and any(key in rule for key in DAILY_RULES)


@dataclass
class RunResult:
    status: str
    nickname: str | None = None
    balance: dict = field(default_factory=dict)
    earn: float = 0.0
    spend: float = 0.0
    spend_calls: int = 0
    expire: float = 0.0
    detail: str = ""
    error: str | None = None


# ---------------------------------------------------------------- 配置


def parse_env_text(text: str) -> dict[str, str]:
    """解析 KEY=VALUE 形式的环境变量文件，忽略注释、空行和引号。"""
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip().lstrip("\ufeff")
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        name, _, value = line.partition("=")
        name = name.strip()
        if not name:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        values[name] = value
    return values


def load_env_file_into(env: dict[str, str]) -> Path | None:
    """`MOLIGATE_ENV` 指向的文件会补进环境变量（已存在的变量优先）。

    青龙的常规用法是在面板里配环境变量，这个口子只是给「想把配置放文件里」的人用。
    """
    raw = (env.get("MOLIGATE_ENV") or "").strip()
    if not raw:
        return None
    path = Path(raw).expanduser()
    if not path.is_file():
        log.warning("MOLIGATE_ENV 指向的文件不存在：%s", path)
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        log.warning("读取 %s 失败：%s", path, exc)
        return None
    for name, value in parse_env_text(text).items():
        if value:
            env.setdefault(name, value)
    return path


def normalize_token(raw: str) -> str:
    """允许直接粘贴整段 Cookie，自动取出其中的 m_session_id。"""
    text = raw.strip()
    if "m_session_id" not in text:
        return text
    for part in text.split(";"):
        name, _, value = part.strip().partition("=")
        if name.strip() == "m_session_id" and value.strip():
            return value.strip()
    return text


_TOKEN_SEPARATOR = re.compile(r"[;；\s]+")


def parse_tokens(raw: str) -> list:
    """把 `MS_TOKEN` 拆成多个账号的 Token 列表，顺序保留、去重（按指纹）。

    分隔符：英文分号 `;`、中文分号 `；`，以及空白/换行。之所以要自己做这一步：
    青龙面板不会按分号拆环境变量（那是 `notify.py` 读 SMTP/WXPUSHER 时才有的约定），
    整串原样注入给脚本，所以拆分只能在这里做。

    一个片段里如果带 `m_session_id=`，说明它是整段 Cookie：先把 Cookie 里的
    `m_session_id` 取出来，再把剩下的属性逐个丢掉，否则 `h_uid=1` 这种属性会被
    当成一个独立 Token。Token 本身不含分隔符，所以不会误拆。
    """
    pieces = [piece for piece in _TOKEN_SEPARATOR.split(raw.strip()) if piece]
    # 出现 m_session_id 才按 Cookie 处理；拆出来的 `h_uid=1`、`csrf_token=x` 都是属性，不是账号
    cookie_mode = any("m_session_id" in piece for piece in pieces)

    tokens: list = []
    seen: set = set()
    for piece in pieces:
        token = normalize_token(piece)
        if not token:
            continue
        if cookie_mode and token == piece and "=" in piece:
            continue
        key = fingerprint(token)
        if key in seen:
            continue
        seen.add(key)
        tokens.append(token)
    return tokens


def fingerprint(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:12]


def default_data_dir(env: Mapping[str, str]) -> Path:
    """青龙的数据目录：优先 $QL_DIR/data/moligate，其次 /ql/data/moligate。"""
    ql_dir = (env.get("QL_DIR") or "").strip()
    if ql_dir:
        return Path(ql_dir).expanduser() / "data" / "moligate"
    if Path("/ql/data").is_dir():
        return Path("/ql/data/moligate")
    return Path(__file__).resolve().parent / "data"


def _env(env: Mapping[str, str], name: str, default: str | None = None) -> str | None:
    raw = env.get(name)
    if raw is None:
        return default
    raw = str(raw).strip()
    return raw or default


def _env_int(env: Mapping[str, str], name: str, default: int, minimum: int = 0) -> int:
    raw = _env(env, name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} 需要是整数，当前为 {raw!r}") from exc
    if value < minimum:
        raise ConfigError(f"{name} 不能小于 {minimum}，当前为 {value}")
    return value


@dataclass(frozen=True)
class Settings:
    tokens: tuple = ()
    base_url: str = DEFAULT_BASE_URL
    tz: ZoneInfo | None = None
    data_dir: Path | None = None
    webhook: str | None = None
    webhook_secret: str | None = None
    notify_mode: str = "auto"
    random_delay_min: int = 0
    account_delay: int = DEFAULT_ACCOUNT_DELAY
    log_level: str = "INFO"

    @property
    def single(self) -> bool:
        return len(self.tokens) == 1

    @property
    def account_keys(self) -> list:
        return [fingerprint(token) for token in self.tokens]

    @property
    def tz_name(self) -> str:
        return getattr(self.tz, "key", str(self.tz))

    def masked_token(self, index: int = 0) -> str:
        token = self.tokens[index]
        if len(token) <= 10:
            return token[:2] + "***"
        return f"{token[:5]}***{token[-4:]}"

    @property
    def state_path(self) -> Path:
        return (self.data_dir or Path(".")) / "state.json"

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "Settings":
        raw_token = _env(env, "MS_TOKEN")
        if not raw_token:
            raise ConfigError("缺少 MS_TOKEN（青龙 → 环境变量 → 新建，值填 m_session_id；多个账号用英文分号隔开）")
        tokens = parse_tokens(raw_token)
        if not tokens:
            raise ConfigError("MS_TOKEN 里没有解析出有效 Token")

        tz_name = _env(env, "TZ", "Asia/Shanghai") or "Asia/Shanghai"
        try:
            tz = ZoneInfo(tz_name)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ConfigError(f"TZ 不是合法时区：{tz_name!r}") from exc

        notify_mode = (_env(env, "MOLIGATE_NOTIFY", "auto") or "auto").lower()
        if notify_mode not in ("auto", "ql", "feishu", "none"):
            raise ConfigError(f"MOLIGATE_NOTIFY 只能是 auto / ql / feishu / none，当前为 {notify_mode!r}")

        return cls(
            tokens=tuple(tokens),
            base_url=(_env(env, "MS_BASE_URL", DEFAULT_BASE_URL) or DEFAULT_BASE_URL).rstrip("/"),
            tz=tz,
            data_dir=Path(_env(env, "DATA_DIR") or default_data_dir(env)).expanduser(),
            webhook=_env(env, "FEISHU_WEBHOOK"),
            webhook_secret=_env(env, "FEISHU_SECRET"),
            notify_mode=notify_mode,
            random_delay_min=_env_int(env, "RANDOM_DELAY_MIN", 0),
            account_delay=_env_int(env, "ACCOUNT_DELAY", DEFAULT_ACCOUNT_DELAY),
            log_level=(_env(env, "LOG_LEVEL", "INFO") or "INFO").upper(),
        )


# ---------------------------------------------------------------- 时间


_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%Y/%m/%d %H:%M:%S",
    "%Y-%m-%d",
    "%Y/%m/%d",
)


def parse_datetime(raw: Any, tz: ZoneInfo) -> datetime | None:
    """没有时区的时间一律按配置时区解释（接口返回的是北京时间）。"""
    if raw is None or raw == "" or isinstance(raw, bool):
        return None

    if isinstance(raw, (int, float)):
        value = float(raw)
        if value > 1e11:
            value /= 1000.0
        try:
            return datetime.fromtimestamp(value, tz)
        except (OverflowError, OSError, ValueError):
            return None

    text = str(raw).strip()
    if not text:
        return None

    parsed: datetime | None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        parsed = None
        for fmt in _FORMATS:
            try:
                parsed = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue

    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tz)
    return parsed.astimezone(tz)


def local_date(raw: Any, tz: ZoneInfo) -> date | None:
    parsed = parse_datetime(raw, tz)
    return parsed.date() if parsed else None


# ---------------------------------------------------------------- 响应解析


def _unwrap_data(payload: Any) -> Any:
    if not isinstance(payload, dict):
        return payload
    for key in _DATA_KEYS:
        if key in payload:
            inner = payload[key]
            if isinstance(inner, dict) and isinstance(inner.get("data"), dict):
                return inner["data"]
            return inner
    return payload


def _pick(mapping: Any, *keys: str) -> Any:
    """按 keys 取值，兼容 camelCase / snake_case / 大小写差异。"""
    if not isinstance(mapping, dict):
        return None
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    normalized = {str(k).replace("_", "").lower(): v for k, v in mapping.items()}
    for key in keys:
        value = normalized.get(key.replace("_", "").lower())
        if value is not None:
            return value
    return None


def _to_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def find_nickname(obj: Any, depth: int = 4) -> str | None:
    if depth <= 0:
        return None
    if isinstance(obj, dict):
        normalized: dict[str, Any] = {}
        for key, value in obj.items():
            normalized.setdefault(str(key).replace("_", "").lower(), value)
        for key in _NICKNAME_KEYS:
            value = normalized.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        for value in obj.values():
            found = find_nickname(value, depth - 1)
            if found:
                return found
    elif isinstance(obj, list):
        for value in obj[:3]:
            found = find_nickname(value, depth - 1)
            if found:
                return found
    return None


def parse_transactions(payload: Any) -> list:
    data = _unwrap_data(payload)
    items: list | None = None
    if isinstance(data, dict):
        for key in _LIST_KEYS:
            if isinstance(data.get(key), list):
                items = data[key]
                break
    elif isinstance(data, list):
        items = data
    if items is None and isinstance(payload, dict):
        for key in _LIST_KEYS:
            if isinstance(payload.get(key), list):
                items = payload[key]
                break

    result: list = []
    for raw in items or []:
        if not isinstance(raw, dict):
            continue
        txn_type = str(_pick(raw, "type", "transaction_type", "change_type", "biz_type") or "").upper()
        rule = str(_pick(raw, "rule_key", "rule", "reason", "source", "remark") or "")
        amount = _to_float(_pick(raw, *_AMOUNT_KEYS))
        occurred = _pick(raw, *_TIME_KEYS)
        expire = _pick(raw, "expire_at", "expired_at", "expire_time", "expiration_time")
        txn_id = _pick(raw, "id", "transaction_id", "biz_id", "record_id")
        if txn_id is None:
            txn_id = f"{txn_type}|{rule}|{amount}|{occurred}"
        count = int(_to_float(_pick(raw, "count", "times", "num")))
        result.append(
            Txn(
                id=str(txn_id),
                type=txn_type,
                rule=rule,
                amount=amount,
                occurred_at=occurred,
                expire_at=expire,
                count=count if count > 0 else 1,
            )
        )
    return result


def parse_balance(payload: Any) -> dict:
    data = _unwrap_data(payload)
    if not isinstance(data, dict):
        data = {}
    return {
        "total": _to_float(_pick(data, "total_balance")),
        "available": _to_float(_pick(data, "available_balance")),
        "frozen": _to_float(_pick(data, "frozen_amount")),
    }


# ---------------------------------------------------------------- 接口客户端


class ModelScopeClient:
    def __init__(self, settings: Settings, token: str, timeout: float = 20.0) -> None:
        self._base_url = settings.base_url
        self._timeout = timeout
        self._session = requests.Session()
        self._session.headers.update(
            {
                "User-Agent": USER_AGENT,
                "Accept": "application/json, text/plain, */*",
                "Referer": f"{settings.base_url}/",
                "Cookie": f"m_session_id={token}",
                "Authorization": f"Bearer {token}",
            }
        )

    def _get(self, path: str, **params: Any) -> dict:
        url = f"{self._base_url}{path}"
        try:
            resp = self._session.get(url, params=params or None, timeout=self._timeout)
        except requests.RequestException as exc:
            raise ModelScopeError(f"请求 {path} 失败：{exc}") from exc

        if resp.status_code == 401:
            raise AuthError(f"{path} 返回 401，Token 可能已失效")
        if resp.status_code == 403:
            raise RiskControlError(f"{path} 返回 403，可能是风控拦截，稍后重试")
        if resp.status_code != 200:
            raise ModelScopeError(f"{path} 返回 HTTP {resp.status_code}")

        try:
            payload = resp.json()
        except ValueError as exc:
            raise ModelScopeError(f"{path} 返回的不是 JSON") from exc

        if isinstance(payload, dict) and payload.get("success") is False:
            message = _pick(payload, "message", "msg", "error") or "未知错误"
            raise ModelScopeError(f"{path} 业务失败：{message}")
        return payload if isinstance(payload, dict) else {"data": payload}

    def login_info(self) -> dict:
        """访问登录信息接口，服务端会记一次登录行为（每日奖励靠这个触发）。"""
        return self._get("/api/v1/users/login/info")

    def profile(self) -> dict:
        """只读资料，不产生登录记录，用于显示昵称。"""
        return self._get("/api/v1/users/info")

    def balance(self) -> dict:
        return parse_balance(self._get("/openapi/v1/magicubes/balance"))

    def transactions(self, page: int = 1, page_size: int = 50) -> list:
        payload = self._get("/openapi/v1/magicubes/transactions", page=page, page_size=page_size)
        return parse_transactions(payload)

    def close(self) -> None:
        self._session.close()


# ---------------------------------------------------------------- 领取判定


def _is_today(txn: Txn, tz: ZoneInfo, today: date) -> bool:
    return local_date(txn.occurred_at, tz) == today


def _daily_sums(items: list, tz: ZoneInfo, today: date) -> dict:
    """当天每日奖励按 rule_key 的合计金额。"""
    sums: dict[str, float] = {}
    for txn in items:
        if not txn.is_daily or not _is_today(txn, tz, today):
            continue
        sums[txn.rule] = round(sums.get(txn.rule, 0.0) + txn.amount, 2)
    return sums


def _has_rule_today(items: list, rule: str, tz: ZoneInfo, today: date) -> bool:
    return any(t.is_earn and t.rule.lower() == rule and _is_today(t, tz, today) for t in items)


def _ever_had_rule(items: list, rule: str) -> bool:
    return any(t.is_earn and t.rule.lower() == rule for t in items)


def missing_daily(items: list, tz: ZoneInfo, today: date) -> list:
    """今天还缺哪些每日奖励。

    `daily_active` 只要当天调过推理 API 就会自动到账；`aliyun_bindlogin`
    只有网页登录才发，所以不能看到前者就收工，否则每天白丢 50。
    """
    missing: list[str] = []
    if not _has_rule_today(items, RULE_DAILY_ACTIVE, tz, today):
        missing.append(RULE_DAILY_ACTIVE)
    if _ever_had_rule(items, RULE_ALIYUN_BIND) and not _has_rule_today(items, RULE_ALIYUN_BIND, tz, today):
        missing.append(RULE_ALIYUN_BIND)
    return missing


def _summarize(result: RunResult, items: list, tz: ZoneInfo, today: date) -> None:
    earn = spend = expire = 0.0
    spend_calls = 0
    for txn in items:
        if not _is_today(txn, tz, today):
            continue
        if txn.is_earn:
            earn += txn.amount
        elif txn.is_spend:
            spend += abs(txn.amount)
            spend_calls += txn.count
        elif txn.is_expire:
            expire += abs(txn.amount)
    result.earn = round(earn, 2)
    result.spend = round(spend, 2)
    result.spend_calls = spend_calls
    result.expire = round(expire, 2)


def _fill_balance(result: RunResult, client: ModelScopeClient) -> None:
    try:
        result.balance = client.balance() or {}
    except ModelScopeError as exc:
        log.warning("余额查询失败（不影响领取判定）：%s", exc)
        result.balance = {}


def _nickname_of(payload: dict) -> str | None:
    try:
        return find_nickname(payload)
    except Exception:  # noqa: BLE001 - 昵称只是展示信息
        log.debug("解析昵称失败", exc_info=True)
        return None


def execute(settings: Settings, client: ModelScopeClient, today: date) -> RunResult:
    """执行一次「检查 + 登录 + 复查」。"""
    tz = settings.tz
    try:
        before = client.transactions()
    except AuthError as exc:
        return RunResult(STATUS_FAILED, error=f"认证失败：{exc}", detail="请重新复制 m_session_id")
    except RiskControlError as exc:
        return RunResult(STATUS_FAILED, error=str(exc), detail="风控拦截，稍后再跑一次")
    except ModelScopeError as exc:
        return RunResult(STATUS_FAILED, error=f"查询流水失败：{exc}")

    if not missing_daily(before, tz, today):
        parts = "、".join(f"{rule} {amount:g}" for rule, amount in sorted(_daily_sums(before, tz, today).items()))
        detail = f"今天已经领取过（{parts}），未重复请求登录接口" if parts else "今天已经领取过，未重复请求登录接口"
        result = RunResult(STATUS_DONE, detail=detail)
        try:
            result.nickname = _nickname_of(client.profile())
        except ModelScopeError as exc:
            log.debug("读取账号资料失败：%s", exc)
        _summarize(result, before, tz, today)
        _fill_balance(result, client)
        return result

    before_sums = _daily_sums(before, tz, today)

    try:
        login_payload = client.login_info()
    except AuthError as exc:
        return RunResult(STATUS_FAILED, error=f"登录请求被拒绝：{exc}", detail="Token 可能已失效")
    except RiskControlError as exc:
        return RunResult(STATUS_FAILED, error=str(exc), detail="风控拦截，稍后再跑一次")
    except ModelScopeError as exc:
        return RunResult(STATUS_FAILED, error=f"登录请求失败：{exc}")

    nickname = _nickname_of(login_payload)

    try:
        after = client.transactions()
    except ModelScopeError as exc:
        return RunResult(STATUS_FAILED, nickname=nickname, error=f"登录成功但复查流水失败：{exc}")

    gains = {
        rule: round(total - before_sums.get(rule, 0.0), 2)
        for rule, total in _daily_sums(after, tz, today).items()
        if round(total - before_sums.get(rule, 0.0), 2) > 0
    }

    if gains:
        reward = round(sum(gains.values()), 2)
        detail = "，".join(f"{rule} +{gain:g}" for rule, gain in gains.items())
        result = RunResult(
            STATUS_CLAIMED,
            nickname=nickname,
            detail=f"新到账 {reward:g} 魔粒（{detail}）",
        )
    else:
        result = RunResult(STATUS_INACTIVE, nickname=nickname, detail="登录成功，但流水里没有新的每日奖励")

    _summarize(result, after, tz, today)
    _fill_balance(result, client)
    return result


# ---------------------------------------------------------------- 状态


class StateStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._data = self._load()

    def _empty(self) -> dict:
        return {"version": 1, "accounts": {}, "runs": []}

    def _load(self) -> dict:
        if not self.path.exists():
            return self._empty()
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError) as exc:
            log.warning("读取状态文件失败，按空状态启动：%s", exc)
            return self._empty()
        if not isinstance(data, dict):
            return self._empty()
        data.setdefault("version", 1)
        if not isinstance(data.get("accounts"), dict):
            data["accounts"] = {}
        if not isinstance(data.get("runs"), list):
            data["runs"] = []
        return data

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        with tmp.open("w", encoding="utf-8") as handle:
            json.dump(self._data, handle, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

    def account(self, key: str) -> dict:
        record = self._data["accounts"].get(key)
        return dict(record) if isinstance(record, dict) else {}

    def cached_nickname(self, key: str) -> str | None:
        nickname = self.account(key).get("nickname")
        return nickname if isinstance(nickname, str) and nickname else None

    def record(self, key: str, result: RunResult, now: datetime) -> None:
        accounts = self._data["accounts"]
        record = self.account(key)

        record.update(
            {
                "last_claim_date": now.date().isoformat(),
                "last_status": result.status,
                "last_run_at": now.isoformat(timespec="seconds"),
                "last_error": result.error,
                "last_summary": {
                    "earn": result.earn,
                    "spend": result.spend,
                    "spend_calls": result.spend_calls,
                    "expire": result.expire,
                    "available": (result.balance or {}).get("available"),
                    "total": (result.balance or {}).get("total"),
                    "frozen": (result.balance or {}).get("frozen"),
                    "detail": result.detail,
                },
            }
        )
        if result.nickname:
            record["nickname"] = result.nickname
        if result.status != STATUS_FAILED:
            record["last_ok_at"] = now.isoformat(timespec="seconds")
        accounts[key] = record

        runs = self._data.setdefault("runs", [])
        runs.append(
            {
                "at": now.isoformat(timespec="seconds"),
                "status": result.status,
                "earn": result.earn,
                "spend": result.spend,
                "available": (result.balance or {}).get("available"),
                "detail": result.detail or result.error or "",
            }
        )
        del runs[:-MAX_RUNS]
        self._save()


def already_done_today(record: Mapping[str, Any], today: date, force: bool = False) -> bool:
    """今天是否已经处理完（成功路径），用来给重复触发的 cron 去重。"""
    if force:
        return False
    if record.get("last_claim_date") != today.isoformat():
        return False
    return record.get("last_status") in SETTLED_STATUSES


def skipped_result(record: Mapping[str, Any]) -> RunResult:
    """用上一次成功的结果还原一份 RunResult，让「跳过」也能发一条内容完整的提醒。"""
    summary = record.get("last_summary") or {}
    available = summary.get("available")
    total = summary.get("total")
    frozen = summary.get("frozen")
    balance = {}
    if available is not None or total is not None or frozen is not None:
        balance = {
            "available": available or 0.0,
            "total": total or 0.0,
            "frozen": frozen or 0.0,
        }

    detail = "本次跳过，未重复请求登录接口"
    previous = str(summary.get("detail") or "").replace("，未重复请求登录接口", "").strip("；，。 ")
    if previous:
        detail = f"{detail}；上次：{previous}"

    return RunResult(
        STATUS_SKIPPED,
        nickname=record.get("nickname"),
        balance=balance,
        earn=summary.get("earn") or 0.0,
        spend=summary.get("spend") or 0.0,
        spend_calls=summary.get("spend_calls") or 0,
        expire=summary.get("expire") or 0.0,
        detail=detail,
    )


# ---------------------------------------------------------------- 通知


def _ql_notify_send(env: Mapping[str, str]):
    """找青龙自带的 notify.py，返回它的 send(title, content)。"""
    candidates: list[Path] = []
    ql_dir = (env.get("QL_DIR") or "").strip()
    if ql_dir:
        candidates.append(Path(ql_dir) / "data" / "scripts")
    candidates.append(Path(__file__).resolve().parent)
    candidates.append(Path("/ql/data/scripts"))
    candidates.append(Path("/ql/scripts"))
    try:
        candidates.append(Path.cwd())
    except OSError:  # pragma: no cover - 极少数环境拿不到 cwd
        pass

    seen: set = set()
    for folder in candidates:
        try:
            key = str(folder.resolve())
        except OSError:
            key = str(folder)
        if key in seen:
            continue
        seen.add(key)
        if not (folder / "notify.py").is_file():
            continue
        sys.path.insert(0, str(folder))
        try:
            import notify  # type: ignore

            sender = getattr(notify, "send", None)
            if not callable(sender):
                continue
            if not _notify_has_channel(notify):
                log.warning(
                    "%s 里没有任何已配置的推送渠道（FSKEY 等），跳过它",
                    folder / "notify.py",
                )
                return None
            log.info("通知通道：青龙 notify.py（%s）", folder / "notify.py")
            return _without_hitokoto(sender)
        except (Exception, SystemExit) as exc:  # noqa: BLE001 - 别人的 notify.py 可能会 sys.exit
            log.warning("加载 %s 失败：%s", folder / "notify.py", exc)
        finally:
            try:
                sys.path.remove(str(folder))
            except ValueError:
                pass
    return None


def _notify_has_channel(module) -> bool:
    """看 notify.py 里有没有配好任何渠道；拿不准时按「有」处理，避免误杀。"""
    checker = getattr(module, "add_notify_function", None)
    if not callable(checker):
        return True
    try:
        return bool(checker())
    except Exception:  # noqa: BLE001 - 各版本内部实现不一致，探测失败就不拦
        return True


def _without_hitokoto(sender):
    """青龙 notify.py 默认会在正文末尾追加一条随机「一言」，这里调用时关掉它。"""
    try:
        params = inspect.signature(sender).parameters.values()
    except (TypeError, ValueError):  # 拿不到签名就原样用
        return sender
    if not any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params):
        return sender

    def _send(title: str, content: str) -> None:
        sender(title, content, HITOKOTO=False)

    return _send


def _sign(secret: str) -> dict:
    timestamp = str(int(time.time()))
    string_to_sign = f"{timestamp}\n{secret}"
    digest = hmac.new(string_to_sign.encode("utf-8"), digestmod=hashlib.sha256).digest()
    return {"timestamp": timestamp, "sign": base64.b64encode(digest).decode("utf-8")}


def send_feishu(webhook: str, text: str, secret: str | None = None, timeout: float = 10.0, retries: int = 1) -> None:
    payload: dict = {"msg_type": "text", "content": {"text": text}}
    if secret:
        payload.update(_sign(secret))

    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            resp = requests.post(webhook, json=payload, timeout=timeout)
            resp.raise_for_status()
            body = resp.json() if resp.content else {}
            code = body.get("code", body.get("StatusCode", 0))
            if code not in (0, "0", None):
                message = body.get("msg") or body.get("StatusMessage") or "未知错误"
                raise NotifyError(f"飞书返回 code={code} msg={message}")
            return
        except Exception as exc:  # noqa: BLE001 - 网络/解析错误统一重试一次
            last_error = exc
            if attempt < retries:
                time.sleep(2)
    raise NotifyError(str(last_error))


@dataclass
class AccountRun:
    """一个账号这一次运行的结果，交给通知层拼消息。"""

    index: int
    masked: str
    result: RunResult


# ---------------------------------------------------------------- 新版消息格式
#
# 目标：一行一个账号，整体宽度不超过 14 个中文字（= 28 个半角字符），
# 不用表格线，靠空格对齐。第一行放总览「成功签到 5/5 个魔搭账号」。
#
# 版式（每行最多 28 半角格，中文占 2 格）：
#   成功签到 3/3 个魔搭账号
#   ✅ LeonX86       +250
#   ✅ Bin9Bing       +50
#   ❌ l68noeL     Token失效
#   合计到账 +300（11-08 00:58）
#
# 昵称列宽度固定，超长截断成「…」，保证后面的到账数字能对齐。

MAX_LINE_UNITS = 28  # 14 个中文字


def _display_units(text: str) -> int:
    """按显示宽度算字符数：中文/全角占 2，其余占 1。"""
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text)


def _truncate_units(text: str, limit: int) -> str:
    """把 text 截到显示宽度 <= limit，被截断时末尾补一个「…」。"""
    if _display_units(text) <= limit:
        return text
    budget = limit - 1  # 给「…」留一格
    out: list = []
    used = 0
    for ch in text:
        width = 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
        if used + width > budget:
            break
        out.append(ch)
        used += width
    return "".join(out) + "…"


def _pad_units(text: str, width: int) -> str:
    """按显示宽度右侧补空格。"""
    return text + " " * max(0, width - _display_units(text))


def _pad_left(text: str, width: int) -> str:
    """按显示宽度左侧补空格（右对齐）。"""
    return " " * max(0, width - _display_units(text)) + text


# 每种状态在多账号列表里用的短标记：图标 + 一句话（新到账另外用数字列表示）
STATUS_MARK = {
    STATUS_CLAIMED: "✅",
    STATUS_DONE: "☑️",
    STATUS_SKIPPED: "☑️",
    STATUS_INACTIVE: "⚠️",
    STATUS_FAILED: "❌",
}


def _is_ok(result: RunResult) -> bool:
    """算不算「签到成功」：领取成功和今天已领都算，失败和跳过不算。"""
    return result.status in (STATUS_CLAIMED, STATUS_DONE)


def _gain_of(result: RunResult) -> float:
    """本次运行新到账多少魔粒（不是今日累计收入）。"""
    match = re.search(r"新到账\s*(-?\d+(?:\.\d+)?)", result.detail or "")
    return float(match.group(1)) if match else 0.0


def _fail_brief(result: RunResult) -> str:
    """失败原因压成几个字，塞得进一行里。"""
    text = result.error or result.detail or "失败"
    lowered = text.lower()
    if "401" in text or "认证" in text or "token" in lowered:
        return "Token失效"
    if "403" in text or "风控" in text:
        return "风控拦截"
    if "超时" in text or "timeout" in lowered:
        return "请求超时"
    return "请求失败"


def _status_token(result: RunResult) -> str:
    """状态列的文字：成功态只显示新到账数字，失败显示简短原因。"""
    if result.status == STATUS_FAILED:
        return _fail_brief(result)
    if result.status == STATUS_INACTIVE:
        return "无奖励"
    if result.status == STATUS_SKIPPED:
        return "已处理"
    gain = _gain_of(result)
    return f"+{gain:g}" if gain > 0 else "已领"


def format_compact(runs: list, now: datetime, settings: Settings) -> tuple:
    """新版紧凑格式：总览一行 + 每账号一行 + 合计一行，宽度 ≤ 14 个中文字。

    昵称列宽度 = 28 - 图标(3) - 状态列(最多 9) - 2 个分隔空格 = 14 半角格，
    够放 7 个英文字符或 7 个汉字的一半，超长就截断，保证数字列对齐。
    """
    total = len(runs)
    ok = sum(1 for run in runs if _is_ok(run.result))
    gain_sum = round(sum(_gain_of(run.result) for run in runs), 2)

    title = f"成功签到 {ok}/{total} 个魔搭账号"

    status_width = 9  # 上限，最长是「Token失效」，中文约占 9 格
    # 这批账号里没有一个长状态（全是 +250 这种）时，不必给状态列留 9 格，把地方让给昵称
    needed = max((_display_units(_status_token(run.result)) for run in runs), default=0)
    status_width = min(status_width, max(needed, 4))
    # 行 = 图标(2) + 空格(1) + 昵称(name_width) + 空格(2) + 状态(<=status_width)
    name_width = MAX_LINE_UNITS - 2 - 1 - 2 - status_width
    # 正文第一行不再重复总览：青龙 notify.py 自己会用 f"{title}\n\n{content}" 拼一遍标题，
    # 这里再写一遍就会显示两次表头。webhook 直推没有这一层，由调用方把 title 补在前面。
    lines: list = []
    for run in runs:
        result = run.result
        icon = STATUS_MARK.get(result.status, "•")
        name = _truncate_units(result.nickname or run.masked, name_width)
        status = _truncate_units(_status_token(result), status_width)
        # 数字列右对齐：「+250」和「+50」末端对齐才好横向比
        lines.append(f"{icon} {_pad_units(name, name_width)}  {_pad_left(status, status_width)}")

    lines.append(f"合计到账 +{gain_sum:g} | {now.strftime('%m-%d %H:%M')}")
    return title, "\n".join(lines)


def _account_name(run: AccountRun, show_index: bool) -> str:
    name = run.result.nickname or "未知账号"
    return f"#{run.index} {name}" if show_index else name


def _earn_total(runs: list) -> float:
    return round(sum(run.result.earn for run in runs), 2)


def _balance_line(balance: Mapping[str, Any]) -> str:
    return "可用 {available:g} / 总计 {total:g}（冻结 {frozen:g}）".format(
        available=balance.get("available", 0.0),
        total=balance.get("total", 0.0),
        frozen=balance.get("frozen", 0.0),
    )


def _today_line(result: RunResult) -> str:
    spend_note = f"（{result.spend_calls} 次调用）" if result.spend_calls else ""
    return "收入 {earn:g} / 消耗 {spend:g}{note} / 过期 {expire:g}".format(
        earn=result.earn, spend=result.spend, note=spend_note, expire=result.expire
    )


def _split_notes(detail: str) -> list:
    """把 `execute()` 里用「，」拼起来的说明拆成条目，方便一条一行。

    只在括号外拆：`新到账 250 魔粒（daily_active +200，aliyun_bindlogin +50）`
    里的逗号是奖励明细的一部分，拆开反而看不懂。
    """
    parts: list = []
    depth = 0
    current: list = []
    for char in detail:
        if char in "（(":
            depth += 1
        elif char in "）)":
            depth = max(0, depth - 1)
        if char in "，,；;" and depth == 0:
            parts.append("".join(current).strip())
            current = []
            continue
        current.append(char)
    parts.append("".join(current).strip())
    return [part for part in parts if part]


def _overall_status(runs: list) -> str:
    """汇总里最坏的那个状态决定标题（失败 > 没奖励 > 已领取/跳过）。"""
    order = (STATUS_CLAIMED, STATUS_DONE, STATUS_SKIPPED, STATUS_INACTIVE, STATUS_FAILED)
    worst = STATUS_DONE
    for run in runs:
        if order.index(run.result.status) > order.index(worst):
            worst = run.result.status
    return worst


def format_account(run: AccountRun, now: datetime, settings: Settings, show_index: bool = False) -> tuple:
    """单账号的 (标题, 正文)。"""
    result = run.result
    title = STATUS_LABEL.get(result.status, result.status)

    lines = [f"账号：{_account_name(run, show_index)}（{run.masked}）"]
    if result.balance:
        lines.append(f"余额：{_balance_line(result.balance)}")
    lines.append(f"今日：{_today_line(result)}")
    lines.append(f"时间：{now.strftime('%Y-%m-%d %H:%M:%S')} ({settings.tz_name})")
    if result.detail:
        lines.append(f"说明：{result.detail}")
    if result.error:
        lines.append(f"错误：{result.error}")
    return title, "\n".join(lines)


def format_summary(runs: list, now: datetime, settings: Settings) -> tuple:
    """多账号汇总成一条 (标题, 正文)：先给总览，再逐个账号给明细。

    - 标题带上最坏状态和各状态计数，扫一眼就知道要不要处理；
    - 📊 总览固定放五列，一个账号一行，方便横向比；
    - 每个账号下面只列「说明/错误」里真正值得看的条目，没奖励就明确写一句，
      不让读者去猜空白代表什么。
    """
    total = len(runs)
    counts = {status: sum(1 for run in runs if run.result.status == status) for status in STATUS_LABEL}
    count_text = " · ".join(
        f"{SHORT_LABEL[status]} {counts[status]}" for status in STATUS_LABEL if counts[status]
    )

    overall = _overall_status(runs)
    # 有多人失败时标题会很长，青龙 notify 的一些渠道会截断；标题只放最坏状态和总数
    title = f"{STATUS_LABEL.get(overall, overall)}｜{total} 个账号"
    if counts.get(STATUS_FAILED):
        title += f"（{counts[STATUS_FAILED]} 个失败）"

    lines = [f"📊 总览：{count_text}"]
    for run in runs:
        result = run.result
        lines.append(
            "  {label}  {name}".format(label=SHORT_LABEL.get(result.status, result.status), name=_account_name(run, True))
        )
        if result.balance:
            lines.append(f"    余额可用 {result.balance.get('available', 0.0):g}")
        lines.append(f"    今日 {_today_line(result)}")
    lines.append("")

    for run in runs:
        result = run.result
        lines.append("──────────")
        lines.append(
            f"{_account_name(run, True)}（{run.masked}） {SHORT_LABEL.get(result.status, result.status)}"
        )
        if result.balance:
            lines.append(f"  · 余额可用 {result.balance.get('available', 0.0):g} / 总计 {result.balance.get('total', 0.0):g}")
        else:
            lines.append("  · 余额查询失败")
        # 请求都没成功（或者没拿到昵称，说明什么都没读到）时，收入/消耗全是 0，列出来只会误导
        if result.status != STATUS_FAILED and result.nickname:
            lines.append(f"  · 今日 {_today_line(result)}")
        for note in _split_notes(result.detail):
            lines.append(f"  · {note}")
        if result.error:
            lines.append(f"  ✗ {result.error}")

    lines.append("──────────")
    lines.append(f"合计：今日收入 {_earn_total(runs):g} 魔粒")
    lines.append(f"时间：{now.strftime('%Y-%m-%d %H:%M:%S')} ({settings.tz_name})")
    return title, "\n".join(lines)


def format_message(runs: list, settings: Settings, now: datetime) -> tuple:
    """按账号数量选单账号格式或多账号汇总格式。

    已换成紧凑格式（format_compact），下面两行是旧版式，保留备查：
    """
    # if len(runs) == 1:
    #     return format_account(runs[0], now, settings)
    # return format_summary(runs, now, settings)
    return format_compact(runs, now, settings)


def build_notifier(settings: Settings, env: Mapping[str, str]):
    """返回 (runs, now) -> None；返回 None 表示不推送。"""
    mode = settings.notify_mode
    if mode == "none":
        return None

    ql_send = None
    if mode in ("auto", "ql"):
        ql_send = _ql_notify_send(env)
        if ql_send is None and mode == "ql":
            log.warning("MOLIGATE_NOTIFY=ql 但没找到可用的青龙 notify.py（文件不存在或没配推送渠道），改为直接推飞书（若有 webhook）")

    def _notify(runs: list, now: datetime) -> None:
        title, content = format_message(runs, settings, now)
        if ql_send is not None:
            try:
                ql_send(title, content)
                log.info("已交给青龙 notify.py 推送（是否送达看它上面的输出）")
                return
            except (Exception, SystemExit) as exc:  # noqa: BLE001 - 推送失败不影响领取结果
                log.warning("青龙 notify.py 推送失败：%s", exc)
                if not settings.webhook:
                    return
        if settings.webhook:
            try:
                send_feishu(settings.webhook, f"{title}\n{content}", settings.webhook_secret)
                log.info("飞书 webhook 推送成功")
                return
            except NotifyError as exc:
                log.warning("飞书推送失败：%s", exc)
                return
        log.info("没有可用的通知通道（既没有青龙 notify.py 的渠道，也没配 FEISHU_WEBHOOK），本次不推送")

    return _notify


# ---------------------------------------------------------------- 入口


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
        force=True,
    )


def ensure_data_dir(settings: Settings) -> bool:
    try:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        probe = settings.data_dir / ".write-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError as exc:
        log.error("数据目录不可写：%s（%s）", settings.data_dir, exc)
        log.error("青龙是 root 运行，一般不会遇到；如果自定义了 DATA_DIR，确认目录存在且可写")
        return False


def cron_lines(minute: int) -> list:
    """给用户手动填进青龙定时规则的表达式（青龙 cron 用容器时区）。"""
    return [
        f"{minute} 7 * * *",
        f"{minute} 7,8 * * *",
        f"{minute} 7,8,9 * * *",
    ]


def print_cron(minute: int | None = None) -> int:
    if minute is None:
        minute = random.randint(2, 57)
    minute = max(0, min(59, int(minute)))
    first, second, third = cron_lines(minute)
    print("把下面任意一条填进「青龙 → 定时任务 → 新建任务 → 定时规则」：")
    print("")
    print(f"  {first:<16} 每天 07:{minute:02d} 跑一次")
    print(f"  {second:<16} 07:{minute:02d} / 08:{minute:02d} 各跑一次，第二次通常因今天已领取立刻退出（推荐）")
    print(f"  {third:<16} 再加 09:{minute:02d}，给失败留两次重试机会")
    print("")
    print("说明：")
    print(f"  - 分钟数 {minute:02d} 可以随便改，建议避开整点，减少和其他脚本撞在同一秒。")
    print("  - 青龙的 cron 按容器时区执行；如果实际执行时间差 8 小时，给青龙容器加环境变量 TZ=Asia/Shanghai 后重启。")
    print("  - 同一天只要跑成功过，脚本会自己跳过，所以多写几条定时规则不会重复领。")
    return 0


def _show_state(settings: Settings, store: StateStore, now: datetime) -> int:
    print(f"数据目录：{settings.data_dir}")
    print(f"今天：{now.date().isoformat()}（{settings.tz_name}）")
    print(f"账号数：{len(settings.tokens)}")
    for index, key in enumerate(settings.account_keys, start=1):
        print("")
        print(f"--- 账号 #{index}（{settings.masked_token(index - 1)}）---")
        _show_account(store.account(key))
    return 0


def _show_account(record: Mapping[str, Any]) -> None:
    if not record:
        print("状态：还没有任何执行记录")
        return
    print(f"昵称：{record.get('nickname') or '未知'}")
    print(f"最近状态：{record.get('last_status')}（{record.get('last_claim_date')}）")
    print(f"最近执行：{record.get('last_run_at')}")
    print(f"最近成功：{record.get('last_ok_at')}")
    if record.get("last_error"):
        print(f"最近错误：{record.get('last_error')}")
    summary = record.get("last_summary") or {}
    if summary:
        print(
            "最近汇总：收入 {earn} / 消耗 {spend} / 可用 {available}".format(
                earn=summary.get("earn"), spend=summary.get("spend"), available=summary.get("available")
            )
        )
        if summary.get("detail"):
            print(f"说明：{summary.get('detail')}")


def _run_account(
    settings: Settings, store: StateStore, run: AccountRun, token: str, today: date, force: bool
) -> RunResult:
    """跑一个账号：查状态 → 必要时登录 → 复查 → 落状态。"""
    key = fingerprint(token)
    record = store.account(key)

    if already_done_today(record, today, force):
        log.info("#%d 今天已经处理过（%s，%s），跳过", run.index, record.get("last_status"), record.get("last_run_at"))
        return skipped_result(record)

    client = ModelScopeClient(settings, token)
    try:
        result = execute(settings, client, today)
    except Exception as exc:  # noqa: BLE001 - 单个账号异常不能拖垮其他账号
        log.exception("#%d 执行过程中出现未预期异常", run.index)
        result = RunResult(STATUS_FAILED, error=f"未预期异常：{exc}")
    finally:
        client.close()

    if not result.nickname:
        result.nickname = store.cached_nickname(key)
    store.record(key, result, datetime.now(settings.tz))
    return result


def _log_result(run: AccountRun) -> None:
    result = run.result
    log.info(
        "#%d %s 结果：%s | 今日累计收入 %g / 消耗 %g | 余额可用 %g",
        run.index,
        result.nickname or run.masked,
        result.status,
        result.earn,
        result.spend,
        (result.balance or {}).get("available", 0.0),
    )
    if result.detail:
        log.info("#%d 说明：%s", run.index, result.detail)
    if result.error:
        log.error("#%d 错误：%s", run.index, result.error)


def _run(settings: Settings, store: StateStore, notify, force: bool) -> int:
    now = datetime.now(settings.tz)
    today = now.date()

    if settings.random_delay_min > 0:
        wait = random.randint(0, settings.random_delay_min * 60)
        log.info("随机延迟 %d 秒后开始（RANDOM_DELAY_MIN=%d）", wait, settings.random_delay_min)
        time.sleep(wait)

    runs: list = []
    for index, token in enumerate(settings.tokens, start=1):
        if index > 1 and settings.account_delay > 0:
            # 取 0~上限之间的随机毫秒：固定间隔会让多个账号在日志里形成明显的规律
            wait_ms = random.randint(0, settings.account_delay * 1000)
            log.info(
                "等待 %.1f 秒再处理下一个账号（ACCOUNT_DELAY=%d，0~%d 秒随机）",
                wait_ms / 1000.0,
                settings.account_delay,
                settings.account_delay,
            )
            time.sleep(wait_ms / 1000.0)
        run = AccountRun(index=index, masked=settings.masked_token(index - 1), result=RunResult(STATUS_FAILED))
        run.result = _run_account(settings, store, run, token, today, force)
        _log_result(run)
        runs.append(run)

    # 所有账号跑完才发一条合并消息：多账号不刷屏；只要有账号失败就返回 1，让青龙把任务标红
    if notify is not None:
        notify(runs, now)

    return 0 if all(run.result.status != STATUS_FAILED for run in runs) else 1


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(prog="moligate_ql", description="ModelScope 每日登录 + 飞书提醒（青龙版）")
    parser.add_argument("--force", action="store_true", help="今天已经成功过也再跑一次（会重复请求登录接口）")
    parser.add_argument("--no-notify", action="store_true", help="本次不推送")
    parser.add_argument("--check", action="store_true", help="只看状态，不请求接口")
    parser.add_argument("--show-config", action="store_true", help="打印生效的配置（Token 脱敏）")
    parser.add_argument("--cron", nargs="?", const=-1, type=int, metavar="MINUTE", help="打印推荐定时规则，可选指定分钟数")
    args = parser.parse_args(argv)

    if args.cron is not None:
        return print_cron(None if args.cron < 0 else args.cron)

    raw_env = dict(os.environ)
    try:
        env_file = load_env_file_into(raw_env)
    except Exception:  # noqa: BLE001
        env_file = None

    setup_logging((raw_env.get("LOG_LEVEL") or "INFO").upper())
    if env_file:
        log.info("已载入环境变量文件：%s", env_file)

    try:
        settings = Settings.from_env(raw_env)
    except ConfigError as exc:
        log.error("配置错误：%s", exc)
        return 2

    setup_logging(settings.log_level)
    masked_all = " ".join(f"#{i} {settings.masked_token(i - 1)}" for i in range(1, len(settings.tokens) + 1))
    log.info(
        "青龙版启动 | %d 个账号 | 时区 %s | 数据目录 %s",
        len(settings.tokens),
        settings.tz_name,
        settings.data_dir,
    )
    log.info("账号：%s", masked_all)
    if len(settings.tokens) > 1:
        log.info("多账号间隔：每个账号前随机等 0~%d 秒（ACCOUNT_DELAY）", settings.account_delay)
    log.info("账号状态目录：%s", settings.state_path)

    if args.show_config:
        print(f"MS_BASE_URL       = {settings.base_url}")
        print(f"MS_TOKEN          = {masked_all}（共 {len(settings.tokens)} 个账号）")
        print(f"TZ                = {settings.tz_name}")
        print(f"DATA_DIR          = {settings.data_dir}")
        print(f"MOLIGATE_NOTIFY   = {settings.notify_mode}")
        print(f"FEISHU_WEBHOOK    = {'已配置' if settings.webhook else '未配置'}")
        print(f"FEISHU_SECRET     = {'已配置' if settings.webhook_secret else '未配置'}")
        print(f"RANDOM_DELAY_MIN  = {settings.random_delay_min}")
        print(f"ACCOUNT_DELAY     = 0~{settings.account_delay} 秒随机（0 表示不等待）")
        print(f"LOG_LEVEL         = {settings.log_level}")
        return 0

    if not ensure_data_dir(settings):
        return 3

    store = StateStore(settings.state_path)
    now = datetime.now(settings.tz)

    if args.check:
        return _show_state(settings, store, now)

    notify = None if args.no_notify else build_notifier(settings, raw_env)
    return _run(settings, store, notify, args.force)


if __name__ == "__main__":
    sys.exit(main())