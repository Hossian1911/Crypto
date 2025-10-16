from __future__ import annotations

import sys
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List, Tuple

BASE_DIR = Path(__file__).resolve().parent
PYTHON = sys.executable or "python"

# 五个数据获取脚本（按需调整路径）
SCRIPTS = {
    "binance": BASE_DIR / "binance_brackets_fetch.py",
    "bybit": BASE_DIR / "bybit_brackets_fetch.py",
    "mexc": BASE_DIR / "mexc_brackets_fetch.py",
    "weex": BASE_DIR / "weex_brackets_fetch.py",
    "surf": BASE_DIR / "surf_limits_fetch.py",
}

LOG_DIR = BASE_DIR.parent / "data" / "dataGet_api" / "_logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)


def _run_script(name: str, script_path: Path, extra_args: List[str] | None = None) -> Tuple[str, int, Path, Path, float]:
    start = time.time()
    extra_args = extra_args or []
    if not script_path.exists():
        raise FileNotFoundError(f"脚本不存在: {script_path}")

    stdout_path = LOG_DIR / f"{name}.stdout.log"
    stderr_path = LOG_DIR / f"{name}.stderr.log"

    with stdout_path.open("w", encoding="utf-8", buffering=1) as f_out, stderr_path.open("w", encoding="utf-8", buffering=1) as f_err:
        cmd = [PYTHON, str(script_path), *extra_args]
        proc = subprocess.Popen(cmd, stdout=f_out, stderr=f_err, cwd=str(BASE_DIR))
        rc = proc.wait()
    dur = time.time() - start
    return name, rc, stdout_path, stderr_path, dur


def main(parallel: int = 4) -> None:
    # 先抓取 CMC Top20（过滤 USDT/USDC）
    cmc_script = BASE_DIR / "cmc_top20_fetch.py"
    if cmc_script.exists():
        print("[cmc_top20] 开始获取 CoinMarketCap Top20（排除 USDT/USDC）…")
        name, rc, outp, errp, dur = _run_script("cmc_top20", cmc_script, [])
        status = "OK" if rc == 0 else f"FAIL({rc})"
        print(f"[cmc_top20] 完成: {status} 用时 {dur:.1f}s  日志: {outp.name} / {errp.name}")
    else:
        print("[cmc_top20] 跳过：脚本不存在")

    # 任务列表：可在此为各脚本添加自定义参数
    jobs: List[Tuple[str, Path, List[str]]] = [
        ("binance", SCRIPTS["binance"], []),
        ("bybit", SCRIPTS["bybit"], []),
        ("mexc", SCRIPTS["mexc"], []),
        ("weex", SCRIPTS["weex"], []),
        ("surf", SCRIPTS["surf"], []),
    ]

    print(f"将并行运行 {len(jobs)} 个数据获取任务（线程池大小={parallel}）...\n日志目录: {LOG_DIR}")

    results: Dict[str, Dict[str, str]] = {}
    with ThreadPoolExecutor(max_workers=max(1, parallel)) as ex:
        fut_map = {ex.submit(_run_script, name, path, args): name for (name, path, args) in jobs}
        for fut in as_completed(fut_map):
            name = fut_map[fut]
            try:
                n, rc, outp, errp, dur = fut.result()
                results[name] = {
                    "returncode": str(rc),
                    "stdout": str(outp),
                    "stderr": str(errp),
                    "duration_sec": f"{dur:.2f}",
                }
                status = "OK" if rc == 0 else f"FAIL({rc})"
                print(f"[{name}] 完成: {status} 用时 {dur:.1f}s  日志: {outp.name} / {errp.name}")
            except Exception as e:
                results[name] = {
                    "returncode": "EXC",
                    "stdout": "",
                    "stderr": str(e),
                    "duration_sec": "-1",
                }
                print(f"[{name}] 异常: {e}")

    print("\n=== 汇总 ===")
    for name in SCRIPTS.keys():
        r = results.get(name)
        if not r:
            print(f"- {name}: 未运行")
            continue
        print(f"- {name}: rc={r['returncode']} dur={r['duration_sec']}s out={r['stdout']} err={r['stderr']}")


if __name__ == "__main__":
    # 默认线程池容量=4，如需调整可直接改 main(parallel=..)
    main(parallel=4)
