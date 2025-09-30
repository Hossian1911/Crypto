from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from selenium import webdriver
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.common.by import By
from selenium.common.exceptions import TimeoutException
from dataGet.utils.multithread_utils import run_multithread

# 读取目标币种文件
SURF_PAIRS_JSON = Path(__file__).resolve().parent.parent / "data" / "currency_kinds" / "surf_pairs.json"

# 输出目录
OUT_BASE = Path(__file__).resolve().parent.parent / "data" / "dataGet_api" / "weex"
OUT_BASE.mkdir(parents=True, exist_ok=True)
OUT_JSON = OUT_BASE / "weex_selected.json"
OUT_META = OUT_BASE / "weex_selected_meta.json"

WEEX_BASE_URL = "https://www.weex.com/zh-CN/futures/introduction/risk-limit"

UL_RE = re.compile(r"<ul[^>]*class=\"[^\"]*\\blist-settle\\b[^\"]*\"[^>]*>(.*?)</ul>", re.S | re.I)
LI_RE = re.compile(r"<li\b[^>]*>(.*?)</li>", re.S | re.I)
SPAN_RE = re.compile(r"<span\b[^>]*>(.*?)</span>", re.S | re.I)
TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")


def _clean_html_text(s: str) -> str:
    s = TAG_RE.sub("", s)
    s = s.replace("\xa0", " ")
    s = s.replace("&nbsp;", " ")
    s = s.strip()
    return s


