import json,sys,time
from pathlib import Path
ROOT=Path(r'D:\Great Scraper\Great Scraper')
sys.path[:0]=[str(ROOT), str(ROOT/'shopee_getpc_crawler'), str(ROOT/'client 4 api integration')]
import captcha_mint as mint
SESS=ROOT/'shopee_getpc_crawler'/'sessions'/'BR'
for name in ['br_1705283243_F3zrbhjv_live.json','br_1707139756_0EAyNQaW_live.json','br_1707408898_LAxNlL5a_live.json','br_1706785017_CoOHjjSQ_live.json']:
  p=SESS/name
  if not p.is_file():
    continue
  doc=json.loads(p.read_text(encoding='utf-8'))
  ck=doc.get('cookies') or {}
  print('=== mint', name, 'SPC_U=', ck.get('SPC_U'), '===', flush=True)
  res=mint.mint('BR', ck, save_shots=True, verbose=True)
  print('mint result ok=', res.get('ok'), 'reason=', res.get('reason'), 'getpc_err=', res.get('getpc_err'), 'cert=', bool((res.get('cookies') or {}).get('AC_CERT_D')), flush=True)
  if res.get('ok'):
    doc['cookies']=res['cookies']
    doc['minted_at']=time.time()
    p.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding='utf-8')
    print('PERSISTED', name, flush=True)
    break
  time.sleep(8)
print('MINT_PHASE_DONE', flush=True)
