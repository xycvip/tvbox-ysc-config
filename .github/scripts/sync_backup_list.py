#!/usr/bin/env python3
# 从备用接口列表补充 sources.json 中已有来源的备用网址。
# 仅匹配已有 name；仅写入测试可访问且未重复的 URL。
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

SOURCE_LIST_URL = "https://raw.githubusercontent.com/lubin776/tvbox-api-backup/main/list.txt"
TARGET_FILE = Path("config/sources.json")
TIMEOUT = 15
USER_AGENT = "Mozilla/5.0 (compatible; TVBoxSourceSync/1.0)"


def fetch_text(url):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        return response.read().decode("utf-8-sig")


def normalized_name(name):
    value = name.strip()
    value = re.sub(r"\.json$", "", value, flags=re.IGNORECASE)
    value = re.sub(r"(?:\d+线|线路)$", "", value)
    return re.sub(r"\s+", "", value)


def shares_two_chars(left, right):
    a = normalized_name(left)
    b = normalized_name(right)
    if len(a) < 2 or len(b) < 2:
        return False
    if a in b or b in a:
        return True
    pairs = {a[i:i + 2] for i in range(len(a) - 1)}
    return any(b[i:i + 2] in pairs for i in range(len(b) - 1))


def canonical_url(url):
    value = url.strip()
    try:
        parts = urllib.parse.urlsplit(value)
        scheme = parts.scheme.lower()
        host = (parts.hostname or "").lower()
        if not scheme or not host:
            return value.rstrip("/")
        try:
            host = host.encode("idna").decode("ascii")
        except UnicodeError:
            pass
        port = ":" + str(parts.port) if parts.port else ""
        auth = ""
        if parts.username:
            auth = parts.username
            if parts.password:
                auth += ":" + parts.password
            auth += "@"
        path = parts.path.rstrip("/") or "/"
        return urllib.parse.urlunsplit(
            (scheme, auth + host + port, path, parts.query, parts.fragment)
        )
    except Exception:
        return value.rstrip("/")


def to_request_url(url):
    parts = urllib.parse.urlsplit(url)
    host = parts.hostname or ""
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError:
        pass
    if ":" in host and not host.startswith("["):
        host = "[" + host + "]"
    if parts.port:
        host += ":" + str(parts.port)
    path = urllib.parse.quote(parts.path, safe="/%:@!$&'()*+,;=-._~")
    query = urllib.parse.quote(parts.query, safe="=&?/:@!$'()*+,;%-._~")
    return urllib.parse.urlunsplit(
        (parts.scheme, host, path, query, parts.fragment)
    )


def is_accessible(url):
    try:
        request = urllib.request.Request(
            to_request_url(url),
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "*/*",
                "Range": "bytes=0-0",
            },
            method="GET",
        )
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            status = response.getcode()
            response.read(1)
            return 200 <= status < 300, "HTTP " + str(status)
    except urllib.error.HTTPError as exc:
        return False, "HTTP " + str(exc.code)
    except Exception as exc:
        return False, type(exc).__name__


def parse_backup_list(content):
    rows = []
    for line_number, raw in enumerate(content.splitlines(), 1):
        line = raw.strip()
        if not line or "|" not in line:
            continue
        parts = [part.strip() for part in line.split("|")]
        if len(parts) < 4:
            print("[跳过] list.txt 第 " + str(line_number) + " 行格式不完整")
            continue
        name = normalized_name(parts[0])
        url = parts[-1]
        if name and url.startswith(("http://", "https://")):
            rows.append((name, url))
    return rows


