from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from selenium import webdriver
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.common.by import By
from selenium.common.exceptions import TimeoutException

from config import settings

# 默认风险限额页（可通过 --url 覆盖）
WEEX_DEFAULT_URL = "https://www.weex.com/zh-CN/futures/introduction/risk-limit?code=cmt_btcusdt"
# 风险限额列表 XPath（用户提供）
WEEX_TABLE_XPATH = "/html/body/div[5]/div/div/div[2]/div/div[2]/div/ul"
WEEX_DROPDOWN_XPATH = "/html/body/div[5]/div/div/div[2]/div/div[2]/div/div[1]"  # 下拉容器（含当前币对与列表）
WEEX_DROPDOWN_TOGGLE_XPATH = WEEX_DROPDOWN_XPATH + "/div[1]/span"  # 可点击的当前币对 span
WEEX_DROPDOWN_LIST_XPATH = WEEX_DROPDOWN_XPATH + "/div[2]//ul/li"  # 列表项 li


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


def _inject_hooks(driver: webdriver.Chrome, capture_all: bool = True) -> None:
    hook_js = r"""
    (function(){
      const __captured = { requests: [], resources: [], initial: null, responses: [] };
      const CAPTURE_ALL = %CAPTURE_ALL_FLAG%;
      function shouldCapture(url){
        try {
          const u = String(url);
          if (CAPTURE_ALL) return true;
          return (u.includes('weex.com') || u.includes('janapw.com'));
        } catch(e){ return false; }
      }
      const origFetch = window.fetch;
      window.fetch = async function(input, init){
        try {
          const url = (typeof input === 'string') ? input : (input && input.url) || '';
          const method = (init && init.method) || 'GET';
          let reqBody = null;
          if (init && init.body) {
            try { reqBody = typeof init.body === 'string' ? init.body : JSON.stringify(init.body); } catch (e) {}
          }
          const startedAt = Date.now();
          const resp = await origFetch.apply(this, arguments);
          const cloned = resp.clone();
          let text = '';
          try { text = await cloned.text(); } catch(e) {}
          __captured.requests.push({ type: 'fetch', url, method, reqBody, status: resp.status, startedAt, duration: Date.now()-startedAt, respLength: (text||'').length });
          if (shouldCapture(url)){
            let body = text || '';
            if (body.length > 2_000_000) body = body.slice(0, 2_000_000);
            let json = null;
            try { json = JSON.parse(body); } catch(e) { json = null; }
            __captured.responses.push({ url, status: resp.status, length: (text||'').length, json, text: json ? undefined : body });
          }
          return resp;
        } catch(e) {
          __captured.requests.push({ type: 'fetch', url: String(input), error: String(e) });
          throw e;
        }
      };

      const OrigXHR = window.XMLHttpRequest;
      function HookXHR(){
        const xhr = new OrigXHR();
        let url = '', method = 'GET', startedAt = 0, body = null;
        const origOpen = xhr.open;
        const origSend = xhr.send;
        xhr.open = function(m, u){ method = m; url = u; return origOpen.apply(xhr, arguments); };
        xhr.send = function(b){ startedAt = Date.now(); body = b; xhr.addEventListener('loadend', function(){
            __captured.requests.push({ type: 'xhr', url, method, reqBody: body ? String(body) : null, status: xhr.status, startedAt, duration: Date.now()-startedAt, respLength: (xhr.responseText||'').length });
            if (shouldCapture(url)){
              let t = xhr.responseText || '';
              if (t.length > 2_000_000) t = t.slice(0, 2_000_000);
              let json = null;
              try { json = JSON.parse(t); } catch(e) { json = null; }
              __captured.responses.push({ url, status: xhr.status, length: (xhr.responseText||'').length, json, text: json ? undefined : t });
            }
          });
          return origSend.apply(xhr, arguments);
        };
        return xhr;
      }
      window.XMLHttpRequest = HookXHR;

      try {
        const cand = (window.__NEXT_DATA__ || window.__APP_DATA__ || window.__INITIAL_STATE__ || null);
        if (cand) __captured.initial = cand;
      } catch(e) {}

      const perf = (window.performance && performance.getEntries) ? performance.getEntries() : [];
      try { __captured.resources = (perf || []).map(p => ({name:p.name, initiatorType:p.initiatorType, duration:p.duration})); } catch(e) { __captured.resources = []; }

      Object.defineProperty(window, '__CAPTURED_WEEX__', { value: __captured, writable: false });
    })();
    """
    # Python 三元表达式修正
    cap_flag = 'true' if capture_all else 'false'
    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": hook_js.replace('%CAPTURE_ALL_FLAG%', cap_flag)})


