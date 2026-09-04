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
| `docs/2026-09-04-转发视频下载失败分析.md` | 嵌套转发视频失败的完整 JSON / NapCat 源码对照。结论已摘进下文「转发嵌套视频」节 |
| `patches/apply_napcat_forward_video_url.py` | 给 gitignore 的 `NapCat/napcat.mjs`（4.18.9）打/重放转发视频 URL 补丁 |
| `patches/napcat-4.18.9-forward-video-url.patch` | 同上的 unified diff，原文件 SHA256 `845E15BE…A9CD9` |

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
- 转发视频「手机能下、bot 下不了」先读下文「转发嵌套视频」节。本地 NapCat 4.18.9 已打补丁；升级覆盖 `napcat.mjs` 后用 `patches/apply_napcat_forward_video_url.py` 重放，不要再加大 `get_file` 超时或重写 B1。
- 控制面板和 bot 都是 `pythonw`。本机还有别的 `pythonw`（如 SnapWC `host.py`、`voice_alarm_tray.py`），**禁止**按进程名无差别杀 `python.exe` / `pythonw.exe`。停 bot 只杀命令行匹配 `bot.py` 的进程；停 UI 只杀监听 8899 或命令行含 `ui_server.py` 的进程。
- 从本 agent 拉起的子进程会被 Job Object 一并杀掉。需要面板在会话结束后仍活着时，用 WMI `Win32_Process.Create` 启动，不要 `Start-Process`。
- `pythonw` 下 `sys.stdout` / `sys.stderr` 为 `None`。`http.server` 写访问日志会崩连接。改 `ui_server.py` 时不要拿掉 `_redirect_stdio()`。
- `tasklist` 等控制台子系统程序必须带 `CREATE_NO_WINDOW`，否则面板轮询会反复弹黑窗。
- 日志：`logs/bot_out.log`、`logs/bot_err.log`（面板「归档日志」读这个）、`logs/ui_server.log`。同一文件可能混 GBK（旧控制台 bot）和 UTF-8（面板启动的 bot，`PYTHONIOENCODING=utf-8`）。读日志必须**按行**先 UTF-8 再 GBK，不能整段一种编码。
- 改 bot / 面板后：bot 需在面板点「停止 Bot」再「启动 Bot」才加载新代码；只改 `ui_server.py` 则要重启 8899。
- 测试：`venv\Scripts\python.exe -m pytest tests`（`test_classifier.py`、`test_link_archive.py`、`test_forward_video_fallback.py`、`test_forward_image_fallback.py`）。
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

**B1 救不了嵌套转发里那批无缓存视频**。2026-09-04 已改本地 `napcat.mjs`（见下一节），不要再加大超时、不要再写一遍 get_file 回退。

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
- 图片 `getImageUrl` ~9503：`originImageUrl` 已是 `appid=1407` 时补 **group_rkey** 就能直连，不需要群号。若内层是 `appid=1406`（私聊 rkey），见下节「转发嵌套图片」

占位 UIN：`1094950020`（假发送者）、`284840486`（`get_forward_msg` 模板假群）。归档出现 `1094950020_` 就是这个，不是原作者。

### 能下的视频 vs 这批失败的，差别在哪

和「视频本身能不能播」无关，只看 **OneBot 段有没有真 CDN**，以及 **本机 nt_data 里有没有文件**。

| 类型 | 例子 | OneBot `url` | 本机缓存 | bot 路径 | 结果 |
|---|---|---|---|---|---|
| 群里**直接发**的视频 | 20:13:58 `13a88228…`，`peerUid=663964060` | `https://multimedia.nt.qq.com.cn/download?appid=1415&…` | 可有可无（归档完缓存可能已删） | HTTP | **成功** |
| 同条转发里的**图片** | 20:18:39 那 24 张 | 段里已有 `appid=1407&fileid=` | 不依赖 | HTTP | **成功** |
| 转发视频，但**主号已经下过** | 20:16:41 `1741796593`（`Video\2026-09\Ori`） | 假本地路径（同样 NaN） | 主号有文件 | 跨账号复制 | **成功**（NapCat URL 仍是坏的） |
| 嵌套转发里的**私聊录像**，谁都没缓存 | 20:18:39 `1860081663` 的 11 个 | 补丁前假本地路径；补丁后 `appid=1415` CDN | 不需要 | HTTP | **补丁后成功** |

