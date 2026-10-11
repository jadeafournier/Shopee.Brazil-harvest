# -*- coding: utf-8 -*-
"""Client 4 结果回传 —— 我方程序提交示例 (submission example).

客户4 的提交地址(URL)尚在开发中。本脚本演示我方将如何回传结果：
  * 方式    : HTTP POST, Content-Type: application/json
  * 认证    : token / api_key 放在 JSON body 顶层 (贵方要求的 3 个字段回传到数据包内)
  * body    : 顶层 task_link/token/api_key + get_pc 原始信封(bff_meta/error/error_msg/data)
              data 为对象、原样不改(不做任何价格/字段加工)

用法:
  # 1) 仅打印我方回传格式(URL 未开发完时用这个给客户4看)
  python client4_submit.py

  # 2) 地址开发完成后, 实际回传:
  python client4_submit.py --url https://<客户4提交地址> --send
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_FILE = HERE / "submit_514098235_110371514051.json"


def load_package(path: Path) -> dict:
    """读取结果数据包。已包含 task_link/token/api_key + get_pc 信封。"""
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def slim_for_preview(payload: dict) -> dict:
    """预览用: 省略庞大的 get_pc data 内容, 只保留结构与关键标识。"""
    preview = {k: payload.get(k) for k in
               ("task_link", "token", "api_key", "bff_meta", "error", "error_msg")}
    data = payload.get("data") or {}
    item = (data.get("item") or {}) if isinstance(data, dict) else {}
    preview["data"] = {"item": {
        "item_id": item.get("item_id"),
        "shop_id": item.get("shop_id"),
        "...": "(数据包内容省略 / full get_pc omitted)",
    }}
    return preview


def to_curl(url: str, payload: dict) -> str:
    body = json.dumps(slim_for_preview(payload), ensure_ascii=False)
    return (f"curl -X POST \"{url}\" \\\n"
            f"  -H \"Content-Type: application/json\" \\\n"
            f"  --data '{body}'")


def main() -> None:
    ap = argparse.ArgumentParser(description="Client 4 回传示例/实发")
    ap.add_argument("--url", default="", help="客户4 提交地址(开发完成后填入)")
    ap.add_argument("--file", default=str(DEFAULT_FILE), help="要回传的结果数据包 JSON")
    ap.add_argument("--send", action="store_true", help="实际发送(需 --url)")
    ap.add_argument("--timeout", type=float, default=60.0)
    args = ap.parse_args()

    path = Path(args.file)
    if not path.is_file():
        sys.exit(f"找不到数据包文件: {path}")
    payload = load_package(path)

    url_show = args.url or "https://<客户4提交地址-开发中>"
    print("=" * 72)
    print("我方回传格式 (submission example) — data 内容已省略:")
    print("=" * 72)
    print(to_curl(url_show, payload))
    print("\n请求规格: POST · application/json · token/api_key 在 body 顶层 · data 为对象(原样)")

    if args.send:
        if not args.url:
            sys.exit("实际发送需要 --url")
        import httpx
        headers = {"Content-Type": "application/json", "Accept": "*/*"}
        body_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        print(f"\n发送中 -> {args.url} ({len(body_bytes)} bytes) ...")
        try:
            r = httpx.post(args.url, content=body_bytes, headers=headers, timeout=args.timeout)
            print("HTTP", r.status_code)
            print(r.text[:800])
        except Exception as exc:  # noqa: BLE001
            sys.exit(f"发送失败: {exc}")


if __name__ == "__main__":
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    main()
