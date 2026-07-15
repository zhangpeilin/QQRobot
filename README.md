# QQ 群媒体自动归档系统

## 技术栈

- NoneBot2 2.5.0（Python 3.14）
- NapCatQQ v4.18.9 Framework 模式（napimain.exe + napiloader.dll）
- OneBot v11 协议（WebSocket）
- 驱动：fastapi + aiohttp（HTTP 服务 + WS 客户端）
- 存储：SQLite（元数据）+ 文件系统（归档文件）

## 系统架构

```
┌──────────────────┐     WebSocket       ┌──────────────────┐
│  NapCatQQ        │◄────────────────────│  NoneBot2 Bot    │
│  (napimain.exe)  │   ws://:3001        │  (bot.py)        │
│  OneBot v11 服务端│                     │  HTTP :8080      │
└──────────────────┘                     │  插件:           │
       ▲                                 │  media_archiver  │
       │ DLL 注入                        └──────────────────┘
       ▼                                        │
┌──────────────────┐                            ▼
│  QQ.exe          │              ┌──────────────────────┐
│  (小号登录)      │              │  data/archive/        │
└──────────────────┘              │  ├─ {group_id}/       │
                                  │  │  ├─ images/        │
                                  │  │  ├─ videos/        │
                                  │  │  └─ audios/        │
                                  │  └─ metadata.db      │
                                  └──────────────────────┘
```

## 项目目录结构

```
E:\QQRobot\
├── .env                    # NoneBot2 环境配置
├── bot.py                  # 机器人入口
├── config.yaml             # 业务配置（归档、下载、去重等）
├── README.md               # 本文件
├── NapCatFramework\        # NapCatQQ Framework 模式
│   ├── napiLoader.bat      # 启动脚本
│   ├── napimain.exe        # 注入器主程序
│   ├── napiloader.dll      # 注入 DLL
│   ├── nativeLoader.cjs    # 加载器入口
│   ├── config\             # NapCat 配置
│   │   ├── onebot11_2065277052.json  # OneBot 配置
│   │   └── webui.json      # Web 管理界面配置
│   └── logs\               # NapCat 日志
├── plugins\
│   └── media_archiver\     # 核心归档插件
│       ├── __init__.py
│       ├── config.py       # 配置模型（Pydantic）
│       ├── classifier.py   # 消息类型识别
│       ├── downloader.py   # 异步下载引擎
│       ├── archiver.py     # 文件归档
│       ├── dedup.py        # MD5 去重
│       ├── database.py     # SQLite 元数据
│       └── listener.py     # 消息监听 + 历史扫描
├── data\
│   ├── archive\            # 归档文件存储根目录
│   │   ├── {group_id}\     # 按群号分目录
│   │   │   ├── images\     # 图片
│   │   │   │   └── {YYYY-MM}\{DD}\
│   │   │   ├── videos\     # 视频
│   │   │   │   └── {YYYY-MM}\{DD}\
│   │   │   ├── audios\     # 语音
│   │   │   │   └── {YYYY-MM}\{DD}\
│   │   │   └── files\      # 文件
│   │   │       └── {YYYY-MM}\{DD}\
│   │   ├── .tmp\           # 下载临时文件
│   │   └── metadata.db     # 归档记录数据库
│   └── logs\               # 插件日志
└── venv\                   # Python 虚拟环境
```

## 启动

### 第一步：启动 NapCatQQ

NapCat Framework 模式通过 napimain.exe 向 QQ 注入 DLL 来实现 OneBot 服务。

```
cd /d "E:\QQRobot\NapCatFramework" && start napiLoader.bat
```

- 会自动从注册表找到 QQ 安装路径
- napimain.exe 注入 DLL 后会自动退出，WebSocket 服务在 QQ.exe 进程内部运行
- 如果 QQ 未登录，会弹出 QQ 登录窗口，用小号登录

### 第二步：启动 Bot

```
cd /d "E:\QQRobot" && start /B "" "venv\Scripts\python.exe" bot.py
```

- /B 表示后台运行，关掉 cmd 也不影响
- 启动后自动连接 NapCat WebSocket（ws://127.0.0.1:3001/onebot/v11/ws）
- 连接成功后触发历史消息扫描（配置见 startup_scan）

或者用 pythonw.exe 完全无窗口：

```
cd /d "E:\QQRobot" && start "" "venv\Scripts\pythonw.exe" bot.py
```

## 停止服务

### 停止 Bot

```
taskkill /f /im python.exe
```

如果系统有其他 Python 进程不想误杀，也可以在任务管理器里找到对应 PID 结束。

