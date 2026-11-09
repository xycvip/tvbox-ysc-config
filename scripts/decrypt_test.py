#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""TVBoxOS-style local decrypt test runner; never calls an online decrypt API."""
import argparse
import base64
import binascii
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

MAX_DOWNLOAD = 12 * 1024 * 1024
TVBOX_KEYS = ("sites", "spiders", "lives", "store", "exts", "parses", "flags")


def normalize_source_url(url):
    """Convert an internationalized hostname to IDNA and quote Unicode URL parts."""
    value = url.strip()
    if not value:
        raise ValueError("源 URL 为空")
    if any(ord(char) < 32 for char in value):
        raise ValueError("源 URL 含有换行或控制字符，请检查输入")
    try:
        parsed = urllib.parse.urlsplit(value)
        scheme = parsed.scheme.lower()
        if scheme not in ("http", "https"):
            raise ValueError("源 URL 只支持 http:// 或 https://")
        hostname = parsed.hostname
        if not hostname:
            raise ValueError("源 URL 缺少有效域名")
        try:
            port = parsed.port
        except ValueError as exc:
            raise ValueError(f"源 URL 端口无效：{exc}") from exc

        # IPv6 literals must remain bracketed; DNS names use IDNA/Punycode.
        if ":" in hostname:
            host_ascii = f"[{hostname}]"
        else:
            try:
                host_ascii = hostname.encode("idna").decode("ascii")
            except UnicodeError as exc:
                raise ValueError(f"中文域名转换失败：{exc}") from exc

        userinfo = ""
        if parsed.username is not None:
            userinfo = urllib.parse.quote(parsed.username, safe="!$&'()*+,;=:")
            if parsed.password is not None:
                userinfo += ":" + urllib.parse.quote(parsed.password, safe="!$&'()*+,;=:")
            userinfo += "@"
        netloc = userinfo + host_ascii
        if port is not None:
            netloc += f":{port}"

        path = urllib.parse.quote(parsed.path or "/", safe="/%:@!$&'()*+,;=-._~")
        query = urllib.parse.quote(parsed.query, safe="/?%:@!$&'()*+,;=-._~[]")
        return urllib.parse.urlunsplit((scheme, netloc, path, query, ""))
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f"源 URL 格式无效：{exc}") from exc


def fetch_input(url):
    normalized_url = normalize_source_url(url)
    req = urllib.request.Request(normalized_url, headers={
        "User-Agent": "okhttp/3.14.9",
        "Accept": "*/*",
        "Accept-Encoding": "identity",
    })
    try:
        with urllib.request.urlopen(req, timeout=25) as response:
            data = response.read(MAX_DOWNLOAD + 1)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"源站返回 HTTP {exc.code} {exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"源站连接失败：{exc.reason}") from exc
    except TimeoutError as exc:
        raise RuntimeError("连接源站超时（25 秒）") from exc
    if len(data) > MAX_DOWNLOAD:
        raise ValueError("源文件超过 12 MB 限制")
    return data


def is_tvbox_json(value):
    return isinstance(value, dict) and (
        any(key in value for key in TVBOX_KEYS)
        or (isinstance(value.get("urls"), list) and bool(value["urls"]))
    )


def strip_json_comments(text):
    """Remove // and /* */ comments while preserving quoted strings and line breaks."""
    out = []
    i = 0
    in_string = False
    escaped = False
    while i < len(text):
        char = text[i]
        nxt = text[i + 1] if i + 1 < len(text) else ""
        if in_string:
            out.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            i += 1
            continue
        if char == '"':
            in_string = True
            out.append(char)
            i += 1
        elif char == "/" and nxt == "/":
            i += 2
            while i < len(text) and text[i] not in "\r\n":
                i += 1
        elif char == "/" and nxt == "*":
            i += 2
            while i < len(text) and not (text[i] == "*" and i + 1 < len(text) and text[i + 1] == "/"):
                if text[i] in "\r\n":
                    out.append(text[i])
                i += 1
            i = min(i + 2, len(text))
        else:
            out.append(char)
            i += 1
    cleaned = "".join(out)
    return re.sub(r',\s*([}\]])', r'\1', cleaned)


