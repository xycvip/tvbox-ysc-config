#!/usr/bin/env python3
# 从 lubin776/tvbox-api-backup/list.txt 中补充 sources.json 的备用网址。
# 只更新已有 name；名称至少共享连续两个字符；只写入 HTTP 可访问且未重复的 URL。
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

SOURCE_LIST_URL = (
    "https://raw.githubusercontent.com/lubin776/tvbox-api-backup/main/list.txt"
)
TARGET_FILE = Path("config/sources.json")
TIMEOUT = 15
USER_AGENT = "Mozilla/5.0 (compatible; TVBoxSourceSync/1.0)"


def fetch_text(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as response:
        return response.read().decode("utf-8-sig")


def normalized_name(name):
    name = name.strip()
    name = re.sub(r"\.json$", "", name, flags=re.I)
    name = re.sub(r"(?:\d+线|线路)$", "", name)
    return re.sub(r"\s+", "", name)


def shares_two_chars(left, right):
    """至少共享一个连续的双字符片段，避免单字误匹配。"""
    a, b = normalized_name(left), normalized_name(right)
    if len(a) < 2 or len(b) < 2:
        return False
    if a in b or b in a:
        return True
    bigrams = {a[i:i + 2] for i in range(len(a) - 1)}
    return any(b[i:i + 2] in bigrams for i in range(len(b) - 1))


def canonical_url(url):
    """用于去重：忽略 scheme/host 大小写及末尾多余斜杠。"""
    value = url.strip()
    try:
        p = urllib.parse.urlsplit(value)
        scheme = p.scheme.lower()
        host = (p.hostname or "").lower()
        if not scheme or not host:
            return value.rstrip("/")
        try:
            host = host.encode("idna").decode("ascii")
        except UnicodeError:
            pass
        port = f":{p.port}" if p.port else ""
        userinfo = ""
        if p.username:
            userinfo = p.username
            if p.password:
                userinfo += ":" + p.password
            userinfo += "@"
        path = p.path.rstrip("/") or "/"
        return urllib.parse.urlunsplit(
            (scheme, userinfo + host + port, path, p.query, p.fragment)
        )
    except Exception:
        return value.rstrip("/")


def is_accessible(url):
    """GET 实测；跟随重定向，要求最终 HTTP 状态为 2xx。"""
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "*/*",
                "Range": "bytes=0-0",
            },
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=TIMEOUT) as response:
            status = getattr(response, "status", response.getcode())
            response.read(1)
            return 200 <= status < 300, f"HTTP {status}"
    except urllib.error.HTTPError as exc:
        return False, f"HTTP {exc.code}"
    except Exception as exc:
        return False, type(exc).__name__


def parse_backup_list(content):
    rows = []
    for line_no, raw in enumerate(content.splitlines(), 1):
        line = raw.strip()
        if not line or "|" not in line:
            continue
        parts = [part.strip() for part in line.split("|")]
        if len(parts) < 4:
            print(f"[跳过] list.txt 第 {line_no} 行格式不完整")
            continue
        name = normalized_name(parts[0])
        url = parts[-1]
        if name and url.startswith(("http://", "https://")):
            rows.append((name, url))
    return rows


