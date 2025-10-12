from __future__ import annotations

import json
import math
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Tuple

import httpx

from config import settings

PAIR_ID_JSON = settings.DATA_DIR / "pair_id.json"
OUT_BASE = settings.DATAGET_OUTPUT_DIR / "surf"
OUT_BASE.mkdir(parents=True, exist_ok=True)
OUT_JSON = OUT_BASE / "surf_limits.json"
OUT_META = OUT_BASE / "surf_limits_meta.json"

API_DETAIL = "https://surfv2-api.surf.one/pool/pair/config"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Origin": "https://www.surf.one",
    "Referer": "https://www.surf.one/",
}


def _load_pair_ids(path: Path) -> List[Tuple[str, str]]:
    if not path.exists():
        raise FileNotFoundError(f"pair_id.json 不存在: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    items = data.get("items") or []
    out: List[Tuple[str, str]] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        sym = str(it.get("symbol") or "").strip().upper()
        pid = str(it.get("pair_id") or "").strip()
        if not sym or not pid:
            continue
        out.append((sym, pid))
    return out


def _fetch_one(client: httpx.Client, symbol: str, pair_id: str, timeout: float) -> Dict[str, Any]:
    params = {"pair_id": pair_id}
    for attempt in range(3):
        try:
            r = client.get(API_DETAIL, params=params, headers=HEADERS, timeout=timeout)
            r.raise_for_status()
            obj = r.json()
            data = obj.get("data") or {}
            pair_name = str(data.get("pair_name") or f"{symbol}/USDT").strip()
            max_leverage = data.get("max_leverage")
            max_order_size = data.get("max_order_size") or data.get("pair_max_hold_limit")
            max_mmr = data.get("max_mmr")
            # 规范数据类型
            try:
                max_leverage = int(max_leverage) if max_leverage is not None else None
            except Exception:
                max_leverage = None
            try:
                # 可能是字符串数字，保留为字符串或转浮点
                mos = str(max_order_size) if max_order_size is not None else None
            except Exception:
                mos = None
            try:
                mmr = float(max_mmr) if max_mmr is not None else None
            except Exception:
                mmr = None
            return {
                "symbol": symbol,
                "pair_id": pair_id,
                "pair_name": pair_name,
                "max_leverage": max_leverage,
                "max_order_size": mos,
                "max_mmr": mmr,
                "source_url": f"{API_DETAIL}?pair_id={pair_id}",
            }
        except Exception:
            time.sleep(0.4 * (attempt + 1))
    return {
        "symbol": symbol,
        "pair_id": pair_id,
        "error": "request_failed",
        "source_url": f"{API_DETAIL}?pair_id={pair_id}",
    }


def main(concurrency: int = 6, timeout: float = None) -> None:
    timeout = timeout or float(settings.SURF_TIMEOUT)
    pairs = _load_pair_ids(PAIR_ID_JSON)

    results: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []

    with httpx.Client(http2=True) as client:
        with ThreadPoolExecutor(max_workers=max(1, concurrency)) as ex:
            fut_map = {ex.submit(_fetch_one, client, sym, pid, timeout): (sym, pid) for sym, pid in pairs}
            for fut in as_completed(fut_map):
                sym, pid = fut_map[fut]
                try:
                    item = fut.result()
                    if "error" in item:
                        errors.append(item)
                    else:
                        results.append(item)
                except Exception as e:
                    errors.append({"symbol": sym, "pair_id": pid, "error": str(e)})

    # 写出
    OUT_JSON.write_text(json.dumps({"items": results}, ensure_ascii=False, indent=2), encoding="utf-8")
    meta = {
        "source": API_DETAIL,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "total": len(pairs),
        "ok": len(results),
        "errors": len(errors),
        "error_samples": errors[:50],
    }
    OUT_META.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已写出: {OUT_JSON} (ok={len(results)}/{len(pairs)}), meta: {OUT_META}")


if __name__ == "__main__":
    main(concurrency=8)