def try_json(data):
    try:
        text = data.decode("utf-8-sig").strip()
        value = json.loads(strip_json_comments(text))
        return value if isinstance(value, (dict, list)) else None
    except Exception:
        return None

def b64decode_flexible(value):
    value = re.sub(r"\s+", "", value).replace("_", "/").replace("-", "+")
    value += "=" * ((4 - len(value) % 4) % 4)
    return base64.b64decode(value, validate=True)


def extract_wrapped_base64(raw):
    candidates = []
    eoi = raw.find(b"\xff\xd9")
    if eoi >= 0:
        candidates.append(raw[eoi + 2:])
    candidates.append(raw)
    for blob in candidates:
        pos = 0
        while True:
            idx = blob.find(b"**", pos)
            if idx < 0:
                break
            pos = idx + 2
            try:
                decoded = base64.b64decode(b"".join(blob[pos:].split()), validate=True)
            except (binascii.Error, ValueError):
                continue
            if try_json(decoded) is not None:
                return decoded
    return None


def decrypt_tvbox_marked_hex(raw):
    """Reproduce TVBoxOS ApiConfig.FindResult() for 2423... hex-wrapped configs."""
    try:
        text = re.sub(r"\s+", "", raw.decode("utf-8-sig").strip())
    except UnicodeDecodeError:
        # Binary image wrappers (for example JPEG) are handled by the next detector.
        return None
    if not text.startswith("2423"):
        return None
    if not re.fullmatch(r"[0-9a-fA-F]+", text) or len(text) % 2:
        raise ValueError("检测到 2423 标记，但外层内容不是完整 HEX")
    decoded = bytes.fromhex(text).decode("latin1")
    marker = re.match(r"^\$#(\d{13})#\$", decoded)
    if not marker:
        raise ValueError("检测到 2423 HEX 外壳，但缺少 TVBoxOS 的 $#{13位数字}#$ 标记")
    iv_text = decoded[-13:]
    if not re.fullmatch(r"\d{13}", iv_text):
        raise ValueError("TVBoxOS 标记数据末尾缺少 13 位数字 IV")
    marker_end = text.lower().find("2324")
    if marker_end < 0:
        raise ValueError("TVBoxOS HEX 数据缺少 2324 分隔符")
    cipher_hex = text[marker_end + 4:-26]
    if not cipher_hex or len(cipher_hex) % 32:
        raise ValueError("TVBoxOS AES-CBC 密文长度异常，无法按 16 字节分块")
    key = marker.group(1).ljust(16, "0")[:16]
    iv = iv_text.ljust(16, "0")[:16]
    plain = aes_decrypt(bytes.fromhex(cipher_hex), key, iv, "CBC", "tvbox")
    return plain


def decode_cipher_input(text, input_format):
    cleaned = text.strip()
    if input_format == "hex":
        return bytes.fromhex(re.sub(r"\s+", "", cleaned))
    if input_format == "base64":
        return b64decode_flexible(cleaned)
    if input_format == "text":
        return cleaned.encode("utf-8")
    try:
        return b64decode_flexible(cleaned)
    except Exception:
        pass
    compact = re.sub(r"\s+", "", cleaned)
    if re.fullmatch(r"(?:[0-9a-fA-F]{2})+", compact):
        return bytes.fromhex(compact)
    return cleaned.encode("utf-8")