def locate_url_arrays(text):
    """按 sources 对象的 name 行定位 urls 数组，避免重新序列化整个 JSON。"""
    lines = text.splitlines(keepends=True)
    found = []
    name_re = re.compile(r'^(\s*)"name"\s*:\s*("(?:\\.|[^"\\])*")\s*,?\s*
    urls_re = re.compile(r'^(\s*)"urls"\s*:\s*\[\s*$')

    for i, line in enumerate(lines):
        match = name_re.match(line.rstrip("\r\n"))
        if not match:
            continue
        try:
            name = json.loads(match.group(2))
        except Exception:
            continue

        urls_line = None
        for j in range(i + 1, len(lines)):
            if name_re.match(lines[j].rstrip("\r\n")):
                break
            if urls_re.match(lines[j].rstrip("\r\n")):
                urls_line = j
                break
        if urls_line is None:
            continue

        close_line = None
        for j in range(urls_line + 1, len(lines)):
            if re.match(r"^\s*\]\s*,?\s*(?:\r?\n)?$", lines[j]):
                close_line = j
                break
        if close_line is None:
            continue

        urls = []
        for j in range(urls_line + 1, close_line):
            item = lines[j].strip().rstrip(",")
            if item:
                try:
                    urls.append(json.loads(item))
                except Exception:
                    pass
        found.append({
            "name": name,
            "urls": urls,
            "open_line": urls_line,
            "close_line": close_line,
        })
    return lines, found


def main():
    if not TARGET_FILE.exists():
        raise SystemExit(f"找不到目标文件：{TARGET_FILE}")

    original = TARGET_FILE.read_text(encoding="utf-8")
    try:
        parsed = json.loads(original)
    except Exception as exc:
        raise SystemExit(f"sources.json 不是有效 JSON，停止修改：{exc}")

    targets = parsed.get("sources")
    if not isinstance(targets, list):
        raise SystemExit("sources.json 缺少 sources 数组，停止修改")

    backup_text = fetch_text(SOURCE_LIST_URL)
    backup_rows = parse_backup_list(backup_text)
    lines, located = locate_url_arrays(original)

    # 以 JSON 实际结构为准，且要求 name 唯一对应到文本中的 urls 数组。
    target_by_name = {}
    for item in targets:
        if isinstance(item, dict) and isinstance(item.get("name"), str):
            target_by_name.setdefault(item["name"], item)

    additions = {}
    for target_name, target_obj in target_by_name.items():
        if not isinstance(target_obj.get("urls"), list):
            continue
        candidates = []
        for backup_name, url in backup_rows:
            if shares_two_chars(target_name, backup_name):
                candidates.append(url)

        existing = {canonical_url(u) for u in target_obj["urls"] if isinstance(u, str)}
        accepted = []
        for url in candidates:
            key = canonical_url(url)
            if key in existing:
                print(f"[重复] {target_name}: {url}")
                continue
            ok, detail = is_accessible(url)
            if not ok:
                print(f"[不可访问] {target_name}: {url} ({detail})")
                continue
            print(f"[可访问] {target_name}: {url} ({detail})")
            existing.add(key)
            accepted.append(url)
        if accepted:
            additions[target_name] = accepted

    if not additions:
        print("没有符合条件的可写入备用网址，文件保持不变。")
        return

    # 只在对应 urls 数组内插入新行，其他字节级文本布局不重新格式化。
    insertions = {}
    for item in located:
        name = item["name"]
        if name not in additions:
            continue
        # 缩进沿用 urls 数组现有元素；空数组则按 urls 键缩进增加 4 个空格。
        open_indent = re.match(r"^\s*", lines[item["open_line"]]).group(0)
        element_indent = None
        for j in range(item["open_line"] + 1, item["close_line"]):
            if lines[j].strip():
                element_indent = re.match(r"^\s*", lines[j]).group(0)
                break
        if element_indent is None:
            element_indent = open_indent + "  "

        close_line = item["close_line"]
        prior = close_line - 1
        while prior > item["open_line"] and not lines[prior].strip():
            prior -= 1
        if prior > item["open_line"]:
            previous_text = lines[prior].rstrip("\r\n").rstrip()
            if not previous_text.endswith(","):
                lines[prior] = previous_text + ",\n"

        new_lines = []
        for index, url in enumerate(additions[name]):
            comma = "," if index < len(additions[name]) - 1 else ""
            new_lines.append(
                element_indent + json.dumps(url, ensure_ascii=False) + comma + "\n"
            )
        insertions.setdefault(close_line, []).extend(new_lines)

    for index in sorted(insertions.keys(), reverse=True):
        lines[index:index] = insertions[index]

    updated = "".join(lines)
    # 写盘前再次校验，防止任何意外破坏 JSON。
    json.loads(updated)
    TARGET_FILE.write_text(updated, encoding="utf-8", newline="")
    total = sum(len(urls) for urls in additions.values())
    print(f"完成：为 {len(additions)} 个已有来源新增 {total} 条可访问备用网址。")


if __name__ == "__main__":
    main()
)
    urls_re = re.compile(r'^(\s*)"urls"\s*:\s*\[\s*$')

    for i, line in enumerate(lines):
        match = name_re.match(line.rstrip("\r\n"))
        if not match:
            continue
        try:
            name = json.loads(match.group(2))
        except Exception:
            continue

        urls_line = None
        for j in range(i + 1, len(lines)):
            if name_re.match(lines[j].rstrip("\r\n")):
                break
            if urls_re.match(lines[j].rstrip("\r\n")):
                urls_line = j
                break
        if urls_line is None:
            continue

        close_line = None
        for j in range(urls_line + 1, len(lines)):
            if re.match(r"^\s*\]\s*,?\s*(?:\r?\n)?$", lines[j]):
                close_line = j
                break
        if close_line is None:
            continue

        urls = []
        for j in range(urls_line + 1, close_line):
            item = lines[j].strip().rstrip(",")
            if item:
                try:
                    urls.append(json.loads(item))
                except Exception:
                    pass
        found.append({
            "name": name,
            "urls": urls,
            "open_line": urls_line,
            "close_line": close_line,
        })
    return lines, found


