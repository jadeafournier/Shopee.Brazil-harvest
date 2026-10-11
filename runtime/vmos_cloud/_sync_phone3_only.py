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
    task_lists.save_incoming(tasks, _now())
    meta = json.loads(task_lists.LIST_PATH.read_text(encoding="utf-8"))
    print("server_count", len(tasks), "saved", meta.get("count"), "updated_at", meta.get("updated_at"))
    assign = list_progress.assign_all_pending_to(0, 3)
    print("assign", assign)
    print("queues", [list_progress.queue_left(i) for i in range(3)])
    for name in ("Phone2", "Phone1", "Phone3"):
        harvest_control.stop(name)
    if int(assign.get("count") or 0) <= 0:
        print("nothing_to_collect")
        return 0
    print("start Phone3", harvest_control.start("Phone3"))
    return 0


if __name__ == "__main__":
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    raise SystemExit(main())
