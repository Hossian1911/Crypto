from __future__ import annotations

import sys
import subprocess
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
PY = sys.executable or "python"

# 路径（以项目根 currency_leverage_collection 为基准）
FETCH_SYMBOLS = BASE_DIR / "currencyGet_surf" / "fetch_symbols.py"
DATAGET_MAIN = BASE_DIR / "dataGet" / "dataGet_main.py"
TABLE_MAKE = BASE_DIR / "tableMake" / "tableMake.py"

LOG_DIR = BASE_DIR / "result" / "_logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)


def run_py(name: str, path: Path, args: list[str] | None = None) -> int:
    args = args or []
    if not path.exists():
        print(f"[X] 脚本不存在: {path}")
        return 127
    ts = time.strftime("%Y%m%d_%H%M%S")
    outp = LOG_DIR / f"{ts}_{name}.stdout.log"
    errp = LOG_DIR / f"{ts}_{name}.stderr.log"
    print(f"[+] 运行 {name}: {path}")
    with outp.open("w", encoding="utf-8", buffering=1) as fo, errp.open("w", encoding="utf-8", buffering=1) as fe:
        rc = subprocess.call([PY, str(path), *args], stdout=fo, stderr=fe, cwd=str(path.parent))
    print(f"[=] 完成 {name}, rc={rc}, 日志: {outp.name} / {errp.name}")
    return rc


def main() -> int:
    start = time.time()

    # 1) 获取目标币种集合
    rc = run_py("fetch_symbols", FETCH_SYMBOLS)
    if rc != 0:
        print("[!] fetch_symbols 失败")
        return rc

    # 2) 并行获取四所数据
    rc = run_py("dataGet_main", DATAGET_MAIN)
    if rc != 0:
        print("[!] dataGet_main 失败")
        return rc

    # 3) 制作表格
    rc = run_py("tableMake", TABLE_MAKE)
    if rc != 0:
        print("[!] tableMake 失败")
        return rc

    dur = time.time() - start
    print(f"[OK] 全流程完成，用时 {dur:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
