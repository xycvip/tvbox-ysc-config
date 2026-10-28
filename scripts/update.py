#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
影视仓 / TVBox 配置自动更新脚本

保留原仓库功能：
- 从 config/sources.json 依次尝试主地址和备用地址
- 支持中文域名、普通 JSON/JSONC 和部分图片伪装的 Base64 JSON
- 校验 TVBox 单仓配置或多仓订阅
- 写入 output/<id>.json、output/多仓订阅.json、output/单仓聚合.json
- 保留旧缓存；生成 status.json、shield.json；更新 README 状态和直链状态
"""
import base64
import binascii
import datetime
import json
import os
import re
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
TIMEOUT = 20
MAX_RETRIES = 2
RETRY_BACKOFF = 2
URL_INTERVAL = 1.0
SOURCE_INTERVAL = 2

TVBOX_KEYS = ("sites", "spiders", "lives", "store", "exts", "parses", "flags")
STATUS_START = "<!-- STATUS_START -->"
STATUS_END = "<!-- STATUS_END -->"
ARTIFACTS_START = "<!-- ARTIFACT_LINKS_START -->"
ARTIFACTS_END = "<!-- ARTIFACT_LINKS_END -->"


def _strip_js_comments(text):
    """删除整行 // 注释；避免破坏 URL 中的 //。"""
    return "\n".join(line for line in text.splitlines()
                     if not line.lstrip().startswith("//"))


def parse_json_lenient(text):
    text = text.lstrip("\ufeff").strip()
    text = _strip_js_comments(text)
    try:
        return json.loads(text, strict=False)
    except json.JSONDecodeError:
        # 某些接口在 JSON 后附加少量字符；只取开头完整 JSON 对象/数组。
        obj, _ = json.JSONDecoder(strict=False).raw_decode(text)
        return obj


def idn_encode(url):
    parsed = urllib.parse.urlsplit(url)
    host = parsed.hostname or ""
    try:
        encoded_host = host.encode("idna").decode("ascii")
    except Exception:
        encoded_host = host
    netloc = encoded_host
    if parsed.port:
        netloc = f"{encoded_host}:{parsed.port}"
    if parsed.username:
        auth = parsed.username
        if parsed.password:
            auth += ":" + parsed.password
        netloc = auth + "@" + netloc
    return urllib.parse.urlunsplit(
        (parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment)
    )


def is_valid_tvbox_config(data):
    if not isinstance(data, dict):
        return False
    # 单仓常见结构
    if any(key in data for key in TVBOX_KEYS):
        return True
    # 多仓订阅必须是非空 urls 数组，元素通常为对象或字符串
    urls = data.get("urls")
    return isinstance(urls, list) and bool(urls)


def _looks_like_json(raw):
    sample = raw.lstrip(b"\xef\xbb\xbf \t\r\n")
    return sample.startswith((b"{", b"["))


def _parse_json_bytes(raw):
    # UTF-8 BOM 兼容；TVBox 配置通常是 UTF-8 JSON 文本。
    text = raw.decode("utf-8-sig", errors="strict")
    return parse_json_lenient(text)


def _decode_base64_candidate(payload):
    """尝试解码图片伪装载荷里的 Base64 JSON。"""
    payload = payload.strip()
    if not payload:
        return None
    # 分隔符后可能有换行/空格，也可能有少量尾部空白。
    candidates = [payload, b"".join(payload.split())]
    for candidate in candidates:
        # 只尝试看起来像 Base64 的内容；避免大段图片二进制误解码。
        if len(candidate) < 16:
            continue
        try:
            decoded = base64.b64decode(candidate, validate=True)
        except (binascii.Error, ValueError):
            continue
        try:
            data = _parse_json_bytes(decoded)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            continue
        if is_valid_tvbox_config(data):
            return data
    return None


