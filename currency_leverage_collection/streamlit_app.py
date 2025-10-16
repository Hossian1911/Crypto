import json
import time
from pathlib import Path
from datetime import datetime
import io
import csv
import pandas as pd
import streamlit as st
import re
import json

ROOT = Path(__file__).resolve().parent
HTML_DIR = ROOT / "result" / "html"
SUGGEST_DIR = ROOT / "result" / "suggest"


def latest_json() -> Path | None:
    files = sorted(HTML_DIR.glob("Leverage&Margin_*.json"))
    return files[-1] if files else None


@st.cache_data(ttl=0)
def load_data(p: Path, mtime_ns: int):
    data = json.loads(p.read_text(encoding="utf-8"))
    return data


def rows_to_csv(rows: list[list[str]]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["Exchange", "Max Leverage", "Max Position (USDT)", "Maintenance Margin Rate"])
    for r in rows or [["", "", "", ""]]:
        writer.writerow([(c if c is not None else "") for c in r])
    return buf.getvalue()


def rows_to_df(rows: list[list[str]]) -> "pd.DataFrame":
    cols = ["Exchange", "Max Leverage", "Max Position (USDT)", "Maintenance Margin Rate"]
    safe_rows = rows or [["", "", "", ""]]
    return pd.DataFrame(safe_rows, columns=cols)


NUM_RE = re.compile(r"[-+]?[0-9]*\.?[0-9]+")


