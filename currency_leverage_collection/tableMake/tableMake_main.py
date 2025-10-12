from __future__ import annotations

import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

STEPS = [
    ("生成 Excel 报表", BASE_DIR / "tableMake.py"),
    ("创建最小表结构 (symbol, exchange, max_leverage, max_size, mmr)", BASE_DIR / "setup_platform_exchanges_setting_schema.py"),
    ("从最新 Excel 写入数据库 (upsert by symbol,exchange)", BASE_DIR / "excel_write_platform_exchanges_setting.py"),
]


def run_step(title: str, script_path: Path) -> None:
    if not script_path.exists():
        raise FileNotFoundError(f"脚本不存在: {script_path}")
    print(f"\n===== {title} =====")
    cmd = [sys.executable, str(script_path)]
    proc = subprocess.run(cmd, cwd=str(BASE_DIR))
    if proc.returncode != 0:
        raise SystemExit(f"步骤失败: {title} -> 返回码 {proc.returncode}")


def main() -> None:
    print("[info] tableMake_main 启动，顺序执行 3 个步骤…")
    for title, script in STEPS:
        run_step(title, script)
    print("\n[ok] 全部步骤完成：已生成 Excel，并将 5 列数据写入数据库表 platform_exchanges_setting_min。")


if __name__ == "__main__":
    main()
