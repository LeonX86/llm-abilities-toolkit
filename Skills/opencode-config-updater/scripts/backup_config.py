#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""定位 opencode 配置文件并在修改前备份。

用法:
    python backup_config.py [--config-dir DIR]

按 $XDG_CONFIG_HOME/opencode → ~/.config/opencode（Windows 即
%USERPROFILE%\\.config\\opencode）顺序查找 opencode.jsonc / opencode.json。
找到则复制为同目录下 <原文件名>.bak-YYYYMMDDTHHMMSS；未找到则报告 missing，
由调用方（agent）按模板新建 opencode.jsonc，此时无需备份。

输出: stdout JSON
退出码: 0 成功（含 missing）；4 备份失败
"""

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config_common import config_dir_candidates, locate_config, parse_jsonc_file


def main():
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
            sys.stderr.reconfigure(encoding="utf-8")
        except Exception:
            pass

    parser = argparse.ArgumentParser(description="备份 opencode 配置文件")
    parser.add_argument("--config-dir", help="覆盖默认的 opencode 配置目录（调试/测试用）")
    args = parser.parse_args()

    candidates = config_dir_candidates(args.config_dir)
    config_path, _ = locate_config(candidates)

    if config_path is None:
        print(json.dumps({
            "status": "ok",
            "action": "missing",
            "config_path": None,
            "backup_path": None,
            "create_dir": str(candidates[0]),
            "note": "未找到已有配置，按模板新建 opencode.jsonc 即可，无需备份",
        }, ensure_ascii=False, indent=2))
        sys.exit(0)

    ts = datetime.now().strftime("%Y%m%dT%H%M%S")
    backup_path = config_path.parent / f"{config_path.name}.bak-{ts}"
    try:
        shutil.copy2(config_path, backup_path)
    except OSError as exc:
        print(json.dumps({"status": "error", "error": f"备份失败: {exc}"}, ensure_ascii=False))
        sys.exit(4)

    warnings = []
    parse_ok = True
    try:
        parse_jsonc_file(config_path)
    except json.JSONDecodeError as exc:
        parse_ok = False
        warnings.append(f"当前配置文件解析失败，修改前请先确认内容: {exc}")

    print(json.dumps({
        "status": "ok",
        "action": "backed_up",
        "config_path": str(config_path),
        "backup_path": str(backup_path),
        "parse_ok": parse_ok,
        "warnings": warnings,
    }, ensure_ascii=False, indent=2))
    sys.exit(0)


if __name__ == "__main__":
    main()
