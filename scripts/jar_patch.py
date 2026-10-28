#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""为 output/单仓聚合.json 中的 Java 站点绑定各来源自己的 JAR。

应在 scripts/update.py 成功生成单仓聚合.json 后运行。
只修改聚合文件中已有站点的 jar 字段，不新增、删除或合并站点。
"""
import json
import os
import urllib.parse

ROOT = (
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if os.path.basename(os.path.dirname(os.path.abspath(__file__))) == "scripts"
    else os.path.dirname(os.path.abspath(__file__))
)
OUTPUT_DIR = os.path.join(ROOT, "output")
SOURCES_FILE = os.path.join(ROOT, "config", "sources.json")
AGGREGATE_FILE = os.path.join(OUTPUT_DIR, "单仓聚合.json")


def read_json(path):
    with open(path, "r", encoding="utf-8-sig") as f:
        return json.load(f)


def resolve_jar(jar, source_url):
    """将相对 JAR 地址转为相对于该来源配置 URL 的绝对地址，并保留 md5 等后缀。"""
    if not isinstance(jar, str) or not jar.strip() or not source_url:
        return jar
    parts = jar.split(";")
    address = parts[0].strip()
    parsed = urllib.parse.urlsplit(address)
    if parsed.scheme:
        return jar
    base = urllib.parse.urlsplit(source_url)
    if not base.scheme or not base.netloc:
        return jar
    if address.startswith("//"):
        parts[0] = base.scheme + ":" + address
    else:
        parts[0] = urllib.parse.urljoin(source_url, address)
    return ";".join(parts)


def get_source_url(source):
    urls = source.get("urls") or []
    if isinstance(urls, str):
        return urls
    return next((u for u in urls if isinstance(u, str) and u.strip()), None)


def is_java_site(site):
    api = site.get("api", "") if isinstance(site, dict) else ""
    return isinstance(api, str) and api.startswith("csp_")


def main():
    cfg = read_json(SOURCES_FILE)
    aggregate = read_json(AGGREGATE_FILE)
    aggregate_sites = aggregate.get("sites", [])
    if not isinstance(aggregate_sites, list):
        raise ValueError("单仓聚合.json 的 sites 不是数组，已停止，未写入文件")

    # 与 scripts/update.py 的 merge_configs 保持相同顺序和 key 去重规则，
    # 这样重复 key 的站点会精确映射到聚合结果中的 sid_key，而不会误改别的来源。
    seen_keys = set()
    expected = []  # (sid, source_site, source_url, source_jar, expected_aggregate_key)
    skipped_duplicate = 0
    source_count = 0

    for source in cfg.get("sources", []):
        sid = source.get("id")
        if not sid:
            continue
        source_file = os.path.join(OUTPUT_DIR, f"{sid}.json")
        if not os.path.isfile(source_file):
            continue
        try:
            original = read_json(source_file)
        except (OSError, json.JSONDecodeError) as exc:
            print(f"警告：跳过无法读取的来源文件 {source_file}: {exc}")
            continue
        sites = original.get("sites", []) or []
        if not isinstance(sites, list):
            continue
        source_count += 1
        source_url = get_source_url(source)
        source_jar = original.get("spider")
        source_jar = resolve_jar(source_jar, source_url) if source_jar else None

        for site in sites:
            if not isinstance(site, dict):
                continue
            raw_key = site.get("key")
            key = raw_key if raw_key is not None else json.dumps(site, ensure_ascii=False, sort_keys=True)
            target_key = key
            if key in seen_keys:
                target_key = f"{sid}_{key}"
            if target_key in seen_keys:
                skipped_duplicate += 1
                continue
            seen_keys.add(target_key)
            expected.append((sid, site, source_url, source_jar, target_key))

    # 只有在聚合站点数量和顺序能与原脚本重建结果对应时才按索引映射；
    # 若不一致则改用唯一 key 精确匹配，无法唯一匹配时跳过并提示，不猜测。
    index_mapping_ok = len(expected) == len(aggregate_sites)
    if index_mapping_ok:
        for i, item in enumerate(expected):
            if not isinstance(aggregate_sites[i], dict) or aggregate_sites[i].get("key") != item[4]:
                index_mapping_ok = False
                break

    if index_mapping_ok:
        pairs = [(item, aggregate_sites[i]) for i, item in enumerate(expected)]
    else:
        by_key = {}
        for target in aggregate_sites:
            if isinstance(target, dict):
                by_key.setdefault(target.get("key"), []).append(target)
        pairs = []
        for item in expected:
            candidates = by_key.get(item[4], [])
            if len(candidates) == 1:
                pairs.append((item, candidates[0]))
            else:
                # 重复 key 无法唯一对应时不修改，避免错绑 JAR。
                if is_java_site(item[1]):
                    print(f"警告：来源 {item[0]} 的站点 key={item[4]!r} 无法唯一匹配，已跳过")

    matched = changed = assigned = preserved = unresolved = 0
    for item, target in pairs:
        sid, source_site, source_url, source_jar, expected_key = item
        if not is_java_site(source_site):
            continue
        matched += 1
        # 仅在聚合站点和来源站点的 API 相同的情况下应用，防止 key 相同但内容不同。
        if target.get("api") != source_site.get("api"):
            unresolved += 1
            print(f"警告：来源 {sid} 的站点 {expected_key!r} API 不一致，已跳过")
            continue

        existing_jar = target.get("jar")
        if isinstance(existing_jar, str) and existing_jar.strip():
            resolved_existing = resolve_jar(existing_jar, source_url)
            if resolved_existing != existing_jar:
                target["jar"] = resolved_existing
                changed += 1
            preserved += 1
        elif source_jar:
            target["jar"] = source_jar
            changed += 1
            assigned += 1
        else:
            unresolved += 1
            print(f"警告：来源 {sid} 的 Java 站点 {expected_key!r} 没有 spider/JAR，未设置 jar")

    # 先写临时文件，完整序列化成功后再替换，避免中途写入造成文件损坏。
    temp_file = AGGREGATE_FILE + ".tmp"
    with open(temp_file, "w", encoding="utf-8") as f:
        json.dump(aggregate, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(temp_file, AGGREGATE_FILE)

    print("处理完成：")
    print(f"- 读取来源文件：{source_count} 个")
    print(f"- 匹配到 Java 站点：{matched} 个")
    print(f"- 新分配来源 JAR：{assigned} 个")
    print(f"- 处理 jar 字段：{changed} 个")
    print(f"- 保留原有站点 jar：{preserved} 个")
    print(f"- 无法处理/缺少 JAR：{unresolved} 个")
    if skipped_duplicate:
        print(f"- 按原聚合规则跳过重复项：{skipped_duplicate} 个")
    if not index_mapping_ok:
        print("提示：聚合文件与来源文件的站点顺序/数量不完全一致，已采用唯一 key 匹配；无法唯一匹配的站点不会被修改。")


if __name__ == "__main__":
    main()
