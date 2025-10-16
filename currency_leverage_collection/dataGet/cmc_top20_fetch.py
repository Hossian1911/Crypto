from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List

import httpx

BASE_URL = "https://coinmarketcap.com/"
DATA_API = (
    "https://api.coinmarketcap.com/data-api/v3/cryptocurrency/listing"
    "?start=1&limit=20&convert=USD&sortBy=market_cap&sortType=desc&cryptocurrencyType=all&tagType=all"
)
HEADERS = {
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "accept-language": "en-US,en;q=0.9",
    "cache-control": "no-cache",
    "pragma": "no-cache",
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
}

ROOT_DIR = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT_DIR / "data" / "dataGet_api" / "cmc"
OUT_DIR.mkdir(parents=True, exist_ok=True)
OUT_FILE = OUT_DIR / "cmc_top20.json"

NEXT_DATA_RE = re.compile(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.S)
SYMBOL_HTML_RE = re.compile(r'class="[^"\n]*coin-item-symbol[^"\n]*">\s*([A-Z0-9]{2,15})\s*</p>')


def _fetch_home() -> str:
    with httpx.Client(timeout=30.0, headers=HEADERS) as client:
        resp = client.get(BASE_URL)
        resp.raise_for_status()
        return resp.text


def _fetch_data_api() -> dict:
    with httpx.Client(timeout=30.0, headers=HEADERS) as client:
        r = client.get(DATA_API)
        r.raise_for_status()
        return r.json()


def _parse_next_data(html: str) -> Dict[str, Any]:
    m = NEXT_DATA_RE.search(html)
    if not m:
        raise RuntimeError("__NEXT_DATA__ script not found on CoinMarketCap page")
    return json.loads(m.group(1))


def _extract_symbols_from_html(html: str) -> List[str]:
    # Fallback: directly extract symbols from HTML markup
    syms = SYMBOL_HTML_RE.findall(html)
    # Preserve order, deduplicate
    seen = set()
    ordered: List[str] = []
    for s in syms:
        if s not in seen:
            seen.add(s)
            ordered.append(s)
    return ordered


def _fetch_coingecko_top(limit: int = 60) -> List[Dict[str, Any]]:
    url = (
        "https://api.coingecko.com/api/v3/coins/markets"
        f"?vs_currency=usd&order=market_cap_desc&per_page={limit}&page=1&sparkline=false&price_change_percentage=24h"
    )
    with httpx.Client(timeout=30.0, headers=HEADERS) as client:
        r = client.get(url)
        r.raise_for_status()
        data = r.json()
        out: List[Dict[str, Any]] = []
        if isinstance(data, list):
            for it in data:
                if not isinstance(it, dict):
                    continue
                sym = str(it.get("symbol") or "").upper().strip()
                if not sym:
                    continue
                mc = _to_float(it.get("market_cap"))
                out.append({"name": sym, "marketcap": mc})
        return out


