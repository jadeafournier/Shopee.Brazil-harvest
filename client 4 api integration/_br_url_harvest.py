# -*- coding: utf-8 -*-
"""Harvest Shopee BR product URLs at scale via browser-intercepted listing APIs.

Uses BR TEST.txt cookies + Patchright (same trust path as get_pc, but listing pages
are far lighter than 100k PDP visits).

Output format (one per line):
  https://shopee.com.br/product/{shop_id}/{item_id}

Usage:
  python "_br_url_harvest.py" --target 100000
  python "_br_url_harvest.py" --status
  python "_br_url_harvest.py" --target 500   # smoke test
"""
from __future__ import annotations

import argparse
import json
import msvcrt
import os
import random
import re
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "shopee_getpc_crawler"))
sys.path.insert(0, str(ROOT / "client 4 api integration"))

from _br_probe import BR_TEST, parse_br_test, parse_url_ids  # noqa: E402
from br_cookies import br_context_kwargs, enforce_br_locale, normalize_br_cookies, pw_cookie_list  # noqa: E402
import br_human as human  # noqa: E402
import captcha_mint as mint  # noqa: E402
import proxy_pool  # noqa: E402

DIR = Path(__file__).resolve().parent
OUT_FILE = DIR / "br_active_product_urls_100k.txt"
STATE_FILE = DIR / "br_url_harvest_state.json"
LOCK_FILE = DIR / "br_url_harvest.lock"
SEED_FILE = DIR / "br_active_product_urls_1000.txt"

# Mutated when --worker-id is set (per-worker checkpoint).
_WORKER_ID = 0
_WORKERS = 1


def _state_path() -> Path:
    if _WORKERS > 1:
        return DIR / f"br_url_harvest_state_w{_WORKER_ID}.json"
    return STATE_FILE
DOM = "shopee.com.br"
BASE = f"https://{DOM}"

# Listing intercept patterns (signed by browser SAP stack).
INTERCEPT_RE = re.compile(
    r"/api/v4/(search/search_items|recommend/recommend|shop/rcmd_items|"
    r"shop/get_shop_detail|pages/get_category_tree|search/search_filter)"
)