def main():
    if not TARGET_FILE.exists():
        raise SystemExit(f"找不到目标文件：{TARGET_FILE}")

    original = TARGET_FILE.read_text(encoding="utf-8")
    try:
        parsed = json.loads(original)
    except Exception as exc:
        raise SystemExit(f"sources.json 不是有效 JSON，停止修改：{exc}")

    targets = parsed.get("sources")
    if not isinstance(targets, list):
        raise SystemExit("sources.json 缺少 sources 数组，停止修改")

    backup_text = fetch_text(SOURCE_LIST_URL)
    backup_rows = parse_backup_list(backup_text)
    lines, located = locate_url_arrays(original)

    # 以 JSON 实际结构为准，且要求 name 唯一对应到文本中的 urls 数组。
    target_by_name = {}
    for item in targets:
        if isinstance(item, dict) and isinstance(item.get("name"), str):
            target_by_name.setdefault(item["name"], item)

    additions = {}
    for target_name, target_obj in target_by_name.items():
        if not isinstance(target_obj.get("urls"), list):
            continue
        candidates = []
        for backup_name, url in backup_rows:
            if shares_two_chars(target_name, backup_name):
                candidates.append(url)

        existing = {canonical_url(u) for u in target_obj["urls"] if isinstance(u, str)}
        accepted = []
        for url in candidates:
            key = canonical_url(url)
            if key in existing:
                print(f"[重复] {target_name}: {url}")
                continue
            ok, detail = is_accessible(url)
            if not ok:
                print(f"[不可访问] {target_name}: {url} ({detail})")
                continue
            print(f"[可访问] {target_name}: {url} ({detail})")
            existing.add(key)
            accepted.append(url)
        if accepted:
            additions[target_name] = accepted

    if not additions:
        print("没有符合条件的可写入备用网址，文件保持不变。")
        return

    # 只在对应 urls 数组内插入新行，其他字节级文本布局不重新格式化。
    insertions = {}
    for item in located:
        name = item["name"]
        if name not in additions:
            continue
        # 缩进沿用 urls 数组现有元素；空数组则按 urls 键缩进增加 4 个空格。
        open_indent = re.match(r"^\s*", lines[item["open_line"]]).group(0)
        element_indent = None
        for j in range(item["open_line"] + 1, item["close_line"]):
            if lines[j].strip():
                element_indent = re.match(r"^\s*", lines[j]).group(0)
                break
        if element_indent is None:
            element_indent = open_indent + "  "

        close_line = item["close_line"]
        prior = close_line - 1
        while prior > item["open_line"] and not lines[prior].strip():
            prior -= 1
        if prior > item["open_line"]:
            previous_text = lines[prior].rstrip("\r\n").rstrip()
            if not previous_text.endswith(","):
                lines[prior] = previous_text + ",\n"

        new_lines = []
        for index, url in enumerate(additions[name]):
            comma = "," if index < len(additions[name]) - 1 else ""
            new_lines.append(
                element_indent + json.dumps(url, ensure_ascii=False) + comma + "\n"
            )
        insertions.setdefault(close_line, []).extend(new_lines)

    for index in sorted(insertions.keys(), reverse=True):
        lines[index:index] = insertions[index]

    updated = "".join(lines)
    # 写盘前再次校验，防止任何意外破坏 JSON。
    json.loads(updated)
    TARGET_FILE.write_text(updated, encoding="utf-8", newline="")
    total = sum(len(urls) for urls in additions.values())
    print(f"完成：为 {len(additions)} 个已有来源新增 {total} 条可访问备用网址。")


if __name__ == "__main__":
    main()