def _num(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    if not s:
        return None
    m = NUM_RE.search(s)
    return float(m.group(0)) if m else None


def _lev_to_float(v):
    # "5X" -> 5.0; "10x" -> 10.0; 50 -> 50.0
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().upper().replace(" ", "")
    s = s.replace("X", "")
    return _num(s)


def _mmr_to_float(v):
    # "0.10%" -> 0.001; 0.001 -> 0.001
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


def _fmt_pos(v: float | None) -> str:
    if v is None:
        return ""
    try:
        f = float(v)
        return f"{int(f):,}" if f.is_integer() else f"{f:,.2f}"
    except Exception:
        return str(v)


def _fmt_pct(v: float | None) -> str:
    if v is None:
        return ""
    try:
        return f"{v*100:.2f}%"
    except Exception:
        return str(v)


def build_aggregate_union_table(sym: str, payload: dict) -> pd.DataFrame:
    # Collect union of leverage tiers from four exchanges (exclude SURF)
    exs = ["BINANCE", "WEEX", "MECX", "BYBIT"]
    by_lev: dict[float, list[tuple[str, float | None, float | None]]] = {}
    for ex in exs:
        rows = payload.get(sym, {}).get(ex, []) or []
        for r in rows:
            if len(r) < 4:
                continue
            lev_raw, pos_raw, mmr_raw = r[1], r[2], r[3]
            lev = _lev_to_float(lev_raw)
            if lev is None:
                continue
            pos = _num(pos_raw)
            mmr = _mmr_to_float(mmr_raw)
            by_lev.setdefault(lev, []).append((ex, pos, mmr))
    if not by_lev:
        return pd.DataFrame(columns=["Leverage", "Max Position (USDT)", "Max Position Source", "Min MMR", "Min MMR Source"])  # empty

    records: list[dict] = []
    for lev in sorted(by_lev.keys()):
        entries = by_lev[lev]
        # Max position
        max_pos_val = None
        max_pos_ex = ""
        for ex, pos, _mmr in entries:
            if pos is None:
                continue
            if max_pos_val is None or float(pos) > max_pos_val:
                max_pos_val = float(pos)
                max_pos_ex = ex
        # Min MMR
        min_mmr_val = None
        min_mmr_ex = ""
        for ex, _pos, mmr in entries:
            if mmr is None:
                continue
            if min_mmr_val is None or float(mmr) < min_mmr_val:
                min_mmr_val = float(mmr)
                min_mmr_ex = ex
        records.append({
            "Leverage": f"{int(lev)}X" if float(lev).is_integer() else f"{lev}X",
            "Max Position (USDT)": _fmt_pos(max_pos_val),
            "Max Position Source": max_pos_ex,
            "Min MMR": _fmt_pct(min_mmr_val),
            "Min MMR Source": min_mmr_ex,
        })
    return pd.DataFrame.from_records(records)


# ===== Suggest Rule helpers =====
MAJOR_TIERS = [50_000, 200_000, 500_000, 1_000_000]
MINOR_TIERS = [20_000, 100_000, 200_000]
EXS_SHOW = ["BINANCE", "WEEX", "MECX", "BYBIT"]
ALPHA_TOP = 0.40
ALPHA_MID = 0.50
LEV_MAX = 1000
LEV_STEP = 5
MMR_FLOOR = 0.0002  # 0.02%


def _round_leverage(x: float) -> float:
    x = min(LEV_MAX, max(1.0, x))
    return float(int(round(x / LEV_STEP)) * LEV_STEP)


def _select_tier_for_threshold(rows: list[list], S: float) -> tuple[float, float] | None:
    parsed: list[tuple[float, float, float]] = []
    for r in rows or []:
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
    parsed.sort(key=lambda t: t[1], reverse=True)
    if S >= parsed[0][1]:
        return parsed[0][0], parsed[0][2]
    for i in range(len(parsed) - 1):
        lev, pos, mmr = parsed[i]
        _, pos_next, _ = parsed[i + 1]
        if pos >= S and pos_next < S:
            return lev, mmr
    return None


def _street_for_symbol(payload: dict, sym: str, S: float) -> tuple[float | None, float | None]:
    levs: list[float] = []
    mmrs: list[float] = []
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


def _load_majors() -> set[str]:
    cmc_path = ROOT / "data" / "dataGet_api" / "cmc" / "cmc_top20.json"
    majors: set[str] = set()
    try:
        if cmc_path.exists():
            arr = json.loads(cmc_path.read_text(encoding="utf-8"))
            for it in arr:
                sym = str(it.get("name") or "").upper().strip()
                if sym and sym not in {"USDT", "USDC"}:
                    majors.add(f"{sym}USDT")
    except Exception:
        pass
    return majors


def build_suggest_rule_table(sym: str, payload: dict) -> pd.DataFrame:
    majors = _load_majors()
    tiers = MAJOR_TIERS if sym in majors else MINOR_TIERS
    recs: list[dict] = []
    for idx, S in enumerate(tiers):
        street_lev, street_mmr = _street_for_symbol(payload, sym, float(S))
        if idx == len(tiers) - 1:
            base_lev = street_lev or 10.0
            sug_lev = _round_leverage(base_lev * 1.10)
            im = 1.0 / sug_lev
            mmr_rule = max(MMR_FLOOR, ALPHA_TOP * im)
            mmr_street = street_mmr if street_mmr is not None else 1.0
            sug_mmr = min(mmr_rule, mmr_street * 0.90)
        else:
            base_lev = street_lev or 10.0
            sug_lev = _round_leverage(base_lev * 0.90)
            im = 1.0 / sug_lev
            mmr_rule = max(MMR_FLOOR, ALPHA_MID * im)
            sug_mmr = min(mmr_rule, street_mmr) if street_mmr is not None else mmr_rule
        recs.append({
            "Max Position (USDT)": _fmt_pos(S),
            "Leverage": f"{int(sug_lev)}X" if float(sug_lev).is_integer() else f"{sug_lev}X",
            "IM": _fmt_pct(1.0 / sug_lev),
            "MMR": _fmt_pct(sug_mmr),
            "Street Max Lev": f"{int(street_lev)}X" if street_lev and float(street_lev).is_integer() else (f"{street_lev}X" if street_lev else ""),
            "Street Min MMR": _fmt_pct(street_mmr) if street_mmr is not None else "",
        })
    df = pd.DataFrame.from_records(recs)
    return df


def main():
    st.set_page_config(page_title="Leverage & MMR Dashboard", layout="wide")
    st.title("Leverage & MMR Dashboard")

    with st.sidebar:
        st.header("Data Source")
        uploaded = st.file_uploader("Upload JSON (optional)", type=["json"], help="If empty, the latest file under result/html will be used")

    if uploaded is not None:
        try:
            data = json.loads(uploaded.getvalue().decode("utf-8"))
            data_path = Path(uploaded.name)
            mtime_ns = int(time.time_ns())
        except Exception:
            st.error("上传的 JSON 文件解析失败")
            return
    else:
        data_path = latest_json()
        if not data_path:
            st.error("No JSON found. Please run the generator (main.py or tableMake_main) first.")
            return
        stat = data_path.stat()
        mtime_ns = stat.st_mtime_ns
        data = load_data(data_path, mtime_ns)

    symbols = data.get("symbols", [])
    summary = data.get("summary", {})
    payload = data.get("data", {})

    with st.sidebar:
        st.header("Controls")
        idx = max(0, symbols.index("BTCUSDT")) if "BTCUSDT" in symbols and len(symbols) > 0 else 0
        sym = st.selectbox("Symbol", symbols, index=idx if symbols else 0)

        # Data Source: show latest updated time from filename (Beijing time)
        st.subheader("Data Source")
        fn = (data_path.name if data_path else (uploaded.name if uploaded else ""))
        ts_beijing = ""
        try:
            # Expecting: Leverage&Margin_YYYYMMDD_HHMMSS.json
            m = re.search(r"Leverage&Margin_(\d{8})_(\d{6})\.json", fn)
            if m:
                ymd = m.group(1)
                hms = m.group(2)
                dt = datetime.strptime(ymd + hms, "%Y%m%d%H%M%S")
                # Treat as local filename time; mark explicitly as Beijing Time
                ts_beijing = dt.strftime("%Y-%m-%d %H:%M:%S 北京时间")
        except Exception:
            ts_beijing = ""
        st.caption(f"File: {fn}")
        if ts_beijing:
            st.caption(f"Last updated: {ts_beijing}")

        # Frequency controls removed; auto update is driven by GitHub Actions hourly.

    if not symbols:
        st.warning("Symbols is empty")
        return

    s = summary.get(sym)
    if not s:
        st.warning("No summary for selected symbol")
        return

    ex_order = ["Aggregate", "Suggest Rule", "BINANCE", "WEEX", "MECX", "BYBIT", "SURF"]
    tabs = st.tabs(ex_order)

    # Aggregate tab: union of leverage tiers across exchanges
    with tabs[0]:
        st.subheader("Aggregate (Cross-Exchange)")
        agg_df = build_aggregate_union_table(sym, payload)
        if agg_df.empty:
            st.info("No aggregated tiers found.")
        else:
            st.dataframe(agg_df, use_container_width=True)

    # Suggest Rule tab: JSON-only from result/suggest
    with tabs[1]:
        st.subheader("Suggest Rule")
        try:
            jfiles = sorted(SUGGEST_DIR.glob("suggest_rules_*.json"))
            df = None
            if jfiles:
                jpath = jfiles[-1]
                jdata = json.loads(jpath.read_text(encoding="utf-8"))
                tiers = (jdata.get("tiers") or {}).get(sym)
                if isinstance(tiers, list):
                    recs = []
                    for t in tiers:
                        recs.append({
                            "max position size": _fmt_pos(t.get("position")),
                            "Max Leverage": t.get("leverage_display") or "",
                            "Max Lev Source": t.get("max_lev_source") or "",
                            "Min MMR": t.get("mmr_display") or "",
                            "Min MMR Source": t.get("min_mmr_source") or "",
                            "IM": t.get("im_display") or "",
                        })
                    df = pd.DataFrame.from_records(recs)
            if df is not None:
                st.dataframe(df, use_container_width=True)
            else:
                st.info("No suggest JSON found under result/suggest.")
        except Exception as e:
            st.error(f"Error loading suggest table: {e}")

    # Exchange tabs (shifted by 1 due to Suggest Rule)
    for tab, ex in zip(tabs[2:], ex_order[2:]):
        with tab:
            rows = payload.get(sym, {}).get(ex, [["", "", "", ""]])
            df = rows_to_df(rows)
            st.table(df)
            csv_text = rows_to_csv(rows)
            st.download_button(
                label=f"Download {ex} CSV",
                data=csv_text,
                file_name=f"{sym}_{ex}.csv",
                mime="text/csv",
            )


if __name__ == "__main__":
    main()