SEARCH_KEYWORDS = [
    "celular", "smartphone", "iphone", "samsung", "xiaomi", "motorola", "tablet",
    "notebook", "computador", "monitor", "mouse", "teclado", "headset", "fone",
    "tv", "smart tv", "geladeira", "fogao", "microondas", "liquidificador",
    "tenis", "sapato", "sandalia", "chinelo", "bolsa", "mochila", "relogio",
    "vestido", "camisa", "camiseta", "calca", "shorts", "jaqueta", "blusa",
    "perfume", "maquiagem", "skincare", "shampoo", "creme", "hidratante",
    "brinquedo", "lego", "boneca", "carrinho", "jogo", "playstation", "xbox",
    "cadeira", "mesa", "sofa", "cama", "colchao", "travesseiro", "luminaria",
    "panela", "frigideira", "talher", "prato", "copo", "garrafa", "termica",
    "ferramenta", "furadeira", "parafusadeira", "martelo", "organizador",
    "suplemento", "whey", "creatina", "vitamina", "proteina", "academia",
    "bicicleta", "patins", "skate", "camping", "pesca", "churrasqueira",
    "bebe", "fralda", "mamadeira", "carrinho bebe", "pet", "racao", "coleira",
    "automotivo", "pneu", "oleo", "som automotivo", "capa celular", "carregador",
    "cabo usb", "power bank", "smartwatch", "camera", "drone", "impressora",
    "livro", "caderno", "caneta", "papel", "escritorio", "decoração", "tapete",
    "cortina", "toalha", "lençol", "pijama", "meia", "cueca", "sutia",
    "oculos", "bijuteria", "anel", "colar", "pulseira", "barbeador", "secador",
    "aspirador", "ventilador", "ar condicionado", "purificador", "umidificador",
    "cafeteira", "chaleira", "batedeira", "processador", "air fryer", "grill",
    "chocolate", "cafe", "cha", "biscoito", "snack", "arroz", "feijao", "oleo",
    "moda feminina", "moda masculina", "moda infantil", "plus size", "fitness",
    "yoga", "corrida", "futebol", "basquete", "natacao", "ciclismo", "musculacao",
    "informatica", "componentes pc", "ssd", "memoria ram", "placa video", "roteador",
    "eletronicos", "eletrodomesticos", "casa", "cozinha", "banheiro", "jardim",
    "beleza", "saude", "farmacia", "higiene", "limpeza", "papel higienico",
    "promocao", "oferta", "desconto", "novidade", "best seller", "mais vendido",
    "importado", "original", "premium", "economico", "atacado", "kit", "combo",
    "iphone 15", "iphone 14", "galaxy s24", "redmi", "poco", "realme", "tcl",
    "philco", "multilaser", "positivo", "dell", "hp", "lenovo", "asus", "acer",
    "nike", "adidas", "puma", "mizuno", "havaianas", "melissa", "arezzo",
    "natura", "boticario", "eudora", "quem disse berenice", "o boticario",
    "lego star wars", "barbie", "hot wheels", "nerf", "pelucia", "quebra cabeca",
    "panela pressao", "jogo de panelas", "fritadeira", "sanduicheira", "torradeira",
    "organizador gaveta", "caixa organizadora", "cabide", "varal", "pregador",
    "luminaria led", "fita led", "lampada", "abajur", "lustre", "spot",
    "capa notebook", "suporte celular", "tripé", "ring light", "webcam",
    "maleta", "estojo", "lapis", "marca texto", "régua", "tesoura", "cola",
    "tinta", "pincel", "tela pintura", "artesanato", "costura", "linha",
    "plantas", "vaso", "semente", "adubo", "mangueira", "regador", "jardineira",
    "churrasco", "espeto", "facas", "tabua", "cooler", "isopor", "carvao",
    "mochila escolar", "lancheira", "garrafa termica", "squeeze", "termo",
    "bebe conforto", "cadeirinha carro", "berco", "andador", "chupeta",
    "gato", "cachorro", "passaro", "aquario", "racao premium", "brinquedo pet",
    "pneu aro", "limpador", "palheta", "macaco", "compressor", "gps",
    "pelicula", "película vidro", "capinha", "pop socket", "suporte magnetico",
    "smart band", "pulseira smart", "fone bluetooth", "fone gamer", "microfone",
    "switch", "controle", "jogo ps5", "jogo ps4", "jogo xbox", "nintendo",
    "camera seguranca", "sensor", "campainha", "fechadura digital", "alarme",
    "liquidificador portatil", "mini ventilador", "umidificador ultrassonico",
    "massageador", "escova rotativa", "prancha cabelo", "modelador cachos",
    "aparelho barbear", "gilette", "creme barbear", "desodorante", "sabonete",
    "escova dente", "pasta dente", "fio dental", "enxaguante", "algodao",
    "protetor solar", "pos sol", "serum", "tonico facial", "mascara facial",
    "batom", "base", "corretivo", "rimel", "sombra", "blush", "delineador",
    "perfume feminino", "perfume masculino", "body splash", "desodorante colonia",
    "whey protein", "bcaa", "pre treino", "hipercalorico", "termogenico",
    "halter", "anilha", "barra", "cinta", "luva academia", "cinto",
    "bota", "coturno", "bota feminina", "salto", "scarpin", "rasteirinha",
    "bermuda", "legging", "moletom", "casaco", "cardigan", "sueter",
    "cueca box", "calcinha", "pijama casal", "robe", "quimono",
    "toalha banho", "toalha rosto", "tapete banheiro", "tapete cozinha",
    "travesseiro cervical", "protetor colchao", "edredom", "cobertor", "manta",
    "espelho", "porta retrato", "quadro decorativo", "adesivo parede", "papel parede",
    "cadeira escritorio", "cadeira gamer", "mesa gamer", "suporte monitor",
    "estante", "rack", "aparador", "buffet", "cristaleira", "armario",
    "penteadeira", "guarda roupa", "comoda", "criado mudo", "beliche",
    "lixeira", "cesto roupa", "cabideiro", "sapateira", "organizador sapato",
    "freezer", "adega", "purificador agua", "filtro agua", "garrafa filtro",
    "batedeira planetaria", "espremedor", "moedor cafe", "balanca cozinha",
    "forma bolo", "forma silicone", "assadeira", "tabua carne", "facas cozinha",
    "jogo copos", "jogo talheres", "sousplat", "guardanapo", "toalha mesa",
    "chaleira eletrica", "cafeteira expresso", "moedor", "prensa francesa",
    "churrasqueira eletrica", "grelha", "fogao camping", "botijao", "regulador",
    "mochila viagem", "mala", "pochete", "necessaire", "porta documento",
    "guarda chuva", "capa chuva", "luvas", "gorro", "cachecol", "meia termica",
    "oculos sol", "oculos grau", "estojo oculos", "limpa lentes",
    "relogio digital", "relogio analogico", "relogio esportivo", "smartwatch feminino",
    "pulseira prata", "brinco", "pingente", "corrente", "alianca", "brinco argola",
    "cinto masculino", "cinto feminino", "carteira", "porta cartao", "porta moeda",
    "chaveiro", "capa documento", "tag mala", "cadeado", "fechadura",
    "pilha", "bateria", "carregador pilha", "extensao", "filtro linha", "tomada",
    "lampada inteligente", "tomada inteligente", "assistente alexa", "google home",
    "notebook gamer", "pc gamer", "gabinete", "fonte", "cooler cpu", "water cooler",
    "pen drive", "hd externo", "ssd nvme", "hub usb", "adaptador hdmi",
    "impressora multifuncional", "toner", "cartucho", "papel a4", "etiqueta",
    "scanner", "calculadora", "plastificadora", "perfurador", "grampeador",
    "cadeira infantil", "mesa infantil", "brinquedo educativo", "livro infantil",
    "fralda descartavel", "lenço umedecido", "pomada assadura", "mamadeira anti colica",
    "moda praia", "biquini", "maio", "sunga", "short praia", "saidas praia",
    "chapeu", "viseira", "protetor ouvido", "colete salva vidas", "boia",
    "patinete", "hoverboard", "longboard", "capacete", "joelheira", "cotoveleira",
    "tenda", "saco dormir", "lanterna", "faca camping", "isqueiro", "fogareiro",
    "varao cortina", "persiana", "blackout", "cortina rolô", "trilho cortina",
    "tapete sala", "tapete quarto", "passadeira", "capacho", "tapete yoga",
    "difusor aromas", "vela aromatica", "incenso", "porta incenso", "sabonete liquido",
    "dispenser", "porta escova", "porta shampoo", "suporte banheiro", "toalheiro",
    "desentupidor", "rodo", "vassoura", "pa lixo", "esfregao", "balde",
    "luva limpeza", "detergente", "desinfetante", "agua sanitaria", "multiuso",
    "amaciante", "sabao po", "sabao liquido", "tira manchas", "limpador vidro",
    "inseticida", "repelente", "ratoeira", "isca formiga", "desumidificador",
    "umidade", "silica gel", "anti mofo", "desodorizador", "aromatizador carro",
    "shopee brasil", "frete gratis", "envio rapido", "loja oficial",
    # Long-tail / regional / seasonal — keep unique yield after popular terms saturate.
    "capinha iphone 13", "capinha iphone 12", "capinha samsung a54", "pelicula iphone",
    "carregador turbo", "carregador tipo c", "cabo tipo c", "fone jbl", "caixa de som",
    "air fryer philco", "air fryer mondial", "ventilador de teto", "ventilador de mesa",
    "tapete peludo", "jogo de cama casal", "jogo de cama solteiro", "cortina blackout",
    "tenis masculino", "tenis feminino", "tenis infantil", "sandalia feminina",
    "vestido longo", "vestido curto", "conjunto feminino", "kit maquiagem",
    "base liquida", "protetor solar facial", "creme hidratante facial",
    "racao golden", "racao premier", "areia sanitaria", "brinquedo cachorro",
    "fralda pampers", "fralda huggies", "lenço umedecido bebe",
    "whey growth", "creatina monohidratada", "pre treino darkness",
    "ssd 1tb", "memoria ram ddr4", "placa mae", "processador ryzen",
    "mouse gamer rgb", "teclado mecanico", "headset gamer", "webcam full hd",
    "panela antiaderente", "jogo de facas", "escorredor louca", "organizador geladeira",
    "luminaria de mesa", "abajur quarto", "fita led 5m", "tomada usb",
    "capa de chuva", "guarda chuva automatico", "necessaire viagem", "organizador mala",
    "relógio feminino", "relogio masculino pulseira", "pulseira masculina",
    "brinco argola dourado", "colar feminino", "anel regulavel",
    "cadeira dobravel", "mesa dobravel", "estante aço", "organizador closet",
    "tapete yoga antiderrapante", "bola pilates", "elastico fitness", "corda pular",
    "isqueiro zippo", "kit churrasco completo", "espeto inox", "tabua de carne",
    "papel higienico folha dupla", "sabonete liquido refil", "detergente ype",
    "arroz tipo 1", "feijao carioca", "cafe torrado", "achocolatado",
    "kit escola", "mochila infantil", "estojo escolar", "caderno universitario",
    "tinta acrilica", "pincel kit", "massa de modelar", "quebra cabeca 1000",
    "drone com camera", "action cam", "tripé celular", "ring light 10",
    "fechadura eletrica", "camera wifi", "sensor de presenca", "lampada smart",
    "oleo motor 5w30", "palheta limpador", "capa banco carro", "organizador porta malas",
    "bermuda jeans", "calca jeans feminina", "camisa social", "blusa cropped",
    "saia midi", "macacao feminino", "legging academia", "top academia",
    "chinelo slide", "bota masculina", "sapatilha feminina", "oxford masculino",
    "perfume 100ml", "kit presente", "caixa presente", "sacola presente",
    "promocao relampago", "oferta do dia", "mais vendidos", "lancamento 2024",
    "importado china", "pronta entrega", "frete gratis sp", "frete gratis rj",
]

