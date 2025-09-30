import os
from pathlib import Path

# ========== SURF 抓取配置 ==========
# 目标页面
SURF_STATS_URL: str = os.environ.get("SURF_STATS_URL", "https://www.surf.one/stats/")

# 浏览器驱动与运行模式
# 备注：你已提供 EdgeDriver，可按默认路径使用。若移到其他位置，请调整此项。
PROJECT_ROOT = Path(__file__).resolve().parents[1]
SURF_DRIVER_PATH: str = os.environ.get(
    "SURF_DRIVER_PATH",
    str(PROJECT_ROOT / "currencyGet_surf" / "drivers" / "msedgedriver.exe"),
)

# 是否无头（不显示浏览器窗口）——默认可视化，便于调试
SURF_HEADLESS: bool = os.environ.get("SURF_HEADLESS", "false").lower() == "true"

# 页面加载与滚动策略
SURF_TIMEOUT: int = int(os.environ.get("SURF_TIMEOUT", "40"))  # 显式等待上限（秒）
SURF_SCROLL_PAUSE: float = float(os.environ.get("SURF_SCROLL_PAUSE", "1.2"))  # 每次滚动后的等待（秒）
SURF_MAX_SCROLLS: int = int(os.environ.get("SURF_MAX_SCROLLS", "60"))  # 最大滚动次数（防止无限循环）

# 过滤：仅导出 quote=USDT 的交易对（true/false）
SURF_ONLY_USDT: bool = os.environ.get("SURF_ONLY_USDT", "true").lower() == "true"
SURF_QUOTE: str = os.environ.get("SURF_QUOTE", "USDT").upper()

# 输出目录
DATA_DIR = PROJECT_ROOT / "data" / "currency_kinds"
OUTPUT_JSON = DATA_DIR / "surf_pairs.json"
OUTPUT_CSV = DATA_DIR / "surf_pairs.csv"
OUTPUT_TXT = DATA_DIR / "surf_bases.txt"

# ========== dataGet 模块配置 ==========
# 结果输出目录
DATAGET_OUTPUT_DIR = PROJECT_ROOT / "data" / "dataGet_api"

# Binance 爬虫运行参数
# 是否无头（是否显示浏览器窗口）：优先使用 BINANCE_HEADLESS；未设置时继承 SURF_HEADLESS
BINANCE_HEADLESS: bool = os.environ.get("BINANCE_HEADLESS", str(SURF_HEADLESS)).lower() == "true"
# 并行线程数默认值（可通过环境变量覆盖）
BINANCE_MAX_WORKERS: int = int(os.environ.get("BINANCE_MAX_WORKERS", "4"))