这批失败视频的共同特征（缺一不可）：

1. 来自**合并转发的内层**，`message_type=private`，`user_id=1094950020`
2. `fileUuid` appid 仍是 **1415**（当初多半是群视频，被一层层转进聊天记录）
3. NapCat 却拿**内层 UID 字符串**当群号
4. QQ **不会**因为你点开聊天记录就把视频预下载到接收号 `nt_data`；手机点下载是当时向 CDN 拉，不是读 PC 缓存

### 治本（2026-09-04 已改本地 NapCat 4.18.9，已核实）

`NapCat/` 整个在 `.gitignore` 里，改动用补丁进 git，不把 3MB minify 提交进去。

补丁做了两处，**没有**把失败时的 `url` 留空（classifier 无 URL 会丢段，主号缓存那条跨账号复制也会断）：

1. `getVideoUrlPacket`：`e` 转成整数且 `>0` 才走 `GetGroupVideoUrl`，否则走 C2C `GetVideoUrl`。不再 `+u_xxxx → NaN`。
2. `videoElement`：`parentMsgPeer.peerUid` 是纯数字群号时，packet 用它而不是内层 UID。日志会出现 `转发视频URL改用父会话 663964060`。

原文件：`NapCat/napcat.mjs.orig`，SHA256 `845E15BE97ECD0F2B3F353C6A235E1F5C111CD53FEE6F1952A3A68A01DAA9CD9`。  
重放：`venv\Scripts\python.exe patches\apply_napcat_forward_video_url.py`  
还原：同上加 `--restore`  
探活：`venv\Scripts\python.exe patches\probe_forward_video_urls.py --id <message_id>`（QQ 刚重启时短 ID 表是空的，先 `get_group_msg_history` 再 `get_forward_msg`）。图片：`patches\probe_forward_images.py`。

改完必须重启 **NapCat 注入的那棵 QQ 进程**（`NapCatWinBootMain.exe` 的进程树）。本机还有主号 QQ，**禁止** `taskkill /IM QQ.exe`。面板「停止 NapCat」会杀全部 QQ.exe，有主号在线时不要用。

### 核实结果（1860081663，2026-09-04 21:58）

| 检查 | 结果 |
|---|---|
| 新日志 `invalid uint 32` | **0**（旧日志 102 次） |
| `转发视频URL改用父会话 663964060` | 有 |
| `get_forward_msg` 11 个视频 `url` | 全部 `https://multimedia.nt.qq.com.cn/download?appid=1415&…` |
| HTTP 下载 | 11/11 HTTP 200，body 是 mp4，大小与 `file_size` 一致 |
| 归档 | `data/archive/663964060/videos/2026-09/04/1094950020_*`，首个 MD5 前缀 `847bceaa`（就是原先失败的那条） |
| 只传 resid、不传外层 message_id | 拆不出这 11 个视频。bot 走数字 `message_id`，这条路径够用 |

内核 `getVideoUrl`（「合并获取视频 URL 失败」）仍然会失败，这是预期；CDN 来自 packet `GetGroupVideoUrl(父群号)`。

### 不要再试（已验证无效）

- 打开 `parseMultMsg=true`：上报变大，URL 仍走同一套 `videoElement`；没补丁时一样 NaN
- 只在主号缓存里盲搜：20:18 这 11 个 hash 主号就没有
- 把 `get_file` `_timeout` 拉到 300s / 再重试：内核问错会话，空等 120s 也不会落盘，还会堵死 OneBot
- 再实现一遍「转发也 get_file」（B1 已在 `listener.py`，测试 `tests/test_forward_video_fallback.py`）
- 当「没归档」只搜 `512239520_`：文件名经常是 `1094950020_`
- 把失败 `url` 留空：classifier 丢段，主号已缓存的转发视频也会不再归档
- 升级 NapCat 后以为补丁还在：`napcat.mjs` 会被覆盖，必须重放 `patches/apply_napcat_forward_video_url.py` 再重启注入进程
- 把转发图片 `appid=1406` 的 fileid 改成 `1407` 再下：fileid 编码不同（`_goo` vs `_woo`），会 HTTP 400 `retcode=-5503023`