PAGE_WAIT_S = 12.0
GAP_S = (2.5, 5.5)
# Cookie-safe pacing (single long-lived browser). Multi-browser same cookie burns sessions.
FAST_GAP_S = (0.7, 1.6)
HOT_GAP_S = (0.35, 0.9)          # after SPA search bundle is warm
MAX_PAGES_PER_KEYWORD = 80
MAX_SHOP_PAGES = 120
# Cycle sorts to pull more unique items from the same keyword (far fewer page loads).
SORT_MODES = ("relevancy", "sales", "ctime", "price")
# Never block stylesheet/script — SPA needs them to fire search_items.
BLOCK_RESOURCE_TYPES = {"image", "media", "font"}
# Cold: search federation bundles can take 20–40s. Hot: usually <3s.
API_WAIT_S = 40.0
FAST_API_WAIT_S = 32.0
HOT_API_WAIT_S = 6.0
# Soft rest — mimic human session length; prevents traffic_error better than raw speed.
HOME_EVERY_PAGES = 35
SOFT_REST_EVERY_OK = 400          # brief pause after this many new URLs in one browser
SOFT_REST_S = (45.0, 90.0)


def _log(msg: str) -> None:
    print(msg, flush=True)


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical(shop_id: int, item_id: int) -> str:
    return f"{BASE}/product/{shop_id}/{item_id}"


def _sync_proxies() -> None:
    _, proxies, _ = parse_br_test(BR_TEST)
    px = ROOT / "proxy(BR shopee).txt"
    if proxies and (_WORKER_ID == 0 or not px.is_file() or px.stat().st_size < 20):
        tmp = px.with_suffix(".tmp")
        tmp.write_text("\n".join(proxies) + "\n", encoding="utf-8")
        tmp.replace(px)
    proxy_pool.set_region("BR")
    proxy_pool._POOL = []
    proxy_pool.load()


def _count_output() -> int:
    if not OUT_FILE.is_file():
        return 0
    return sum(1 for ln in OUT_FILE.read_text(encoding="utf-8").splitlines() if ln.strip())


def _load_seen() -> set[tuple[int, int]]:
    seen: set[tuple[int, int]] = set()
    for path in (OUT_FILE, SEED_FILE, DIR / "br_active_product_urls.txt"):
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                seen.add(parse_url_ids(line))
            except ValueError:
                pass
    return seen


def _load_state() -> dict[str, Any]:
    path = _state_path()
    if path.is_file():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {
        "updated_at": None,
        "total_written": 0,
        "keyword_idx": 0,
        "keyword_page": 0,
        "sort_idx": 0,
        "shop_idx": 0,
        "shop_offset": 0,
        "phase": "search",
        "stopped_reason": None,
        "worker_id": _WORKER_ID,
    }


