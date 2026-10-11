import json,sys,tempfile,time
from pathlib import Path
ROOT=Path(r'D:\Great Scraper\Great Scraper'); HERE=ROOT/'client 4 api integration'
sys.path[:0]=[str(ROOT),str(ROOT/'shopee_getpc_crawler'),str(HERE)]
from br_cookies import br_context_kwargs,pw_cookie_list,normalize_br_cookies,enforce_br_locale
import captcha_mint as mint, br_human as human
from patchright.sync_api import sync_playwright

SESS=ROOT/'shopee_getpc_crawler'/'sessions'/'BR'
# Use account that worked at 04:28
doc=json.loads((SESS/'br_5196720216_hsRjFTxp_live.json').read_text(encoding='utf-8'))
jar=dict(doc.get('cookies') or doc)
for k in ('AC_CERT_D','AC_CERT_S','AC_CERT_A'):
    jar.pop(k, None)
jar=enforce_br_locale(normalize_br_cookies(jar, fresh_cert=False))
proxy,_,sub=mint._proxy_dict('BR')
print('proxy', sub, flush=True)
p=sync_playwright().start(); udd=tempfile.mkdtemp(prefix='brsign_'); cx=br_context_kwargs()
ctx=p.chromium.launch_persistent_context(udd,channel='chrome',headless=False,proxy=proxy,viewport={'width':1366,'height':768},locale=cx['locale'],timezone_id=cx['timezone_id'],user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',extra_http_headers=cx['extra_http_headers'],args=['--no-sandbox','--disable-dev-shm-usage','--disable-blink-features=AutomationControlled'])
ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")
ctx.add_cookies(pw_cookie_list(jar, fresh_cert=False))
page=ctx.new_page()
holder={}
def on_req(r):
  if 'get_pc' in r.url:
    h=r.headers
    holder['req']={k:h.get(k) for k in h if 'sap' in k.lower() or k.lower() in ('x-sap-ri','x-sap-sec','af-ac-enc-dat','sz-token','x-csrftoken','referer')}
    holder['req']['all_sap']=[k for k in h if 'sap' in k.lower() or k.startswith('x-')]
def on_resp(r):
  if 'get_pc' in r.url and r.status==200:
    try:
      j=r.json(); item=(j.get('data') or {}).get('item') or {}
      holder['resp']={'err':j.get('error'),'keys':len(item) if isinstance(item,dict) else 0,'msg':j.get('error_msg')}
    except Exception as e:
      holder['resp_e']=str(e)
page.on('request', on_req); page.on('response', on_resp)
print('warm…', flush=True)
human.warm_home(page,'shopee.com.br')
for _ in range(4):
  if '/verify/traffic' in (page.url or ''):
    print('traffic', page.url, flush=True)
    mint._wait_traffic_pass(page,'shopee.com.br', secs=28)
  else:
    break
  page.wait_for_timeout(1500)
print('home', page.url, flush=True)
url='https://shopee.com.br/product/1006215031/24076819963'
page.goto(url, wait_until='commit', timeout=60000)
t0=time.time()
while time.time()-t0<55:
  if 'resp' in holder: break
  page.wait_for_timeout(500)
print('RESULT', json.dumps(holder, ensure_ascii=False)[:1200], flush=True)
ck={c['name']:c['value'] for c in ctx.cookies()}
print('cert', 'Y' if ck.get('AC_CERT_D') else 'N', 'SPC_U', ck.get('SPC_U'), flush=True)
# save if good
if holder.get('resp',{}).get('keys',0)>40:
  from pathlib import Path
  DELIV=HERE/'br_deliverable_getpc'
  # re-fetch via last - can't; just print
  print('SUCCESS keys', holder['resp']['keys'], flush=True)
ctx.close(); p.stop()
