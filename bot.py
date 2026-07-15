"""QQ 群媒体自动归档系统 - NoneBot2 入口"""

import nonebot
from nonebot.adapters.onebot.v11 import Adapter as OneBotV11Adapter

nonebot.init()

driver = nonebot.get_driver()
driver.register_adapter(OneBotV11Adapter)

# 加载插件
nonebot.load_plugins("plugins")

if __name__ == "__main__":
    nonebot.run()