def aes_decrypt(data, key, iv, mode, flavor):
    from Crypto.Cipher import AES
    from Crypto.Util.Padding import unpad

    mode = mode.upper().replace("AES/", "").replace("/PKCS5PADDING", "").replace("/PKCS7PADDING", "")
    if mode not in ("ECB", "CBC"):
        raise ValueError("AES 模式仅支持 ECB 或 CBC")

    key_bytes = key.encode("utf-8")
    if flavor == "tvbox":
        if mode == "ECB":
            key_bytes = key.strip()[:16].ljust(16, "0").encode("utf-8")
        iv_bytes = iv.encode("utf-8")
    else:
        if len(key_bytes) < 16:
            key_bytes = key_bytes.ljust(16, b"\0")
        iv_bytes = iv.encode("utf-8") if iv else b""
        if iv and len(iv_bytes) < 16:
            iv_bytes = iv_bytes.ljust(16, b"\0")

    if len(key_bytes) not in (16, 24, 32):
        raise ValueError(f"AES key 长度无效：{len(key_bytes)} 字节")
    if mode == "ECB":
        cipher = AES.new(key_bytes, AES.MODE_ECB)
    else:
        if len(iv_bytes) != 16:
            raise ValueError(f"AES-CBC IV 必须为 16 字节，当前 {len(iv_bytes)} 字节")
        cipher = AES.new(key_bytes, AES.MODE_CBC, iv_bytes)
    return unpad(cipher.decrypt(data), AES.block_size, style="pkcs7")


def rsa_decrypt(data, key_text, key_type, long_mode):
    from Crypto.Cipher import PKCS1_v1_5
    from Crypto.PublicKey import RSA

    if not key_text.strip():
        raise ValueError("RSA key 为空")
    key_bytes = key_text.encode("ascii") if "-----BEGIN" in key_text else b64decode_flexible(key_text)
    key = RSA.import_key(key_bytes)
    if key_type == "private" and not key.has_private():
        raise ValueError("选择了私钥解密，但提供的 RSA key 不包含私钥")
    block_size = key.size_in_bytes()
    if len(data) % block_size:
        raise ValueError(f"RSA 密文长度 {len(data)} 不是模数块长 {block_size} 的整数倍")
    if not long_mode and len(data) > block_size:
        raise ValueError("密文超过单个 RSA 块，请开启分段解密")
    result = []
    for offset in range(0, len(data), block_size):
        block = data[offset:offset + block_size]
        if key.has_private():
            cipher = PKCS1_v1_5.new(key)
            chunk = cipher.decrypt(block, None)
        else:
            value = pow(int.from_bytes(block, "big"), key.e, key.n)
            encoded = value.to_bytes(block_size, "big")
            if encoded[:2] not in (b"\x00\x01", b"\x00\x02"):
                chunk = None
            else:
                sep = encoded.find(b"\x00", 2)
                padding = encoded[2:sep] if sep >= 0 else b""
                valid_padding = len(padding) >= 8 and (
                    (encoded[1] == 1 and all(byte == 0xff for byte in padding))
                    or (encoded[1] == 2 and all(byte != 0 for byte in padding))
                )
                chunk = encoded[sep + 1:] if valid_padding else None
        if chunk is None:
            raise ValueError(f"RSA 第 {offset // block_size + 1} 块解密失败")
        result.append(chunk)
    return b"".join(result)


