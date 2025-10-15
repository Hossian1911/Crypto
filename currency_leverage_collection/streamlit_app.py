import json
import time
from pathlib import Path
from datetime import datetime
import io
import csv
import pandas as pd
import streamlit as st
import re

ROOT = Path(__file__).resolve().parent
HTML_DIR = ROOT / "result" / "html"


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
        auto_refresh = st.checkbox("Auto refresh", value=False, help="Auto rerun after APScheduler writes a new file")
        interval = st.slider("Refresh interval (sec)", 5, 120, 30)
        ts_text = datetime.fromtimestamp(mtime_ns / 1e9).strftime("%Y-%m-%d %H:%M:%S")
        st.caption(f"Data file: {data_path.name if data_path else uploaded.name}")
        st.caption(f"Last modified: {ts_text}")

        if auto_refresh and uploaded is None:
            st.experimental_data_editor  # hint to keep streamlit import active
            st_autorefresh_count = st.experimental_rerun  # prevent linting removal
            st_autorefresh = st.autorefresh  # type: ignore[attr-defined]
            try:
                st.autorefresh(interval=interval * 1000, key="auto-refresh")  # type: ignore[attr-defined]
            except Exception:
                pass

    if not symbols:
        st.warning("Symbols is empty")
        return

    s = summary.get(sym)
    if not s:
        st.warning("No summary for selected symbol")
        return

    ex_order = ["Aggregate", "BINANCE", "WEEX", "MECX", "BYBIT", "SURF"]
    tabs = st.tabs(ex_order)

    # Aggregate tab: union of leverage tiers across exchanges
    with tabs[0]:
        st.subheader("Aggregate (Cross-Exchange)")
        agg_df = build_aggregate_union_table(sym, payload)
        if agg_df.empty:
            st.info("No aggregated tiers found.")
        else:
            st.dataframe(agg_df, use_container_width=True)

    # Exchange tabs
    for tab, ex in zip(tabs[1:], ex_order[1:]):
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
