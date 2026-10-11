import time,subprocess,sys
from pathlib import Path
DELIV=Path(r'D:\Great Scraper\Great Scraper\client 4 api integration\br_deliverable_getpc')
PY=r'D:\Great Scraper\Great Scraper\.venv\Scripts\python.exe'
SCRIPT=r'D:\Great Scraper\Great Scraper\client 4 api integration\_br_ultraslow_1k.py'
LOG=Path(r'D:\Great Scraper\Great Scraper\client 4 api integration\br_supervisor.log')
def n(): return len(list(DELIV.glob('output_*.json')))
def log(m):
  line=time.strftime('%H:%M:%S')+' '+m
  print(line,flush=True); LOG.open('a',encoding='utf-8').write(line+'\n')
while n()<1000:
  log(f'supervisor n={n()} spawn ultraslow')
  p=subprocess.Popen([PY,'-u',SCRIPT], cwd=str(Path(SCRIPT).parent))
  while p.poll() is None and n()<1000:
    time.sleep(60)
    if n()%10==0: log(f'n={n()}')
  rc=p.wait()
  log(f'ultraslow exit {rc} n={n()}')
  if n()>=1000: break
  time.sleep(120)
log(f'SUPERVISOR DONE n={n()}')
