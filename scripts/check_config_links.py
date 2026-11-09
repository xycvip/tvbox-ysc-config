#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Check every HTTP(S) URL found in every JSON file under config/."""
import concurrent.futures
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path("config")
OUT = Path("link-check-output")
TIMEOUT = 15
MAX_SAMPLE = 4096
URL_RE = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)


def normalize_url(value):
    value = value.strip().rstrip(".,;，。；）)]}、")
    parts = urlsplit(value)
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        raise ValueError("不是有效的 HTTP(S) URL")
    host = parts.hostname
    host_ascii = f"[{host}]" if ":" in host else host.encode("idna").decode("ascii")
    userinfo = ""
    if parts.username is not None:
        from urllib.parse import quote
        userinfo = quote(parts.username, safe="!$&'()*+,;=:")
        if parts.password is not None:
            userinfo += ":" + quote(parts.password, safe="!$&'()*+,;=:")
        userinfo += "@"
    netloc = userinfo + host_ascii
    if parts.port is not None:
        netloc += f":{parts.port}"
    from urllib.parse import quote, urlunsplit
    path = quote(parts.path or "/", safe="/%:@!$&'()*+,;=-._~")
    query = quote(parts.query, safe="/?%:@!$&'()*+,;=-._~[]")
    return urlunsplit((parts.scheme.lower(), netloc, path, query, ""))


def walk_strings(value, path="$"):
    if isinstance(value, dict):
        for key, child in value.items():
            yield from walk_strings(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from walk_strings(child, f"{path}[{index}]")
    elif isinstance(value, str):
        for match in URL_RE.findall(value):
            yield match, path


def load_sources():
    files = sorted(ROOT.rglob("*.json")) if ROOT.exists() else []
    if not files:
        raise RuntimeError("config/ 下没有找到任何 JSON 文件")
    occurrences = []
    for file in files:
        try:
            data = json.loads(file.read_text(encoding="utf-8-sig"))
        except Exception as exc:
            raise RuntimeError(f"JSON 文件解析失败：{file}：{exc}") from exc
        for raw_url, json_path in walk_strings(data):
            try:
                url = normalize_url(raw_url)
            except Exception as exc:
                occurrences.append({
                    "file": file.as_posix(), "json_path": json_path,
                    "url": raw_url, "normalized_url": None,
                    "status": "INVALID_URL", "error": str(exc)
                })
                continue
            occurrences.append({
                "file": file.as_posix(), "json_path": json_path,
                "url": raw_url, "normalized_url": url
            })
    return files, occurrences


def check_url(url):
    started = time.monotonic()
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (compatible; TVBoxConfigLinkChecker/1.0)",
        "Accept": "*/*",
        "Accept-Encoding": "identity",
        "Range": f"bytes=0-{MAX_SAMPLE - 1}",
    })
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as response:
            sample = response.read(MAX_SAMPLE)
            code = response.getcode()
            return {
                "status": "OK" if 200 <= code < 400 else "HTTP_ERROR",
                "http_status": code,
                "final_url": response.geturl(),
                "content_type": response.headers.get("Content-Type", ""),
                "sample_bytes": len(sample),
                "elapsed_ms": round((time.monotonic() - started) * 1000),
                "error": None,
            }
    except urllib.error.HTTPError as exc:
        return {
            "status": "HTTP_ERROR", "http_status": exc.code,
            "final_url": exc.geturl(), "content_type": exc.headers.get("Content-Type", ""),
            "sample_bytes": 0, "elapsed_ms": round((time.monotonic() - started) * 1000),
            "error": str(exc.reason),
        }
    except Exception as exc:
        return {
            "status": "ERROR", "http_status": None, "final_url": None,
            "content_type": "", "sample_bytes": 0,
            "elapsed_ms": round((time.monotonic() - started) * 1000),
            "error": f"{type(exc).__name__}: {exc}",
        }


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    files, occurrences = load_sources()
    unique_urls = sorted({item["normalized_url"] for item in occurrences if item.get("normalized_url")})
    results = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
        futures = {pool.submit(check_url, url): url for url in unique_urls}
        for future in concurrent.futures.as_completed(futures):
            url = futures[future]
            try:
                results[url] = future.result()
            except Exception as exc:
                results[url] = {"status": "ERROR", "http_status": None,
                                "final_url": None, "content_type": "",
                                "sample_bytes": 0, "elapsed_ms": 0,
                                "error": f"{type(exc).__name__}: {exc}"}

    rows = []
    for item in occurrences:
        row = dict(item)
        if row.get("normalized_url"):
            row.update(results[row["normalized_url"]])
        rows.append(row)

    ok = sum(row.get("status") == "OK" for row in rows)
    failed = len(rows) - ok
    summary = {
        "json_files": len(files),
        "link_occurrences": len(rows),
        "unique_urls": len(unique_urls),
        "ok_occurrences": ok,
        "failed_occurrences": failed,
        "results": rows,
    }
    (OUT / "config-link-report.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    lines = [
        "# Config JSON 链接检测报告", "",
        f"- JSON 文件：{len(files)}",
        f"- 链接出现次数：{len(rows)}",
        f"- 去重后实际检测：{len(unique_urls)}",
        f"- 可访问：{ok}",
        f"- 异常/不可访问：{failed}", "",
        "| 状态 | HTTP | 文件 | JSON 路径 | URL | 耗时 | 结果/错误 |",
        "|---|---:|---|---|---|---:|---|",
    ]
    for row in rows:
        status = row.get("status", "UNKNOWN")
        http = row.get("http_status") or "-"
        elapsed = f'{row.get("elapsed_ms", "-")} ms' if "elapsed_ms" in row else "-"
        detail = row.get("error") or row.get("content_type", "")
        url = row.get("normalized_url") or row["url"]
        lines.append(
            f"| {status} | {http} | {row['file']} | {row['json_path']} | "
            f"{url.replace('|', '%7C')} | {elapsed} | {str(detail).replace('|', '/')} |"
        )
    (OUT / "config-link-report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"\nREPORT_JSON={OUT / 'config-link-report.json'}")
    print(f"REPORT_MD={OUT / 'config-link-report.md'}")
    if failed:
        print(f"\nLINK_CHECK_FAILED: {failed} link occurrence(s) returned an error.")
        sys.exit(2)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"CONFIG_LINK_CHECK_FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)