---

## 转发嵌套图片：手机能开、bot 报 1406 下载失败（2026-09-04）

样例：群 `663964060`、`message_id=11912981`、`forward_id=7681684748037124985`、22:20:03。内层 7 条（再嵌套），**57 张图**当时全失败。JSON：`docs/samples/forward_11912981_images.json`。

### 不是视频补丁回归

对照 `NapCat/napcat.mjs.orig`：`picElement` / `getImageUrl` 与原版一致。视频补丁只动了 `getVideoUrlPacket` 和 `videoElement`。同一时段群里**直接发**的图（`appid=1407`）都归档成功。

### 一句话

**拆包成功了，错在图片 URL 的 appid。** 内层来自私聊/匿名转发时，内核 `originImageUrl` 是 `appid=1406`，`getImageUrl` 配 **private_rkey**。这条 multimedia CDN 直连失败（私聊图在本仓库里一直如此，以前能下是因为 Pic 缓存还在）。手机 QQ 用当前群场景去拉，能打开；点开之后内核会把 URL 刷成 `appid=1407` + `_woo` fileid。

当时 bot 日志全是 `appid=1406` + `get_file file not found`（Jt 缓存按内层 dummy peer 存，也救不了）。事后 `get_msg` / `get_forward_msg` 已是 57/57 `appid=1407`，HTTP 200 JPEG。同 MD5 的旧图床 `https://gchat.qpic.cn/gchatpic_new/0/0-0-{MD5}/0` 也能 200（与 `file_size` 一致）。

| 谁 | URL | 结果 |
|---|---|---|
| 群里直接发的图 | `appid=1407` + group_rkey | 成功 |
| 上次 `1860081663` 转发里的图 | 本来就是 `1407` | 成功 |
| 这次 `11912981` 嵌套转发图（接收当时） | `appid=1406` + private_rkey | HTTP 失败，无本地缓存 |
| 同上，手机点开之后 / 再 `get_forward_msg` | 刷成 `1407` | 成功 |
| 同上，文件名是 32 位 MD5 | `gchatpic_new/0/0-0-{MD5}/0` | 成功 |

### 已修（bot 立刻生效；NapCat 下次重启注入后生效）

1. **bot** `listener.py`：图片 CDN 失败后，文件名是 32 位 MD5 则先试 gchatpic，再本地 / `get_file`。测试 `tests/test_forward_image_fallback.py`。补归档：`venv\Scripts\python.exe patches\archive_forward_images.py --id 11912981`。
2. **NapCat** `picElement`：`parentMsgPeer` 是数字群号时，`Jt.encode` 用父会话；若生成的 URL 含 `appid=1406` 且有 `md5HexStr`，改成 `getImageUrlFromMd5`。日志 `转发图片URL改用MD5 663964060`。同一 `patches/apply_napcat_forward_video_url.py` 重放。

改 bot 后面板停/启 Bot；NapCat 图片补丁要重启 NapCatWinBootMain 进程树才加载，**不要** `taskkill /IM QQ.exe`。

核实（2026-09-04 22:39，当前 OneBot 已是 1407）：`archive_forward_images.py --id 11912981` → **51 张新归档 + 6 张 MD5 已存在跳过 + 0 失败**，目录 `data/archive/663964060/images/2026-09/04/1094950020_*`。

---

## 已排查、尚未改代码的问题

- **部分转发视频 packet 已发出但回包没有 `download.info`**：日志 `GetGroupVideoUrl` → `Cannot read properties of undefined (reading 'info')`。OIDB 发出去了（不再是 NaN），服务端没给 URL。与 1860081663 这 11 条无关。不要为此再改 B1。
- **`get_forward_msg` 只拿 resid、没有外层群 peer**：模板 `parentMsgPeer.peerUid=""`，补丁用不上父群号。bot 用外层数字 `message_id`。
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
重放补丁    venv\Scripts\python.exe patches\apply_napcat_forward_video_url.py
            然后只重启 NapCatWinBootMain 进程树，不要 taskkill /IM QQ.exe
```
