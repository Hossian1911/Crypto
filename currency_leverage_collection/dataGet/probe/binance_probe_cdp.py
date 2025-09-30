from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from selenium import webdriver
from selenium.webdriver.chrome.options import Options as ChromeOptions

from config import settings

# 默认目标页（可通过 --url 覆盖）
BINANCE_DEFAULT_URL = "https://www.binance.com/zh-CN/futures/BTCUSDT"


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


def _inject_hooks(driver: webdriver.Chrome) -> None:
    hook_js = r"""
    (function(){
      const __captured = { requests: [], resources: [], initial: null, responses: [] };
      function shouldCapture(url){
        try {
          const u = String(url);
          return (
            u.includes('/fapi/') || u.includes('/dapi/') ||
            u.includes('/futures') || u.includes('risk') || u.includes('leverage')
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

      Object.defineProperty(window, '__CAPTURED_BINANCE__', { value: __captured, writable: false });
    })();
    """
    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": hook_js})


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


def run_probe(url: Optional[str] = None, headless: Optional[bool] = None, wait_seconds: float = 3.0) -> Path:
    if headless is None:
        try:
            headless = bool(getattr(settings, "BINANCE_HEADLESS", True))
        except Exception:
            headless = True
    target = url or BINANCE_DEFAULT_URL

    out_base = settings.DATAGET_OUTPUT_DIR / "binance" / "probe"
    out_dir = out_base / time.strftime("%Y%m%d_%H%M%S")

    driver = _build_driver(headless=headless)
    try:
        _inject_hooks(driver)
        driver.set_page_load_timeout(60)
        driver.get(target)
        time.sleep(wait_seconds)
        resources = _collect_resources(driver)
        captured = driver.execute_script("return window.__CAPTURED_BINANCE__ || { requests: [], resources: [], initial: null, responses: [] };")
        page_html = driver.page_source
        _save_outputs(out_dir, captured, resources, page_html)
        return out_dir
    finally:
        driver.quit()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Binance 专用 Probe（CDP 注入），输出到 data/dataGet_api/binance/probe/<timestamp>/")
    parser.add_argument("--url", default=None, help="目标页面 URL（默认 BTCUSDT U 本位期货页）")
    parser.add_argument("--wait", type=float, default=3.0, help="进入页面后的等待秒数")
    parser.add_argument("--headed", action="store_true", help="打开有头模式（默认跟随 BINANCE_HEADLESS 配置）")
    args = parser.parse_args()

    out = run_probe(url=args.url, headless=(False if args.headed else None), wait_seconds=args.wait)
    print(f"已保存探针输出: {out}")
