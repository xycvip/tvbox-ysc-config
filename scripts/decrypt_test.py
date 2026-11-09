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
import urllib.request
from pathlib import Path

MAX_DOWNLOAD = 12 * 1024 * 1024
TVBOX_KEYS = ("sites", "spiders", "lives", "store", "exts", "parses", "flags")


def fetch_input(url):
    req = urllib.request.Request(url, headers={
        "User-Agent": "okhttp/3.14.9",
        "Accept": "*/*",
        "Accept-Encoding": "identity",
    })
    with urllib.request.urlopen(req, timeout=25) as response:
        data = response.read(MAX_DOWNLOAD + 1)
    if len(data) > MAX_DOWNLOAD:
        raise ValueError("源文件超过 12 MB 限制")
    return data


def is_tvbox_json(value):
    return isinstance(value, dict) and (
        any(key in value for key in TVBOX_KEYS)
        or (isinstance(value.get("urls"), list) and bool(value["urls"]))
    )


def try_json(data):
    try:
        value = json.loads(data.decode("utf-8-sig").strip())
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
            # TVBoxOS Crypto.rsa(pub=true, encrypt=false) also exposes public-key
            # decrypt. Reproduce the RSA public operation and remove PKCS#1 v1.5
            # type-1/type-2 block padding locally.
            value = pow(int.from_bytes(block, "big"), key.e, key.n)
            encoded = value.to_bytes(block_size, "big")
            if encoded[:2] not in (b"\\x00\\x01", b"\\x00\\x02"):
                chunk = None
            else:
                sep = encoded.find(b"\\x00", 2)
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
        "auto", "base64", "aes-ecb-tvbox", "aes-cbc-tvbox",
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
        source = args.url
    elif args.payload:
        raw = args.payload.encode("utf-8")
        source = "workflow_dispatch payload"
    else:
        raise ValueError("必须提供 source_url 或 payload")

    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)
    plain = raw
    detected = "raw"
    wrapped = extract_wrapped_base64(raw)
    if wrapped is not None:
        plain, detected = wrapped, "image-wrapper(** + Base64)"
    elif try_json(raw) is not None and args.algorithm in ("auto", "base64"):
        detected = "plain-json"

    if detected not in ("plain-json", "image-wrapper(** + Base64)") and args.algorithm == "base64":
        plain = b64decode_flexible(raw.decode("utf-8-sig"))
        detected = "Base64"
    elif detected not in ("plain-json", "image-wrapper(** + Base64)") and args.algorithm.startswith("aes-"):
        mode = "ECB" if "ecb" in args.algorithm else "CBC"
        flavor = "tvbox" if args.algorithm.endswith("-tvbox") else "cryptox"
        fmt = "hex" if flavor == "tvbox" else args.input_format
        cipher_bytes = decode_cipher_input(raw.decode("utf-8-sig"), fmt)
        plain = aes_decrypt(cipher_bytes, args.aes_key, args.aes_iv, mode, flavor)
        detected = f"AES-{mode}-{flavor}"
    elif detected not in ("plain-json", "image-wrapper(** + Base64)") and args.algorithm == "rsa":
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
