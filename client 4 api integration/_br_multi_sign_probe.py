import json,sys,tempfile,time
from pathlib import Path
ROOT=Path(r'D:\Great Scraper\Great Scraper'); HERE=ROOT/'client 4 api integration'
sys.path[:0]=[str(ROOT),str(ROOT/'shopee_getpc_crawler'),str(HERE)]
from br_cookies import br_context_kwargs,pw_cookie_list,normalize_br_cookies,enforce_br_locale
import captcha_mint as mint, br_human as human
from patchright.sync_api import sync_playwright

SESS=ROOT/'shopee_getpc_crawler'/'sessions'/'BR'
DELIV=HERE/'br_deliverable_getpc'
OUT=HERE/'br_sign_probe_result.json'

accounts=['br_5196720216_hsRjFTxp_live.json','br_1707407334_mRNUQARQ_live.json','br_1706801030_ydvUxsIK_live.json']
results=[]
for acc in accounts:
  doc=json.loads((SESS/acc).read_text(encoding='utf-8'))
  jar=dict(doc.get('cookies') or doc)
  for k in ('AC_CERT_D','AC_CERT_S','AC_CERT_A'):
    jar.pop(k, None)
  jar=enforce_br_locale(normalize_br_cookies(jar, fresh_cert=False))
  proxy,_,sub=mint._proxy_dict('BR')
  print('===', acc, sub[-40:], flush=True)
  p=sync_playwright().start(); udd=tempfile.mkdtemp(prefix='brp_'); cx=br_context_kwargs()
  try:
    ctx=p.chromium.launch_persistent_context(udd,channel='chrome',headless=False,proxy=proxy,viewport={'width':1366,'height':768},locale=cx['locale'],timezone_id=cx['timezone_id'],user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',extra_http_headers=cx['extra_http_headers'],args=['--no-sandbox','--disable-dev-shm-usage','--disable-blink-features=AutomationControlled'])
    ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")
    ctx.add_cookies(pw_cookie_list(jar, fresh_cert=False))
    page=ctx.new_page()
    holder={'signed':False}
    def on_req(r):
      if 'get_pc' in r.url:
        h={k.lower():v for k,v in r.headers.items()}
        holder['signed']=bool(h.get('x-sap-sec'))
        holder['ri']=h.get('x-sap-ri','')[:20]
    def on_resp(r):
      if 'get_pc' in r.url and r.status==200:
        try:
          j=r.json(); item=(j.get('data') or {}).get('item') or {}
          holder['err']=j.get('error'); holder['keys']=len(item) if isinstance(item,dict) else 0
          holder['body']=j
        except Exception as e:
          holder['e']=str(e)
    page.on('request', on_req); page.on('response', on_resp)
    try:
      human.warm_home(page,'shopee.com.br')
    except Exception as e:
      print('warm',e,flush=True)
    for _ in range(5):
      u=page.url or ''
      if '/verify/traffic/error' in u:
        print('traffic_error',flush=True); break
      if '/verify/traffic' in u:
        print('pass traffic',flush=True)
        try: mint._wait_traffic_pass(page,'shopee.com.br',secs=30)
        except Exception as e: print('tp',e,flush=True)
      else:
        break
      page.wait_for_timeout(1200)
    page.goto('https://shopee.com.br/product/1006215031/24076819963', wait_until='commit', timeout=60000)
    t0=time.time()
    while time.time()-t0<60:
      if 'err' in holder: break
      if '/verify/traffic' in (page.url or ''):
        try: mint._wait_traffic_pass(page,'shopee.com.br',secs=25)
        except Exception: pass
      page.wait_for_timeout(400)
    ck={c['name']:c['value'] for c in ctx.cookies()}
    row={'acc':acc,'err':holder.get('err'),'keys':holder.get('keys'),'signed':holder.get('signed'),'cert':bool(ck.get('AC_CERT_D')),'url':page.url}
    print(row, flush=True)
    results.append(row)
    if holder.get('keys',0)>40 and holder.get('err') in (0,None):
      j=holder['body']
      item=j['data']['item']
      shop=str(item.get('shop_id') or item.get('shopid'))
      it=str(item.get('item_id') or item.get('itemid'))
      out=DELIV/f'output_{shop}_{it}.json'
      if not out.exists():
        payload={'task_link':page.url,'token':'br_sign_probe','api_key':'br_sign_probe','bff_meta':{'extract_method':'browser_get_pc'},'error':None,'error_msg':None,'data':j['data']}
        out.write_text(json.dumps(payload,ensure_ascii=False),encoding='utf-8')
        print('SAVED', out.name, flush=True)
      # persist jar
      jar.update(ck)
      (SESS/acc).write_text(json.dumps({'cookies':jar,'ts':time.time()},ensure_ascii=False),encoding='utf-8')
    ctx.close()
  except Exception as e:
    print('EXC',e,flush=True); results.append({'acc':acc,'exc':str(e)})
  finally:
    try: p.stop()
    except Exception: pass
  time.sleep(5)
OUT.write_text(json.dumps(results,indent=2),encoding='utf-8')
print('DONE', results, flush=True)
