from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill, Border, Side

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data" / "dataGet_api"
HTML_DIR = ROOT / "result" / "html"
OUT_DIR = ROOT / "result" / "suggest"
OUT_DIR.mkdir(parents=True, exist_ok=True)

MAJOR_TIERS = [50_000, 200_000, 500_000, 1_000_000]
MINOR_TIERS = [20_000, 100_000, 200_000]

EXS_SHOW = ["BINANCE", "WEEX", "MECX", "BYBIT"]

LEV_MAX = 1000
LEV_STEP = 5       # 杠杆取整刻度（5X）


def _latest_json() -> Optional[Path]:
    files = sorted(HTML_DIR.glob("Leverage&Margin_*.json"))
    return files[-1] if files else None


def _num(v: Any) -> Optional[float]:
    try:
        if v is None:
            return None
        if isinstance(v, (int, float)):
            return float(v)
        s = str(v).replace(",", "").strip()
        if s == "":
            return None
        return float(s)
    except Exception:
        return None


def _mmr_to_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    if s.endswith('%'):
        try:
            return float(s.rstrip('%')) / 100.0
        except Exception:
            return None
    return _num(s)


def _lev_to_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).upper().replace(" ", "").strip()
    if s.endswith("X"):
        s = s[:-1]
    return _num(s)


def _lev_fmt(x: float) -> str:
    if float(x).is_integer():
        return f"{int(x)}X"
    return f"{x}X"


def _im_from_lev(lev: float) -> float:
    return 1.0 / max(1e-9, lev)


def _mmr_fmt(v: float) -> str:
    return f"{v*100:.2f}%"


def _pos_fmt(v: float) -> str:
    if float(v).is_integer():
        return f"{int(v):,}"
    return f"{v:,.2f}"


def _round_leverage(x: float) -> float:
    x = min(LEV_MAX, max(1.0, x))
    # 四舍五入到 5X 刻度
    return float(int(round(x / LEV_STEP)) * LEV_STEP)


def _select_tier_for_threshold(rows: List[List[Any]], S: float) -> Optional[Tuple[float, float]]:
    """
    在一所的行表中，根据阈值 S 选择一档：
    - 原则：找到“Max Position >= S 且下一档 < S”的那一行；若所有行均 >= S，取第一行（通常杠杆最低、仓位最大）；若所有行均 < S，返回 None。
    输入 rows 格式：[[ex_name, lev_str, pos_float, mmr_str], ...]
    返回 (leverage_float, mmr_float)
    """
    # 过滤出 pos、lev、mmr 都可解析的行
    parsed = []
    for r in rows:
        if len(r) < 4:
            continue
        lev = _lev_to_float(r[1])
        pos = _num(r[2])
        mmr = _mmr_to_float(r[3])
        if lev is None or pos is None or mmr is None:
            continue
        parsed.append((lev, pos, mmr))
    if not parsed:
        return None
    # 假设 pos 随 lev 增大而下降，但为稳妥：按 pos 从大到小排序
    parsed.sort(key=lambda t: t[1], reverse=True)
    max_pos = parsed[0][1]
    min_pos = parsed[-1][1]
    # 若 S 大于等于最大 pos，则选第一行（更低杠杆那档）
    if S >= max_pos:
        return (parsed[0][0], parsed[0][2])
    # 若 S 小于等于最小 pos，则选最后一行（“最后一个也要拿”）
    if S <= min_pos:
        return (parsed[-1][0], parsed[-1][2])
    # 遍历查找“当前 >= S 且下一档 < S”的位置
    for i in range(len(parsed) - 1):
        lev, pos, mmr = parsed[i]
        _, pos_next, _ = parsed[i + 1]
        if pos >= S and pos_next < S:
            return (lev, mmr)
    # 兜底（理论不应到达）：返回最后一行
    if parsed:
        return (parsed[-1][0], parsed[-1][2])
    return None