def _save_state(state: dict[str, Any]) -> None:
    state["updated_at"] = _iso()
    state["worker_id"] = _WORKER_ID
    _state_path().write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _with_file_lock(timeout_s: float = 30.0):
    """Windows exclusive lock around append/dedupe of the shared URL file."""
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    fh = open(LOCK_FILE, "a+b")
    deadline = time.time() + timeout_s
    while True:
        try:
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            return fh
        except OSError:
            if time.time() >= deadline:
                fh.close()
                raise TimeoutError("url harvest file lock timeout")
            time.sleep(0.05)


def _unlock(fh) -> None:
    try:
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
    except Exception:
        pass
    try:
        fh.close()
    except Exception:
        pass


def _append_urls(pairs: list[tuple[int, int]], seen: set[tuple[int, int]]) -> int:
    if not pairs:
        return 0
    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    fh = _with_file_lock()
    try:
        # Re-check against live file under lock (other workers may have written).
        live: set[tuple[int, int]] = set()
        if OUT_FILE.is_file():
            for line in OUT_FILE.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    live.add(parse_url_ids(line))
                except ValueError:
                    pass
        new_pairs = []
        for p in pairs:
            if p in seen or p in live:
                continue
            seen.add(p)
            live.add(p)
            new_pairs.append(p)
        if not new_pairs:
            return 0
        with OUT_FILE.open("a", encoding="utf-8") as f:
            for sid, iid in new_pairs:
                f.write(_canonical(sid, iid) + "\n")
        return len(new_pairs)
    finally:
        _unlock(fh)


