import json
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Tuple
from config import settings

# Selenium imports必须在文件顶部
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.edge.service import Service as EdgeService
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.edge.options import Options as EdgeOptions
from selenium.common.exceptions import SessionNotCreatedException, WebDriverException


PAIR_RE = re.compile(r"^[A-Z0-9-]+/[A-Z0-9-]+$")


@dataclass
class SurfPair:
    pair: str
    base: str
    quote: str


def _build_driver() -> webdriver.Edge:
    opts = EdgeOptions()
    if settings.SURF_HEADLESS:
        opts.add_argument("--headless=new")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1200,1600")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    
    # 仅使用本地提供的 EdgeDriver（不做任何自动下载）
    service = EdgeService(executable_path=settings.SURF_DRIVER_PATH)
    driver = webdriver.Edge(service=service, options=opts)
    driver.set_page_load_timeout(settings.SURF_TIMEOUT)
    return driver


STATS_CONTAINER_XPATH = "/html/body/div[1]/div/main/div/div[2]/div[2]/div[2]/div/div/div[2]/div"


def _find_pairs_container(driver: webdriver.Edge):
    # 优先使用用户提供的父节点 XPath
    try:
        return WebDriverWait(driver, settings.SURF_TIMEOUT).until(
            EC.presence_of_element_located((By.XPATH, STATS_CONTAINER_XPATH))
        )
    except Exception:
        # 兜底：尝试根据“交易对”列标题上层容器反向定位
        try:
            header = WebDriverWait(driver, 5).until(
                EC.presence_of_element_located((By.XPATH, "//div[contains(text(),'交易对') or contains(text(),'Pairs')]/ancestor::div[3]"))
            )
            return header
        except Exception:
            return None


def _wait_for_loaded(driver: webdriver.Edge) -> None:
    # 先等待文档就绪
    WebDriverWait(driver, settings.SURF_TIMEOUT).until(
        lambda d: d.execute_script("return document.readyState") == "complete"
    )
    time.sleep(1.0)

    # 等待容器出现或至少3个包含斜杠的元素
    container = _find_pairs_container(driver)
    if container is None:
        WebDriverWait(driver, settings.SURF_TIMEOUT).until(
            lambda d: len(d.find_elements(By.XPATH, "//*[contains(text(), '/USDT') or contains(text(), '/')]")) >= 3
        )


def _dismiss_popups(driver: webdriver.Edge) -> None:
    # 尝试关闭可能的 cookie/同意弹窗，容错点击
    candidates = [
        "//button[contains(., '同意')]",
        "//button[contains(., '接受')]",
        "//button[contains(., 'I agree')]",
        "//button[contains(., 'Accept')]",
        "//div[@role='dialog']//button[contains(., 'OK')]",
    ]
    for xp in candidates:
        try:
            btns = driver.find_elements(By.XPATH, xp)
            if btns:
                btns[0].click()
                time.sleep(0.5)
        except Exception:
            pass


def _try_switch_to_pairs_tab(driver: webdriver.Edge) -> None:
    # 有些站点使用 Tabs，确保切到“交易对/Pairs”页签
    candidates = [
        "//button[contains(., '交易对')]",
        "//div[@role='tab' and contains(., '交易对')]",
        "//button[contains(., 'Pairs')]",
        "//div[@role='tab' and contains(., 'Pairs')]",
    ]
    for xp in candidates:
        try:
            el = WebDriverWait(driver, 3).until(
                EC.element_to_be_clickable((By.XPATH, xp))
            )
            el.click()
            time.sleep(0.5)
            return
        except Exception:
            continue


def _scroll_to_load_all(driver: webdriver.Edge) -> None:
    # 先进行若干次小步滚动，触发懒加载
    for _ in range(5):
        driver.execute_script("window.scrollBy(0, 400);")
        time.sleep(0.6)

    last_height = driver.execute_script("return document.body.scrollHeight")
    last_count = len(driver.find_elements(By.XPATH, f"{STATS_CONTAINER_XPATH}//p[contains(text(), '/')] | {STATS_CONTAINER_XPATH}//div[contains(text(), '/')] | {STATS_CONTAINER_XPATH}//span[contains(text(), '/')]") )
    for i in range(settings.SURF_MAX_SCROLLS):
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(settings.SURF_SCROLL_PAUSE)
        new_height = driver.execute_script("return document.body.scrollHeight")
        new_count = len(driver.find_elements(By.XPATH, f"{STATS_CONTAINER_XPATH}//p[contains(text(), '/')] | {STATS_CONTAINER_XPATH}//div[contains(text(), '/')] | {STATS_CONTAINER_XPATH}//span[contains(text(), '/')]") )
        print(f"[scroll] step={i+1} height={new_height} items={new_count}")
        # 若页面高度和候选数量都不再增长，则结束
        if new_height == last_height and new_count == last_count:
            break
        last_height = new_height
        last_count = new_count


