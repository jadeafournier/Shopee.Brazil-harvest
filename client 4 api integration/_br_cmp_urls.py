import json,sys,tempfile,time
from pathlib import Path
ROOT=Path(r'D:\Great Scraper\Great Scraper'); HERE=ROOT/'client 4 api integration'
sys.path[:0]=[str(ROOT),str(ROOT/'shopee_getpc_crawler'),str(HERE)]
from br_cookies import br_context_kwargs,pw_cookie_list,normalize_br_cookies,enforce_br_locale
import captcha_mint as mint
from patchright.sync_api import sync_playwright
SESS=ROOT/'shopee_getpc_crawler'/'sessions'/'BR'
doc=json.loads((SESS/'br_1706801030_ydvUxsIK_live.json').read_text(encoding='utf-8'))
jar=enforce_br_locale(normalize_br_cookies(dict(doc.get('cookies') or doc), fresh_cert=False))
proxy,_,_=mint._proxy_dict('BR')
urls=[
 'https://shopee.com.br/product/980944158/26142163515',
 'https://shopee.com.br/product/1006215031/24076819963',
]
p=sync_playwright().start(); udd=tempfile.mkdtemp(prefix='brcmp_'); cx=br_context_kwargs()
ctx=p.chromium.launch_persistent_context(udd,channel='chrome',headless=False,proxy=proxy,viewport={'width':1366,'height':768},locale=cx['locale'],timezone_id=cx['timezone_id'],user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',extra_http_headers=cx['extra_http_headers'],args=['--no-sandbox','--disable-dev-shm-usage','--disable-blink-features=AutomationControlled'])
ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")
ctx.add_cookies(pw_cookie_list(jar,fresh_cert=False))
page=ctx.new_page()
for url in urls:
  holder={}
  def on_resp(r, holder=holder):
    try:
      if 'get_pc' in r.url and r.status==200:
        j=r.json(); item=(j.get('data') or {}).get('item') or {}
        holder['j']=(j.get('error'), len(item) if isinstance(item,dict) else 0, r.url[-60:])
    except Exception as e:
      holder['e']=str(e)
  page.on('response', on_resp)
  print('GOTO', url, flush=True)
  page.goto(url, wait_until='commit', timeout=60000)
  t0=time.time()
  while time.time()-t0 < 50:
    if 'j' in holder: break
    page.wait_for_timeout(500)
  print('RESULT', holder or 'NONE', 'elapsed', round(time.time()-t0,1), 'title', repr(page.title()), flush=True)
  page.remove_listener('response', on_resp)
ctx.close(); p.stop()