def _parse_image_wrapped_config(raw):
    """
    兼容部分公开 TVBox 源的图片伪装方式。
    已知一种方式是在 JPEG EOI (FF D9) 后追加内容，并以 ** 分隔后跟 Base64 JSON。
    也会扫描完整响应，以兼容其他图片头；最终仍必须通过 JSON 结构校验。
    这不是对所有 .bmp 源的保证，格式不匹配时会返回明确错误。
    """
    payloads = []
    eoi = raw.find(b"\xff\xd9")
    if eoi >= 0:
        payloads.append(raw[eoi + 2:])
    payloads.append(raw)

    seen = set()
    for payload in payloads:
        start = 0
        while True:
            marker = payload.find(b"**", start)
            if marker < 0:
                break
            start = marker + 2
            candidate = payload[start:].strip()
            if not candidate or candidate in seen:
                continue
            seen.add(candidate)
            data = _decode_base64_candidate(candidate)
            if data is not None:
                return data
    raise ValueError("没有找到可解码的 '**' + Base64 JSON 载荷")


def _response_diagnostic(resp, raw):
    if resp is None:
        return "没有收到 HTTP 响应"
    preview_bytes = raw[:96]
    preview = preview_bytes.decode("utf-8", errors="replace")
    preview = " ".join(preview.split())
    if not preview:
        preview = repr(preview_bytes)
    # 只打印短预览，避免把整份配置或敏感内容写进日志。
    return (
        f"HTTP={resp.status_code}; final_url={resp.url}; "
        f"Content-Type={resp.headers.get('Content-Type', '')!r}; "
        f"Content-Disposition={resp.headers.get('Content-Disposition', '')!r}; "
        f"bytes={len(raw)}; preview={preview[:96]!r}"
    )


def _should_retry(err, resp_text_len=-1):
    if isinstance(err, (requests.exceptions.Timeout,
                        requests.exceptions.ConnectionError)):
        return True
    if isinstance(err, requests.exceptions.HTTPError):
        return False
    return resp_text_len <= 0


def fetch_config(url):
    """下载并解析 TVBox 配置；普通 JSON 优先，图片伪装解析其次。"""
    target = idn_encode(url)
    last_err = None
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "*/*",
        "Accept-Encoding": "identity",
        "Connection": "close",
    }

    for attempt in range(1, MAX_RETRIES + 1):
        resp = None
        raw = b""
        try:
            resp = requests.get(
                target, headers=headers, timeout=TIMEOUT, allow_redirects=True
            )
            raw = resp.content
            resp.raise_for_status()

            direct_error = None
            try:
                data = _parse_json_bytes(raw)
                if is_valid_tvbox_config(data):
                    return data
                direct_error = "响应可解析为 JSON，但缺少 TVBox 单仓/多仓关键字段"
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                direct_error = f"普通 JSON 解析失败：{exc}"

            try:
                data = _parse_image_wrapped_config(raw)
                if is_valid_tvbox_config(data):
                    print(f"      [解码] 图片伪装配置解析成功：{url}")
                    return data
            except Exception as wrapped_exc:
                raise ValueError(
                    f"{direct_error}；伪装载荷解析失败：{wrapped_exc}；"
                    f"响应诊断：{_response_diagnostic(resp, raw)}"
                ) from wrapped_exc

        except Exception as exc:
            last_err = exc
            if _should_retry(exc, len(raw)) and attempt < MAX_RETRIES:
                wait = RETRY_BACKOFF * attempt * 2
                print(f"      (第 {attempt} 次失败：{exc}，{wait}s 后重试)")
                time.sleep(wait)
            else:
                break
    if last_err is None:
        raise RuntimeError(f"请求失败：{url}")
    raise last_err


def check_http_url(url, timeout=10):
    result = {"url": url, "http_status": None, "final_url": None, "error": None}
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
    except requests.exceptions.RequestException as exc:
        result["error"] = f"连接失败：{exc.__class__.__name__}"
    except Exception as exc:
        result["error"] = f"检测异常：{exc.__class__.__name__}"
    return result


def _merge_list(fetched, field):
    seen, out = set(), []
    for _sid, _name, data in fetched:
        values = data.get(field, []) or []
        if not isinstance(values, list):
            continue
        for item in values:
            if isinstance(item, dict):
                ident = item.get("name") or item.get("url") or json.dumps(
                    item, ensure_ascii=False, sort_keys=True
                )
            else:
                ident = str(item)
            if ident not in seen:
                seen.add(ident)
                out.append(item)
    return out