def main():
    p = argparse.ArgumentParser(description="TVBoxOS 本地离线解密测试")
    p.add_argument("--url", default="")
    p.add_argument("--payload", default="")
    p.add_argument("--algorithm", required=True, choices=(
        "auto", "base64", "tvbox-marked-cbc", "aes-ecb-tvbox", "aes-cbc-tvbox",
        "aes-ecb-cryptox", "aes-cbc-cryptox", "rsa"))
    p.add_argument("--input-format", default="auto", choices=("auto", "base64", "hex", "text"))
    p.add_argument("--aes-key", default=os.getenv("AES_KEY", ""))
    p.add_argument("--aes-iv", default=os.getenv("AES_IV", ""))
    p.add_argument("--rsa-key", default=os.getenv("RSA_KEY", ""))
    p.add_argument("--rsa-key-type", default=os.getenv("RSA_KEY_TYPE", "private"),
                   choices=("private", "public"))
    p.add_argument("--rsa-long", action="store_true")
    p.add_argument("--output", default="decrypt-output")
    args = p.parse_args()

    if args.url:
        raw = fetch_input(args.url)
        source = normalize_source_url(args.url)
    elif args.payload:
        raw = args.payload.encode("utf-8")
        source = "workflow_dispatch payload"
    else:
        raise ValueError("必须提供 source_url 或 payload")

    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)
    plain = raw
    detected = "raw"
    if args.algorithm in ("auto", "tvbox-marked-cbc"):
        marked_plain = decrypt_tvbox_marked_hex(raw)
        if marked_plain is not None:
            plain, detected = marked_plain, "TVBoxOS-marked-hex/AES-CBC"
        elif args.algorithm == "tvbox-marked-cbc":
            raise ValueError("输入内容不是 TVBoxOS 2423 HEX 标记格式")
    wrapped = extract_wrapped_base64(raw) if detected == "raw" else None
    if wrapped is not None:
        plain, detected = wrapped, "image-wrapper(** + Base64)"
    elif detected == "raw" and try_json(raw) is not None and args.algorithm in ("auto", "base64"):
        detected = "plain-json"

    if detected not in ("plain-json", "image-wrapper(** + Base64)", "TVBoxOS-marked-hex/AES-CBC") and args.algorithm == "base64":
        plain = b64decode_flexible(raw.decode("utf-8-sig"))
        detected = "Base64"
    elif detected not in ("plain-json", "image-wrapper(** + Base64)", "TVBoxOS-marked-hex/AES-CBC") and args.algorithm.startswith("aes-"):
        mode = "ECB" if "ecb" in args.algorithm else "CBC"
        flavor = "tvbox" if args.algorithm.endswith("-tvbox") else "cryptox"
        fmt = "hex" if flavor == "tvbox" else args.input_format
        cipher_bytes = decode_cipher_input(raw.decode("utf-8-sig"), fmt)
        plain = aes_decrypt(cipher_bytes, args.aes_key, args.aes_iv, mode, flavor)
        detected = f"AES-{mode}-{flavor}"
    elif detected not in ("plain-json", "image-wrapper(** + Base64)", "TVBoxOS-marked-hex/AES-CBC") and args.algorithm == "rsa":
        cipher_bytes = b64decode_flexible(raw.decode("utf-8-sig"))
        plain = rsa_decrypt(cipher_bytes, args.rsa_key, args.rsa_key_type, args.rsa_long)
        detected = f"RSA-PKCS1-{args.rsa_key_type}"
    elif args.algorithm == "auto" and detected == "raw":
        try:
            candidate = b64decode_flexible(raw.decode("utf-8-sig"))
            if try_json(candidate) is not None:
                plain, detected = candidate, "Base64(JSON)"
        except Exception:
            pass

    parsed = try_json(plain)
    summary = {
        "source": source,
        "algorithm": detected,
        "bytes_in": len(raw),
        "bytes_out": len(plain),
        "json": parsed is not None,
        "tvbox_config": is_tvbox_json(parsed),
    }
    (out_dir / "decrypted.bin").write_bytes(plain)
    try:
        (out_dir / "decrypted.txt").write_text(plain.decode("utf-8-sig"), encoding="utf-8")
    except UnicodeDecodeError:
        pass
    if parsed is not None:
        (out_dir / "decrypted.json").write_text(
            json.dumps(parsed, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "result.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if summary["tvbox_config"]:
        print("RESULT: 已识别为 TVBox 配置 JSON")
    elif summary["json"]:
        print("RESULT: 解密结果是 JSON，但未命中 TVBox 配置关键字段")
    else:
        print("RESULT: 已输出解密数据；请检查算法、密钥、IV 和输入格式")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"DECRYPT_TEST_FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)
