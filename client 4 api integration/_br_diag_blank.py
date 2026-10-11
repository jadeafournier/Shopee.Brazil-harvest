import json, sys, tempfile, time
from pathlib import Path
ROOT = Path(r'D:\Great Scraper\Great Scraper')
HERE = ROOT / 'client 4 api integration'
sys.path[:0] = [str(ROOT), str(ROOT/'shopee_getpc_crawler'), str(HERE)]
from br_cookies import br_context_kwargs, pw_cookie_list, normalize_br_cookies, enforce_br_locale
import captcha_mint as mint
from patchright.sync_api import sync_playwright

SESS = ROOT / 'shopee_getpc_crawler' / 'sessions' / 'BR'
doc = json.loads((SESS/'br_5196720216_hsRjFTxp_live.json').read_text(encoding='utf-8'))
jar = enforce_br_locale(normalize_br_cookies(dict(doc.get('cookies') or doc), fresh_cert=False))
proxy,_,sub = mint._proxy_dict('BR')
print('proxy', sub, flush=True)
p = sync_playwright().start()
udd = tempfile.mkdtemp(prefix='brdiag_')
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
reqs=[]
def on_req(r):
    if 'shopee' in r.url: reqs.append(r.url[:120])
page.on('request', on_req)
url='https://shopee.com.br/product/1006215031/24076819963'
page.goto(url, wait_until='commit', timeout=60000)
for i in range(20):
    page.wait_for_timeout(1000)
    t=page.title() or ''
    u=page.url or ''
    html_len=len(page.content() or '')
    print(f't={i}s title={t[:50]!r} url={u[:70]} html={html_len} reqs={len(reqs)}', flush=True)
    if t or html_len>50000:
        break
print('sample reqs:', *reqs[:8], sep='\n', flush=True)
print('FINAL title', page.title(), 'cookies', len(ctx.cookies()), flush=True)
ck={c['name']:c['value'] for c in ctx.cookies()}
print('AC_CERT', 'Y' if ck.get('AC_CERT_D') else 'N', 'SPC_U', ck.get('SPC_U'), flush=True)
ctx.close(); p.stop()
