#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
影视仓 / TVBox 配置自动更新脚本
=================================
功能：
  1. 读取 config/sources.json 中的配置源列表
  2. 对每个源依次尝试主线和备用地址，自动做中文域名 punycode 转换
  3. 校验返回内容是否为合法的 TVBox/影视仓单仓配置或多仓订阅
  4. 将每个源的最新配置保存到 output/<id>.json
  5. 生成多仓订阅文件 output/多仓订阅.json
  6. 生成聚合单仓 output/单仓聚合.json
  7. 生成 shields 徽章、更新 README 状态表，并生成所有入口的 HTTP 状态和复制地址
"""
import datetime
import json
import os
import sys
import time
import urllib.parse

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_FILE = os.path.join(ROOT, "config", "sources.json")
OUTPUT_DIR = os.path.join(ROOT, "output")
README_PATH = os.path.join(ROOT, "README.md")

DEFAULT_REPO = "xycvip/tvbox-ysc-config"
USER_AGENT = "okhttp/3.14.9"
TIMEOUT = 15
MAX_RETRIES = 2
RETRY_BACKOFF = 2
URL_INTERVAL = 1.0
SOURCE_INTERVAL = 2

TVBOX_KEYS = ("sites", "spiders", "lives", "store", "exts", "parses", "flags")
MULTI_KEYS = ("urls", "订阅")

STATUS_START = "<!-- STATUS_START -->"
STATUS_END = "<!-- STATUS_END -->"
ARTIFACTS_START = "<!-- ARTIFACT_LINKS_START -->"
ARTIFACTS_END = "<!-- ARTIFACT_LINKS_END -->"


def _strip_js_comments(text: str) -> str:
    lines = text.split("\n")
    cleaned = [ln for ln in lines if not ln.lstrip().startswith("//")]
    return "\n".join(cleaned)


def parse_json_lenient(text: str):
    text = text.lstrip("\ufeff").strip()
    text = _strip_js_comments(text)
    try:
        return json.loads(text, strict=False)
    except json.JSONDecodeError:
        decoder = json.JSONDecoder(strict=False)
        obj, _ = decoder.raw_decode(text)
        return obj


def idn_encode(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    host = parsed.hostname or ""
    try:
        encoded_host = host.encode("idna").decode("ascii")
    except Exception:
        encoded_host = host
    netloc = encoded_host
    if parsed.port:
        netloc = f"{encoded_host}:{parsed.port}"
    return urllib.parse.urlunsplit(
        (parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment)
    )


def is_valid_tvbox_config(data) -> bool:
    if not isinstance(data, dict):
        return False
    if any(key in data for key in TVBOX_KEYS):
        return True
    return "urls" in data and isinstance(data.get("urls"), list) and bool(data["urls"])


def _should_retry(err: Exception, resp_text_len: int = -1) -> bool:
    if isinstance(err, (requests.exceptions.Timeout, requests.exceptions.ConnectionError)):
        return True
    if isinstance(err, requests.exceptions.HTTPError):
        return False
    if isinstance(err, json.JSONDecodeError):
        return True
    return resp_text_len <= 0


def fetch_config(url: str) -> dict:
    target = idn_encode(url)
    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        resp = None
        try:
            resp = requests.get(
                target,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "application/json,text/plain,*/*",
                },
                timeout=TIMEOUT,
                allow_redirects=True,
            )
            resp.raise_for_status()
            data = parse_json_lenient(resp.text)
            if not is_valid_tvbox_config(data):
                raise ValueError("返回内容不是 TVBox 配置（缺少 sites/spiders/lives 或 urls 字段）")
            return data
        except Exception as e:
            last_err = e
            text_len = len(getattr(resp, "text", "") or "") if resp is not None else -1
            if _should_retry(e, text_len) and attempt < MAX_RETRIES:
                wait = RETRY_BACKOFF * attempt * 2
                print(f"      (第 {attempt} 次失败：{e}，{wait}s 后重试)")
                time.sleep(wait)
            else:
                break
    raise last_err


def check_http_url(url: str, timeout: int = 10) -> dict:
    """通过 GET 检查链接 HTTP 状态，不下载完整响应体。"""
    result = {
        "url": url,
        "http_status": None,
        "final_url": None,
        "error": None,
    }
    try:
        with requests.get(
            url,
            headers={"User-Agent": USER_AGENT, "Accept": "*/*"},
            timeout=timeout,
            allow_redirects=True,
            stream=True,
        ) as resp:
            result["http_status"] = resp.status_code
            result["final_url"] = resp.url
    except requests.exceptions.Timeout:
        result["error"] = "请求超时"
    except requests.exceptions.RequestException as e:
        result["error"] = f"连接失败：{e.__class__.__name__}"
    except Exception as e:
        result["error"] = f"检测异常：{e.__class__.__name__}"
    return result


def _merge_list(fetched, field):
    seen = set()
    out = []
    for _sid, _name, data in fetched:
        for item in data.get(field, []) or []:
            if isinstance(item, dict):
                ident = item.get("name") or item.get("url") or json.dumps(
                    item, ensure_ascii=False, sort_keys=True
                )
            else:
                ident = str(item)
            if ident in seen:
                continue
            seen.add(ident)
            out.append(item)
    return out


def merge_configs(fetched):
    if not fetched:
        return None
    _base_sid, base_name, base = fetched[0]
    merged = {}
    skip = {"sites", "lives", "parses", "doh", "rules", "flags", "exts", "ads"}
    for k, v in base.items():
        if k not in skip:
            merged[k] = v
    merged["warningText"] = (
        f"本配置由 {len(fetched)} 个公开源自动聚合（含{base_name}等），仅供学习交流，请勿商用"
    )

    seen_keys = set()
    sites = []
    for sid, _name, data in fetched:
        for s in data.get("sites", []) or []:
            if not isinstance(s, dict):
                continue
            key = s.get("key")
            if key is None:
                key = json.dumps(s, ensure_ascii=False, sort_keys=True)
            new_key = key
            if key in seen_keys:
                new_key = f"{sid}_{key}"
            if new_key in seen_keys:
                continue
            seen_keys.add(new_key)
            if new_key != key:
                s = dict(s)
                s["key"] = new_key
            sites.append(s)
    merged["sites"] = sites

    for field in ("lives", "parses", "doh", "rules", "flags", "exts"):
        merged[field] = _merge_list(fetched, field)
    return merged


def update_readme(status, ok_count, total):
    """更新 README 中 STATUS 标记之间的线路抓取状态表。"""
    if not os.path.exists(README_PATH):
        return
    with open(README_PATH, "r", encoding="utf-8") as f:
        readme = f.read()
    if STATUS_START not in readme or STATUS_END not in readme:
        return

    lines = [
        f"> 🔄 最近更新：{status['updated_at']}（北京时间） · 成功 **{ok_count}/{total}** 个源",
        "",
        "| 配置源 | 状态 | 使用地址 |",
        "| --- | --- | --- |",
    ]
    for s in status["sources"]:
        if s.get("cached"):
            lines.append(f"| {s['name']} | 📦 缓存 | 上次成功配置（本次更新未抓到，沿用缓存） |")
        elif s["ok"]:
            lines.append(f"| {s['name']} | ✅ 成功 | `{s['used_url']}` |")
        else:
            err = (s.get("error") or "").replace("|", "/")
            if len(err) > 60:
                err = err[:57] + "..."
            lines.append(f"| {s['name']} | ❌ 失败 | {err} |")
    block = "\n".join(lines)
    prefix = readme.split(STATUS_START, 1)[0]
    suffix = readme.split(STATUS_END, 1)[1]
    with open(README_PATH, "w", encoding="utf-8") as f:
        f.write(f"{prefix}{STATUS_START}\n{block}\n{STATUS_END}{suffix}")


def http_status_text(result):
    code = result.get("http_status")
    error = result.get("error")
    if code is None:
        return f"⚠️ 未检测成功（{error or '未知错误'}）"
    if 200 <= code < 300:
        return f"✅ HTTP {code}"
    if code in (301, 302, 303, 307, 308):
        return f"↪️ HTTP {code}（发生跳转）"
    if code in (401, 403):
        return f"🔒 HTTP {code}（需要授权或访问被拒绝）"
    if code == 404:
        return "❌ HTTP 404（地址不存在）"
    return f"⚠️ HTTP {code}"


def update_artifact_links(cfg, repo):
    """生成两个聚合入口和所有线路的原始/代理地址与 HTTP 状态。"""
    if not os.path.exists(README_PATH):
        return
    with open(README_PATH, "r", encoding="utf-8") as f:
        readme = f.read()
    if ARTIFACTS_START not in readme or ARTIFACTS_END not in readme:
        print("[WARN] README 缺少地址区域标记，跳过地址更新")
        return

    files = [
        ("多仓订阅", "多仓订阅.json"),
        ("单仓聚合", "单仓聚合.json"),
    ]
    for src in cfg.get("sources", []):
        sid = src.get("id", src.get("name", "未知"))
        name = src.get("name", sid)
        files.append((f"线路 · {name}", f"{sid}.json"))

    lines = [
        "以下地址由 GitHub Actions 自动更新，每个代码框右上角可复制。",
        "",
        "> HTTP 状态为 GitHub Actions 运行环境中的检测结果，不代表所有手机、电视或网络均可访问。",
        "",
    ]

    for label, filename in files:
        encoded = urllib.parse.quote(filename, safe="")
        raw_url = f"https://raw.githubusercontent.com/{repo}/main/output/{encoded}"
        proxy_url = f"https://gh-proxy.org/{raw_url}"

        print(f"[HTTP] 检测原始直链：{label}")
        raw_result = check_http_url(raw_url)
        raw_status = http_status_text(raw_result)
        print(f"       {raw_status}")

        print(f"[HTTP] 检测代理链接：{label}")
        proxy_result = check_http_url(proxy_url)
        proxy_status = http_status_text(proxy_result)
        print(f"       {proxy_status}")

        lines.extend([
            f"### {label}",
            "",
            f"**原始直链状态：{raw_status}**",
            "",
            "**原始直链**",
            "",
            "```text",
            raw_url,
            "```",
            "",
            f"**代理链接状态：{proxy_status}**",
            "",
            "**代理链接**",
            "",
            "```text",
            proxy_url,
            "```",
            "",
        ])

    block = "\n".join(lines).rstrip()
    prefix = readme.split(ARTIFACTS_START, 1)[0]
    suffix = readme.split(ARTIFACTS_END, 1)[1]
    with open(README_PATH, "w", encoding="utf-8") as f:
        f.write(f"{prefix}{ARTIFACTS_START}\n{block}\n{ARTIFACTS_END}{suffix}")


def write_badge(ok_count, total):
    if total == 0 or ok_count == 0:
        color = "red"
    elif ok_count < total:
        color = "orange"
    else:
        color = "green"
    badge = {
        "schemaVersion": 1,
        "label": "TVBox 配置",
        "message": f"{ok_count}/{total} 可用",
        "color": color,
    }
    with open(os.path.join(OUTPUT_DIR, "shield.json"), "w", encoding="utf-8") as f:
        json.dump(badge, f, ensure_ascii=False, indent=2)


def main() -> int:
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    tz = datetime.timezone(datetime.timedelta(hours=8))
    now_str = datetime.datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")

    repo = (
        os.environ.get("GITHUB_REPOSITORY")
        or os.environ.get("REPO")
        or DEFAULT_REPO
    )
    raw_base = f"https://gh-proxy.org/https://raw.githubusercontent.com/{repo}/main/output"

    status = {"updated_at": now_str, "repo": repo, "sources": []}
    subscriptions = []
    fetched = []
    ok_count = 0

    for src in cfg.get("sources", []):
        name = src.get("name", "未知")
        sid = src.get("id", name)
        urls = src.get("urls", [])
        entry = {"id": sid, "name": name, "ok": False, "used_url": None, "error": None}
        saved = False

        for url in urls:
            try:
                data = fetch_config(url)
                out_path = os.path.join(OUTPUT_DIR, f"{sid}.json")
                with open(out_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                entry["ok"] = True
                entry["used_url"] = url
                entry["error"] = None
                subscriptions.append({"name": name, "url": f"{raw_base}/{sid}.json"})
                fetched.append((sid, name, data))
                ok_count += 1
                saved = True
                print(f"[OK]   {name} <- {url}")
                break
            except Exception as e:
                print(f"[FAIL] {name}  {url}  ->  {e}")
                entry["error"] = str(e)
            time.sleep(URL_INTERVAL)

        if not saved:
            cache_path = os.path.join(OUTPUT_DIR, f"{sid}.json")
            if os.path.exists(cache_path):
                try:
                    with open(cache_path, "r", encoding="utf-8") as f:
                        cached = json.load(f)
                    if is_valid_tvbox_config(cached):
                        subscriptions.append({"name": name, "url": f"{raw_base}/{sid}.json"})
                        fetched.append((sid, name, cached))
                        entry["ok"] = True
                        entry["cached"] = True
                        entry["used_url"] = "(缓存)"
                        ok_count += 1
                        saved = True
                        print(f"[CACHE] {name} 使用上次缓存配置")
                except Exception:
                    pass
            if not saved:
                entry["error"] = entry["error"] or "所有地址均失败"
                print(f"[FAIL] {name} 全部地址失败")
        status["sources"].append(entry)
        time.sleep(SOURCE_INTERVAL)

    sub_path = os.path.join(OUTPUT_DIR, "多仓订阅.json")
    with open(sub_path, "w", encoding="utf-8") as f:
        json.dump({"urls": subscriptions}, f, ensure_ascii=False, indent=2)

    merged = merge_configs(fetched)
    merged_path = os.path.join(OUTPUT_DIR, "单仓聚合.json")
    if merged is not None:
        with open(merged_path, "w", encoding="utf-8") as f:
            json.dump(merged, f, ensure_ascii=False, indent=2)
        print(f"聚合单仓：{merged_path}（共 {len(merged.get('sites', []))} 个站点）")

    status["updated_at"] = datetime.datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")
    with open(os.path.join(OUTPUT_DIR, "status.json"), "w", encoding="utf-8") as f:
        json.dump(status, f, ensure_ascii=False, indent=2)

    total = len(cfg.get("sources", []))
    write_badge(ok_count, total)
    update_readme(status, ok_count, total)
    update_artifact_links(cfg, repo)

    print("\n" + "=" * 50)
    print(f"本次更新：{ok_count}/{total} 个源成功")
    print(f"多仓订阅：{sub_path}")
    if ok_count == 0:
        print("没有任何源更新成功，返回非 0 退出码。")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
