#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CLI 入口。
"""

from __future__ import annotations

import argparse
import os
import platform
import shlex
import subprocess
import sys
from datetime import datetime
from typing import List

from .core import (
    build_ffmpeg_command,
    build_headers,
    check_url,
    download_file,
    extract_title,
    find_m3u8_candidates,
    get_page_content,
    guess_output_name,
    is_stream_url,
    make_title_based_name,
)
from .curl_parser import parse_curl_command
from .har_parser import extract_media_candidates, load_har


def format_command_multiline(cmd: List[str]) -> str:
    parts = [shlex.quote(p) for p in cmd]
    if len(parts) <= 2:
        return " ".join(parts)
    lines: List[str] = []
    for i, part in enumerate(parts):
        suffix = " \\" if i < len(parts) - 1 else ""
        prefix = "" if i == 0 else "  "
        lines.append(f"{prefix}{part}{suffix}")
    return "\n".join(lines)


def add_timestamp_to_path(path: str) -> str:
    ts = datetime.now().strftime("%Y%m%d%H%M%S")
    if "." not in path or path.endswith("."):
        return f"{path}{ts}"
    base, dot, ext = path.rpartition(".")
    return f"{base}-{ts}{dot}{ext}"


def prompt_for_m3u8(candidates: List[str]) -> str:
    if candidates:
        print("\n找到以下可能的 m3u8 連結（已依長度排序）：")
        for i, url in enumerate(candidates, 1):
            print(f"{i}. {url}")
        print("\n可輸入序號或直接貼上完整 m3u8 連結。")
    else:
        print("\n未找到明顯的 m3u8，請自行貼上完整連結。")

    user_input = input("m3u8： ").strip()
    if not user_input:
        return ""
    if user_input.isdigit():
        idx = int(user_input)
        if 1 <= idx <= len(candidates):
            return candidates[idx - 1]
    return user_input


def read_curl_from_stdin(prompt: bool = True) -> str:
    if not sys.stdin.isatty():
        return sys.stdin.read()

    if prompt:
        print("請貼上 curl 指令（可多行），完成後按 Enter 空行或 Ctrl+D 結束：")
    lines: List[str] = []
    while True:
        try:
            line = input()
        except EOFError:
            break
        if line.strip() == "":
            break
        lines.append(line)
    return "\n".join(lines)


def has_unbalanced_quotes(s: str) -> bool:
    single = 0
    double = 0
    escaped = False
    for ch in s:
        if escaped:
            escaped = False
            continue
        if ch == "\\":
            escaped = True
            continue
        if ch == "'":
            single ^= 1
        elif ch == "\"":
            double ^= 1
    return bool(single or double)


def read_curl_from_clipboard() -> str:
    system = platform.system()
    if system == "Darwin":
        try:
            result = subprocess.run(
                ["pbpaste"],
                check=True,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            return result.stdout
        except Exception as exc:
            print(f"讀取剪貼簿失敗：{exc}")
            return ""
    print("目前僅支援 macOS 剪貼簿讀取，請改用 --curl-stdin 或管線。")
    return ""


def interactive_bootstrap() -> List[str]:
    """
    Interactive entry:
    - Paste HAR file path
    - Paste curl command
    - Press Enter for multi-line curl paste
    """
    print("請貼上 HAR 檔案路徑或 curl 指令（直接 Enter 可進入多行貼上）：")
    first_line = input().strip()
    if not first_line:
        return ["--curl-stdin", "--run"]

    # Strip surrounding quotes if present
    if (first_line.startswith("'") and first_line.endswith("'")) or (
        first_line.startswith("\"") and first_line.endswith("\"")
    ):
        first_line = first_line[1:-1].strip()

    expanded = os.path.expanduser(first_line)
    if os.path.isfile(expanded):
        return ["--har-file", expanded, "--run"]

    if first_line.lower().startswith("curl "):
        if first_line.rstrip().endswith("\\") or has_unbalanced_quotes(first_line):
            rest = read_curl_from_stdin(prompt=False)
            if rest:
                return ["--curl", first_line + "\n" + rest, "--run"]
        return ["--curl", first_line, "--run"]

    if first_line.startswith("http://") or first_line.startswith("https://"):
        return ["--curl", f"curl '{first_line}'", "--run"]

    # Fallback: treat as curl text and allow user to continue in stdin mode
    print("無法判斷輸入內容，將進入多行 curl 貼上模式。")
    return ["--curl-stdin", "--run"]


def resolve_title_name(
    page_content: str,
    page_url: str | None,
    headers: dict,
    timeout: int,
) -> str:
    title = extract_title(page_content)
    if title:
        return title

    if page_url:
        content = get_page_content(page_url, headers, timeout)
        title = extract_title(content)
        if title:
            return title

    referer = headers.get("Referer")
    if referer:
        content = get_page_content(referer, headers, timeout)
        title = extract_title(content)
        if title:
            return title
    return ""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="M3U8 下載輔助工具（僅限合法授權內容）")
    parser.add_argument("-p", "--page-url", help="影片頁面網址（用來掃描 m3u8）")
    parser.add_argument("-m", "--m3u8-url", help="已知的 m3u8 網址")
    parser.add_argument("--curl", help="貼上 curl 指令（自動解析 URL 與 headers）")
    parser.add_argument("--curl-file", help="讀取含 curl 指令的檔案路徑")
    parser.add_argument(
        "--curl-stdin",
        action="store_true",
        help="從 stdin 讀取 curl（支援貼上多行，空行或 EOF 結束）",
    )
    parser.add_argument(
        "--clipboard",
        action="store_true",
        help="直接從剪貼簿讀取 curl（macOS: pbpaste）",
    )
    parser.add_argument("--har-file", help="從 HAR 檔案解析可能的媒體連結")
    parser.add_argument("-o", "--output", help="輸出檔名（預設依 m3u8 推測）")
    parser.add_argument(
        "--name-from-title",
        action="store_true",
        help="嘗試以頁面標題當作檔名（使用 page-url 或 Referer）",
    )
    parser.add_argument(
        "--no-timestamp",
        action="store_true",
        help="停用自動在檔名加入 yyyyMMddHHmmss",
    )
    parser.add_argument("--referer", help="Referer 標頭")
    parser.add_argument("--user-agent", help="User-Agent 標頭")
    parser.add_argument("--cookie", help="Cookie 標頭（僅限你有權限的登入內容）")
    parser.add_argument("-H", "--header", action="append", help="額外 Header，格式：Key: Value")
    parser.add_argument("--timeout", type=int, default=15, help="連線逾時秒數")
    parser.add_argument("--max", type=int, default=10, help="最多顯示幾個候選 m3u8")
    parser.add_argument("--auto", action="store_true", help="自動選擇第一個候選")
    parser.add_argument("--ffmpeg", default="ffmpeg", help="ffmpeg 執行檔路徑")
    parser.add_argument("--overwrite", action="store_true", help="若輸出檔存在，直接覆蓋")
    parser.add_argument("--check", action="store_true", help="先檢查 m3u8 是否可存取")
    parser.add_argument("--debug-headers", action="store_true", help="列出實際送出的 headers")
    parser.add_argument("--run", action="store_true", help="直接執行 ffmpeg 下載")
    return parser


def main(argv: List[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if not argv:
        # Interactive default: paste HAR path or curl and auto-download
        argv = interactive_bootstrap()

    parser = build_parser()
    args = parser.parse_args(argv)

    curl_cmd = ""
    if args.curl:
        if args.curl.strip() == "-":
            curl_cmd = read_curl_from_stdin()
        else:
            curl_cmd = args.curl
    elif args.clipboard:
        curl_cmd = read_curl_from_clipboard()
    elif args.curl_stdin:
        curl_cmd = read_curl_from_stdin()
    elif args.curl_file:
        try:
            with open(args.curl_file, "r", encoding="utf-8") as f:
                curl_cmd = f.read()
        except OSError as exc:
            print(f"無法讀取 curl 檔案：{exc}")
            return 1

    curl_url = ""
    curl_headers = {}
    if curl_cmd:
        curl_url, curl_headers = parse_curl_command(curl_cmd)

    har_candidates = []
    if args.har_file:
        try:
            har_data = load_har(args.har_file)
            har_candidates = extract_media_candidates(har_data)
        except Exception as exc:
            print(f"無法解析 HAR：{exc}")
            return 1

    page_url = args.page_url
    m3u8_url = args.m3u8_url
    direct_url = ""
    if curl_url:
        if not m3u8_url and is_stream_url(curl_url):
            m3u8_url = curl_url
        elif not page_url and not m3u8_url:
            direct_url = curl_url

    if har_candidates and not m3u8_url and not direct_url:
        urls = [u for (u, _m) in har_candidates]
        picked = ""
        if args.auto:
            picked = urls[0]
        else:
            print("\n從 HAR 找到可能的媒體連結（已依長度排序）：")
            for i, url in enumerate(urls, 1):
                print(f"{i}. {url}")
            user_input = input("\n請選擇序號或直接貼上完整網址： ").strip()
            if user_input.isdigit():
                idx = int(user_input)
                if 1 <= idx <= len(urls):
                    picked = urls[idx - 1]
            else:
                picked = user_input

        if picked:
            if is_stream_url(picked):
                m3u8_url = picked
            else:
                direct_url = picked
    elif args.har_file and not har_candidates:
        print("HAR 解析不到媒體連結。")
        print("請確認：")
        print("1. 重新整理並播放影片後再匯出 HAR")
        print("2. 匯出時選擇「Save all as HAR with content」")
        print("3. Network 面板勾選 Preserve log")
        return 1
    if not page_url and not m3u8_url and not direct_url:
        print("請提供 --page-url 或 --m3u8-url，或使用 --curl/--har-file。")
        return 1

    headers = build_headers(
        args.user_agent,
        args.referer,
        args.cookie,
        args.header,
        base_headers=curl_headers,
    )
    if args.debug_headers:
        print("\n=== Request Headers ===")
        for k, v in headers.items():
            print(f"{k}: {v}")

    page_content = ""

    if direct_url:
        output_path = args.output or guess_output_name(direct_url)
        if args.name_from_title and not args.output:
            title = resolve_title_name(page_content, page_url, headers, args.timeout)
            if title:
                output_path = make_title_based_name(title, direct_url)
        if not args.no_timestamp:
            output_path = add_timestamp_to_path(output_path)
        if args.check:
            print("\n=== 檢查下載連結 ===")
            result = check_url(direct_url, headers, args.timeout)
            print(f"{result['method']} {result['status']} {result['reason']}")
            if result.get("final_url") and result["final_url"] != direct_url:
                print(f"Final URL: {result['final_url']}")
            if result["status"] >= 400 or result["status"] == 0:
                print("警告：伺服器回應可能拒絕存取或連線失敗。")
                if not args.run:
                    return 2

        if args.run:
            try:
                print("\n正在下載檔案...\n")
                result = download_file(
                    direct_url,
                    headers,
                    output_path,
                    args.timeout,
                    args.overwrite,
                )
                print(f"下載完成：{result}")
            except FileExistsError as exc:
                print(str(exc))
                return 1
            except Exception as exc:
                print(f"下載失敗：{exc}")
                return 1
            return 0

        print("\n=== 建議使用的 curl 下載指令 ===\n")
        curl_cmd = ["curl", "-L", direct_url]
        for k, v in headers.items():
            curl_cmd.extend(["-H", f"{k}: {v}"])
        curl_cmd.extend(["-o", output_path])
        print(format_command_multiline(curl_cmd))
        return 0

    if not m3u8_url and page_url:
        print("正在取得頁面內容...")
        page_content = get_page_content(page_url, headers, args.timeout)
        if not page_content:
            print("無法取得頁面內容，請檢查網址或網路狀態。")
            return 1

        candidates = find_m3u8_candidates(page_content, page_url, args.max)
        if args.auto:
            if not candidates:
                print("找不到 m3u8，請改用 --m3u8-url。")
                return 1
            m3u8_url = candidates[0]
            print(f"已自動選擇：{m3u8_url}")
        else:
            m3u8_url = prompt_for_m3u8(candidates)
            if not m3u8_url:
                print("未提供 m3u8，結束。")
                return 0

    output_path = args.output or guess_output_name(m3u8_url)
    if args.name_from_title and not args.output:
        title = resolve_title_name(page_content, page_url, headers, args.timeout)
        if title:
            output_path = make_title_based_name(title, m3u8_url)
    if not args.no_timestamp:
        output_path = add_timestamp_to_path(output_path)

    ffmpeg_cmd = build_ffmpeg_command(
        m3u8_url,
        headers,
        output_path,
        args.ffmpeg,
        args.overwrite,
    )

    if args.check:
        print("\n=== 檢查 m3u8 連結 ===")
        result = check_url(m3u8_url, headers, args.timeout)
        print(f"{result['method']} {result['status']} {result['reason']}")
        if result.get("final_url") and result["final_url"] != m3u8_url:
            print(f"Final URL: {result['final_url']}")
        if result["status"] >= 400 or result["status"] == 0:
            print("警告：伺服器回應可能拒絕存取或連線失敗。")
            if not args.run:
                return 2

    print("\n=== 建議使用的 ffmpeg 指令 ===\n")
    print(format_command_multiline(ffmpeg_cmd))

    if args.run:
        print("\n正在執行 ffmpeg...\n")
        try:
            subprocess.run(ffmpeg_cmd, check=True)
        except FileNotFoundError:
            print("找不到 ffmpeg，請確認已安裝且在 PATH 內，或使用 --ffmpeg 指定路徑。")
            return 1
        except subprocess.CalledProcessError as exc:
            print(f"ffmpeg 執行失敗（return code={exc.returncode}）。")
            return exc.returncode

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n使用者中斷")
        sys.exit(130)
