from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

from config import settings

MEXC_BASE = "https://futures.mexc.com"
API_DETAIL_V2 = "/api/v1/contract/detailV2?client=web"
API_TICKER = "/api/v1/contract/ticker?"  # 全量ticker

DEFAULT_HEADERS = {
    "accept": "application/json, text/plain, */*",
    "content-type": "application/json",
    "origin": "https://www.mexc.com",
    "referer": "https://www.mexc.com/",
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
}

def _num(v):
    try:
        if v is None:
            return None
        if isinstance(v, (int, float)):
            return v
        s = str(v).strip().replace(',', '')
        if s == '':
            return None
        return float(s)
    except Exception:
        return None


def _load_target_symbols_flat_from_surf() -> List[str]:
    """从 surf_pairs.json 读取 base 与 quote，筛选 USDT，拼接为 BASEUSDT（无下划线）。"""
    surf_path = settings.OUTPUT_JSON  # data/currency_kinds/surf_pairs.json
    data = json.loads(surf_path.read_text(encoding="utf-8"))
    targets: List[str] = []
    for p in data.get("pairs", []):
        base = str(p.get("base", "")).upper().strip()
        quote = str(p.get("quote", "")).upper().strip()
        if not base or not quote:
            continue
        if quote != "USDT":
            continue
        base = "".join(ch for ch in base if ch.isalnum())
        quote = "".join(ch for ch in quote if ch.isalnum())
        if not base or not quote:
            continue
        targets.append(f"{base}{quote}")
    return sorted(set(targets))


def _fetch_detail_v2(timeout: float = 30.0) -> Dict[str, Any]:
    url = f"{MEXC_BASE}{API_DETAIL_V2}"
    with httpx.Client(timeout=timeout, headers=DEFAULT_HEADERS) as client:
        r = client.get(url)
        r.raise_for_status()
        return r.json()


def _fetch_tickers(timeout: float = 20.0) -> Dict[str, float]:
    """获取全量 ticker，返回 { 'BTC_USDT': price_float, ... }。
    兼容字段名：lastPrice、last_price、price、last
    """
    url = f"{MEXC_BASE}{API_TICKER}"
    with httpx.Client(timeout=timeout, headers=DEFAULT_HEADERS) as client:
        r = client.get(url)
        r.raise_for_status()
        data = r.json()
    price_map: Dict[str, float] = {}
    # 期望 data 为 list
    lst = None
    if isinstance(data, list):
        lst = data
    elif isinstance(data, dict):
        # 兼容 {data:[...]} 或 {symbolTicker:[...]}
        lst = data.get("data") or data.get("symbolTicker") or data.get("list")
    if isinstance(lst, list):
        for it in lst:
            if not isinstance(it, dict):
                continue
            sym = str(it.get("symbol") or it.get("s") or "").strip().upper()
            if not sym:
                continue
            p = _num(it.get("lastPrice")) or _num(it.get("last_price")) or _num(it.get("price")) or _num(it.get("last"))
            if p is None:
                continue
            price_map[sym] = float(p)
    return price_map