def merge_configs(fetched):
    if not fetched:
        return None
    _base_sid, base_name, base = fetched[0]
    merged = {}
    skip = {"sites", "lives", "parses", "doh", "rules", "flags", "exts", "ads"}
    for key, value in base.items():
        if key not in skip:
            merged[key] = value
    merged["warningText"] = (
        f"本配置由 {len(fetched)} 个公开源自动聚合（含{base_name}等），"
        "仅供学习交流，请勿商用"
    )

    seen_keys, sites = set(), []
    for sid, _name, data in fetched:
        for site in data.get("sites", []) or []:
            if not isinstance(site, dict):
                continue
            key = site.get("key")
            if key is None:
                key = json.dumps(site, ensure_ascii=False, sort_keys=True)
            new_key = key
            if key in seen_keys:
                new_key = f"{sid}_{key}"
            if new_key in seen_keys:
                continue
            seen_keys.add(new_key)
            if new_key != key:
                site = dict(site)
                site["key"] = new_key
            sites.append(site)
    merged["sites"] = sites
    for field in ("lives", "parses", "doh", "rules", "flags", "exts"):
        merged[field] = _merge_list(fetched, field)
    return merged


def update_readme(status, ok_count, total):
    if not os.path.exists(README_PATH):
        return
    with open(README_PATH, "r", encoding="utf-8") as file:
        readme = file.read()
    if STATUS_START not in readme or STATUS_END not in readme:
        return

    lines = [
        f"> 🔄 最近更新：{status['updated_at']}（北京时间） · 成功 **{ok_count}/{total}** 个源",
        "",
        "| 配置源 | 状态 | 使用地址 |",
        "| --- | --- | --- |",
    ]
    for source in status["sources"]:
        if source.get("cached"):
            lines.append(
                f"| {source['name']} | 📦 缓存 | 上次成功配置（本次更新未抓到，沿用缓存） |"
            )
        elif source["ok"]:
            lines.append(f"| {source['name']} | ✅ 成功 | `{source['used_url']}` |")
        else:
            error = (source.get("error") or "").replace("|", "/")
            if len(error) > 100:
                error = error[:97] + "..."
            lines.append(f"| {source['name']} | ❌ 失败 | {error} |")

    block = "\n".join(lines)
    prefix = readme.split(STATUS_START, 1)[0]
    suffix = readme.split(STATUS_END, 1)[1]
    with open(README_PATH, "w", encoding="utf-8") as file:
        file.write(f"{prefix}{STATUS_START}\n{block}\n{STATUS_END}{suffix}")


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
    if not os.path.exists(README_PATH):
        return
    with open(README_PATH, "r", encoding="utf-8") as file:
        readme = file.read()
    if ARTIFACTS_START not in readme or ARTIFACTS_END not in readme:
        print("[WARN] README 缺少地址区域标记，跳过地址更新")
        return

    files = [("多仓订阅", "多仓订阅.json"), ("单仓聚合", "单仓聚合.json")]
    for source in cfg.get("sources", []):
        sid = source.get("id", source.get("name", "未知"))
        files.append((f"线路 · {source.get('name', sid)}", f"{sid}.json"))

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
        raw_status = http_status_text(check_http_url(raw_url))
        print(f"       {raw_status}")
        print(f"[HTTP] 检测代理链接：{label}")
        proxy_status = http_status_text(check_http_url(proxy_url))
        print(f"       {proxy_status}")
        lines.extend([
            f"### {label}", "",
            f"**原始直链状态：{raw_status}**", "",
            "**原始直链**", "", "```text", raw_url, "```", "",
            f"**代理链接状态：{proxy_status}**", "",
            "**代理链接**", "", "```text", proxy_url, "```", "",
        ])

    block = "\n".join(lines).rstrip()
    prefix = readme.split(ARTIFACTS_START, 1)[0]
    suffix = readme.split(ARTIFACTS_END, 1)[1]
    with open(README_PATH, "w", encoding="utf-8") as file:
        file.write(f"{prefix}{ARTIFACTS_START}\n{block}\n{ARTIFACTS_END}{suffix}")


