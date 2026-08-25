"""pytest 公共配置：初始化 NoneBot 环境"""

import nonebot

# 必须在测试模块收集之前初始化（package __init__ 会导入 listener）
nonebot.init()
