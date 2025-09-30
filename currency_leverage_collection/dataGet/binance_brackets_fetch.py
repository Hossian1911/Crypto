from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, List, Tuple, Set

import httpx

from config import settings

BAPI_BRACKETS_URL = "https://www.binance.com/bapi/futures/v1/friendly/future/common/brackets"
DEFAULT_HEADERS = {
    "accept": "application/json, text/plain, */*",
    "content-type": "application/json",
    "clienttype": "web",
    "locale": "zh-CN",
    "origin": "https://www.binance.com",
    "referer": "https://www.binance.com/",
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
}

# 无需解析与过滤，仅保存原始返回


def _fetch_all_brackets(timeout: float = 30.0) -> Tuple[List[Dict], Path]:
    """一次请求抓取所有 brackets 数据，返回 (items, raw_path)。"""
    out_dir = settings.DATAGET_OUTPUT_DIR / "binance"
    out_dir.mkdir(parents=True, exist_ok=True)
    raw_path = out_dir / "brackets_raw.json"

    def normalize(payload: Dict) -> List[Dict]:
        """将不同返回结构统一为 item 列表。
        兼容：
        - {"code":"000000","data":[...]}
        - {"data":{"brackets":[...]}}
        - {"data":{"list":[...]}} / items / rows / result
        - {"data":{"BTCUSDT":{...}, ...}} map 形式
        - 顶层直接为列表
        """
        data_field = payload.get("data", payload)
        # 若 data 为 dict，可能内含 list / map
        if isinstance(data_field, dict):
            # 直接包含列表键
            for k in ("brackets", "list", "items", "rows", "result"):
                v = data_field.get(k)
                if isinstance(v, list):
                    return v
            # 可能是以 symbol 为 key 的 map
            values = list(data_field.values())
            if values and all(isinstance(x, dict) for x in values):
                return values
            # 兜底：若 dict 内含单个关键键
            return [data_field]
        # 顶层即为列表
        if isinstance(data_field, list):
            return data_field
        return []

    with httpx.Client(timeout=timeout) as client:
        attempts = [
            ("post_empty", {"method": "POST", "json": {}}),
            ("post_contractType", {"method": "POST", "json": {"contractType": "PERPETUAL"}}),
            ("post_tradeType", {"method": "POST", "json": {"tradeType": "UMFUTURE"}}),
            ("get_fallback", {"method": "GET", "json": None}),
        ]
        for tag, req in attempts:
            try:
                if req["method"] == "POST":
                    r = client.post(BAPI_BRACKETS_URL, headers=DEFAULT_HEADERS, json=req["json"]) 
                else:
                    r = client.get(BAPI_BRACKETS_URL, headers=DEFAULT_HEADERS)
                r.raise_for_status()
                data = r.json()
                # 保存本次尝试的原始返回
                try:
                    (out_dir / f"{tag}_raw.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                except Exception:
                    pass
                items = normalize(data)
                if items:
                    # 最终成功则也写一份通用 raw
                    try:
                        raw_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                    except Exception:
                        pass
                    return items, raw_path
            except Exception:
                continue
        return [], raw_path


def _load_target_symbols() -> Set[str]:
    """从 surf_pairs.json 读取 USDT 交易对，生成如 'KUSDT' 的目标符号集。"""
    surf_path = Path(__file__).resolve().parent.parent / "data" / "currency_kinds" / "surf_pairs.json"
    data = json.loads(surf_path.read_text(encoding="utf-8"))
    pairs = data.get("pairs") or []
    symbols: Set[str] = set()
    for it in pairs:
        if not isinstance(it, dict):
            continue
        base = str(it.get("base") or "").strip().upper()
        quote = str(it.get("quote") or "").strip().upper()
        if not base or quote != "USDT":
            continue
        symbols.add(f"{base}{quote}")
    return symbols


def _filter_items(items: List[Dict], wanted: Set[str]) -> Tuple[List[Dict], List[str]]:
    """仅用无后缀（不含下划线）的 USDT 符号进行匹配。
    - 原始数据里可能存在 BTCUSDT 与 BTCUSDT_251226 等；我们只取精确的 BTCUSDT。
    - 兼容 symbol 字段名：symbol / s / pair。
    """
    def extract_sym(it: Dict) -> str:
        for k in ("symbol", "s", "pair"):
            v = it.get(k)
            if v:
                return str(v).strip().upper()
        return ""

    by_exact: Dict[str, Dict] = {}
    for it in items:
        sym = extract_sym(it)
        if not sym:
            continue
        # 仅接受无下划线且以 USDT 结尾的现货/合约通用符号
        if "_" in sym:
            continue
        if not sym.endswith("USDT"):
            continue
        by_exact[sym] = it

    out: List[Dict] = []
    missing: List[str] = []
    for sym in sorted(wanted):
        hit = by_exact.get(sym)
        if hit is None:
            missing.append(sym)
        else:
            out.append(hit)
    return out, missing


def main() -> Path:
    # 1) 请求并将原始返回落盘到 brackets_raw.json
    items, raw_path = _fetch_all_brackets()

    # 2) 识别/过滤：仅保留 surf_pairs.json 中的 USDT 符号
    try:
        wanted = _load_target_symbols()
    except Exception:
        wanted = set()

    out_dir = settings.DATAGET_OUTPUT_DIR / "binance"
    out_dir.mkdir(parents=True, exist_ok=True)
    selected_path = out_dir / "binance_selected.json"
    meta_path = out_dir / "binance_selected_meta.json"

    if items and wanted:
        selected, missing = _filter_items(items, wanted)
        selected_path.write_text(json.dumps(selected, ensure_ascii=False, indent=2), encoding="utf-8")
        meta = {
            "source_raw": str(raw_path),
            "total_items_all": len(items),
            "wanted_count": len(wanted),
            "selected_count": len(selected),
            "missing_count": len(missing),
            "missing": missing[:300],
            "unmatched": missing[:300],
        }
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        # 落空时也写入空结构，便于排查
        selected_path.write_text(json.dumps([], ensure_ascii=False, indent=2), encoding="utf-8")
        meta = {
            "source_raw": str(raw_path),
            "total_items_all": len(items),
            "wanted_count": len(wanted),
            "selected_count": 0,
            "missing_count": len(wanted),
            "missing": sorted(list(wanted))[:300],
            "note": "items或目标集合为空，无法筛选",
        }
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    return raw_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="从 Binance BAPI 抓取所有合约的风险分层原始数据，仅保存 raw JSON")
    _ = parser.parse_args()

    out = main()
    print(f"已保存原始文件: {out}")