def _extract_pairs(obj: Any, out: set[tuple[int, int]]) -> None:
    if isinstance(obj, dict):
        sid = obj.get("shopid") if obj.get("shopid") is not None else obj.get("shop_id")
        iid = obj.get("itemid") if obj.get("itemid") is not None else obj.get("item_id")
        if sid is not None and iid is not None:
            try:
                out.add((int(sid), int(iid)))
            except (TypeError, ValueError):
                pass
        for v in obj.values():
            _extract_pairs(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _extract_pairs(v, out)


class HarvestSession:
    """Long-lived headed Chrome session for BR listing capture.

    Design for cookie survival (vs prior burn pattern):
      * ONE browser / ONE sticky proxy for the whole shift
      * Warm home + prime search SPA once (cold 30s), then hot-path waits (~6s)
      * Soft traffic challenge; hard-stop only on captcha / repeated traffic/error
      * Periodic homepage breaks + soft rest — not 3× parallel browsers
    """

    def __init__(self, cookies: dict, *, fast: bool = False):
        self.fast = fast
        self.work = enforce_br_locale(normalize_br_cookies(cookies, fresh_cert=False))
        self.proxy, _, self.sub_key = mint._proxy_dict("BR")
        if not self.proxy:
            raise RuntimeError("no BR proxy available")
        self._p = None
        self._ctx = None
        self.page = None
        self.risk = False
        self.risk_reason = ""
        self._batch: set[tuple[int, int]] = set()
        self._listing_hits = 0
        self._last_listing_at = 0.0
        self._soft_traffic = 0
        self.spa_hot = False
        self.pages_done = 0
        self.ok_urls = 0

    def __enter__(self):
        from patchright.sync_api import sync_playwright

        self._p = sync_playwright().start()
        udd = tempfile.mkdtemp(prefix="brharv_")
        ctx_kw = br_context_kwargs()
        self._ctx = self._p.chromium.launch_persistent_context(
            udd, channel="chrome", headless=False, proxy=self.proxy,
            viewport={"width": mint.VIEW_W, "height": mint.VIEW_H},
            locale=ctx_kw["locale"],
            timezone_id=ctx_kw["timezone_id"],
            extra_http_headers=ctx_kw["extra_http_headers"],
            args=["--no-sandbox", "--disable-dev-shm-usage",
                  "--disable-blink-features=AutomationControlled"],
        )
        self._ctx.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
        )
        # Block heavy assets — listing JSON still arrives; pages load much faster.
        self._ctx.route("**/*", self._route_filter)
        self._ctx.add_cookies(pw_cookie_list(self.work, fresh_cert=False))
        self.page = self._ctx.new_page()
        self.page.on("response", self._on_response)
        return self

    def __exit__(self, *_):
        self._sync_cookies()
        for closer in (self._ctx, self._p):
            try:
                if closer:
                    closer.close() if hasattr(closer, "close") else closer.stop()
            except Exception:
                pass

    def _sync_cookies(self) -> None:
        if not self._ctx:
            return
        try:
            ck = {c["name"]: c["value"] for c in self._ctx.cookies()}
            for k, v in ck.items():
                if v and v not in ("-", "deleted"):
                    self.work[k] = v
            self.work = enforce_br_locale(self.work)
        except Exception:
            pass

    def _api_timeout_s(self) -> float:
        if self.spa_hot:
            return HOT_API_WAIT_S
        return FAST_API_WAIT_S if self.fast else API_WAIT_S

    def _route_filter(self, route) -> None:
        try:
            req = route.request
            rtype = req.resource_type
            if rtype in BLOCK_RESOURCE_TYPES:
                route.abort()
                return
            # Abort trackers only (keep XHR/fetch/document/script intact).
            if rtype in ("ping", "beacon"):
                route.abort()
                return
            url = req.url
            if any(x in url for x in (
                "google-analytics.com", "googletagmanager.com", "doubleclick.net",
                "facebook.com/tr", "hotjar.com", "clarity.ms",
            )):
                route.abort()
                return
            route.continue_()
        except Exception:
            try:
                route.continue_()
            except Exception:
                pass

    def _ingest_listing_payload(self, data: Any) -> int:
        if not isinstance(data, dict):
            return 0
        if data.get("error") == 90309999:
            return 0
        before = len(self._batch)
        _extract_pairs(data, self._batch)
        gained = len(self._batch) - before
        if gained > 0:
            self._listing_hits += 1
            self._last_listing_at = time.time()
            self.spa_hot = True
            self._soft_traffic = max(0, self._soft_traffic - 1)
        return gained

    def _on_response(self, resp) -> None:
        url = resp.url
        if self.risk:
            return
        # Captcha document is a hard stop. traffic/error is tracked softly.
        if "/verify/captcha" in url and "/api/" not in url and "traffic" not in url:
            self.risk = True
            self.risk_reason = "captcha"
            return
        if "/verify/traffic/error" in url:
            self._soft_traffic += 1
            return
        is_listing = bool(INTERCEPT_RE.search(url)) or any(
            x in url for x in ("search_items", "rcmd_items", "recommend/recommend")
        )
        if not is_listing:
            return
        try:
            if resp.status != 200:
                return
            data = resp.json()
        except Exception:
            return
        self._ingest_listing_payload(data)

    def warmup(self) -> None:
        """Human home + prime search SPA once (expensive cold load, then hot path)."""
        human.warm_home(self.page, DOM)
        self._handle_traffic_page(hard_fail=False)
        try:
            self.page.wait_for_timeout(2500 if self.fast else 4000)
        except Exception:
            time.sleep(3)
        _log("  priming search SPA (one-time cold load)…")
        self.search_page("camiseta", 0, sort_by="sales")
        # Keep _batch for harvest() collect_from_page — do not drain here.
        if self._batch:
            self.spa_hot = True
            _log(f"  SPA hot — primed {len(self._batch)} items; subsequent pages use short waits")
        else:
            _log("  SPA still cold — first keywords will use long waits")
        self._sync_cookies()
        human.mouse_move(self.page)
        try:
            self.page.wait_for_timeout(800 if self.fast else 1500)
        except Exception:
            pass

    def maybe_session_break(self) -> None:
        """Homepage break / soft rest to keep cookies cool."""
        self.pages_done += 1
        if HOME_EVERY_PAGES > 0 and self.pages_done % HOME_EVERY_PAGES == 0:
            _log(f"  session break: homepage (every {HOME_EVERY_PAGES} pages)")
            human.maybe_visit_home(self.page, DOM, HOME_EVERY_PAGES, HOME_EVERY_PAGES)
            self._handle_traffic_page(hard_fail=False)
            self._sync_cookies()
        if self.ok_urls > 0 and self.ok_urls % SOFT_REST_EVERY_OK == 0:
            rest = random.uniform(*SOFT_REST_S)
            _log(f"  soft rest {rest:.0f}s after {self.ok_urls} new URLs (cookie protect)")
            time.sleep(rest)
            human.warm_home(self.page, DOM)
            self._handle_traffic_page(hard_fail=False)

    def _handle_traffic_page(self, *, hard_fail: bool = True) -> bool:
        """Pass soft traffic challenge; hard-fail only after repeated heat."""
        try:
            u = self.page.url or ""
        except Exception:
            return True
        if "/verify/captcha" in u:
            self.risk = True
            self.risk_reason = "captcha"
            return False
        if "/verify/traffic/error" in u:
            self._soft_traffic += 1
            if hard_fail and self._soft_traffic >= 4:
                self.risk = True
                self.risk_reason = "traffic_error"
                return False
            _log(f"  traffic/error (soft={self._soft_traffic}) — cool navigation")
            try:
                human.pause(self.page, 2000, 4000)
            except Exception:
                time.sleep(2.5)
            return False
        if "/verify/traffic" in u:
            self._soft_traffic += 1
            _log(f"  soft traffic challenge (n={self._soft_traffic}) — waiting…")
            ok = human.traffic_challenge(self.page, 20.0 if self.fast else 30.0)
            if "/verify/captcha" in (self.page.url or ""):
                self.risk = True
                self.risk_reason = "captcha"
                return False
            if not ok or "/verify/traffic/error" in (self.page.url or ""):
                if hard_fail and self._soft_traffic >= 4:
                    self.risk = True
                    self.risk_reason = "traffic_error"
                    return False
                return False
        return True

    def drain_batch(self) -> list[tuple[int, int]]:
        out = list(self._batch)
        self._batch.clear()
        return out

    def scrape_page_links(self) -> list[tuple[int, int]]:
        """Fallback HTML scrape — only used when API batch is empty."""
        pairs: set[tuple[int, int]] = set()
        try:
            hrefs = self.page.eval_on_selector_all(
                "a[href*='/product/'], a[href*='-i.']",
                "els => els.map(e => e.getAttribute('href') || e.href)",
            )
            for href in hrefs or []:
                for m in re.finditer(r"/product/(\d+)/(\d+)", href or ""):
                    pairs.add((int(m.group(1)), int(m.group(2))))
                for m in re.finditer(r"-i\.(\d+)\.(\d+)", href or ""):
                    pairs.add((int(m.group(1)), int(m.group(2))))
        except Exception:
            pass
        if pairs:
            return list(pairs)
        try:
            html = self.page.content()
        except Exception:
            return []
        for m in re.finditer(r"/product/(\d+)/(\d+)", html):
            pairs.add((int(m.group(1)), int(m.group(2))))
        for m in re.finditer(r"-i\.(\d+)\.(\d+)", html):
            pairs.add((int(m.group(1)), int(m.group(2))))
        return list(pairs)

    def collect_from_page(self, *, allow_html: bool = True) -> list[tuple[int, int]]:
        merged = set(self.drain_batch())
        # Prefer API intercept — skip expensive HTML parse when we already have items.
        if allow_html and len(merged) < 8:
            merged.update(self.scrape_page_links())
        return list(merged)

    def _wait_listing(self, min_pairs: int = 10, timeout_s: float | None = None) -> bool:
        """Wait until search/shop listing API filled the batch (event-driven)."""
        wait = timeout_s if timeout_s is not None else self._api_timeout_s()
        deadline = time.time() + wait
        start_hits = self._listing_hits
        while time.time() < deadline:
            if self.risk:
                return False
            if len(self._batch) >= min_pairs and self._listing_hits > start_hits:
                return True
            if self._listing_hits > start_hits and (time.time() - self._last_listing_at) > 0.35:
                return True
            try:
                self.page.wait_for_timeout(100)
            except Exception:
                time.sleep(0.1)
        return len(self._batch) > 0

    def _quick_nudge(self) -> None:
        """One short scroll to trigger lazy listing if SPA needs it."""
        try:
            human.scroll(self.page, random.randint(500, 900))
        except Exception:
            pass
        try:
            self.page.wait_for_timeout(200 if self.spa_hot else 400)
        except Exception:
            time.sleep(0.25)

    def search_page(self, keyword: str, page_no: int, *, sort_by: str = "relevancy") -> None:
        """Navigate search; cold wait once, then hot-path short waits."""
        self._batch.clear()
        before_hits = self._listing_hits
        kw = quote(keyword)
        url = f"{BASE}/search?keyword={kw}&page={page_no}&sortBy={sort_by}"
        timeout_ms = int(self._api_timeout_s() * 1000)
        # expect_response reads body before traffic redirects can discard it.
        try:
            with self.page.expect_response(
                lambda r: "search_items" in (r.url or "") and r.status == 200,
                timeout=timeout_ms,
            ) as ri:
                try:
                    self.page.goto(url, wait_until="commit", timeout=45000)
                except Exception:
                    pass
            resp = ri.value
            try:
                self._ingest_listing_payload(resp.json())
            except Exception:
                pass
        except Exception:
            try:
                self.page.goto(url, wait_until="commit", timeout=45000)
            except Exception:
                pass
        try:
            self.page.wait_for_timeout(250 if self.spa_hot else 600)
        except Exception:
            time.sleep(0.3)
        if "/verify/captcha" in (self.page.url or ""):
            self.risk = True
            self.risk_reason = "captcha"
            return
        self._handle_traffic_page(hard_fail=False)
        if len(self._batch) < 5:
            extra = 8.0 if self.spa_hot else 14.0
            if not self._wait_listing(min_pairs=8, timeout_s=extra):
                for _ in range(2):
                    self._quick_nudge()
                    if self._wait_listing(min_pairs=5, timeout_s=5.0 if self.spa_hot else 8.0):
                        break
        if self._listing_hits == before_hits or len(self._batch) < 5:
            try:
                self.page.evaluate("window.scrollTo(0, Math.min(2200, document.body.scrollHeight))")
            except Exception:
                self._quick_nudge()
            self._wait_listing(min_pairs=5, timeout_s=6.0 if self.spa_hot else 10.0)
        if "/verify/captcha" in (self.page.url or ""):
            self.risk = True
            self.risk_reason = "captcha"
        human.mouse_move(self.page)

    def shop_page(self, shop_id: int, offset: int = 0) -> None:
        self._batch.clear()
        url = f"{BASE}/shop/{shop_id}#product_list"
        try:
            self.page.goto(url, wait_until="commit", timeout=45000)
        except Exception:
            pass
        try:
            self.page.wait_for_timeout(350 if self.spa_hot else 700)
        except Exception:
            time.sleep(0.5)
        self._handle_traffic_page(hard_fail=False)
        if not self._wait_listing(min_pairs=10, timeout_s=self._api_timeout_s()):
            self._quick_nudge()
            if offset > 0:
                try:
                    self.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                except Exception:
                    human.scroll(self.page, 1400)
                self._wait_listing(min_pairs=8, timeout_s=5.0 if self.spa_hot else 8.0)
        self._handle_traffic_page(hard_fail=False)


