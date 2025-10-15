from __future__ import annotations

import sys
import subprocess
import time
from pathlib import Path
import os
import logging
import random
import traceback

def _resolve_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path.cwd()
    return Path(__file__).resolve().parent

def _resolve_python(base_dir: Path) -> str:
    if getattr(sys, "frozen", False):
        venv_py = base_dir / "venv" / "Scripts" / "python.exe"
        if venv_py.exists():
            return str(venv_py)
        return "python"
    return sys.executable or "python"

BASE_DIR = _resolve_base_dir()
PY = _resolve_python(BASE_DIR)

# 路径（以项目根 currency_leverage_collection 为基准）
# 优先使用基于 API 的币种抓取
FETCH_SYMBOLS = BASE_DIR / "currencyGet_surf" / "fetch_symbols_api.py"
DATAGET_MAIN = BASE_DIR / "dataGet" / "dataGet_main.py"
TABLE_MAKE = BASE_DIR / "tableMake" / "tableMake_main.py"
PUBLISH_PS1 = BASE_DIR / "scripts" / "publish_latest_json.ps1"

LOG_DIR = BASE_DIR / "result" / "_logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
APP_LOG = LOG_DIR / "app.log"
LOCK_PATH = BASE_DIR / "app.lock"


def run_py(name: str, path: Path, args: list[str] | None = None) -> int:
    args = args or []
    if not path.exists():
        print(f"[X] 脚本不存在: {path}")
        logging.error("脚本不存在: %s", path)
        return 127
    ts = time.strftime("%Y%m%d_%H%M%S")
    outp = LOG_DIR / f"{ts}_{name}.stdout.log"
    errp = LOG_DIR / f"{ts}_{name}.stderr.log"
    print(f"[+] 运行 {name}: {path}")
    logging.info("运行子任务 %s: %s", name, path)
    with outp.open("w", encoding="utf-8", buffering=1) as fo, errp.open("w", encoding="utf-8", buffering=1) as fe:
        # 统一以项目根为工作目录，保证包导入如 `from config import settings` 正常
        rc = subprocess.call([PY, str(path), *args], stdout=fo, stderr=fe, cwd=str(BASE_DIR))
    print(f"[=] 完成 {name}, rc={rc}, 日志: {outp.name} / {errp.name}")
    logging.info("完成子任务 %s, rc=%s", name, rc)
    return rc


def run_ps1(name: str, path: Path, args: list[str] | None = None) -> int:
    args = args or []
    if not path.exists():
        print(f"[X] 脚本不存在: {path}")
        logging.error("脚本不存在: %s", path)
        return 127
    ts = time.strftime("%Y%m%d_%H%M%S")
    outp = LOG_DIR / f"{ts}_{name}.stdout.log"
    errp = LOG_DIR / f"{ts}_{name}.stderr.log"
    print(f"[+] 运行 {name}: {path}")
    logging.info("运行子任务 %s: %s", name, path)
    with outp.open("w", encoding="utf-8", buffering=1) as fo, errp.open("w", encoding="utf-8", buffering=1) as fe:
        rc = subprocess.call(["powershell", "-ExecutionPolicy", "Bypass", "-File", str(path), *args], stdout=fo, stderr=fe, cwd=str(BASE_DIR))
    print(f"[=] 完成 {name}, rc={rc}, 日志: {outp.name} / {errp.name}")
    logging.info("完成子任务 %s, rc=%s", name, rc)
    return rc


def acquire_lock() -> bool:
    try:
        fd = os.open(LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode("utf-8"))
        os.close(fd)
        return True
    except FileExistsError:
        return False
    except Exception:
        logging.exception("创建锁文件失败")
        return False


def release_lock() -> None:
    try:
        if LOCK_PATH.exists():
            LOCK_PATH.unlink(missing_ok=True)
    except Exception:
        logging.exception("移除锁文件失败")


def run_once() -> int:
    start = time.time()

    # 1) 获取目标币种集合
    rc = run_py("fetch_symbols", FETCH_SYMBOLS)
    if rc != 0:
        print("[!] fetch_symbols 失败")
        logging.error("fetch_symbols 失败, rc=%s", rc)
        return rc

    # 2) 并行获取四所数据
    rc = run_py("dataGet_main", DATAGET_MAIN)
    if rc != 0:
        print("[!] dataGet_main 失败")
        logging.error("dataGet_main 失败, rc=%s", rc)
        return rc

    # 3) 制作表格并写库（tableMake_main 内部包含：生成 Excel → 建最小表 → Excel 入库）
    rc = run_py("tableMake_main", TABLE_MAKE)
    if rc != 0:
        print("[!] tableMake 失败")
        logging.error("tableMake 失败, rc=%s", rc)
        return rc
    prc = run_ps1("publish_latest_json", PUBLISH_PS1)
    if prc != 0:
        print("[!] publish_latest_json 失败")
        logging.error("publish_latest_json 失败, rc=%s", prc)
        return prc

    dur = time.time() - start
    print(f"[OK] 全流程完成，用时 {dur:.1f}s")
    logging.info("全流程完成, 用时 %.1fs", dur)
    return 0


def main() -> int:
    # 基础日志：控制台 + 文件
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(APP_LOG, encoding="utf-8")
        ]
    )

    if not acquire_lock():
        print("[!] 已有实例在运行，当前进程退出")
        logging.error("已有实例在运行，退出")
        return 1

    logging.info("服务启动，进入常驻循环：每1小时执行一次")
    try:
        while True:
            try:
                rc = run_once()
                if rc != 0:
                    logging.warning("一次执行返回非零 rc=%s", rc)
            except Exception:
                logging.error("一次执行抛出异常：\n%s", traceback.format_exc())
            # 抖动 0~60 秒，避免固定卡点
            jitter = random.randint(0, 60)
            sleep_seconds = 3600 + jitter
            logging.info("休眠 %s 秒后再次执行", sleep_seconds)
            time.sleep(sleep_seconds)
    except KeyboardInterrupt:
        logging.info("收到中断信号，准备退出")
    finally:
        release_lock()
    return 0


if __name__ == "__main__":
    sys.exit(main())