def _extract_listing(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    props = data.get("props", {})
    page_props = props.get("pageProps", {})
    for key in ("cryptoCurrencyList", "cryptocurrencyList", "tableData"):
        val = page_props.get(key)
        if isinstance(val, list) and val:
            return val
    data_field = page_props.get("data")
    if isinstance(data_field, dict):
        for key in ("cryptoCurrencyList", "cryptocurrencyList", "tableData"):
            val = data_field.get(key)
            if isinstance(val, list) and val:
                return val
    initial_state = props.get("initialState", {})
    listing = initial_state.get("cryptocurrency", {}).get("listingLatest", {})
    if isinstance(listing, dict):
        items = listing.get("data")
        if isinstance(items, list) and items:
            return items
    raise RuntimeError("Unable to locate cryptocurrency listing data inside __NEXT_DATA__ JSON")


def _to_float(v: Any) -> float | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        s = str(v).strip().replace(",", "")
        if not s:
            return None
        return float(s)
    except Exception:
        return None


def _extract_market_cap(item: Dict[str, Any]) -> float | None:
    quotes = item.get("quotes")
    if isinstance(quotes, list):
        for q in quotes:
            if not isinstance(q, dict):
                continue
            for key in ("marketCap", "market_cap"):
                val = _to_float(q.get(key))
                if val is not None:
                    return val
            usd = q.get("USD") or q.get("usd")
            if isinstance(usd, dict):
                for key in ("marketCap", "market_cap"):
                    val = _to_float(usd.get(key))
                    if val is not None:
                        return val
    quote = item.get("quote")
    if isinstance(quote, dict):
        usd = quote.get("USD") or quote.get("usd")
        if isinstance(usd, dict):
            for key in ("marketCap", "market_cap"):
                val = _to_float(usd.get(key))
                if val is not None:
                    return val
    metrics = item.get("metrics")
    if isinstance(metrics, dict):
        for key in ("marketCap", "market_cap"):
            mc = metrics.get(key)
            if isinstance(mc, dict):
                val = _to_float(mc.get("current")) or _to_float(mc.get("value"))
                if val is not None:
                    return val
            val = _to_float(mc)
            if val is not None:
                return val
    stats = item.get("statistics") or item.get("stats")
    if isinstance(stats, dict):
        for key in ("marketCap", "market_cap"):
            val = _to_float(stats.get(key))
            if val is not None:
                return val
    return None


EXCLUDE = {"USDT", "USDC"}


def fetch_top20() -> List[Dict[str, Any]]:
    # 1) 优先尝试 data-api（稳定 JSON）
    try:
        api_json = _fetch_data_api()
        data = api_json.get("data") or {}
        crypto = data.get("cryptoCurrencyList") or data.get("list") or []
        result: List[Dict[str, Any]] = []
        for it in crypto[:50]:  # 扫描更多，便于过滤后补足20
            if not isinstance(it, dict):
                continue
            symbol = it.get("symbol") or it.get("slug")
            if not symbol:
                continue
            symbol_u = str(symbol).upper().strip()
            if symbol_u in EXCLUDE:
                continue
            # market cap under quotes[0].marketCap or quotes.USD.marketCap
            market_cap = None
            quotes = it.get("quotes")
            if isinstance(quotes, list) and quotes:
                market_cap = _to_float((quotes[0] or {}).get("marketCap"))
                if market_cap is None:
                    usd = (quotes[0] or {}).get("USD") or {}
                    market_cap = _to_float(usd.get("marketCap"))
            if market_cap is None:
                market_cap = _to_float(it.get("marketCap"))
            result.append({"name": symbol_u, "marketcap": market_cap})
            if len(result) >= 20:
                return result
    except Exception:
        pass

    # 2) 回退：解析首页 __NEXT_DATA__
    html = _fetch_home()
    try:
        data = _parse_next_data(html)
        listing = _extract_listing(data)
        result2: List[Dict[str, Any]] = []
        for item in listing[:60]:
            if not isinstance(item, dict):
                continue
            symbol = item.get("symbol") or item.get("slug")
            if not symbol:
                continue
            symbol_u = str(symbol).upper().strip()
            if symbol_u in EXCLUDE:
                continue
            market_cap = _extract_market_cap(item)
            result2.append({"name": symbol_u, "marketcap": market_cap})
            if len(result2) >= 20:
                return result2
        if len(result2) >= 20:
            return result2
    except Exception:
        pass

    # 3) 最终回退：直接从 HTML 扫描 symbol 文本
    symbols = _extract_symbols_from_html(html)
    result3: List[Dict[str, Any]] = []
    for s in symbols[:80]:
        su = s.upper().strip()
        if su in EXCLUDE:
            continue
        result3.append({"name": su, "marketcap": None})
        if len(result3) >= 20:
            return result3
    # 4) 终极兜底：CoinGecko Top 市值
    try:
        cg_items = _fetch_coingecko_top(80)
        result4: List[Dict[str, Any]] = []
        for it in cg_items:
            sym = str(it.get("name")).upper().strip()
            if sym in EXCLUDE:
                continue
            result4.append({"name": sym, "marketcap": it.get("marketcap")})
            if len(result4) >= 20:
                return result4
    except Exception:
        pass
    raise RuntimeError("Unable to obtain 20 symbols after all fallbacks (CMC + HTML + CoinGecko)")


def main() -> Path:
    data = fetch_top20()
    OUT_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return OUT_FILE


if __name__ == "__main__":
    path = main()
    print(f"Saved: {path}")