def locate_url_arrays(source_text):
    lines = source_text.splitlines(keepends=True)
    found = []
    name_pattern = re.compile(
        r'^(\s*)"name"\s*:\s*("(?:\\.|[^"\\])*")\s*,?\s*$'
    )
    urls_pattern = re.compile(r'^(\s*)"urls"\s*:\s*\[\s*$')

    for index, line in enumerate(lines):
        match = name_pattern.match(line.rstrip("\r\n"))
        if not match:
            continue
        try:
            name = json.loads(match.group(2))
        except Exception:
            continue

        urls_line = None
        for cursor in range(index + 1, len(lines)):
            current_line = lines[cursor].rstrip("\r\n")
            if name_pattern.match(current_line):
                break
            if urls_pattern.match(current_line):
                urls_line = cursor
                break
        if urls_line is None:
            continue

        close_line = None
        for cursor in range(urls_line + 1, len(lines)):
            if re.match(r"^\s*\]\s*,?\s*$", lines[cursor].rstrip("\r\n")):
                close_line = cursor
                break
        if close_line is None:
            continue

        found.append({
            "name": name,
            "open_line": urls_line,
            "close_line": close_line,
        })
    return lines, found


def main():
    if not TARGET_FILE.exists():
        raise RuntimeError("找不到目标文件：" + str(TARGET_FILE))

    original = TARGET_FILE.read_text(encoding="utf-8")
    parsed = json.loads(original)
    targets = parsed.get("sources")
    if not isinstance(targets, list):
        raise RuntimeError("sources.json 缺少 sources 数组")

    backup_rows = parse_backup_list(fetch_text(SOURCE_LIST_URL))
    lines, located = locate_url_arrays(original)

    target_by_name = {}
    for item in targets:
        if isinstance(item, dict) and isinstance(item.get("name"), str):
            target_by_name[item["name"]] = item

    located_names = {item["name"] for item in located}
    additions = {}
    for target_name, target in target_by_name.items():
        if not isinstance(target.get("urls"), list):
            continue
        if target_name not in located_names:
            raise RuntimeError("无法安全定位来源 urls 数组：" + target_name)

        existing = {
            canonical_url(value)
            for value in target["urls"]
            if isinstance(value, str)
        }
        accepted = []
        for backup_name, url in backup_rows:
            if not shares_two_chars(target_name, backup_name):
                continue
            key = canonical_url(url)
            if key in existing:
                print("[重复] " + target_name + ": " + url)
                continue
            accessible, detail = is_accessible(url)
            if not accessible:
                print("[不可访问] " + target_name + ": " + url + " (" + detail + ")")
                continue
            print("[可访问] " + target_name + ": " + url + " (" + detail + ")")
            existing.add(key)
            accepted.append(url)
        if accepted:
            additions[target_name] = accepted

    if not additions:
        print("没有符合条件的可写入备用网址，文件保持不变。")
        return

    insertions = {}
    for item in located:
        name = item["name"]
        if name not in additions:
            continue
        open_line = item["open_line"]
        close_line = item["close_line"]
        open_indent = re.match(r"^\s*", lines[open_line]).group(0)
        element_indent = None
        for cursor in range(open_line + 1, close_line):
            if lines[cursor].strip():
                element_indent = re.match(r"^\s*", lines[cursor]).group(0)
                break
        if element_indent is None:
            element_indent = open_indent + "  "

        previous = close_line - 1
        while previous > open_line and not lines[previous].strip():
            previous -= 1
        if previous > open_line:
            previous_text = lines[previous].rstrip("\r\n").rstrip()
            if not previous_text.endswith(","):
                ending = "\r\n" if lines[previous].endswith("\r\n") else "\n"
                lines[previous] = previous_text + "," + ending

        new_lines = []
        urls = additions[name]
        for position, url in enumerate(urls):
            comma = "," if position < len(urls) - 1 else ""
            new_lines.append(
                element_indent + json.dumps(url, ensure_ascii=False) + comma + "\n"
            )
        insertions.setdefault(close_line, []).extend(new_lines)

    for position in sorted(insertions, reverse=True):
        lines[position:position] = insertions[position]

    updated = "".join(lines)
    json.loads(updated)
    TARGET_FILE.write_text(updated, encoding="utf-8", newline="")
    count = sum(len(urls) for urls in additions.values())
    print("同步完成：为 " + str(len(additions)) + " 个已有来源新增 " + str(count) + " 条可访问备用网址。")


if __name__ == "__main__":
    main()
