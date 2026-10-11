# -*- coding: utf-8 -*-
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import list_progress
import task_lists
from _sever_bridge import _env, _get

SENT_PATH = Path(__file__).resolve().parents[1] / "br_member_hub" / "m02_push_upload.json"

DIGEST_100 = "9f720edf0f37d8e3da09"
DIGEST_99 = "38a1299f531e6887cc9a"


def pairs_from_tasks(tasks: list) -> set[tuple[int, int]]:
    out: set[tuple[int, int]] = set()
    for row in tasks:
        if not isinstance(row, dict):
            continue
        try:
            out.add((int(row["shop_id"]), int(row["item_id"])))
        except (TypeError, ValueError, KeyError):
            pass
    return out


def audit(tasks: list, saved: set[tuple[int, int]], sent: dict[str, dict]) -> dict:
    pairs = pairs_from_tasks(tasks)
    missing_upload = [p for p in pairs if p not in saved]
    upload_not_sent = []
    hub_sent = 0
    for p in pairs:
        fn = f"{p[0]}_{p[1]}.json"
        if fn in sent:
            hub_sent += 1
        elif p in saved:
            upload_not_sent.append(fn)
    return {
        "total": len(pairs),
        "in_upload": len(pairs) - len(missing_upload),
        "missing_upload": len(missing_upload),
        "hub_sent": hub_sent,
        "upload_not_sent": len(upload_not_sent),
    }


def main() -> int:
    saved = list_progress._saved_pairs()
    sent = {}
    if SENT_PATH.is_file():
        try:
            sent = json.loads(SENT_PATH.read_text(encoding="utf-8")).get("sent") or {}
        except (OSError, json.JSONDecodeError):
            sent = {}

    cur = json.loads(task_lists.LIST_PATH.read_text(encoding="utf-8"))
    tasks99 = cur.get("tasks") or []
    a99 = audit(tasks99, saved, sent)
    print("CURRENT_99", cur.get("count"), cur.get("updated_at"))
    print(json.dumps(a99, ensure_ascii=False))
    print("pending99", sum(1 for p in pairs_from_tasks(tasks99) if p not in saved))
    print("queue", [list_progress.queue_left(i) for i in range(3)])

    env = _env()
    api_tasks: list = []
    try:
        data = _get(
            f"http://{env['BR_MEMBER_HOST']}:{env['BR_MEMBER_PORT']}/v1/tasks?limit=15000",
            env["BR_MEMBER_KEY"],
        )
        raw = data.get("tasks") if isinstance(data, dict) else None
        if isinstance(raw, list):
            api_tasks = raw
    except Exception as exc:  # noqa: BLE001
        print("API_ERROR", str(exc)[:120])
    a_api = audit(api_tasks, saved, sent) if api_tasks else {}
    print("API_TASKS", len(api_tasks), json.dumps(a_api, ensure_ascii=False))

    # 100-list pairs = saved pairs that belong to 100-era shops OR union heuristic:
    # Re-fetch cannot recover overwritten file; compare upload pairs to current tasks.json
    # Approximate: items collected between 100 assign and 99 assign from upload not in 99
    p99 = pairs_from_tasks(tasks99)
    only_100ish = saved - p99
    sent_100ish = sum(
        1 for p in only_100ish if f"{p[0]}_{p[1]}.json" in sent
    )
    print(
        "HINT_100_OVERLAP",
        "pairs_in_upload_not_in_99",
        len(only_100ish),
        "of_those_hub_sent",
        sent_100ish,
    )
    print("NOTE_100", "local tasks.json was overwritten; full 100-pair audit needs hub copy")
    return 0


if __name__ == "__main__":
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    raise SystemExit(main())
