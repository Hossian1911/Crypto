from __future__ import annotations

import json
import time
from pathlib import Path
from typing import List, Dict, Any

from selenium import webdriver
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.common.by import By
from selenium.common.exceptions import TimeoutException

from config import settings

# 目标页面（可替换 symbol 参数）
MEXC_RISK_URL = "https://www.mexc.com/zh-MY/futures/information/risk_limit?lang=zh-MY&symbol=BTC_USDT"
# 等待的表格 XPath（你提供的）
TABLE_XPATH = "/html/body/div[3]/div/div/div[2]/div/div/div[2]/div[2]/table"


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

    driver = webdriver.Chrome(options=opts)

    # 预注入：拦截 fetch 与 XHR
    hook_js = r"""
    (function(){
      const __captured = { requests: [], resources: [], initial: null, responses: [] };
      function shouldCapture(url){
        try {
          const u = String(url);
          return (
            u.includes('futures.mexc.com/api/v1/contract/detailV2') ||
            u.includes('/risk') || u.includes('risk_limit')
          );
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
          // 条件保存响应体（限制大小）
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

      // 初始数据（若站点注入了全局数据）
      try {
        const cand = (window.__NEXT_DATA__ || window.__APP_DATA__ || window.__INITIAL_STATE__ || null);
        if (cand) __captured.initial = cand;
      } catch(e){}

      Object.defineProperty(window, '__CAPTURED_MEXC__', { value: __captured, writable: false });
    })();
    """

    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": hook_js})
    return driver


def _wait_for_table(driver: webdriver.Chrome, timeout: float = 25.0) -> None:
    end = time.time() + timeout
    last_err: Exception | None = None
    while time.time() < end:
        try:
            el = driver.find_element(By.XPATH, TABLE_XPATH)
            if el and el.is_displayed():
                return
        except Exception as e:
            last_err = e
        time.sleep(0.5)
    raise TimeoutException(f"等待表格渲染超时: {last_err}")


def _collect_resources(driver: webdriver.Chrome) -> List[str]:
    get_entries_js = r"""
      (function(){
        try {
          const entries = performance.getEntriesByType('resource')||[];
          return entries.map(e => e.name).slice(0, 2000);
        } catch(e) { return []; }
      })();
    """
    try:
        return driver.execute_script(get_entries_js) or []
    except Exception:
        return []


def save_outputs(out_dir: Path, captured: Dict[str, Any], resources: List[str]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    # 请求日志（精简）
    req_lines: List[str] = []
    for r in captured.get("requests", []):
        url = r.get("url", "")
        method = r.get("method", "")
        status = r.get("status", "")
        dur = r.get("duration", "")
        req_lines.append(f"{method} {status} {dur}ms {url}")
    (out_dir / "_cdp_requests.txt").write_text("\n".join(req_lines), encoding="utf-8")

    # 资源时序 URL 列表
    (out_dir / "_resources.txt").write_text("\n".join(resources), encoding="utf-8")

    # 初始数据（若有）
    initial = captured.get("initial")
    if initial is not None:
        (out_dir / "__initial_data.json").write_text(json.dumps(initial, ensure_ascii=False, indent=2), encoding="utf-8")

    # 完整快照
    (out_dir / "_captured_full.json").write_text(json.dumps(captured, ensure_ascii=False, indent=2), encoding="utf-8")


def run_probe(headless: bool = True, url: str | None = None) -> Path:
    out_base = settings.DATAGET_OUTPUT_DIR / "mexc" / "probe"
    out_dir = out_base / time.strftime("%Y%m%d_%H%M%S")
    driver = _build_driver(headless=headless)
    try:
        target = url or MEXC_RISK_URL
        driver.set_page_load_timeout(60)
        driver.get(target)
        _wait_for_table(driver, timeout=30)
        # 等待页面触发相关请求
        time.sleep(3)
        resources = _collect_resources(driver)
        captured = driver.execute_script("return window.__CAPTURED_MEXC__ || { requests: [], resources: [], initial: null };")
        # 保存页面 HTML 以排查 SSR
        try:
            (out_dir / "page.html").write_text(driver.page_source, encoding="utf-8")
        except Exception:
            pass
        save_outputs(out_dir, captured, resources)
        return out_dir
    finally:
        driver.quit()


if __name__ == "__main__":
    headless = True
    try:
        headless = bool(getattr(settings, "BINANCE_HEADLESS", True))
    except Exception:
        pass

    target_dir = run_probe(headless=headless, url=None)
    print(f"已保存探针输出到: {target_dir}")
