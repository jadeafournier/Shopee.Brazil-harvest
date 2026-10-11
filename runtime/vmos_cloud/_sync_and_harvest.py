# -*- coding: utf-8 -*-
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import harvest_control
import list_progress
import task_lists
from _sever_bridge import _env, _get


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def main() -> int:
    old = {}
    if task_lists.LIST_PATH.is_file():
        try:
            old = json.loads(task_lists.LIST_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            old = {}
    env = _env()
    data = _get(
        f"http://{env['BR_MEMBER_HOST']}:{env['BR_MEMBER_PORT']}/v1/tasks?limit=15000",
        env["BR_MEMBER_KEY"],
    )
    raw = data.get("tasks") if isinstance(data, dict) else None
    tasks = [r for r in raw if isinstance(r, dict)] if isinstance(raw, list) else []
    for row in tasks:
        shop, item = row.get("shop_id"), row.get("item_id")
        if not row.get("url") and shop and item:
            row["url"] = f"https://shopee.com.br/product/{shop}/{item}"
    changed = task_lists.save_incoming(tasks, _now())
    meta = json.loads(task_lists.LIST_PATH.read_text(encoding="utf-8"))
    print("local_was", old.get("count"), old.get("updated_at"))
    print(
        "server",
        len(tasks),
        "saved",
        meta.get("count"),
        meta.get("updated_at"),
        "changed",
        bool(changed),
    )
    only = list_progress.phone_only_shard()
    if only is not None:
        rd = list_progress.assign_all_pending_to(only)
    else:
        list_progress.CLAIMS_PATH.write_text("{}\n", encoding="utf-8")
        rd = list_progress.redistribute_pending_evenly()
    print("pending", rd.get("pending"), rd.get("phones"), "phone_only", only)
    import phone_fleet

    phones = phone_fleet.load_phones_raw()
    names = [str(p["name"]) for p in phones]
    for p in phones:
        print(p["name"], "queue", list_progress.queue_left(int(p["shard"])))
    pending = int(rd.get("pending") or rd.get("count") or 0)
    if pending <= 0:
        print("nothing_to_collect")
        return 0
    for name in names:
        harvest_control.stop(name)
    if only is not None:
        by_shard = {int(p["shard"]): str(p["name"]) for p in phones}
        target = by_shard.get(int(only))
        if target:
            print("start", target, harvest_control.start(target))
    else:
        for name in names:
            print("start", name, harvest_control.start(name))
    return 0


if __name__ == "__main__":
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    raise SystemExit(main())