def _street_for_symbol(payload: Dict[str, Dict[str, List[List[Any]]]], sym: str, S: float) -> Tuple[Optional[float], Optional[float]]:
    """跨所基准：返回 (max_leverage, min_mmr) for level S。"""
    levs: List[float] = []
    mmrs: List[float] = []
    sym_map = payload.get(sym) or {}
    for ex in EXS_SHOW:
        rows = sym_map.get(ex) or []
        pick = _select_tier_for_threshold(rows, S)
        if pick is None:
            continue
        lev, mmr = pick
        levs.append(lev)
        mmrs.append(mmr)
    return (max(levs) if levs else None, min(mmrs) if mmrs else None)


def _build_groups() -> Tuple[List[str], List[str]]:
    # Majors: 从 cmc_top20.json 读取，已在 dataGet_main 里拉取
    cmc_path = DATA_DIR / "cmc" / "cmc_top20.json"
    majors: List[str] = []
    if cmc_path.exists():
        try:
            arr = json.loads(cmc_path.read_text(encoding="utf-8"))
            for it in arr:
                s = str(it.get("name") or "").upper().strip()
                if s:
                    majors.append(f"{s}USDT")
        except Exception:
            pass
    # 所有 SURF 支持的 USDT 交易对
    surf_pairs = (ROOT / "data" / "currency_kinds" / "surf_pairs.json").read_text(encoding="utf-8")
    sp = json.loads(surf_pairs)
    all_syms = []
    for p in sp.get("pairs", []):
        base = str(p.get("base") or "").upper().strip()
        quote = str(p.get("quote") or "").upper().strip()
        if base and quote == "USDT":
            all_syms.append(f"{base}{quote}")
    majors = [s for s in majors if s in set(all_syms)]
    minors = sorted([s for s in set(all_syms) if s not in set(majors)])
    return majors, minors


def generate_excel() -> Path:
    latest = _latest_json()
    if latest is None:
        raise FileNotFoundError("未找到 result/html 下的 Leverage&Margin_*.json")
    data = json.loads(latest.read_text(encoding="utf-8"))
    payload: Dict[str, Dict[str, List[List[Any]]]] = data.get("data") or {}

    majors, minors = _build_groups()

    wb = Workbook()
    # 清理默认sheet
    default_ws = wb.active
    wb.remove(default_ws)

    def write_sheet(sym: str, tiers: List[int]) -> None:
        ws = wb.create_sheet(title=f"{sym} Suggest Rule")
        header = ["max position size", "Max Leverage", "Min MMR", "IM(%)"]
        ws.append(header)
        # 样式
        for c in range(1, len(header) + 1):
            cell = ws.cell(row=1, column=c)
            cell.font = Font(bold=True)
            cell.alignment = Alignment(horizontal="center", vertical="center")
            cell.fill = PatternFill("solid", fgColor="FFFFF2AB")
        # 逐层级写入：严格按街上基准聚合
        for S in tiers:
            street_lev, street_mmr = _street_for_symbol(payload, sym, float(S))
            # 若缺失，留空
            if street_lev is None and street_mmr is None:
                ws.append([_pos_fmt(S), "", "", ""])
                continue
            lev_str = _lev_fmt(street_lev) if street_lev is not None else ""
            mmr_str = _mmr_fmt(street_mmr) if street_mmr is not None else ""
            im_pct = f"{(_im_from_lev(street_lev)*100):.2f}" if street_lev is not None else ""
            ws.append([
                _pos_fmt(S),
                lev_str,
                mmr_str,
                im_pct,
            ])
        # 边框
        thin = Side(style="thin", color="FFCCCCCC")
        border_all = Border(left=thin, right=thin, top=thin, bottom=thin)
        for r in range(1, ws.max_row + 1):
            for c in range(1, ws.max_column + 1):
                ws.cell(row=r, column=c).border = border_all
        # 列宽
        for c in range(1, ws.max_column + 1):
            ws.column_dimensions[chr(ord('A') + c - 1)].width = 18

    for sym in majors:
        write_sheet(sym, MAJOR_TIERS)
    for sym in minors:
        write_sheet(sym, MINOR_TIERS)

    out = OUT_DIR / f"suggest_rules_{time.strftime('%Y%m%d_%H%M%S')}.xlsx"
    wb.save(out)
    return out


if __name__ == "__main__":
    path = generate_excel()
    print(f"Saved: {path}")