def _extract_combined(detail: Dict[str, Any], targets_flat: List[str], price_map: Dict[str, float]) -> tuple[Dict[str, Any], Dict[str, List[str]]]:
    """从 detailV2 的 data 中筛选出 USDT 计价目标，并转换为 { BTCUSDT: [tiers...] }。

    唯一性匹配策略：
    - 先基于 API 全量构建 flat 符号索引：flat = symbol.replace('_','')
      score = (has_rlcs, rlcs_len, state_ok)；有 rlcs 的优先，其次 rlcs 越多越好，再次 state==0 优先
    - 最终对每个 targets_flat 仅保留一个条目
    - 输出键为 BASEUSDT（无下划线）
    """
    result: Dict[str, Any] = {}

    def score_item(it: Dict[str, Any]) -> tuple:
        rlcs = it.get("rlcs")
        has_rlcs = 1 if isinstance(rlcs, list) and len(rlcs) > 0 else 0
        rlcs_len = len(rlcs) if isinstance(rlcs, list) else 0
        state_ok = 1 if it.get("state") == 0 else 0
        return (has_rlcs, rlcs_len, state_ok)

    data_list = detail.get("data")
    if not isinstance(data_list, list):
        # 返回空字典与空诊断
        return result, {"matched": [], "no_tiers": [], "unmatched": list(targets_flat)}

    # 1) 构建 flat -> best_item 索引
    best_by_flat: Dict[str, Dict[str, Any]] = {}
    for item in data_list:
        if not isinstance(item, dict):
            continue
        sym_api = str(item.get("symbol", "") or "").strip().upper()
        if not sym_api:
            continue
        flat = "".join(ch for ch in sym_api if ch.isalnum())  # 去掉下划线等
        # 只考虑 USDT 计价
        if not flat.endswith("USDT"):
            continue
        prev = best_by_flat.get(flat)
        if prev is None or score_item(item) > score_item(prev):
            best_by_flat[flat] = item

    # 2) 逐个目标填充结果
    matched: List[str] = []
    no_tiers: List[str] = []
    for flat_key in targets_flat:
        it = best_by_flat.get(flat_key)
        tiers: List[Dict[str, Any]] = []
        if isinstance(it, dict):
            # 价格与合约面值
            sym_api = str(it.get("symbol") or "").upper().strip()
            price = price_map.get(sym_api)
            cs_val = _num(it.get("cs")) or 1.0
            rlcs = it.get("rlcs")
            if isinstance(rlcs, list) and len(rlcs) > 0:
                for tier in rlcs:
                    if not isinstance(tier, dict):
                        continue
                    vol_ct = _num(tier.get("vol"))
                    notional = None
                    if vol_ct is not None and price is not None and cs_val is not None:
                        notional = float(vol_ct) * float(cs_val) * float(price)
                    tiers.append({
                        "lv": tier.get("lv"),
                        "vol_contracts": vol_ct,
                        "notional_usdt": notional,
                        "mmr": _num(tier.get("mmr")),
                        "imr": _num(tier.get("imr")),
                        "mlev": _num(tier.get("mlev")),
                    })
            else:
                # 回退：合成单档（档位1）
                # 取量纲优先：lmv > maxV > rbv（取正数）
                vol_candidates = []
                for key in ("lmv", "maxV", "rbv"):
                    try:
                        v = _num(it.get(key))
                        if v is not None and v > 0:
                            vol_candidates.append(int(v))
                    except Exception:
                        pass
                vol_val = vol_candidates[0] if vol_candidates else None
                mmr = _num(it.get("mmr"))
                imr = _num(it.get("imr"))
                mlev = _num(it.get("maxL")) or _num(it.get("mlev"))
                notional = None
                if vol_val is not None and price is not None and cs_val is not None:
                    notional = float(vol_val) * float(cs_val) * float(price)
                # 只有当关键字段存在时才生成
                if (mmr is not None or imr is not None or mlev is not None or vol_val is not None):
                    tiers.append({
                        "lv": 1,
                        "vol_contracts": vol_val,
                        "notional_usdt": notional,
                        "mmr": mmr,
                        "imr": imr,
                        "mlev": mlev,
                    })
            matched.append(flat_key)
            if len(tiers) == 0:
                no_tiers.append(flat_key)
        result[flat_key] = tiers
    unmatched = [s for s in targets_flat if s not in matched]
    return result, {"matched": matched, "no_tiers": no_tiers, "unmatched": unmatched}


def main(max_symbols: Optional[int] = None) -> Path:
    out_dir = settings.DATAGET_OUTPUT_DIR / "mexc"
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1) 读取 surf 目标，形式：BTCUSDT（无下划线）
    target_syms_flat = _load_target_symbols_flat_from_surf()
    if max_symbols:
        target_syms_flat = target_syms_flat[:max_symbols]

    # 2) 拉取 detailV2（一次性大包）
    detail = _fetch_detail_v2()
    # 2.1) 拉取全量 ticker 价格
    price_map = _fetch_tickers()

    # 3) 落盘原始返回
    raw_file = out_dir / "detailV2_raw.json"
    raw_file.write_text(json.dumps(detail, ensure_ascii=False, indent=2), encoding="utf-8")

    # 4) 提取合并（以 BTCUSDT 作为键，值为 rlcs 列表）
    combined, diag = _extract_combined(detail, target_syms_flat, price_map)
    combined_file = out_dir / "mexc_selected.json"
    combined_file.write_text(json.dumps(combined, ensure_ascii=False, indent=2), encoding="utf-8")

    # 5) meta
    meta = {
        "targets_from_surf": len(target_syms_flat),
        "matched_count": len(diag.get("matched", [])),
        "no_tiers_count": len(diag.get("no_tiers", [])),
        "unmatched_count": len(diag.get("unmatched", [])),
        "api_detail_v2": f"{MEXC_BASE}{API_DETAIL_V2}",
        "note": "keys are BASEQUOTE (no underscore), tiers from rlcs: lv, vol, mmr, imr, mlev",
        "selected_file": str(combined_file),
    }
    (out_dir / "mexc_selected_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    # 额外输出诊断列表
    (out_dir / "detailV2_unmatched.txt").write_text("\n".join(diag.get("unmatched", [])), encoding="utf-8")
    (out_dir / "detailV2_no_tiers.txt").write_text("\n".join(diag.get("no_tiers", [])), encoding="utf-8")

    return raw_file


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="MEXC: 拉取 detailV2（raw）并输出按 surf USDT 列表过滤后的 combined（BTCUSDT 键）")
    parser.add_argument("--max-symbols", type=int, default=None, help="最多处理的交易对数量（调试用）")
    args = parser.parse_args()

    out = main(max_symbols=args.max_symbols)
    print(f"已保存: {out}")
