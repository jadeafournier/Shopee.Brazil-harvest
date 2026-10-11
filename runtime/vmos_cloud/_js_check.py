import re
import urllib.request

html = urllib.request.urlopen("http://127.0.0.1:8766/").read().decode("utf-8", "replace")
m = re.search(r"<script>(.*?)</script>", html, re.S)
if not m:
    print("no script")
    raise SystemExit(1)
js = m.group(1)
path = __file__.replace("_js_check.py", "_status_ui.js")
open(path, "w", encoding="utf-8").write(js)
print("js bytes", len(js), "written", path)
