#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从 models.dev 查询模型信息。

用法:
    python query_model_info.py "Qwen3.8-Flash-Next"            # 人类可读输出
    python query_model_info.py "Qwen3.8-Flash-Next" --json     # JSON 输出（供程序解析）
    python query_model_info.py "Qwen3.8-Flash-Next" "GLM-5.1"  # 一次查多个

查询规则（与 opencode-config-updater skill 配套）:
    - 大小写不敏感，空格会归一化成 "-"。
    - 带 "/" 的输入按 models.json 的 key（lab/model）精确匹配。
    - 不带 "/" 的输入先按 key 末段精确匹配，再退化为模糊子串匹配。
      ModelScope 的 model id 与 models.dev/厂商的 id 存在差异是正常的，
      因此调用本脚本时通常传斜杠后面的裸名称。
    - 价格优先取官方渠道（api.json 按 provider/model 精确查）；查不到则遍历
      api.json 所有 provider 的挂价，input/output/cache_read/cache_write
      分别取众数。缓存写入(cache_write)价格是参考版脚本没有、配置模板需要的。
    - 返回结果附带 provider_id / provider_name 等额外字段，仅供展示；
      写入 opencode 配置时只允许取配置模板允许的字段。

退出码: 0 全部查到; 1 有未查到的模型; 3 网络错误
"""

import argparse
import json
import sys

MODELS_URL = "https://models.dev/models.json"  # 顶层"一个模型只存一条"，key 是 lab/model
API_URL = "https://models.dev/api.json"

# opencode 配置模板允许的价格字段，超出部分禁止写入配置
COST_KEYS = ("input", "output", "cache_read", "cache_write")


def _fetch_json(url):
    """GET 一个 JSON URL。优先用 requests，未安装时退回标准库 urllib。"""
    try:
        import requests
    except ImportError:
        requests = None
    if requests is not None:
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        return resp.json()
    import urllib.request

    req = urllib.request.Request(url, headers={"User-Agent": "opencode-config-updater/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_dev_data():
    """一次性拉取 models.dev 的两个数据源，供多次查询复用。"""
    return _fetch_json(MODELS_URL), _fetch_json(API_URL)


def get_model_info(model_id, models_data=None, api_data=None):
    """从 models.dev 查询单个模型的信息，查不到返回 None。

    model_id 支持 "provider/model" 精确查询或裸名称匹配（大小写不敏感）。
    models_data / api_data 可传入 fetch_dev_data() 的结果以避免重复拉取。
    """
    if models_data is None or api_data is None:
        models_data, api_data = fetch_dev_data()

    model_id_lower = model_id.lower().replace(" ", "-")

    # 精确匹配（大小写不敏感）
    if "/" in model_id_lower:
        candidates = [k for k in models_data if k.lower() == model_id_lower]
    else:
        # 在 models.json 的 key 中找官方映射（精确匹配末段，大小写不敏感）
        candidates = [k for k in models_data if k.split("/")[-1].lower() == model_id_lower]
        if not candidates:
            # 退化为模糊匹配
            candidates = [k for k in models_data if model_id_lower in k.split("/")[-1].lower()]

    canonical_key = candidates[0] if candidates else None
    if not canonical_key or canonical_key not in models_data:
        return None

    result = dict(models_data[canonical_key])

    # 从 api.json 补充价格信息
    provider_id = canonical_key.split("/")[0]
    model_name = canonical_key.split("/", 1)[1]
    provider_info = api_data.get(provider_id, {})
    provider_model = provider_info.get("models", {}).get(model_name, {})

    provider_cost = provider_model.get("cost")
    if isinstance(provider_cost, dict):
        # 只保留配置模板允许的价格字段
        result["cost"] = {k: provider_cost[k] for k in COST_KEYS if k in provider_cost}
    else:
        # 官方渠道无价格：遍历所有 provider 的挂价，各价格项分别取众数。
        # 各 provider 的模型 id 前缀五花八门，但末段都是裸模型名，按末段匹配。
        name = model_name.lower()
        costs = [
            m["cost"]
            for p in api_data.values()
            for k, m in p.get("models", {}).items()
            if k.split("/")[-1].lower() == name and isinstance(m.get("cost"), dict)
        ]
        cost = {}
        for key in COST_KEYS:
            values = [c[key] for c in costs if key in c]
            if values:
                cost[key] = max(values, key=values.count)
        if cost:
            result["cost"] = cost

    result["provider_id"] = provider_id
    result["provider_name"] = provider_info.get("name", "")
    return result


def print_readable(model_id, info):
    if info is None:
        print(f"未找到模型: {model_id}")
        return
    cost = info.get("cost") or {}
    limit = info.get("limit") or {}
    modalities = info.get("modalities") or {}
    print(f"模型: {info.get('id', model_id)}")
    print(f"名称: {info.get('name', 'N/A')}")
    print(f"Provider: {info.get('provider_name', 'N/A')}")
    print(f"Family: {info.get('family', 'N/A')}")
    print(f"知识截止: {info.get('knowledge', 'N/A')}")
    print(f"发布日期: {info.get('release_date', 'N/A')}")
    print(f"最近更新: {info.get('last_updated', 'N/A')}")
    print(f"开源权重: {info.get('open_weights', 'N/A')}")
    print("---")
    print(f"支持附件: {info.get('attachment', 'N/A')}")
    print(f"支持 Temperature: {info.get('temperature', 'N/A')}")
    print(f"支持 Structured Output: {info.get('structured_output', 'N/A')}")
    print(f"支持 Tool Call: {info.get('tool_call', 'N/A')}")
    print(f"支持 Reasoning: {info.get('reasoning', 'N/A')}")
    print("---")
    print(f"输入价格: ${cost.get('input', 'N/A')}/M tokens")
    print(f"输出价格: ${cost.get('output', 'N/A')}/M tokens")
    print(f"缓存读取价格: ${cost.get('cache_read', 'N/A')}/M tokens")
    print("---")
    print(f"上下文窗口: {limit.get('context', 'N/A')} tokens")
    print(f"输出上限: {limit.get('output', 'N/A')} tokens")
    print(f"输入模态: {modalities.get('input', 'N/A')}")
    print(f"输出模态: {modalities.get('output', 'N/A')}")


def main():
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
            sys.stderr.reconfigure(encoding="utf-8")
        except Exception:
            pass

    parser = argparse.ArgumentParser(description="从 models.dev 查询模型信息")
    parser.add_argument("model", nargs="+", help='模型 id 或名称，如 "Qwen3.8-Flash-Next" 或 "Qwen/Qwen3.8-Flash-Next"')
    parser.add_argument("--json", action="store_true", help="以 JSON 输出（供程序解析）")
    args = parser.parse_args()

    try:
        models_data, api_data = fetch_dev_data()
    except Exception as exc:
        print(f"NETWORK_ERROR: 拉取 models.dev 数据失败: {exc}", file=sys.stderr)
        sys.exit(3)

    results = {}
    any_missing = False
    for name in args.model:
        info = get_model_info(name, models_data, api_data)
        results[name] = info
        if info is None:
            any_missing = True

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        for name in args.model:
            print_readable(name, results[name])
            print("=" * 40)

    sys.exit(1 if any_missing else 0)


if __name__ == "__main__":
    main()
