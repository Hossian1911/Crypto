import json
import time
from pathlib import Path
from datetime import datetime
import io
import csv
import pandas as pd
import streamlit as st

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

    # Aggregate tab
    with tabs[0]:
        st.subheader("Aggregate (Cross-Exchange)")
        ag_cols = st.columns(2)
        with ag_cols[0]:
            st.metric("Max Leverage", s.get("max_leverage", {}).get("display", ""), help=s.get("max_leverage", {}).get("exchange", ""))
        with ag_cols[1]:
            st.metric("Min MMR", s.get("min_mmr", {}).get("display", ""), help=s.get("min_mmr", {}).get("exchange", ""))

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
