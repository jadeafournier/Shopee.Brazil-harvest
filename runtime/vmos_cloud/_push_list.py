# -*- coding: utf-8 -*-
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import task_lists
from _sever_bridge import _load_sent, deliver
from collect_layout import UPLOAD_DIR


def main() -> int:
    meta = json.loads(task_lists.LIST_PATH.read_text(encoding="utf-8"))
    sent = _load_sent()
    ok = fail = skip = 0
    for row in meta.get("tasks") or []:
        fn = f"{row['shop_id']}_{row['item_id']}.json"
        path = UPLOAD_DIR / fn
        if fn in sent:
            skip += 1
            continue
        if not path.is_file():
            fail += 1
            continue
        try:
            result = deliver(path)
            if result in ("ok", "saved"):
                ok += 1
            else:
                fail += 1
        except Exception:
            fail += 1
    print("push_ok", ok, "skip", skip, "fail", fail)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