def _wait_for_table(driver: webdriver.Chrome, xpath: str, timeout: float = 20.0) -> None:
    end = time.time() + timeout
    last_err: Exception | None = None
    while time.time() < end:
        try:
            el = driver.find_element(By.XPATH, xpath)
            if el and el.is_displayed():
                return
        except Exception as e:
            last_err = e
        time.sleep(0.5)
    raise TimeoutException(f"等待元素渲染超时: {last_err}")


def _collect_resources(driver: webdriver.Chrome) -> List[Dict[str, Any]]:
    try:
        perf_entries = driver.execute_script(
            "return (window.performance && performance.getEntries) ? performance.getEntries() : [];"
        )
        resources: List[Dict[str, Any]] = []
        for p in perf_entries or []:
            name = p.get("name", "")
            initiator = p.get("initiatorType", "")
            duration = p.get("duration", 0)
            if initiator in {"xmlhttprequest", "fetch", "img", "script"}:
                resources.append({"name": name, "initiatorType": initiator, "duration": duration})
        return resources
    except Exception:
        return []


def _auto_scroll(driver: webdriver.Chrome, total_steps: int = 8, step_delay: float = 0.6) -> None:
    try:
        for i in range(total_steps):
            driver.execute_script("window.scrollBy(0, Math.max(200, window.innerHeight*0.8));")
            time.sleep(step_delay)
        driver.execute_script("window.scrollTo(0, 0);")
    except Exception:
        pass


def _safe_click(driver: webdriver.Chrome, xpath: str, wait_after: float = 0.3) -> bool:
    try:
        el = driver.find_element(By.XPATH, xpath)
        el.click()
        time.sleep(wait_after)
        return True
    except Exception:
        return False


def _list_coin_items(driver: webdriver.Chrome) -> List[str]:
    try:
        items = driver.find_elements(By.XPATH, WEEX_DROPDOWN_LIST_XPATH)
        return [f"({i}) " + (it.text or "").strip() for i, it in enumerate(items)]
    except Exception:
        return []


def _click_through_coins(driver: webdriver.Chrome, max_clicks: int, per_wait: float, out_dir: Path) -> None:
    """尝试点击下拉中的前 max_clicks 个币对，每次点击后等待，并保存阶段性快照。"""
    clicks_dir = out_dir / "clicks"
    clicks_dir.mkdir(parents=True, exist_ok=True)

    # 打开下拉
    _safe_click(driver, WEEX_DROPDOWN_TOGGLE_XPATH, wait_after=0.5)
    try:
        items = driver.find_elements(By.XPATH, WEEX_DROPDOWN_LIST_XPATH)
    except Exception:
        items = []

    count = min(max_clicks, len(items)) if max_clicks > 0 else 0
    for i in range(count):
        try:
            # 再次确保下拉展开
            _safe_click(driver, WEEX_DROPDOWN_TOGGLE_XPATH, wait_after=0.2)
            items = driver.find_elements(By.XPATH, WEEX_DROPDOWN_LIST_XPATH)
            if i >= len(items):
                break
            label = (items[i].text or "").strip().replace("/", "_") or f"item{i}"
            items[i].click()
            time.sleep(per_wait)
            # 每次点击后抓取一次快照
            cap = driver.execute_script("return window.__CAPTURED_WEEX__ || { requests: [], resources: [], initial: null, responses: [] };")
            (clicks_dir / f"_captured_full_click_{i:02d}_{label}.json").write_text(json.dumps(cap, ensure_ascii=False, indent=2), encoding="utf-8")
            # 简表
            lines: List[str] = []
            for idx, r in enumerate(cap.get("requests", []), start=1):
                method = r.get("method") or r.get("type") or "GET"
                status = r.get("status")
                dur = r.get("duration")
                url = r.get("url")
                lines.append(f"{idx:4d}→{method} {status} {dur}ms {url}")
            (clicks_dir / f"_cdp_requests_click_{i:02d}_{label}.txt").write_text("\n".join(lines), encoding="utf-8")
        except Exception:
            continue