def _load_pairs(path: Path) -> List[Dict[str, str]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    pairs = data.get("pairs") or []
    out: List[Dict[str, str]] = []
    for it in pairs:
        if not isinstance(it, dict):
            continue
        base = str(it.get("base") or "").strip().upper()
        quote = str(it.get("quote") or "").strip().upper()
        if not base or quote != "USDT":
            continue
        out.append({"base": base, "quote": quote})
    return out


def _build_code(base: str) -> str:
    # cmt_<base lower>usdt
    return f"cmt_{base.lower()}usdt"

def _build_driver(headless: bool = True) -> webdriver.Chrome:
    opts = ChromeOptions()
    if headless:
        opts.add_argument("--headless=new")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument(
        "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
    return webdriver.Chrome(options=opts)

def _wait_ul_render(driver: webdriver.Chrome, timeout: float = 15.0) -> None:
    end = time.time() + timeout
    last_err: Optional[Exception] = None
    while time.time() < end:
        try:
            ul = driver.find_element(By.CSS_SELECTOR, "ul.list-settle")
            if ul and ul.is_displayed():
                # 自适应：不仅等待行数，还检测关键模式（~ / x / %）
                lis = ul.find_elements(By.CSS_SELECTOR, ":scope > li")
                if len(lis) >= 2:
                    # 检查前3-5行是否有内容模式
                    check_count = min(5, len(lis))
                    for i in range(check_count):
                        spans = lis[i].find_elements(By.CSS_SELECTOR, ":scope > span")
                        texts = [(s.text or "").strip() for s in spans]
                        if len(texts) >= 4:
                            second, third, fourth = texts[1], texts[2], texts[3]
                            if ("~" in second) or ("x" in third.lower()) or ("%" in fourth):
                                return
        except Exception as e:
            last_err = e
        time.sleep(0.3)
    raise TimeoutException(f"等待 list-settle 渲染超时: {last_err}")

def _parse_from_dom(driver: webdriver.Chrome) -> List[Dict[str, str]]:
    try:
        ul = driver.find_element(By.CSS_SELECTOR, "ul.list-settle")
    except Exception:
        return []
    items: List[Dict[str, str]] = []
    lis = ul.find_elements(By.CSS_SELECTOR, ":scope > li")
    for li in lis:
        cls = (li.get_attribute("class") or "").lower()
        if "list-title" in cls:
            continue
        spans = li.find_elements(By.CSS_SELECTOR, ":scope > span")
        texts = [(s.text or "").strip() for s in spans]
        if len(texts) >= 4:
            items.append({
                "lv": texts[0],
                "range": texts[1],
                "mlev": texts[2],
                "mmr": texts[3],
            })
        elif texts:
            items.append({f"col{i}": v for i, v in enumerate(texts)})
    return items


def _parse_ul(html: str) -> List[Dict[str, str]]:
    """解析 UL.list-settle，返回 [ {lv, range, mlev, mmr}, ... ] 字段均为字符串。"""
    m = UL_RE.search(html or "")
    if not m:
        return []
    ul_html = m.group(1)  # ul 内部
    items: List[Dict[str, str]] = []
    for li_html in LI_RE.findall(ul_html):
        # 跳过标题行：含有 list-title 或包含“档位/持仓/杠杆/维持”等关键字
        li_plain = _clean_html_text(li_html).lower()
        if ("档位" in li_plain) or ("持仓" in li_plain) or ("杠杆" in li_plain) or ("维持" in li_plain) or ("list-title" in li_html.lower()):
            continue
        spans = [
            _clean_html_text(x)
            for x in SPAN_RE.findall(li_html)
        ]
        if len(spans) >= 4:
            items.append({
                "lv": spans[0],
                "range": spans[1],
                "mlev": spans[2],
                "mmr": spans[3],
            })
        elif len(spans) > 0:
            # 容错：少列也保留原始文本
            items.append({f"col{i}": v for i, v in enumerate(spans)})
    return items


def _process_batch(bases: List[str], headless: bool, per_wait: float, render_timeout: float) -> Dict[str, Any]:
    """每个线程处理一批base，线程内复用一个driver。返回 {result, errors}。"""
    result: Dict[str, List[Dict[str, str]]] = {}
    errors: List[Dict[str, Any]] = []
    driver = _build_driver(headless=headless)
    try:
        driver.set_page_load_timeout(60)
        for base in bases:
            flat = f"{base}USDT"
            code = _build_code(base)
            url = f"{WEEX_BASE_URL}?code={code}"
            try:
                driver.get(url)
                _wait_ul_render(driver, timeout=render_timeout)
                # 自适应等到条件满足立即解析，无需固定等待；但为安全可以短暂缓冲
                if per_wait > 0:
                    time.sleep(per_wait)
                tiers = _parse_from_dom(driver)
            except TimeoutException:
                tiers = []
            except Exception:
                tiers = []

            if not tiers:
                errors.append({"symbol": flat, "code": code, "url": url, "error": "no_table"})
            result[flat] = tiers
            time.sleep(0.05)
    finally:
        driver.quit()
    return {"result": result, "errors": errors}


def _chunk_list(items: List[str], chunks: int) -> List[List[str]]:
    if chunks <= 1:
        return [items]
    n = max(1, len(items) // chunks + (1 if len(items) % chunks else 0))
    out: List[List[str]] = []
    for i in range(0, len(items), n):
        out.append(items[i:i+n])
    return out


def main(headless: bool = True, per_wait: float = 0.8, render_timeout: float = 15.0, concurrency: int = 4) -> None:
    pairs = _load_pairs(SURF_PAIRS_JSON)
    bases = [it["base"] for it in pairs]

    batches = _chunk_list(bases, concurrency)

    def runner(batch: List[str]) -> Dict[str, Any]:
        return _process_batch(batch, headless=headless, per_wait=per_wait, render_timeout=render_timeout)

    mt_results = run_multithread(func=runner, data_list=batches, max_workers=concurrency, show_progress=True)

    merged_result: Dict[str, List[Dict[str, str]]] = {}
    merged_errors: List[Dict[str, Any]] = []
    for r in mt_results:
        if not isinstance(r, dict):
            continue
        merged_result.update(r.get("result", {}))
        merged_errors.extend(r.get("errors", []))

    OUT_JSON.write_text(json.dumps(merged_result, ensure_ascii=False, indent=2), encoding="utf-8")
    meta = {
        "source": str(WEEX_BASE_URL),
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "count_symbols": len(merged_result),
        "count_errors": len(merged_errors),
        "errors": merged_errors[:200],
        "note": "所有字段均为字符串，直接来自页面展示（lv, range, mlev, mmr）",
        "headless": headless,
        "render_timeout": render_timeout,
        "per_wait": per_wait,
        "concurrency": concurrency,
        "batches": len(batches),
    }
    OUT_META.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已写出: {OUT_JSON} ({len(merged_result)} symbols), meta: {OUT_META} (errors={len(merged_errors)})")


if __name__ == "__main__":
    # 默认使用并发多实例+自适应等待。需要观察可设 headless=False。
    main(headless=True, per_wait=0.8, render_timeout=15.0, concurrency=4)
