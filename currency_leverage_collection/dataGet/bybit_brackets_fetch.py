from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx

from config import settings
from dataGet.utils.multithread_utils import run_multithread

BYBIT_BASE = "https://www.bybitglobal.com"
API_SYMBOL_RISK = "/x-api/contract/v5/public/support/symbol-risk"

DEFAULT_HEADERS = {
    "accept": "application/json, text/plain, */*",
    "content-type": "application/json",
    "clienttype": "web",
    "locale": "zh-MY",
    "origin": BYBIT_BASE,
    "referer": f"{BYBIT_BASE}/",
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
}


def _load_target_symbols_from_surf() -> List[str]:
    """从 surf_pairs.json 读取 base 与 quote，筛选 USDT，拼接为 BASEUSDT。"""
    surf_path = settings.OUTPUT_JSON  # data/currency_kinds/surf_pairs.json
    data = json.loads(surf_path.read_text(encoding="utf-8"))
    targets: List[str] = []
    for p in data.get("pairs", []):
        base = str(p.get("base", "")).upper().strip()
        quote = str(p.get("quote", "")).upper().strip()
        if not base or not quote:
            continue
        # 只取 USDT 计价
        if quote != "USDT":
            continue
        # 清理异常字符（防止含空格、符号等）
        base = "".join(ch for ch in base if ch.isalnum())
        quote = "".join(ch for ch in quote if ch.isalnum())
        if not base or not quote:
            continue
        targets.append(f"{base}{quote}")
    # 去重排序
    return sorted(set(targets))


# 不再调用 brief-symbol-list，按用户要求直接使用 surf 提供的目标列表


def _fetch_symbol_risk(symbol: str, timeout: float = 25.0) -> Dict[str, Any]:
    url = f"{BYBIT_BASE}{API_SYMBOL_RISK}?symbol={symbol}"
    with httpx.Client(timeout=timeout, headers=DEFAULT_HEADERS) as client:
        r = client.get(url)
        r.raise_for_status()
        return {
            "symbol": symbol,
            "url": url,
            "status": r.status_code,
            "data": r.json(),
        }


def main(max_symbols: Optional[int] = None, max_workers: Optional[int] = None) -> Path:
    out_dir = settings.DATAGET_OUTPUT_DIR / "bybit"
    out_dir.mkdir(parents=True, exist_ok=True)

    # Step 1. 读取 surf 目标（AAVEUSDT 形式）
    target_syms = _load_target_symbols_from_surf()
    if max_symbols:
        target_syms = target_syms[:max_symbols]

    # Step 2. 按用户要求：不做交集，直接使用 surf 列表
    final_syms = list(target_syms)

    # Step 3. 并发拉取 support/symbol-risk
    if max_workers is None:
        try:
            max_workers = int(getattr(settings, "BINANCE_MAX_WORKERS", 8))
        except Exception:
            max_workers = 8

    def job(sym: str) -> Optional[Dict[str, Any]]:
        try:
            return _fetch_symbol_risk(sym)
        except Exception as e:
            return {"symbol": sym, "error": str(e)}

    results = run_multithread(func=job, data_list=final_syms, max_workers=max_workers, show_progress=True)

    # Step 4. 落盘 raw（合并成一个文件）
    out_file = out_dir / "support_symbol_risk_raw.json"
    out_file.write_text(json.dumps({"symbols": final_syms, "results": results}, ensure_ascii=False, indent=2), encoding="utf-8")

    # 额外：提取每个 symbol 的 result.list，输出一个便于直接消费的合并文件
    combined: Dict[str, Any] = {}
    for item in results:
        sym = item.get("symbol") if isinstance(item, dict) else None
        data = item.get("data") if isinstance(item, dict) else None
        tiers = []
        if isinstance(data, dict):
            # 兼容 {ret_code, result:{list:[...]}}
            ret_code = data.get("ret_code")
            result = data.get("result") or {}
            if isinstance(result, dict):
                lst = result.get("list")
                if isinstance(lst, list):
                    tiers = lst
        if sym:
            combined[sym] = tiers

    combined_file = out_dir / "bybit_selected.json"
    combined_file.write_text(json.dumps(combined, ensure_ascii=False, indent=2), encoding="utf-8")

    # 同时保存 meta
    meta = {
        "requested_from_surf": len(target_syms),
        "intersect_count": len(final_syms),
        "results_count": len(results),
        "api_symbol_risk": f"{BYBIT_BASE}{API_SYMBOL_RISK}?symbol=",
        "selected_file": str(combined_file),
    }
    (out_dir / "bybit_selected_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    return out_file


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Bybit: 拉取 brief-symbol-list 与 support/symbol-risk（与 surf_pairs.json 交集），仅保存原始 raw")
    parser.add_argument("--max-symbols", type=int, default=None, help="最多处理的交易对数量（调试用）")
    parser.add_argument("--max-workers", type=int, default=None, help="最大并发线程数")
    args = parser.parse_args()

    out = main(max_symbols=args.max_symbols, max_workers=args.max_workers)
    print(f"已保存: {out}")
