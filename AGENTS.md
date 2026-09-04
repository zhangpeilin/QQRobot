# QQRobot — 项目说明（给后续会话）

本地 QQ 群媒体自动归档系统。根目录：`E:\QQRobot`。
新会话若不是在本目录启动，第一条消息请 `@E:\QQRobot\AGENTS.md`，或先 `cd /d E:\QQRobot` 再开 grok。

不要把 `README.md` 当现状：它仍写旧的 `NapCatFramework` 启动方式和 `data\` 日志路径。以本文件和代码为准。

---

## 架构

三层进程，本机回环通信：

```
QQ.exe  ← DLL 注入 ←  NapCatWinBootMain.exe (E:\QQRobot\NapCat)
                              │
                              │ OneBot v11 WS  ws://127.0.0.1:3001/onebot/v11/ws
                              ▼
                     NoneBot2 bot.py  HTTP :8080
                              │  插件 plugins/media_archiver
                              ▼
                     data/archive/{group_id|private}/{images|videos|audios|files|links}
                     data/archive/metadata.db
```

另有独立控制面板：`ui_server.py`（stdlib `ThreadingHTTPServer`）监听 `http://127.0.0.1:8899/`。
面板用 `pythonw.exe` 后台跑，负责启停 NapCat / Bot、看日志、打开归档目录。

