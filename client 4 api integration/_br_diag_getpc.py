import json, sys, tempfile, time
from pathlib import Path
ROOT = Path(r'D:\Great Scraper\Great Scraper')
HERE = ROOT / 'client 4 api integration'
sys.path[:0] = [str(ROOT), str(ROOT/'shopee_getpc_crawler'), str(HERE)]
from br_cookies import br_context_kwargs, pw_cookie_list, normalize_br_cookies, enforce_br_locale
import captcha_mint as mint, br_human as human
from patchright.sync_api import sync_playwright

SESS = ROOT / 'shopee_getpc_crawler' / 'sessions' / 'BR'
doc = json.loads((SESS/'br_5196720216_hsRjFTxp_live.json').read_text(encoding='utf-8'))
jar = dict(doc.get('cookies') or doc)
for k in ('AC_CERT_D','AC_CERT_S','AC_CERT_A'):
    jar.pop(k, None)
jar = enforce_br_locale(normalize_br_cookies(jar, fresh_cert=False))
proxy,_,sub = mint._proxy_dict('BR')
print('proxy', sub, flush=True)
p = sync_playwright().start()
udd = tempfile.mkdtemp(prefix='brdiag2_')
cx = br_context_kwargs()
ctx = p.chromium.launch_persistent_context(
    udd, channel='chrome', headless=False, proxy=proxy,
    viewport={'width':1366,'height':768}, locale=cx['locale'], timezone_id=cx['timezone_id'],
    user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
    extra_http_headers=cx['extra_http_headers'],
    args=['--no-sandbox','--disable-dev-shm-usage','--disable-blink-features=AutomationControlled'])
ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")
ctx.add_cookies(pw_cookie_list(jar, fresh_cert=False))
page = ctx.new_page()
apis=[]
def on_resp(r):
    u=r.url
    if '/api/' in u:
        apis.append((r.status, u.split('shopee.com.br')[-1][:140]))
page.on('response', on_resp)
print('warm…', flush=True)
try:
    human.warm_home(page, 'shopee.com.br')
except Exception as e:
    print('warm', e, flush=True)
print('after warm url', page.url, 'apis', len(apis), flush=True)
for st,u in apis[-10:]:
    print(' ', st, u, flush=True)
apis.clear()
url='https://shopee.com.br/product/1006215031/24076819963'
print('goto pdp', flush=True)
page.goto(url, wait_until='commit', timeout=60000)
for i in range(45):
    page.wait_for_timeout(1000)
    gps=[x for x in apis if 'get_pc' in x[1]]
    if gps:
        print(f't={i}s GOT get_pc', gps[-1], flush=True)
        break
    if i in (5,10,15,20,30,40):
        print(f't={i}s url={page.url[:80]} apis={len(apis)} title={page.title()!r}', flush=True)
        for st,u in apis[-5:]:
            print('  ', st, u, flush=True)
else:
    print('NO get_pc', flush=True)
    print('all apis:', flush=True)
    for st,u in apis:
        print(st, u, flush=True)
# if get_pc, dump body
for st,u in apis:
    if 'get_pc' in u:
        print('found', st, u, flush=True)
ctx.close(); p.stop()
