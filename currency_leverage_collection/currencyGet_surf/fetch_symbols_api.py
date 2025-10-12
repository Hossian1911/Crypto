import json
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Tuple

import httpx

from config import settings

API_URL = "https://surfv2-api.surf.one/public/pair/profit/stats"


@dataclass
class SurfPair:
    pair: str
    base: str
    quote: str


def _ensure_output_dir() -> None:
    settings.DATA_DIR.mkdir(parents=True, exist_ok=True)


def _save_outputs(pairs: List[SurfPair]) -> Tuple[Path, Path, Path]:
    _ensure_output_dir()

    payload = {
        "source_url": API_URL,
        "collected_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S%z"),
        "pairs": [p.__dict__ for p in pairs],
        "count": len(pairs),
    }
    settings.OUTPUT_JSON.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    lines = ["pair,base,quote"] + [f"{p.pair},{p.base},{p.quote}" for p in pairs]
    settings.OUTPUT_CSV.write_text("\n".join(lines), encoding="utf-8")

    bases = sorted({p.base for p in pairs})
    settings.OUTPUT_TXT.write_text("\n".join(bases), encoding="utf-8")

    return settings.OUTPUT_JSON, settings.OUTPUT_CSV, settings.OUTPUT_TXT


def _extract_symbols(obj) -> List[str]:
    # 兼容两种返回结构：
    # 1) [{"symbol": "ETH", ...}, ...]
    # 2) {"errno": "200", "data": [{"symbol": "ETH", ...}, ...]}
    # 3) {"errno": "200", "data": {"list": [{"symbol": "ETH", ...}, ...]}}
    items = None
    if isinstance(obj, list):
        items = obj
    elif isinstance(obj, dict):
        data = obj.get("data")
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            lst = data.get("list")
            if isinstance(lst, list):
                items = lst
            else:
                items = data.get("items") if isinstance(data.get("items"), list) else []
        else:
            # 如果 data 不可用，尝试直接在 obj 顶层
            items = obj.get("items") if isinstance(obj.get("items"), list) else []
    else:
        items = []

    out: List[str] = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        sym = it.get("symbol") or it.get("base")
        if not sym:
            continue
        out.append(str(sym).strip().upper())
    return out


def _extract_symbol_pair_ids(obj) -> List[dict]:
    """从 API 响应中提取 [{symbol, pair_id}] 列表，兼容 data.list 结构。"""
    items = None
    if isinstance(obj, list):
        items = obj
    elif isinstance(obj, dict):
        data = obj.get("data")
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            lst = data.get("list")
            if isinstance(lst, list):
                items = lst
            else:
                items = data.get("items") if isinstance(data.get("items"), list) else []
        else:
            items = []
    else:
        items = []

    out: List[dict] = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        sym = (it.get("symbol") or it.get("base") or "").strip().upper()
        pid = it.get("pair_id")
        if not sym or pid is None:
            continue
        out.append({"symbol": sym, "pair_id": str(pid)})
    return out


def fetch_and_save_api() -> Tuple[Path, Path, Path]:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Origin": "https://www.surf.one",
        "Referer": "https://www.surf.one/",
    }
    timeout = settings.SURF_TIMEOUT

    last_err = None
    for attempt in range(3):
        try:
            resp = httpx.get(API_URL, headers=headers, timeout=timeout)
            resp.raise_for_status()
            data = resp.json()
            symbols = _extract_symbols(data)
            symbol_ids = _extract_symbol_pair_ids(data)
            break
        except Exception as e:
            last_err = e
            time.sleep(0.6 * (attempt + 1))
    else:
        raise RuntimeError(f"调用 API 失败: {last_err}")

    # 写出 pair_id.json
    pair_id_path = settings.DATA_DIR / "pair_id.json"
    pair_id_payload = {
        "source_url": API_URL,
        "collected_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S%z"),
        "items": symbol_ids,
        "count": len(symbol_ids),
    }
    pair_id_path.write_text(json.dumps(pair_id_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    quote = settings.SURF_QUOTE
    pairs: List[SurfPair] = []
    seen = set()
    for sym in symbols:
        base = sym
        if settings.SURF_ONLY_USDT and quote != "USDT":
            # 若限定仅 USDT，但配置不为 USDT，则仍以配置为准
            pass
        pair = f"{base}/{quote}"
        if pair in seen:
            continue
        seen.add(pair)
        pairs.append(SurfPair(pair=pair, base=base, quote=quote))

    return _save_outputs(pairs)


if __name__ == "__main__":
    j, c, t = fetch_and_save_api()
    print(f"Saved: {j}")
    print(f"Saved: {c}")
    print(f"Saved: {t}")
