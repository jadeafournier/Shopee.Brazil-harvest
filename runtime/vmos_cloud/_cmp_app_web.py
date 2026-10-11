import json
import re
from collections import Counter
from pathlib import Path

APP_DIR = Path(r"D:\Luxury\Great Scraper2\Great Scraper\client 4 api integration\br_deliverable_getpc")
WEB_ROOTS = [
    Path(r"D:\Luxury\Great Scraper2\Great Scraper\runtime\client5_related\pdp_json"),
    Path(r"D:\Luxury\Great Scraper2\Great Scraper\runtime\client5_related\packets"),
]


def top_keys(d):
    return set(d.keys()) if isinstance(d, dict) else set()


def dig(d, *path):
    cur = d
    for p in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(p)
    return cur


def models_n(doc):
    m = dig(doc, "data", "item", "models")
    return len(m) if isinstance(m, list) else None


def userid(doc):
    acc = dig(doc, "data", "account") or {}
    return {
        "userid": acc.get("userid") or acc.get("user_id"),
        "username": acc.get("username") or acc.get("user_name"),
        "portrait": acc.get("portrait"),
        "keys": sorted(acc.keys()) if isinstance(acc, dict) else [],
    }


app_files = {}
for p in APP_DIR.glob("output_*.json"):
    m = re.match(r"output_(\d+)_(\d+)\.json$", p.name)
    if m:
        app_files[(int(m.group(1)), int(m.group(2)))] = p

web_files = {}
for root in WEB_ROOTS:
    if not root.exists():
        continue
    for p in root.rglob("*.json"):
        m = re.match(r"(\d+)_(\d+)\.json$", p.name)
        if m:
            web_files[(int(m.group(1)), int(m.group(2)))] = p

overlap = sorted(set(app_files) & set(web_files))
print("APP", len(app_files))
print("WEB", len(web_files))
print("OVERLAP", len(overlap))
print("APP_ONLY", len(set(app_files) - set(web_files)))
print("WEB_ONLY", len(set(web_files) - set(app_files)))

BIZ = [
    ("title", ("data", "item", "title")),
    ("price", ("data", "item", "price")),
    ("price_min", ("data", "item", "price_min")),
    ("price_max", ("data", "item", "price_max")),
    ("sold", ("data", "item", "sold")),
    ("historical_sold", ("data", "item", "historical_sold")),
    ("stock", ("data", "item", "stock")),
    ("shop_location", ("data", "item", "shop_location")),
    ("brand", ("data", "item", "brand")),
    ("cat_id", ("data", "item", "cat_id")),
    ("currency", ("data", "item", "currency")),
    ("show_discount", ("data", "item", "show_discount")),
    ("item_status", ("data", "item", "item_status")),
    ("image", ("data", "item", "image")),
    ("ctime", ("data", "item", "ctime")),
]

field_same = Counter()
field_diff = Counter()
app_only_keys = Counter()
web_only_keys = Counter()
data_key_app = Counter()
data_key_web = Counter()
pair_summaries = []

for key in overlap:
    sid, iid = key
    app = json.loads(app_files[key].read_text(encoding="utf-8"))
    web = json.loads(web_files[key].read_text(encoding="utf-8"))
    a_item = dig(app, "data", "item") or {}
    w_item = dig(web, "data", "item") or {}
    a_data = app.get("data") or {}
    w_data = web.get("data") or {}
    for k in top_keys(a_data):
        data_key_app[k] += 1
    for k in top_keys(w_data):
        data_key_web[k] += 1
    only_a = top_keys(a_item) - top_keys(w_item)
    only_w = top_keys(w_item) - top_keys(a_item)
    for k in only_a:
        app_only_keys[k] += 1
    for k in only_w:
        web_only_keys[k] += 1
    diffs = []
    for name, path in BIZ:
        av = dig(app, *path)
        wv = dig(web, *path)
        if av == wv:
            field_same[name] += 1
        else:
            field_diff[name] += 1
            diffs.append({"field": name, "app": av, "web": wv})
    am, wm = models_n(app), models_n(web)
    if am == wm:
        field_same["models_n"] += 1
    else:
        field_diff["models_n"] += 1
        diffs.append({"field": "models_n", "app": am, "web": wm})
    pair_summaries.append(
        {
            "shop": sid,
            "item": iid,
            "title_app": a_item.get("title"),
            "title_web": w_item.get("title"),
            "price_app": a_item.get("price"),
            "price_web": w_item.get("price"),
            "sold_app": a_item.get("sold"),
            "sold_web": w_item.get("sold"),
            "hist_app": a_item.get("historical_sold"),
            "hist_web": w_item.get("historical_sold"),
            "models_app": am,
            "models_web": wm,
            "account_app": userid(app),
            "account_web": userid(web),
            "env_app": sorted(app.keys()),
            "env_web": sorted(web.keys()),
            "data_app": sorted(a_data.keys()) if isinstance(a_data, dict) else [],
            "data_web": sorted(w_data.keys()) if isinstance(w_data, dict) else [],
            "item_app_n": len(a_item),
            "item_web_n": len(w_item),
            "only_app_item": sorted(only_a),
            "only_web_item": sorted(only_w),
            "diffs": diffs,
            "app_bytes": app_files[key].stat().st_size,
            "web_bytes": web_files[key].stat().st_size,
            "app_src": str(app_files[key]),
            "web_src": str(web_files[key]),
        }
    )

print("FIELD_SAME", dict(field_same))
print("FIELD_DIFF", dict(field_diff))
print("ITEM_KEYS_ONLY_APP", app_only_keys.most_common(40))
print("ITEM_KEYS_ONLY_WEB", web_only_keys.most_common(40))
print("DATA_KEYS_APP", data_key_app.most_common())
print("DATA_KEYS_WEB", data_key_web.most_common())
print("=== PAIRS ===")
for s in pair_summaries:
    print(
        json.dumps(
            {
                k: s[k]
                for k in [
                    "shop",
                    "item",
                    "title_app",
                    "price_app",
                    "price_web",
                    "sold_app",
                    "sold_web",
                    "hist_app",
                    "hist_web",
                    "models_app",
                    "models_web",
                    "item_app_n",
                    "item_web_n",
                    "only_app_item",
                    "only_web_item",
                    "app_bytes",
                    "web_bytes",
                    "account_app",
                    "account_web",
                ]
            },
            ensure_ascii=False,
        )
    )

out = Path(r"D:\Luxury\Great Scraper2\Great Scraper\runtime\vmos_cloud\app_vs_web_compare.json")
out.write_text(
    json.dumps(
        {
            "app": len(app_files),
            "web": len(web_files),
            "overlap": len(overlap),
            "app_only": len(set(app_files) - set(web_files)),
            "web_only": len(set(web_files) - set(app_files)),
            "field_same": dict(field_same),
            "field_diff": dict(field_diff),
            "item_keys_only_app": app_only_keys.most_common(),
            "item_keys_only_web": web_only_keys.most_common(),
            "data_keys_app": data_key_app.most_common(),
            "data_keys_web": data_key_web.most_common(),
            "pairs": pair_summaries,
        },
        ensure_ascii=False,
        indent=2,
    ),
    encoding="utf-8",
)
print("WROTE", out)
