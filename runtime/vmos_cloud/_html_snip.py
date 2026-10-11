import urllib.request

h = urllib.request.urlopen("http://127.0.0.1:8766/").read().decode("utf-8", "replace")
print("len", len(h))
print("list-pool-v2", "list-pool-v2" in h)
print("wl div", '<div class="workload-picks" id="wl-picks">' in h)
for tag in ("collect-actions", "workload-picks", "서버 목록 잔량", "phone-rows"):
    print("idx", tag, h.find(tag))
# layout snippet
a = h.find('<div class="collect-actions">')
b = h.find('<div class="workload-picks"')
print("collect before wl-picks div", a >= 0 and b >= 0 and a < b)