当前实际启动链是 **Desktop / WinBoot 模式**（`start_napcat.bat` → `NapCat\NapCatWinBootMain.exe`），不是 README 里的 `NapCatFramework\napiLoader.bat`。`NapCatFramework\` 目录还在，但日常不要当线上路径改。

### 端口

| 端口 | 进程 | 用途 |
|------|------|------|
| 3001 | QQ.exe（NapCat 注入后） | OneBot WS 服务端 |
| 8080 | `pythonw.exe bot.py` 子进程 | NoneBot2 HTTP |
| 6099 | NapCat | NapCat WebUI |
| 8899 | `pythonw.exe ui_server.py` | 归档控制面板 |

### 运行身份

- 小号 / NapCat 登录 UIN：`2065277052`（配置 `NapCat\config\onebot11_2065277052.json`）
- 主号（私聊白名单）：`512239520`
- 主要归档群：`663964060`（`config.yaml` 里 `watch_groups: []` = 监听全部群）
- QQ 安装：`C:\Program Files\Tencent\QQNT\QQ.exe`
- 本机 QQ 文件缓存根：`D:\QQFile\Tencent Files\Tencent Files\{QQ}\nt_qq\nt_data`

### 关键文件

| 路径 | 职责 |
|------|------|
| `bot.py` | NoneBot2 入口，加载 `plugins/` |
| `.env` | `DRIVER=~fastapi+~aiohttp`，`ONEBOT_WS_URLS`，`PORT=8080`。缺 aiohttp 则连不上 WS |
| `config.yaml` | 业务配置（监听、归档目录、下载、去重、启动扫描） |
| `ui_server.py` | 控制面板。启动时必须 `_redirect_stdio()` |
| `plugins/media_archiver/listener.py` | 消息入口：群/私聊、合并转发拆包、磁力/网盘链接归档 |
| `classifier.py` | 从 OneBot 消息段提取 image/video/record/file |
| `downloader.py` | aiohttp 并发下载，先落临时文件 |
| `archiver.py` | 按类型/日期移动并命名 `{user_id}_{ts}_{md5[:8]}.{ext}` |
| `database.py` | `metadata.db`，唯一约束 `(message_id, media_type, file_md5)` |
| `dedup.py` | MD5 去重 |
| `notifier.py` | Server酱，默认关 |
| `start_ui.bat` / `stop_ui.bat` | 启停面板 |
| `start_napcat.bat` | 提权后启动 NapCat Desktop |
| `venv\Scripts\pythonw.exe` | 面板和从面板拉起的 bot 都用这个 |
| `docs/2026-09-04-转发视频下载失败分析.md` | 嵌套转发视频失败的完整 JSON / NapCat 源码对照。结论已摘进下文「转发嵌套视频」节，不要当新 bug 重做实验 |

归档路径：

- 群：`data/archive/{group_id}/{images\|videos\|audios\|files\|links}/{YYYY-MM}/[DD]/...`
- 私聊：`data/archive/private/{user_id}/...`
- 转发里的媒体文件名用内层 `user_id`。NapCat 对匿名「QQ用户」填占位 UIN **`1094950020`**（不是真实原作者）；主号 `512239520` 转发别人的视频，文件常叫 `1094950020_*`。先按日期/hash 搜群目录，不要只搜自己的号。
- 磁力：`.../links/{YYYY-MM}/magnet_{源消息ID}.md`
- 网盘链接：`.../links/{YYYY-MM}/forward_{源消息ID}.md`

OneBot 相关：`reportSelfMessage: false`（小号自己发的消息不会上报）；`parseMultMsg: false`（合并转发要 bot 自己 `get_msg` / `get_forward_msg` 拆）。

---

## 工作约定

- 改代码前先读现有实现；用户说「先排查不要改」就只诊断。
- 转发视频「手机能下、bot 下不了」先读下文「转发嵌套视频」节，不要再加大 `get_file` 超时或重写 B1。
- 控制面板和 bot 都是 `pythonw`。本机还有别的 `pythonw`（如 SnapWC `host.py`、`voice_alarm_tray.py`），**禁止**按进程名无差别杀 `python.exe` / `pythonw.exe`。停 bot 只杀命令行匹配 `bot.py` 的进程；停 UI 只杀监听 8899 或命令行含 `ui_server.py` 的进程。
- 从本 agent 拉起的子进程会被 Job Object 一并杀掉。需要面板在会话结束后仍活着时，用 WMI `Win32_Process.Create` 启动，不要 `Start-Process`。
- `pythonw` 下 `sys.stdout` / `sys.stderr` 为 `None`。`http.server` 写访问日志会崩连接。改 `ui_server.py` 时不要拿掉 `_redirect_stdio()`。
- `tasklist` 等控制台子系统程序必须带 `CREATE_NO_WINDOW`，否则面板轮询会反复弹黑窗。
- 日志：`logs/bot_out.log`、`logs/bot_err.log`（面板「归档日志」读这个）、`logs/ui_server.log`。同一文件可能混 GBK（旧控制台 bot）和 UTF-8（面板启动的 bot，`PYTHONIOENCODING=utf-8`）。读日志必须**按行**先 UTF-8 再 GBK，不能整段一种编码。
- 改 bot / 面板后：bot 需在面板点「停止 Bot」再「启动 Bot」才加载新代码；只改 `ui_server.py` 则要重启 8899。
- 测试：`venv\Scripts\python.exe -m pytest tests`（`test_classifier.py`、`test_link_archive.py`、`test_forward_video_fallback.py`）。
- Python ≥3.11，当前 venv 是 3.14。风格跟现有代码：中文注释、ruff line-length 120。

---

## 已修好的问题（2026-09-03）

不要重新当新 bug 修，除非回归。

### 控制面板打不开（端口在听、浏览器连上即断）

`pythonw` + `BaseHTTPRequestHandler.log_message` 写 `sys.stderr`，stderr 为 None → 连接被掐断。
已在 `ui_server.py` 启动时把 stdout/stderr 重定向到 `logs/ui_server.log`。

### 面板轮询反复弹黑窗

`/api/status` 每 3 秒调 `tasklist`。已给 `_proc_names()` 加 `CREATE_NO_WINDOW`。

### 网页日志中文乱码

`logs/bot_err.log` 混有 GBK 和 UTF-8。旧逻辑整段当 UTF-8，失败则**整段落成 GBK**，后面的 UTF-8 全乱。
已改 `_decode_log_line` / `_tail_log` 按行判断。

### `stop_ui.bat` 杀错端口

脚本曾杀 8090，代码已是 8899。已改成杀 8899 监听进程。`ui_server.py` 文件头注释仍可能写「8090」，以 `PORT = 8899` 为准。

### 「停止 Bot」点了进程还在

面板用 `sys.executable` 拉起 bot，实际是 `pythonw.exe bot.py`（父进程 + 子进程，子进程听 8080）。旧 `_stop_bot` 只查 `Name='python.exe'`，杀不到。
现逻辑：WMI 查 `python.exe` **和** `pythonw.exe`，命令行匹配 `(^|\s|\\)bot\.py(\s|"|$)`，再补 8080 监听 PID（若是 python），`taskkill /F /T`，等到 8080 关掉。`bot_running` 的判定是「8080 在听」，不是进程名。

### 磁力链接归档路径

磁力写入同目录 `magnet_{消息ID}.md`，不要和 `forward_*.md` 搞混。改完需重启 bot。

### `get_msg` 解包看错 `data`（2026-09-04 已修）

NoneBot `call_api` 通常已解开 OneBot `data`。旧代码 `msg_resp.get("data", {}).get("message")` 永远拿不到 content。现用 `_unwrap_api_data` / `_forward_content_from_msg`。这只影响少打一次 `get_forward_msg`，**不解决视频 URL**。

### 转发视频 B1：本地复制失败后试 `get_file`（2026-09-04 已修，治标）

`listener.py`：转发不再因 `skip_url_refresh=True` 跳过 `get_file`。本地路径不存在 → 跨账号搜 → `get_file(file=文件名)`（NapCat `Jt` 内存缓存 24h）。

超时与并发（不要改回 300s 串行）：

- `_GET_FILE_TIMEOUT = 40`，`asyncio.wait_for` 兜底
- 超时后只短等 5s 再扫本地缓存，**不再发第二次 `get_file`**（避免和仍在跑的 `downloadMedia` 叠请求）
- `_GET_FILE_SEM = 3`；同层媒体 + 嵌套转发 `asyncio.gather`
- `file not found` 立刻放弃，不空等

**B1 救不了嵌套转发里那批无缓存视频**（见下一节）。不要再加大超时、不要再写一遍 get_file 回退。

---

## 转发嵌套视频：手机能下、NapCat 超时（2026-09-04，不要反复试错）

完整证据：`docs/2026-09-04-转发视频下载失败分析.md`，JSON 在 `docs/samples/forward_1860081663_*.json`。样例消息：群 `663964060`、`message_id=1860081663`、20:18:39，11 视频全失败、24 图片全成功。

### 一句话

**不是 QQ 服务器没响应，是 NapCat 的 bug：用内层 dummy peer 去组「群视频 URL」包，本地编码就抛了 `invalid uint 32: NaN`，OIDB 请求根本没发出去。** 手机 QQ 能点开，是因为官方客户端用 `fileUuid` + **当前/父聊天的群场景** 去拉播放地址；资源在 CDN 上是齐的。

### 手机能下 vs NapCat 超时

| 谁 | 怎么取视频 | 结果 |
|---|---|---|
| 手机 QQ | 聊天记录 resid 拆包后，用 **fileUuid + 你正在看的那个群/会话** 走 NT 播放/下载 | 能下 |
| NapCat `videoElement` 内核 | `getVideoPlayUrlV2(内层 chatType/peerUid, 外层 msgId, elementId)`。内层是假私聊，外层群消息上没有这个 videoElement | 失败（日志「合并获取视频 URL 失败」） |
| NapCat packet 兜底 | `getVideoUrlPacket(n.peerUid, fileUuid)`。`fileUuid` protobuf appid=**1415** → 走 `GetGroupVideoUrl(+peerUid)`。内层 `peerUid` 是 `u_xxxx`，`+"u_xxxx" === NaN`，`UINT32 groupUin` 在 `bm()` 里抛错 | **包没发出去**。日志 `invalid uint 32: NaN` |
| NapCat 失败回退 | `url = e.filePath`（小号 `Video\YYYY-MM\Ori\hash.mp4`） | 路径字符串，文件经常不存在 |
| bot B1 `get_file` | `Jt.decode(文件名)` → `downloadMedia(内层 msgId, 内层私聊, 内层 peerUid)`。内层消息不在 NT 本地库，内核等 `onRichMediaDownloadComplete` 直到默认 120s | 客户端 40s 超时；`nt_data` 里始终没有文件 |

所以：

- **不是**「接口没回包 / CDN 403 / HTTP 超时」。packet 路径在组包前就死了。
- **是** NapCat 源码逻辑错误：`napcat.mjs` `getVideoUrlPacket` 无条件 `+e`；`videoElement` 用的是内层 `n.peerUid`，**没用已经挂上的 `parentMsgPeer`（这次外层是群号 `663964060`）**。
- `get_file` 超时是第二条死路（问错会话让内核空等），不是服务器拒绝。把超时加到 300s 只会卡死后续消息，文件照样不会出现。

源码位置（Desktop 包 `NapCat/napcat.mjs`）：

- `videoElement` 转换 ~72684：假 UIN `1094950020` / `284840486` 走「合并获取」；失败填 `filePath`
- `getVideoUrlPacket` ~9324：appid 1415 → `GetGroupVideoUrl(+e)`
- `MH.build` ~14787：`groupUin` 必须是数字；schema `Pi.GroupUin` 是 `UINT32`
- `parseMultiMessageContent` ~73159：内层消息 **有** `parentMsgPeer`，视频转换没用它
- 图片 `getImageUrl` ~9503：用 `originImageUrl` 里现成的 `appid=1407&fileid=...` 再补 rkey，**不需要群号**

占位 UIN：`1094950020`（假发送者）、`284840486`（`get_forward_msg` 模板假群）。归档出现 `1094950020_` 就是这个，不是原作者。

### 能下的视频 vs 这批失败的，差别在哪

和「视频本身能不能播」无关，只看 **OneBot 段有没有真 CDN**，以及 **本机 nt_data 里有没有文件**。

| 类型 | 例子 | OneBot `url` | 本机缓存 | bot 路径 | 结果 |
|---|---|---|---|---|---|
| 群里**直接发**的视频 | 20:13:58 `13a88228…`，`peerUid=663964060` | `https://multimedia.nt.qq.com.cn/download?appid=1415&…` | 可有可无（归档完缓存可能已删） | HTTP | **成功** |
| 同条转发里的**图片** | 20:18:39 那 24 张 | 段里已有 `appid=1407&fileid=` | 不依赖 | HTTP | **成功** |
| 转发视频，但**主号已经下过** | 20:16:41 `1741796593`（`Video\2026-09\Ori`） | 假本地路径（同样 NaN） | 主号有文件 | 跨账号复制 | **成功**（NapCat URL 仍是坏的） |
| 嵌套转发里的**私聊录像**，谁都没缓存 | 20:18:39 `1860081663` 的 11 个，路径在 `2026-08\Ori` | 假本地路径 | 小号无、主号无 | 复制失败 → get_file 40s 超时 | **失败** |

这批失败视频的共同特征（缺一不可）：

1. 来自**合并转发的内层**，`message_type=private`，`user_id=1094950020`
2. `fileUuid` appid 仍是 **1415**（当初多半是群视频，被一层层转进聊天记录）
3. NapCat 却拿**内层 UID 字符串**当群号
4. QQ **不会**因为你点开聊天记录就把视频预下载到接收号 `nt_data`；手机点下载是当时向 CDN 拉，不是读 PC 缓存

### 治本（还没做，不要用 bot 再绕）

改 `napcat.mjs`（或等上游；硬改 minify 后升级会被覆盖）：

1. `getVideoUrlPacket`：`e` 不是纯数字就不要 `GetGroupVideoUrl(+e)`；改走 C2C `GetVideoUrl(uid字符串)`，或用 `parentMsgPeer.peerUid`（群号）
2. `videoElement` 失败时不要把不存在的 `filePath` 填进 `url`；留空，让 bot 知道没 URL
3. 不要把 `get_file`/`downloadMedia` 超时加很长来赌内核

### 不要再试（已验证无效）

- 打开 `parseMultMsg=true`：上报变大，URL 仍走同一套 `videoElement`，一样 NaN
- 只在主号缓存里盲搜：20:18 这 11 个 hash 主号就没有
- 把 `get_file` `_timeout` 拉到 300s / 再重试：内核问错会话，空等 120s 也不会落盘，还会堵死 OneBot
- 再实现一遍「转发也 get_file」（B1 已在 `listener.py`，测试 `tests/test_forward_video_fallback.py`）
- 当「没归档」只搜 `512239520_`：文件名经常是 `1094950020_`

---

## 已排查、尚未改代码的问题

- **嵌套转发视频无 CDN、无本地缓存**：根因是上一节 NapCat `GetGroupVideoUrl(+非数字 peerUid)`。bot B1 已做完，再修只能改 NapCat。2026-09-03 的 `dc83f60c…` / `9b46f94e…` 和 08:41 独立视频若当时也是假本地路径，同一类问题。
- **历史扫描统计会骗人**：`_process_history_message` 处理完转发后可能返回 0，日志里「含媒体: 0 | 归档: 0」不代表没存。
- **小号自己转发**：`reportSelfMessage: false` 时 NapCat 不上报，bot 不会归档。用主号转到监听群才能被收到。
- **合并转发被 QQ 风控**：不是 NapCat 没推。风控拦了则协议层也没有这条，bot 无消息可处理。
- `stop.bat` 仍按窗口标题杀 `python.exe`，过时且不安全；日常用面板停止。
- 启动扫描 `startup_scan.enabled: true` 时会扫最多 500 条，缺缓存的旧转发视频每个 `get_file` 最多 40s（3 并发）。这是预期，不是卡死。

---

## 常用操作

```
控制面板    start_ui.bat          → http://127.0.0.1:8899/   （http，不要 https）
停面板      stop_ui.bat
NapCat      start_napcat.bat      （要管理员；扫码登小号）
只跑 bot    start.bat             （有窗口的 python.exe）
测端口      netstat -ano | findstr ":3001 :8080 :8899 :6099"
日志        E:\QQRobot\logs\
归档        E:\QQRobot\data\archive\
NapCat UI   http://127.0.0.1:6099/web/   token 在 NapCat\config\webui.json
```
