import json
from collections import Counter
from pathlib import Path

rep = json.loads(
    Path(r"D:\Luxury\Great Scraper2\Great Scraper\runtime\vmos_cloud\app_vs_web_compare.json").read_text(
        encoding="utf-8"
    )
)


def dig(d, *path):
    cur = d
    for p in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(p)
    return cur


pairs = rep["pairs"]
price_rows = []
review_rows = []
schema_rows = []
acc_app = Counter()
acc_web = Counter()
sold_same = 0
sold_diff = 0
desc_app = []
desc_web = []
has_shop_app = 0
has_shop_web = 0
has_removed_app = 0
has_removed_web = 0

for s in pairs:
    app = json.loads(Path(s["app_src"]).read_text(encoding="utf-8"))
    web = json.loads(Path(s["web_src"]).read_text(encoding="utf-8"))
    pa, pw = s["price_app"], s["price_web"]
    if isinstance(pa, (int, float)) and isinstance(pw, (int, float)) and pw:
        delta = (pa - pw) / 100000
        pct = (pa - pw) / pw * 100
        price_rows.append(
            {
                "shop": s["shop"],
                "item": s["item"],
                "title": (s["title_app"] or "")[:60],
                "app_brl": round(pa / 100000, 2),
                "web_brl": round(pw / 100000, 2),
                "delta_brl": round(delta, 2),
                "pct": round(pct, 1),
            }
        )
    ar = dig(app, "data", "product_review") or {}
    wr = dig(web, "data", "product_review") or {}
    if ar.get("sold_count_display") == wr.get("sold_count_display"):
        sold_same += 1
    else:
        sold_diff += 1
    review_rows.append(
        {
            "shop": s["shop"],
            "item": s["item"],
            "rating_app": ar.get("rating_star"),
            "rating_web": wr.get("rating_star"),
            "cmt_app": ar.get("total_rating_count"),
            "cmt_web": wr.get("total_rating_count"),
            "liked_app": ar.get("liked_count"),
            "liked_web": wr.get("liked_count"),
            "sold_app": ar.get("sold_count_display"),
            "sold_web": wr.get("sold_count_display"),
        }
    )
    a_keys = set((app.get("data") or {}).keys())
    w_keys = set((web.get("data") or {}).keys())
    if "shop" in a_keys:
        has_shop_app += 1
    if "shop" in w_keys:
        has_shop_web += 1
    if "removed_fields" in a_keys:
        has_removed_app += 1
    if "removed_fields" in w_keys:
        has_removed_web += 1
    schema_rows.append(
        {
            "shop": s["shop"],
            "item": s["item"],
            "app_only_data": sorted(a_keys - w_keys),
            "web_only_data": sorted(w_keys - a_keys),
            "app_n": len(a_keys),
            "web_n": len(w_keys),
        }
    )
    ad = dig(app, "data", "product_description")
    wd = dig(web, "data", "product_description")
    if isinstance(ad, dict):
        ad = ad.get("description") or ad.get("text") or str(ad)
    if isinstance(wd, dict):
        wd = wd.get("description") or wd.get("text") or str(wd)
    desc_app.append(len(ad) if isinstance(ad, str) else 0)
    desc_web.append(len(wd) if isinstance(wd, str) else 0)
    acc_app[s["account_app"].get("userid")] += 1
    acc_web[s["account_web"].get("userid")] += 1

price_rows.sort(key=lambda x: abs(x["pct"]), reverse=True)
same_price = sum(1 for r in price_rows if r["delta_brl"] == 0)
print("PRICE_N", len(price_rows), "SAME", same_price, "DIFF", len(price_rows) - same_price)
print("PRICE_MEDIAN_ABS_PCT", sorted(abs(r["pct"]) for r in price_rows)[len(price_rows) // 2])
print("PRICE_MAX", price_rows[0])
print("SOLD_DISPLAY_SAME", sold_same, "DIFF", sold_diff)
print("HAS_SHOP app/web", has_shop_app, has_shop_web)
print("HAS_REMOVED app/web", has_removed_app, has_removed_web)
print("DESC_APP avg", sum(desc_app) / len(desc_app), "WEB", sum(desc_web) / len(desc_web))
print("ACC_APP", acc_app)
print("ACC_WEB", acc_web)

app_only_union = Counter()
web_only_union = Counter()
for r in schema_rows:
    for k in r["app_only_data"]:
        app_only_union[k] += 1
    for k in r["web_only_data"]:
        web_only_union[k] += 1
print("DATA_APP_ONLY", app_only_union.most_common())
print("DATA_WEB_ONLY", web_only_union.most_common())

cmt_diff = []
for r in review_rows:
    if r["cmt_app"] and r["cmt_web"]:
        cmt_diff.append(r["cmt_app"] - r["cmt_web"])
print("CMT_DIFF min/max/avg", min(cmt_diff), max(cmt_diff), sum(cmt_diff) / len(cmt_diff))

out = {
    "price_rows": price_rows,
    "review_rows": review_rows,
    "schema_rows": schema_rows,
    "sold_display_same": sold_same,
    "sold_display_diff": sold_diff,
    "has_shop": {"app": has_shop_app, "web": has_shop_web},
    "has_removed": {"app": has_removed_app, "web": has_removed_web},
    "desc_avg": {"app": sum(desc_app) / len(desc_app), "web": sum(desc_web) / len(desc_web)},
    "acc_app": acc_app.most_common(),
    "acc_web": acc_web.most_common(),
    "data_app_only": app_only_union.most_common(),
    "data_web_only": web_only_union.most_common(),
}
Path(r"D:\Luxury\Great Scraper2\Great Scraper\runtime\vmos_cloud\app_vs_web_compare2.json").write_text(
    json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
)
print("WROTE compare2")