def _extract_pairs(driver: webdriver.Edge) -> List[SurfPair]:
    # 仅在 STATS 列表容器内寻找包含 斜杠 的文本（避免页面其他位置的干扰）
    container = _find_pairs_container(driver)
    if container is not None:
        candidates = container.find_elements(By.XPATH, ".//p[contains(text(), '/')] | .//div[contains(text(), '/')] | .//span[contains(text(), '/')]")
    else:
        candidates = driver.find_elements(By.XPATH, "//p[contains(text(), '/')] | //div[contains(text(), '/')] | //span[contains(text(), '/')]")
    pairs: List[SurfPair] = []
    seen = set()
    for el in candidates:
        text = el.text.strip().upper()
        if not text:
            continue
        if not PAIR_RE.match(text):
            continue
        try:
            base, quote = text.split("/")
        except ValueError:
            continue
        if settings.SURF_ONLY_USDT and quote != settings.SURF_QUOTE:
            continue
        key = f"{base}/{quote}"
        if key in seen:
            continue
        seen.add(key)
        pairs.append(SurfPair(pair=key, base=base, quote=quote))

    # 若 DOM 提取为空，退化为从 page_source 通过正则提取（兜底）
    if not pairs:
        html = driver.page_source.upper()
        tokens = sorted(set(re.findall(r"[A-Z0-9-]+/USDT", html)))
        for tok in tokens:
            base, quote = tok.split("/")
            if settings.SURF_ONLY_USDT and quote != settings.SURF_QUOTE:
                continue
            if tok not in seen:
                seen.add(tok)
                pairs.append(SurfPair(pair=tok, base=base, quote=quote))
    
    # 仍然为空，再通过 JS 抓取所有可见文本进行二次匹配
    if not pairs:
        try:
            texts: List[str] = driver.execute_script(
                "return Array.from(document.querySelectorAll('p,div,span,a')).map(e=> (e.innerText||'').toUpperCase()).filter(Boolean);"
            )
            tokens = sorted({t for t in texts if PAIR_RE.match(t)})
            for tok in tokens:
                base, quote = tok.split("/")
                if settings.SURF_ONLY_USDT and quote != settings.SURF_QUOTE:
                    continue
                if tok not in seen:
                    seen.add(tok)
                    pairs.append(SurfPair(pair=tok, base=base, quote=quote))
        except Exception:
            pass
    return pairs


def _ensure_output_dir() -> None:
    settings.DATA_DIR.mkdir(parents=True, exist_ok=True)


def _save_outputs(pairs: List[SurfPair]) -> Tuple[Path, Path, Path]:
    _ensure_output_dir()

    # JSON
    payload = {
        "source_url": settings.SURF_STATS_URL,
        "collected_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S%z"),
        "pairs": [p.__dict__ for p in pairs],
        "count": len(pairs),
    }
    settings.OUTPUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    # CSV
    lines = ["pair,base,quote"] + [f"{p.pair},{p.base},{p.quote}" for p in pairs]
    settings.OUTPUT_CSV.write_text("\n".join(lines), encoding="utf-8")

    # TXT（仅 base 去重）
    bases = sorted({p.base for p in pairs})
    settings.OUTPUT_TXT.write_text("\n".join(bases), encoding="utf-8")

    return settings.OUTPUT_JSON, settings.OUTPUT_CSV, settings.OUTPUT_TXT


def fetch_and_save() -> Tuple[Path, Path, Path]:
    driver = _build_driver()
    try:
        driver.get(settings.SURF_STATS_URL)
        _wait_for_loaded(driver)
        _dismiss_popups(driver)
        _try_switch_to_pairs_tab(driver)
        _scroll_to_load_all(driver)
        pairs = _extract_pairs(driver)
        # 若仍然没有抓到，保存调试信息
        if not pairs:
            debug_dir = settings.DATA_DIR / "debug"
            debug_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            png = debug_dir / f"stats_{ts}.png"
            html = debug_dir / f"stats_{ts}.html"
            try:
                driver.save_screenshot(str(png))
            except Exception:
                pass
            try:
                html.write_text(driver.page_source, encoding="utf-8")
            except Exception:
                pass
        return _save_outputs(pairs)
    finally:
        driver.quit()


if __name__ == "__main__":
    j, c, t = fetch_and_save()
    print(f"Saved: {j}")
    print(f"Saved: {c}")
    print(f"Saved: {t}")