def write_badge(ok_count, total):
    color = "red" if total == 0 or ok_count == 0 else (
        "orange" if ok_count < total else "green"
    )
    badge = {
        "schemaVersion": 1,
        "label": "TVBox 配置",
        "message": f"{ok_count}/{total} 可用",
        "color": color,
    }
    with open(os.path.join(OUTPUT_DIR, "shield.json"), "w", encoding="utf-8") as file:
        json.dump(badge, file, ensure_ascii=False, indent=2)


def main():
    with open(CONFIG_FILE, "r", encoding="utf-8") as file:
        cfg = json.load(file)
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    tz = datetime.timezone(datetime.timedelta(hours=8))
    now_str = datetime.datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")
    repo = (os.environ.get("GITHUB_REPOSITORY")
            or os.environ.get("REPO") or DEFAULT_REPO)
    raw_base = (
        f"https://gh-proxy.org/https://raw.githubusercontent.com/"
        f"{repo}/main/output"
    )

    status = {"updated_at": now_str, "repo": repo, "sources": []}
    subscriptions, fetched = [], []
    ok_count = 0

    for source in cfg.get("sources", []):
        name = source.get("name", "未知")
        sid = source.get("id", name)
        urls = source.get("urls", [])
        entry = {
            "id": sid, "name": name, "ok": False,
            "used_url": None, "error": None,
        }
        saved = False

        for url in urls:
            try:
                data = fetch_config(url)
                out_path = os.path.join(OUTPUT_DIR, f"{sid}.json")
                with open(out_path, "w", encoding="utf-8") as file:
                    json.dump(data, file, ensure_ascii=False, indent=2)
                entry.update({"ok": True, "used_url": url, "error": None})
                subscriptions.append({"name": name, "url": f"{raw_base}/{sid}.json"})
                fetched.append((sid, name, data))
                ok_count += 1
                saved = True
                print(f"[OK]   {name} <- {url}")
                break
            except Exception as exc:
                error = str(exc)
                entry["error"] = error
                print(f"[FAIL] {name}  {url}  ->  {error}")
            time.sleep(URL_INTERVAL)

        if not saved:
            cache_path = os.path.join(OUTPUT_DIR, f"{sid}.json")
            if os.path.exists(cache_path):
                try:
                    with open(cache_path, "r", encoding="utf-8") as file:
                        cached = json.load(file)
                    if is_valid_tvbox_config(cached):
                        subscriptions.append({"name": name, "url": f"{raw_base}/{sid}.json"})
                        fetched.append((sid, name, cached))
                        entry.update({
                            "ok": True, "cached": True,
                            "used_url": "(缓存)",
                        })
                        ok_count += 1
                        saved = True
                        print(f"[CACHE] {name} 使用上次缓存配置")
                except Exception as exc:
                    print(f"[WARN] {name} 缓存不可用：{exc}")
            if not saved:
                entry["error"] = entry["error"] or "所有地址均失败"
                print(f"[FAIL] {name} 全部地址失败")

        status["sources"].append(entry)
        time.sleep(SOURCE_INTERVAL)

    sub_path = os.path.join(OUTPUT_DIR, "多仓订阅.json")
    with open(sub_path, "w", encoding="utf-8") as file:
        json.dump({"urls": subscriptions}, file, ensure_ascii=False, indent=2)

    merged = merge_configs(fetched)
    if merged is not None:
        merged_path = os.path.join(OUTPUT_DIR, "单仓聚合.json")
        with open(merged_path, "w", encoding="utf-8") as file:
            json.dump(merged, file, ensure_ascii=False, indent=2)
        print(f"聚合单仓：{merged_path}（共 {len(merged.get('sites', []))} 个站点）")

    status["updated_at"] = datetime.datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")
    with open(os.path.join(OUTPUT_DIR, "status.json"), "w", encoding="utf-8") as file:
        json.dump(status, file, ensure_ascii=False, indent=2)

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