def _seed_shops(seen: set[tuple[int, int]]) -> list[int]:
    shops: list[int] = []
    known: set[int] = set()
    for path in (SEED_FILE, OUT_FILE):
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                sid, _ = parse_url_ids(line)
            except ValueError:
                continue
            if sid not in known:
                known.add(sid)
                shops.append(sid)
    for sid, _ in seen:
        if sid not in known:
            known.add(sid)
            shops.append(sid)
    return shops


def cmd_status() -> int:
    seen = _load_seen()
    state = _load_state()
    out_lines = _count_output()
    _log("BR URL harvest status")
    _log(f"  output file: {OUT_FILE.name}")
    _log(f"  urls in output: {out_lines:,}")
    _log(f"  dedupe pool (seeds+output): {len(seen):,}")
    _log(f"  phase: {state.get('phase')} | keyword_idx={state.get('keyword_idx')} page={state.get('keyword_page')}")
    _log(f"  shop_idx={state.get('shop_idx')} offset={state.get('shop_offset')}")
    if state.get("stopped_reason"):
        _log(f"  last stop: {state.get('stopped_reason')}")
    return 0


def harvest(
    target: int,
    *,
    resume: bool = True,
    fast: bool = False,
    worker_id: int = 0,
    workers: int = 1,
    cookies_override: dict | None = None,
    account_id: str | None = None,
    shift_pages: int = 0,
    soft_rotate_at: int = 2,
) -> int:
    """Harvest URLs.

    Return codes:
      0 target reached / exhausted
      1 error
      2 hard risk (captcha / traffic_error) — cool account
      3 shift complete (page budget or soft-traffic) — rotate account, no penalty
    """
    global _WORKER_ID, _WORKERS
    _WORKER_ID = max(0, int(worker_id))
    _WORKERS = max(1, int(workers))

    if cookies_override is not None:
        cookies = dict(cookies_override)
    else:
        cookies, _, _ = parse_br_test(BR_TEST)
    # Login-less mode: search/get_pc only need the device cert (AC_CERT_D), not the
    # login account (SPC_U). Stripping SPC_U avoids the account-linked captcha that
    # burns cookies — verified: login-less search yields items with 0 traffic/captcha.
    if not cookies.get("SPC_U") and not cookies.get("AC_CERT_D"):
        _log("no SPC_U / AC_CERT_D cookie")
        return 1
    login_less = not cookies.get("SPC_U")

    # Register fresh BR TEST cookies as primary only when not using an explicit account.
    if cookies_override is None:
        try:
            from br_account_pool import AccountPool
            pool = AccountPool()
            aid = pool.register_br_test()
            if aid:
                _log(f"  registered primary account: {aid}")
        except Exception as e:
            _log(f"  account pool register skipped: {e}")

    _sync_proxies()
    seen = _load_seen()
    state = _load_state() if resume else {
        "total_written": 0, "keyword_idx": 0, "keyword_page": 0, "sort_idx": 0,
        "shop_idx": 0, "shop_offset": 0, "phase": "search", "stopped_reason": None,
    }
    state.setdefault("sort_idx", 0)
    start_out = _count_output()
    state["stopped_reason"] = None
    empty_break = 1 if fast else 2
    gap = FAST_GAP_S if fast else GAP_S
    if start_out >= target:
        _log(f"Already have {start_out:,} URLs in output (target {target:,}).")
        return 0

    all_shops = _seed_shops(seen)
    all_keywords = SEARCH_KEYWORDS
    # Shard keywords/shops across workers for parallel browsers.
    # WARNING: >1 worker with the SAME cookie burns traffic_error — prefer workers=1.
    if _WORKERS > 1:
        keywords = [kw for i, kw in enumerate(all_keywords) if i % _WORKERS == _WORKER_ID]
        shops = [s for i, s in enumerate(all_shops) if i % _WORKERS == _WORKER_ID]
    else:
        keywords = all_keywords
        shops = all_shops
    _log("=" * 70)
    _log("BR URL HARVEST (cookie-safe browser capture)")
    _log(f"  cookies SPC_U={cookies.get('SPC_U')} AC_CERT={'yes' if cookies.get('AC_CERT_D') else 'no'}")
    if account_id:
        _log(f"  account={account_id} shift_pages={shift_pages or '∞'} soft_rotate_at={soft_rotate_at}")
    _log(f"  output_count={start_out:,} target={target:,} seed_shops={len(shops):,}")
    _log(f"  worker={_WORKER_ID + 1}/{_WORKERS} keywords={len(keywords)}")
    _log(f"  output={OUT_FILE}")
    _log(f"  mode={'SAFE-FAST' if fast else 'normal'} | 1 long-lived browser, sticky proxy, SPA prime")
    if _WORKERS > 1:
        _log("  WARNING: multiple workers share one cookie — high traffic_error risk")
    _log("=" * 70)

    # Proactive rotation flag — set when shift budget or soft-traffic threshold hit.
    rotate = {"now": False, "reason": None}

    def _should_rotate(sess) -> bool:
        if shift_pages > 0 and sess.pages_done >= shift_pages:
            rotate["now"] = True
            rotate["reason"] = "shift_budget"
            return True
        if soft_rotate_at > 0 and sess._soft_traffic >= soft_rotate_at:
            rotate["now"] = True
            rotate["reason"] = "soft_traffic_preempt"
            return True
        return False

    with HarvestSession(cookies, fast=fast) as sess:
        sess.warmup()
        batch = sess.collect_from_page()
        if batch:
            n = _append_urls(batch, seen)
            if n:
                _log(f"  warmup +{n} (output {_count_output():,})")

        phase = state.get("phase") or "search"
        out_n = _count_output

        # --- search phase ---
        # Outer: keyword. Inner: sort mode × page. Sort cycling multiplies unique yield
        # per keyword with fewer wasted duplicate relevancy pages.
        if phase == "search":
            ki = int(state.get("keyword_idx") or 0)
            pg = int(state.get("keyword_page") or 0)
            sort_idx = int(state.get("sort_idx") or 0)
            zero_new_keywords = int(state.get("zero_new_keywords") or 0)
            max_pages = 25 if fast else MAX_PAGES_PER_KEYWORD
            while ki < len(keywords) and out_n() < target:
                if sess.risk or _should_rotate(sess):
                    break
                kw = keywords[ki]
                keyword_new = 0
                while sort_idx < len(SORT_MODES) and out_n() < target:
                    if sess.risk or _should_rotate(sess):
                        break
                    sort_by = SORT_MODES[sort_idx]
                    dup_pages = 0
                    empty_pages = 0
                    while pg < max_pages and out_n() < target:
                        if sess.risk or _should_rotate(sess):
                            break
                        _log(
                            f"search [{ki+1}/{len(keywords)}] sort={sort_by} "
                            f"page={pg} kw={kw!r} output={out_n():,}"
                        )
                        sess.search_page(kw, pg, sort_by=sort_by)
                        if sess.risk:
                            break
                        batch = sess.collect_from_page(allow_html=True)
                        n = _append_urls(batch, seen)
                        keyword_new += n
                        if n:
                            _log(f"  +{n} new (output {out_n():,})")
                            dup_pages = 0
                            empty_pages = 0
                            sess._soft_traffic = 0
                            sess.ok_urls += n
                        elif batch:
                            dup_pages += 1
                            if dup_pages >= 2:
                                _log(f"  duplicates under sort={sort_by} — next sort/keyword")
                                break
                        else:
                            empty_pages += 1
                            # Abort run only after many consecutive empty pages + traffic heat.
                            if empty_pages > empty_break:
                                if sess._soft_traffic >= 5:
                                    sess.risk = True
                                    sess.risk_reason = "traffic_error"
                                    break
                                break
                        state.update({
                            "keyword_idx": ki, "keyword_page": pg + 1,
                            "sort_idx": sort_idx, "phase": "search",
                            "zero_new_keywords": zero_new_keywords,
                            "total_written": out_n() - start_out,
                        })
                        _save_state(state)
                        pg += 1
                        sess.maybe_session_break()
                        gap = HOT_GAP_S if (fast and sess.spa_hot) else (FAST_GAP_S if fast else GAP_S)
                        time.sleep(random.uniform(*gap))
                    pg = 0
                    sort_idx += 1
                    state["sort_idx"] = sort_idx
                    state["keyword_page"] = 0
                    _save_state(state)
                sort_idx = 0
                pg = 0
                if keyword_new == 0:
                    zero_new_keywords += 1
                    # Do NOT abandon the whole keyword list early — popular early
                    # keywords saturate first; later long-tail still yields uniques.
                    if zero_new_keywords >= 40 and ki + 1 < len(keywords):
                        _log(
                            f"Search soft-saturated ({zero_new_keywords} zero-yield keywords) "
                            f"at {out_n():,} — will finish remaining then shop"
                        )
                else:
                    zero_new_keywords = 0
                ki += 1
                state["keyword_idx"] = ki
                state["keyword_page"] = 0
                state["sort_idx"] = 0
                state["zero_new_keywords"] = zero_new_keywords
                _save_state(state)
            # Shop only after ALL sharded keywords are done (or risk).
            if out_n() < target and not sess.risk and ki >= len(keywords):
                state["phase"] = "shop"
                state["shop_idx"] = int(state.get("shop_idx") or 0)
                state["shop_offset"] = 0
                state["zero_new_keywords"] = 0
                _save_state(state)
                phase = "shop"
                _log(
                    f"Search phase exhausted at {out_n():,} "
                    f"— switching to shop expansion ({len(shops):,} shops)"
                )

        # --- shop expansion phase ---
        if phase == "shop" and out_n() < target and not sess.risk:
            si = int(state.get("shop_idx") or 0)
            off = int(state.get("shop_offset") or 0)
            dup_offsets = int(state.get("shop_dup_offsets") or 0)
            zero_shops = int(state.get("zero_shops") or 0)
            while si < len(shops) and out_n() < target:
                if sess.risk or _should_rotate(sess):
                    break
                shop_id = shops[si]
                _log(f"shop [{si+1}/{len(shops)}] id={shop_id} offset={off} output={out_n():,}")
                sess.shop_page(shop_id, off)
                batch = sess.collect_from_page()
                n = _append_urls(batch, seen)
                if n:
                    _log(f"  +{n} new (output {out_n():,})")
                    dup_offsets = 0
                    zero_shops = 0
                else:
                    dup_offsets += 1
                off += 30
                # Empty first page OR 2 dup pages → next shop (seed shops are mostly known).
                if not batch or dup_offsets >= 2 or off >= MAX_SHOP_PAGES * 30:
                    if not n:
                        zero_shops += 1
                    if dup_offsets >= 2 and batch:
                        _log(f"  shop {shop_id} duplicate-only — next shop")
                    si += 1
                    off = 0
                    dup_offsets = 0
                    # Skip ahead when a long streak of shops yields nothing.
                    if zero_shops >= 15:
                        jump = min(80, len(shops) - si - 1)
                        if jump > 0:
                            _log(f"  {zero_shops} dry shops — jump +{jump}")
                            si += jump
                        zero_shops = 0
                state.update({
                    "shop_idx": si, "shop_offset": off, "phase": "shop",
                    "shop_dup_offsets": dup_offsets, "zero_shops": zero_shops,
                    "total_written": out_n() - start_out,
                })
                _save_state(state)
                time.sleep(random.uniform(*gap))

        if sess.risk:
            state["stopped_reason"] = sess.risk_reason
            _save_state(state)
            _log(f"STOP: {sess.risk_reason} — saved checkpoint. Resume later with same command.")
            return 2

        if rotate["now"]:
            state["stopped_reason"] = rotate["reason"]
            _save_state(state)
            _log(
                f"SHIFT END: {rotate['reason']} at {_count_output():,} URLs "
                f"(+{_count_output() - start_out} this shift) — rotate account (cookie-safe)."
            )
            return 3

    state["stopped_reason"] = "target_reached" if _count_output() >= target else "exhausted"
    state["total_written"] = _count_output() - start_out
    _save_state(state)
    _log(f"\nDone: {_count_output():,} URLs in output ({_count_output() - start_out:,} new this run)")
    _log(f"Output: {OUT_FILE}")
    return 0 if _count_output() >= target else 1


def main() -> int:
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    ap = argparse.ArgumentParser(description="Harvest Shopee BR product URLs")
    ap.add_argument("--target", type=int, default=100_000)
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--reset", action="store_true", help="reset harvest state (keeps output file)")
    ap.add_argument("--fast", action="store_true", help="max speed (shorter waits, still uses browser)")
    ap.add_argument("--worker-id", type=int, default=0, help="0-based worker index for parallel runs")
    ap.add_argument("--workers", type=int, default=1, help="total parallel workers (keyword sharding)")
    args = ap.parse_args()

    if args.reset:
        for p in DIR.glob("br_url_harvest_state*.json"):
            p.unlink(missing_ok=True)
        _log("Harvest state reset.")
        return 0
    if args.status:
        return cmd_status()
    return harvest(
        args.target,
        fast=args.fast,
        worker_id=args.worker_id,
        workers=args.workers,
    )


if __name__ == "__main__":
    raise SystemExit(main())
