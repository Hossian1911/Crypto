import json
import time
from pathlib import Path
from datetime import datetime
import io
import csv
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
    # 写入简单表头
    writer.writerow(["", "最大杠杆", "最大持仓 (USDT)", "维持保证金率"])
    for r in rows or [["", "", "", ""]]:
        writer.writerow([(c if c is not None else "") for c in r])
    return buf.getvalue()


def main():
    st.set_page_config(page_title="Leverage & MMR Dashboard", layout="wide")
    st.title("Leverage & MMR Dashboard")

    with st.sidebar:
        st.header("数据源")
        uploaded = st.file_uploader("手动上传 JSON (可选)", type=["json"], help="如不上传，则自动读取 result/html 下最新的 JSON")

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
            st.error("未找到 JSON 数据，请先运行生成流程（main.py 或 tableMake_main）")
            return
        stat = data_path.stat()
        mtime_ns = stat.st_mtime_ns
        data = load_data(data_path, mtime_ns)

    symbols = data.get("symbols", [])
    summary = data.get("summary", {})
    payload = data.get("data", {})

    with st.sidebar:
        st.header("控制台")
        idx = max(0, symbols.index("BTCUSDT")) if "BTCUSDT" in symbols and len(symbols) > 0 else 0
        sym = st.selectbox("币种", symbols, index=idx if symbols else 0)
        auto_refresh = st.checkbox("自动刷新", value=False, help="结合 APScheduler 每小时更新后前端自动轮询")
        interval = st.slider("刷新间隔(秒)", 5, 120, 30)
        ts_text = datetime.fromtimestamp(mtime_ns / 1e9).strftime("%Y-%m-%d %H:%M:%S")
        st.caption(f"数据文件: {data_path.name if data_path else uploaded.name}")
        st.caption(f"最后修改: {ts_text}")

        if auto_refresh and uploaded is None:
            st.experimental_data_editor  # hint to keep streamlit import active
            st_autorefresh_count = st.experimental_rerun  # prevent linting removal
            st_autorefresh = st.autorefresh  # type: ignore[attr-defined]
            try:
                st.autorefresh(interval=interval * 1000, key="auto-refresh")  # type: ignore[attr-defined]
            except Exception:
                pass

    if not symbols:
        st.warning("symbols 为空")
        return

    s = summary.get(sym)
    if not s:
        st.warning("该币种缺少 summary")
        return

    col1, col2 = st.columns(2)
    with col1:
        st.metric("跨所最大杠杆", s.get("max_leverage", {}).get("display", ""), help=s.get("max_leverage", {}).get("exchange", ""))
    with col2:
        st.metric("全所最低MMR", s.get("min_mmr", {}).get("display", ""), help=s.get("min_mmr", {}).get("exchange", ""))

    ex_order = ["BINANCE", "WEEX", "MECX", "BYBIT", "SURF"]
    tabs = st.tabs(ex_order)
    for tab, ex in zip(tabs, ex_order):
        with tab:
            rows = payload.get(sym, {}).get(ex, [["", "", "", ""]])
            st.table(rows if rows else [["", "", "", ""]])
            csv_text = rows_to_csv(rows)
            st.download_button(
                label=f"下载 {ex} CSV",
                data=csv_text,
                file_name=f"{sym}_{ex}.csv",
                mime="text/csv",
            )


if __name__ == "__main__":
    main()
