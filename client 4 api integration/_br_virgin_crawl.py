import json,sys,tempfile,time,uuid
from pathlib import Path
ROOT=Path(r'D:\Great Scraper\Great Scraper'); HERE=ROOT/'client 4 api integration'
sys.path[:0]=[str(ROOT),str(ROOT/'shopee_getpc_crawler'),str(HERE)]
from br_cookies import br_context_kwargs,pw_cookie_list,normalize_br_cookies,enforce_br_locale
import captcha_mint as mint, br_human as human
from patchright.sync_api import sync_playwright

DELIV=HERE/'br_deliverable_getpc'
URLS=HERE/'br_active_product_urls_100k.txt'
import re
have=set()
for f in DELIV.glob('output_*.json'):
  m=re.match(r'output_(\d+)_(\d+)\.json', f.name)
  if m: have.add((m.group(1),m.group(2)))
pending=[]
for line in URLS.read_text(encoding='utf-8',errors='replace').splitlines():
  line=line.strip()
  if not line: continue
  m=re.search(r'/product/(\d+)/(\d+)', line) or re.search(r'i\.(\d+)\.(\d+)', line)
  if m and (m.group(1),m.group(2)) not in have:
    pending.append(line)

# virgin jar: only locale cookies
jar=enforce_br_locale(normalize_br_cookies({}, fresh_cert=True))
print('virgin keys', sorted(jar.keys()), flush=True)
proxy,_,sub=mint._proxy_dict('BR')
print('proxy', sub, flush=True)
p=sync_playwright().start(); udd=tempfile.mkdtemp(prefix='brvir_'); cx=br_context_kwargs()
ctx=p.chromium.launch_persistent_context(udd,channel='chrome',headless=False,proxy=proxy,viewport={'width':1440,'height':900},locale=cx['locale'],timezone_id=cx['timezone_id'],user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',extra_http_headers=cx['extra_http_headers'],args=['--no-sandbox','--disable-dev-shm-usage','--disable-blink-features=AutomationControlled'])
ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")
ctx.add_cookies(pw_cookie_list(jar, fresh_cert=True))
page=ctx.new_page()
saved=0
def on_resp(r):
  global saved
  try:
    if '/api/v4/pdp/get_pc' not in r.url or r.status!=200: return
    j=r.json(); item=(j.get('data') or {}).get('item') or {}
    err=j.get('error'); keys=len(item) if isinstance(item,dict) else 0
    print(f'get_pc err={err} keys={keys}', flush=True)
    if err in (0,None) and keys>=40:
      shop=str(item.get('shop_id') or item.get('shopid')); it=str(item.get('item_id') or item.get('itemid'))
      out=DELIV/f'output_{shop}_{it}.json'
      if not out.exists():
        payload={'task_link':r.url,'token':'br_virgin','api_key':'br_virgin','bff_meta':{'extract_method':'browser_get_pc'},'error':None,'error_msg':None,'data':j['data']}
        out.write_text(json.dumps(payload,ensure_ascii=False),encoding='utf-8')
        saved+=1
        print('OK', out.name, 'total', len(list(DELIV.glob('output_*.json'))), flush=True)
  except Exception as e:
    print('parse',e,flush=True)
page.on('response', on_resp)
print('warm…', flush=True)
try: human.warm_home(page,'shopee.com.br')
except Exception as e: print('warm',e,flush=True)
for _ in range(6):
  u=page.url or ''
  print('url', u[:100], flush=True)
  if '/verify/traffic/error' in u:
    print('traffic_error', flush=True); break
  if '/verify/traffic' in u:
    mint._wait_traffic_pass(page,'shopee.com.br',secs=35)
  elif '/verify/captcha' in u:
    print('captcha page — try local solver', flush=True)
    try:
      import captcha_playwright as cpw
      sol=cpw.solve_captcha_page(page, dom='shopee.com.br', gen_flag={'ok':True})
      print('sol', sol, flush=True)
    except Exception as e:
      print('captcha', e, flush=True)
    break
  else:
    break
  page.wait_for_timeout(1500)
ck={c['name']:c['value'] for c in ctx.cookies()}
print('after warm cert', bool(ck.get('AC_CERT_D')), 'nck', len(ck), flush=True)
for i,u in enumerate(pending[:6]):
  print('goto', i, u[-50:], flush=True)
  before=saved
  page.goto(u, wait_until='commit', timeout=50000)
  t0=time.time()
  while time.time()-t0<60:
    if '/verify/traffic/error' in (page.url or ''):
      print('te', flush=True); break
    if '/verify/traffic' in (page.url or ''):
      mint._wait_traffic_pass(page,'shopee.com.br',secs=25)
    if '/verify/captcha' in (page.url or ''):
      print('captcha mid', flush=True)
      try:
        import captcha_playwright as cpw
        print(cpw.solve_captcha_page(page, dom='shopee.com.br', gen_flag={'ok':True}), flush=True)
      except Exception as e:
        print(e, flush=True)
      break
    if saved>before: break
    page.wait_for_timeout(400)
  else:
    if saved==before: print('miss', flush=True)
  if '/verify/traffic/error' in (page.url or ''):
    break
  time.sleep(3)
print('VIRGIN_DONE saved', saved, 'total', len(list(DELIV.glob('output_*.json'))), flush=True)
# persist virgin mint if any cert
ck={c['name']:c['value'] for c in ctx.cookies()}
if ck.get('AC_CERT_D') or ck.get('SPC_F'):
  (ROOT/'shopee_getpc_crawler'/'sessions'/'BR'/f'br_virgin_{int(time.time())}.json').write_text(json.dumps({'cookies':ck,'ts':time.time()},ensure_ascii=False),encoding='utf-8')
  print('persisted virgin jar', flush=True)
ctx.close(); p.stop()
