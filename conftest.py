"""pytest 公共配置：把项目根目录加入 sys.path，方便导入 cleaner 和 app。"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