def _save_outputs(out_dir: Path, captured: Dict[str, Any], resources: List[Dict[str, Any]], page_html: str) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    # 简表
    lines: List[str] = []
    for idx, r in enumerate(captured.get("requests", []), start=1):
        method = r.get("method") or r.get("type") or "GET"
        status = r.get("status")
        dur = r.get("duration")
        url = r.get("url")
        lines.append(f"{idx:4d}→{method} {status} {dur}ms {url}")
    (out_dir / "_cdp_requests.txt").write_text("\n".join(lines), encoding="utf-8")

    # 资源
    (out_dir / "_resources.json").write_text(json.dumps(resources, ensure_ascii=False, indent=2), encoding="utf-8")

    # 全量
    (out_dir / "_captured_full.json").write_text(json.dumps(captured, ensure_ascii=False, indent=2), encoding="utf-8")

    # 页面
    (out_dir / "page.html").write_text(page_html or "", encoding="utf-8")


def run_probe(url: Optional[str] = None, headless: Optional[bool] = None, wait_seconds: float = 10.0, wait_xpath: Optional[str] = None, capture_all: bool = True, scroll_steps: int = 20, click_coins: int = 20, per_wait: float = 1.5) -> Path:
    if headless is None:
        # 优先使用 WEEX_HEADLESS，其次回退 BINANCE_HEADLESS
        try:
            headless = bool(getattr(settings, "WEEX_HEADLESS"))
        except Exception:
            try:
                headless = bool(getattr(settings, "BINANCE_HEADLESS", True))
            except Exception:
                headless = True
    target = url or WEEX_DEFAULT_URL

    out_base = settings.DATAGET_OUTPUT_DIR / "weex" / "probe"
    out_dir = out_base / time.strftime("%Y%m%d_%H%M%S")

    driver = _build_driver(headless=headless)
    try:
        _inject_hooks(driver, capture_all=capture_all)
        driver.set_page_load_timeout(60)
        driver.get(target)
        # 可选等待特定元素（表格）
        if wait_xpath:
            try:
                _wait_for_table(driver, wait_xpath, timeout=25)
            except Exception:
                pass
        _auto_scroll(driver, total_steps=scroll_steps)
        # 可选：自动点击下拉中的若干币种以触发潜在请求
        if click_coins and click_coins > 0:
            _click_through_coins(driver, max_clicks=click_coins, per_wait=per_wait, out_dir=out_dir)
        time.sleep(wait_seconds)
        resources = _collect_resources(driver)
        captured = driver.execute_script("return window.__CAPTURED_WEEX__ || { requests: [], resources: [], initial: null, responses: [] };")
        page_html = driver.page_source
        _save_outputs(out_dir, captured, resources, page_html)
        return out_dir
    finally:
        driver.quit()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="WEEX 风险限额探针（CDP 注入），输出到 data/dataGet_api/weex/probe/<timestamp>/")
    parser.add_argument("--url", default=None, help="目标页面 URL（默认风险限额页）")
    parser.add_argument("--wait", type=float, default=10.0, help="进入页面后的等待秒数（默认10s）")
    parser.add_argument("--wait-xpath", default=WEEX_TABLE_XPATH, help="进入页面后等待渲染的元素 XPath")
    parser.add_argument("--no-capture-all", action="store_true", help="关闭捕获所有请求响应（默认开启）")
    parser.add_argument("--scroll-steps", type=int, default=20, help="自动滚动步数，用于触发懒加载（默认20步）")
    # 运行模式：默认有头；提供 --no-headed 以强制无头，也保留 --headed 兼容
    parser.add_argument("--headed", action="store_true", help="强制有头模式（默认即有头）")
    parser.add_argument("--no-headed", action="store_true", help="强制无头模式（覆盖默认有头）")
    # 自动点击：默认点击 20 个币对，每次等待 1.5s
    parser.add_argument("--click-coins", type=int, default=20, help="自动点击下拉中的前 N 个币对以触发请求（默认20）")
    parser.add_argument("--per-wait", type=float, default=1.5, help="每次点击后等待秒数（默认1.5s）")
    args = parser.parse_args()

    # 解析有头/无头：默认有头；--no-headed 优先将其置为无头；--headed 可显式指定有头
    headless_arg: Optional[bool]
    if args.no_headed:
        headless_arg = True
    elif args.headed:
        headless_arg = False
    else:
        headless_arg = False

    out = run_probe(
        url=args.url,
        headless=headless_arg,
        wait_seconds=args.wait,
        wait_xpath=args.wait_xpath,
        capture_all=(not args.no_capture_all),
        scroll_steps=args.scroll_steps,
        click_coins=args.click_coins,
        per_wait=args.per_wait,
    )
    print(f"已保存探针输出: {out}")