### 停止 NapCatQQ

NapCat Framework 模式下，napimain.exe 注入 DLL 后会自动退出，WebSocket 服务实际运行在 **QQ.exe 进程内部**。所以停止 NapCat 需要结束 QQ：

```
taskkill /f /im QQ.exe
```

### 一键停止全部

```
taskkill /f /im python.exe && taskkill /f /im QQ.exe
```

### 重启 NapCat（修改配置后）

修改 OneBot 配置（如 heartInterval）后需要重启 NapCat：

```
taskkill /f /im QQ.exe
cd /d "E:\QQRobot\NapCatFramework" && start napiLoader.bat
```

等待 QQ 登录完成后，再启动 Bot。

## 配置说明

### config.yaml（业务配置）

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| watch_groups | 监听的群号列表，留空=全部 | [] |
| archive_root | 归档根目录 | ./data/archive |
| download.max_concurrent | 最大并发下载数 | 3 |
| download.timeout | 下载超时(秒) | 120 |
| download.max_retries | 下载重试次数 | 3 |
| media_types.* | 各类型开关 | true |
| dedup.enabled | 启用 MD5 去重 | true |
| dedup.strategy | 去重策略：md5 或 message_id | md5 |
| startup_scan.enabled | 启动时扫描历史 | true |
| startup_scan.time_range_hours | 扫描范围(小时)，0=全部 | 0 |
| startup_scan.max_per_group | 每群最多拉取条数 | 500 |

### .env（NoneBot2 环境）

```
DRIVER=~fastapi+~aiohttp
ONEBOT_WS_URLS=["ws://127.0.0.1:3001/onebot/v11/ws"]
HOST=0.0.0.0
PORT=8080
```

- DRIVER 必须同时包含 fastapi（HTTP 服务）和 aiohttp（WS 客户端），缺一不可

## 归档文件命名和目录结构

### 目录结构

```
data/archive/{group_id}/{类型}/{YYYY-MM}/{DD}/{user_id}_{timestamp}_{md5[:8]}.{ext}
```

示例：

```
data/archive/663964060/images/2026-07/15/284079424_1784064112_54f9a364.jpg
```

### 文件名组成

- user_id — 发送者的 QQ 号
- timestamp — 归档时的 Unix 时间戳
- md5[:8] — 文件 MD5 的前 8 位（去重和校验用）
- 扩展名优先从原始文件/URL 推断，否则按类型给默认值（.jpg / .mp4 / .silk）

### 媒体类型对应目录

| 类型 | 目录名 |
|------|--------|
| image | images |
| video | videos |
| record | audios |
| file | files |
| 其他 | others |

## NapCat Web 管理界面

地址：http://localhost:6099/web/

Token 在 NapCatFramework\config\webui.json 中查看。

## 检查运行状态

### 看进程

```
tasklist | findstr napimain
tasklist | findstr python
```

### 测 WebSocket 端口

```
curl http://127.0.0.1:3001/
```

返回 Upgrade Required = 正常

### 看 Bot 日志

```
type E:\QQRobot\data\bot_out.log        # 标准输出
type E:\QQRobot\data\bot_err.log        # 标准错误/插件日志
```

### 看 NapCat 日志

```
dir E:\QQRobot\NapCatFramework\logs\
```

## 常见问题

### Bot 连不上 WebSocket
检查 .env 中的 DRIVER=~fastapi+~aiohttp — 如果只有 ~fastapi，缺少 WebSocket 客户端能力。

### NapCat 启动后 QQ 报"文件已损坏"
Shell/WinBoot 模式在 Win11 25H2 上触发 QQ 完整性检查。用 Framework 模式（napimain.exe + napiloader.dll）可解决。

### 历史消息 URL 过期
QQ 图片/视频的 CDN URL 会过期，返回 HTTP 400。系统会自动通过 get_image / get_record API 获取新 URL 重试。

### 下载临时文件锁住
Windows 上 tempfile.mkstemp() 会打开文件描述符，需要 os.close(fd) 后才能移动文件。

### 日志看不到 INFO
NoneBot2 的 loguru 桥接器会抑制标准 logging.getLogger() 的 INFO 级别输出。关键消息已改用 WARNING 级别。

### 清理数据和重新扫描
停止 Bot，删除 data\archive\ 下的子目录和 metadata.db，重启 Bot 即可。

## 排错命令

```
:: 看当前 Python 进程
tasklist | findstr python

:: 看 napimain 进程
tasklist | findstr napimain

:: 看 3001 端口
netstat -ano | findstr :3001

:: 看 8080 端口
netstat -ano | findstr :8080
```
