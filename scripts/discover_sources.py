
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""自动发现指定 TVBox 源，验证后追加到 config/sources.json。"""

import json
import sys
import time
import unicodedata
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import requests

ROOT = Path(__file__).resolve().parent.parent
CONFIG_FILE = ROOT / "config" / "sources.json"

UPSTREAM_URL = (
    "https://raw.githubusercontent.com/"
    "yangxiaoge/tvbox_cust/master/tvbox/"
    "%E5%A4%9A%E4%BB%93.json"
)

# 按来源 ID 匹配名称；潇洒也接受 XS 这个名称。
TARGETS = {
    "fantaiying": {"name": "饭太硬", "aliases": []},
    "moyuer": {"name": "摸鱼儿", "aliases": []},
    "xiaosa": {"name": "潇洒", "aliases": ["xs"]},
    "wangerxiao": {"name": "王二小", "aliases": []},
    "aowu": {"name": "嗷呜", "aliases": []},
    "feimao": {"name": "肥猫", "aliases": []},
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; TVBoxSourceUpdater/1.0)",
    "Accept": "*/*",
}


def log(message):
    print(f"[发现源] {message}", flush=True)


def normalize_url(url):
    """规范化 URL，用于比较和全局去重。"""
    if not isinstance(url, str):
        return ""

    url = unicodedata.normalize("NFC", url.strip())
    if not url:
        return ""

    try:
        parts = urlsplit(url)
        if parts.scheme.lower() not in ("http", "https") or not parts.netloc:
            return ""

        scheme = parts.scheme.lower()
        host = parts.netloc.lower()
        path = parts.path.rstrip("/") or "/"

        # 不改动查询参数，避免把不同接口误认为同一个地址。
        return urlunsplit((scheme, host, path, parts.query, ""))
    except Exception:
        return ""


def match_source(name):
    """返回匹配的目标来源 ID；不匹配其他来源。"""
    if not isinstance(name, str):
        return None

    value = name.strip()
    lowered = value.casefold()

    for source_id, target in TARGETS.items():
        if target["name"] in value:
            return source_id

        if lowered in [x.casefold() for x in target["aliases"]]:
            return source_id

    return None


def load_json(url, timeout=20):
    response = requests.get(
        url,
        headers=HEADERS,
        timeout=timeout,
        allow_redirects=True,
    )
    response.raise_for_status()
    response.encoding = "utf-8"
    return response.json()


def validate_tvbox_url(url):
    """复用仓库现有 update.py 的解析器验证地址。"""
    try:
        scripts_dir = str(ROOT / "scripts")
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)

        from update import fetch_config

        data = fetch_config(url)
        if not isinstance(data, dict):
            return False, "返回内容不是 JSON 对象"

        # 与 update.py 的 TVBox 配置结构规则保持一致。
        keys = ("sites", "spiders", "lives", "store", "exts", "parses", "flags")
        if any(key in data for key in keys):
            return True, "TVBox 配置结构有效"

        urls = data.get("urls")
        if isinstance(urls, list) and urls:
            return True, "多仓配置结构有效"

        return False, "缺少 TVBox 配置关键字段"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def main():
    log(f"读取上游清单：{UPSTREAM_URL}")

    try:
        upstream = load_json(UPSTREAM_URL)
    except Exception as exc:
        log(f"读取上游失败：{exc}")
        raise SystemExit(1)

    entries = upstream.get("urls", [])
    if not isinstance(entries, list):
        log("上游 JSON 中没有有效的 urls 数组")
        raise SystemExit(1)

    if not CONFIG_FILE.exists():
        log(f"目标文件不存在：{CONFIG_FILE}")
        raise SystemExit(1)

    try:
        config = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception as exc:
        log(f"读取本地来源清单失败：{exc}")
        raise SystemExit(1)

    sources = config.get("sources")
    if not isinstance(sources, list):
        log("本地清单缺少 sources 数组")
        raise SystemExit(1)

    # 已有 ID 映射和所有来源的全局 URL 集合。
    by_id = {}
    existing_urls = set()

    for source in sources:
        if not isinstance(source, dict):
            continue

        source_id = source.get("id")
        if source_id:
            by_id[source_id] = source

        urls = source.get("urls", [])
        if isinstance(urls, list):
            for url in urls:
                key = normalize_url(url)
                if key:
                    existing_urls.add(key)

    added = 0
    checked = set()
    found = set()

    for item in entries:
        if not isinstance(item, dict):
            continue

        name = item.get("name", "")
        url = item.get("url", "")
        source_id = match_source(name)

        if source_id is None:
            continue

        found.add(source_id)
        key = normalize_url(url)

        if not key:
            log(f"跳过无效地址：{name} | {url}")
            continue

        if key in checked:
            log(f"跳过上游重复地址：{name} | {url}")
            continue
        checked.add(key)

        if key in existing_urls:
            log(f"跳过已收录地址：{name} | {url}")
            continue

        target = TARGETS[source_id]
        log(f"检测地址：{target['name']} | {url}")

        ok, reason = validate_tvbox_url(url)
        if not ok:
            log(f"不可用或解析失败：{url} | {reason}")
            continue

        source = by_id.get(source_id)

        if source is None:
            source = {
                "id": source_id,
                "name": target["name"],
                "urls": [],
            }
            sources.append(source)
            by_id[source_id] = source
            log(f"新增来源条目：{target['name']}")

        if not isinstance(source.get("urls"), list):
            source["urls"] = []

        source["urls"].append(url)
        existing_urls.add(key)
        added += 1

        log(f"已追加：{target['name']} | {url}")
        time.sleep(1)

    for source_id, target in TARGETS.items():
        if source_id not in found:
            log(f"上游未发现来源：{target['name']}")

    if added:
        CONFIG_FILE.write_text(
            json.dumps(config, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        log(f"完成：新增 {added} 个地址，已更新 config/sources.json")
    else:
        log("完成：没有新地址，本地文件未修改")


if __name__ == "__main__":
    main()
