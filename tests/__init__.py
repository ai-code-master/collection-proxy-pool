"""项目测试包。"""

import os
from pathlib import Path


FIXTURES = Path(__file__).resolve().parent / 'fixtures'
os.environ.setdefault('PROXY_POOL_CONFIG', str(FIXTURES / 'config.json'))
