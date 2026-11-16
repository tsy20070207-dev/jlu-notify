# 吉大通知 → 企业微信 / 微信

把吉林大学各单位的「通知公告」抓下来，合并成一条消息推到**企业微信群机器人**、**WxPusher** 或**个人微信**。
不用装 App、不用交学号密码、不自动登录 —— 就是一份低频只读的轮询脚本 + 三个可选推送通道。

```
校内栏目页 (92 个)  →  按块解析  →  SQLite 按 URL 去重  →  合并成 1 条  →  企微群 + 微信
```

## 它做什么

| 能力 | 说明 |
| --- | --- |
| 覆盖全校 | 学校机构设置页的 118 个单位站点，自动测出 **92 个**可解析的「通知公告」栏目 |
| 多通道 | 企业微信群机器人（20 条/分，无日上限）、WxPusher（SPT 极简 / appToken 标准）、PushPlus，互不影响 |
| 不重复 | 以详情页 URL 为唯一键，同一条通知被三个站点转载也只推一次 |
| 不轰炸 | 首次运行只建基线不推送；多条合并成一条；23:30–07:00 不打扰，早上合并补发 |
| 分级轮询 | A 级（学校通知/本科生院/你的学院）5 分钟，B 级（其余院系）30 分钟 |
| 直连校内 | 强制 `ProxyHandler({})`，绕过 Clash / TUN 的 fake-ip，不需要配 NO_PROXY |

## 覆盖范围（2026-11 实测）

- 学校机构设置页列出 **118** 个单位站点，其中 **109 个公网可直连**；
  余下 9 个（`bmb cw jgdw jjw lab sjc xinfang xyk zzb`）只能内网访问或已下线。
- 109 个可直连站点里 **92 个**的通知栏目能解析出条目，`sources.json` 就是这 92 个源
  （91 个站点源默认启用 + 1 个默认停用的内网 OA 源）。
- 剩下 17 个站点首页是纯 JS 渲染（如图书馆），HTML 里没有列表，暂时抓不到。
- 吉大几乎所有单位站点都是**博达建站系统**，通知栏目统一叫 `tzgg`，
  所以一个通用解析器就能覆盖绝大多数站点，不需要给每个学院写爬虫。
  实测踩过的坑：电子学院叫 `jwgg`、物理学院把标题放 `<h3>` 里、
  化学院的通知和新闻都在 `xwtz/` 下——发现器按栏目名分「通知类 / 新闻类」排序，
  不会把「新闻动态」当成「通知公告」。

## 5 分钟上手

### 1. 准备一个推送通道（三选一，配几个都行）

**① WxPusher 极简推送 SPT**（没有企业微信就选它，5 分钟）

1. 打开 <https://wxpusher.zjiecode.com/docs/#/?id=spt>，**微信扫码**，拿到 `SPT_` 开头的令牌
2. 装接收端（不装收不到）：<https://wxpusher.zjiecode.com/download/> → Mac 的 dmg 或手机 App
3. 把令牌填进 `channels.wxpusher.spt`，完事

> 一个 `SPT_` 只能发给自己或极少数人——够用了，而且不用建应用、不用管 UID。
> 想多推几个人就用英文逗号分隔，最多 10 个。

**② 企业微信群机器人**（有企业微信就选它，稳定、免费、无日上限）

> 群聊 → 右上角「···」→ **消息推送**（2024 年前叫「群机器人」，找不到就两个名字都点）
> → 添加 → 起个名 → 复制 Webhook 地址
> 地址形如 `https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxxxxxxx`

自己一个人也想用？企业微信里建一个只有自己的群即可。

**③ PushPlus**（要实名，介意就跳过）

<https://www.pushplus.plus> → 微信扫码 → 「一对一消息」→ 复制 token →
填 `channels.pushplus.token`，再把 `enabled` 改成 `true`。

**WxPusher 标准推送**（要给一群人发、要管理订阅用户时才需要）

1. <https://wxpusher.zjiecode.com/admin/> 微信扫码登录 → **应用管理 → 创建应用** → 复制 **appToken**（`AT_` 开头）
2. **UID** 在应用详情的「用户列表」里，或 WxPusher 客户端「我的」页面复制（`UID_` 开头）
   —— 老教程的「关注公众号 → 我的 UID」已经过时，公众号不再是主通道了
3. 填 `channels.wxpusher.app_token` / `uids`（`spt` 留空即可）

### 2. 填配置

```bash
cd jlu-notify
cp config.example.json config.json
cp sources.example.json sources.json     # 或者用下一步的自动发现生成
```

编辑 `config.json`，按你选的通道填：最省事是 `channels.wxpusher.spt`，
有企业微信就填 `channels.wecom.webhook`。没用到的通道把 `enabled` 改成 `false`，
`test-push` 就不会再提示它「未配置」。

### 3. 验证通道

```bash
python3 cli.py test-push
```

手机 / 群里应该立刻收到一条「通道自测」。没收到就看终端里的错误码。

### 4. 自动发现全校通知栏目

```bash
python3 discover.py --write           # 联网探测 118 个站点，直接生成 sources.json
python3 discover.py --reuse --write   # 不联网，用上次的候选清单按新规则重排
```

探测约 600 个请求、1 分钟，**一次性操作**，别放进定时任务。
探测结果会先落到 `sources_candidates.json`（每个站点所有候选栏目的条目数），
`--write` 时每站取一个：**先看栏目类型**（`tzgg` 类通知 > 其它 > `xwdt` 类新闻），
**再看不带党建、路径最浅、条目最多**——所以化学学院取的是 `xwtz/tzgg.htm`
而不是条数一样多的 `xwtz/xwdt.htm`（新闻动态）。
改过 `discover.py` 里的排序规则后可以用 `--reuse` 立刻重排，不用重新联网。
想只留自己关心的学院，编辑 `sources.json` 把不关心的 `"enabled": false` 即可。

### 5. 试跑

```bash
python3 cli.py run --dry-run    # 只抓+预览，不写库不推送
python3 cli.py run --once       # 正式抓一轮：首次会建基线（不推送）
python3 cli.py list-sources     # 看每个源的游标和报错
python3 cli.py stats            # 看库里有多少条
```

第一次 `run --once` 只把现有通知记成「已推送」，**不会响手机**。
之后任何源出现新条目，下一轮就会合并成一条推给你。

## 命令速查

| 命令 | 作用 |
| --- | --- |
| `python3 cli.py test-push` | 发一条测试消息，验证已启用的通道 |
| `python3 cli.py run --dry-run` | 只抓取并打印预览，不写库、不推送 |
| `python3 cli.py run --once` | 抓一轮就退出（launchd / cron 用这个） |
| `python3 cli.py run --loop` | 常亮设备上无限循环 |
| `python3 cli.py run --source 本科生院 --force` | 只跑某个源并强制忽略频率限制 |
| `python3 cli.py run --only wxpusher` | 本轮只走某个通道 |
| `python3 cli.py run --no-quiet` | 忽略免打扰时段 |
| `python3 cli.py list-sources` | 各源上次抓取时间 / 条数 / 报错 |
| `python3 cli.py stats` | 数据库统计 |
| `python3 discover.py --write` | 重新发现全部通知栏目（联网，约 1 分钟） |
| `python3 discover.py --reuse --write` | 不联网，按新规则重排一遍栏目 |
| `python3 tests/smoke_test.py` | 夹具冒烟测试（不联网） |


## 工作原理（三个关键设计）

**1. 按 `<li>` 块解析，而不是全局找链接**

吉大列表页不是「一个 `<a>` 一行」这么简单：有的把日期写在链接**后面**，有的写在**前面**，
化学学院这类还把日期拆成 `class="day">11-11` + `class="year">2026` 两个标签。
所以解析器先切出最小的 `<li>`/`<tr>` 块，只在**块内**找标题和日期 ——
否则会把第三条的日期配给第一条。

还有一个坑：有些条目下面的摘要正文里写着「报名时间：2026年11月11日」，
那是内容不是发布时间。解析器只认排版位置上的日期标签，
并优先用「与标题同级的 span / class=day / 纯文本日期」，不会去正文里抓。
`tests/smoke_test.py` 里有一条用例（`test_date_trap`）专门钉住这个行为。

**2. 以详情页 URL 为主键去重**

同一份通知常被三个站点转载（学院、学校主页、本科生院）。用 URL 做唯一键，
标题只当兜底。另有一张 `sources` 表记录每个源的上次抓取时间，实现分级频率。

**3. 首次运行只建基线**

第一次跑的时候，栏目里全是「历史通知」。如果直接推送，你会一次收到 50 条旧消息。
所以 `push.first_run_baseline_only` 默认 `true`：首轮只入库并标记为已推送，**不响手机**。
之后才推增量。想立刻验证推送效果，用 `python3 cli.py test-push`。

推送侧还有三道闸：`max_items`（一条消息最多几条）、`max_chars`（按字节算，中文 1 字 3 字节）、
通道熔断（某通道连续失败 5 次后 1 小时内不再尝试，不影响另一个通道）。

## config.json 字段

```jsonc
{
  "db_path": "data/notify.db",
  "request": {
    "timeout": 15,            // 单请求超时（秒）
    "retries": 2,             // 失败重试次数
    "workers": 4,             // 并发抓取数，别超过 6
    "jitter_ms": [0, 1500]    // 每个请求前随机等待，把流量摊开
  },
  "schedule": {
    "level_a_minutes": 5,     // A 级源轮询间隔
    "level_b_minutes": 30,    // B 级源轮询间隔
    "quiet_hours": ["23:30", "07:00"],   // 免打扰，支持跨零点；留空不静音
    "max_run_seconds": 240
  },
  "push": {
    "merge": true,            // 多条合并成一条（关掉逐条推会被风控限流）
    "max_items": 8,           // 一条消息最多列几条，其余下轮继续
    "max_chars": 1800,
    "title_prefix": "吉大通知",
    "first_run_baseline_only": true,
    "max_per_run": 60
  },
  "channels": {
    "wecom":    { "enabled": true, "webhook": "...", "max_chars": 3800 },
    "wxpusher": { "enabled": true, "spt": "SPT_...",           // 极简推送，填这个就够
                  "app_token": "AT_...", "uids": ["UID_..."],  // 标准推送才需要
                  "topic_ids": [], "max_chars": 1700 },
    "pushplus": { "enabled": false, "token": "", "topic": "" }
  }
}
```

`channels.*.enabled` 为 `false` 或凭证没填时，该通道被静默跳过，
**另一个通道照发**，不会因为一个通道没配就整体失败。

## 部署

### macOS（launchd，每 5 分钟一轮）

```bash
cat > ~/Library/LaunchAgents/cn.jlu.notify.plist <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>cn.jlu.notify</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/python3</string>
    <string>/Users/你的用户名/jlu-notify/cli.py</string>
    <string>run</string><string>--once</string>
  </array>
  <key>WorkingDirectory</key><string>/Users/你的用户名/jlu-notify</string>
  <key>StartInterval</key><integer>300</integer>
  <key>StandardOutPath</key><string>/tmp/jlu-notify.out</string>
  <key>StandardErrorPath</key><string>/tmp/jlu-notify.err</string>
  <key>RunAtLoad</key><true/>
</dict>
</plist>
EOF
launchctl load ~/Library/LaunchAgents/cn.jlu.notify.plist
```

### GitHub Actions（推荐：不用自己电脑常开）

仓库自带 `.github/workflows/notify.yml`，每 5 分钟在 GitHub 的免费机器上跑一次
`cli.py run --once`，你的电脑可以随便关。**和下面的 launchd / systemd 二选一**，
两套同时跑会重复推送（各有一份数据库）。

> 本机这次是从 launchd 切过来的：`cn.jlu.notify` 已 `launchctl disable`，
> plist 挪到了 `~/Library/LaunchAgents/disabled/`，登录也不会自启。
> 想换回本机：先从 `state` 分支取回数据库（见本节末），再
> `mv ~/Library/LaunchAgents/disabled/cn.jlu.notify.plist ~/Library/LaunchAgents/`、
> `launchctl enable gui/$(id -u)/cn.jlu.notify`、
> `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/cn.jlu.notify.plist`。


1. 建仓库并推上去（`main` 分支已含 workflow）。
2. 配置进 Secret（`config.json` 在 `.gitignore` 里，绝不进仓库）：
   ```bash
   gh secret set CONFIG_JSON_B64 -R 你的用户名/jlu-notify -b "$(base64 -i config.json | tr -d '\n')"
   ```
   或网页：Settings → Secrets and variables → Actions → New repository secret，
   名字 `CONFIG_JSON_B64`，值是 `base64 -i config.json` 的输出（一行）。
3. 去重数据库放在 `state` 分支，workflow 每轮跑完 force push 回一条提交。
   第一次建议把本地 `data/notify.db` 推上去当基线，否则第一轮会按「首次运行」重新建基线；
   不种也行 —— 那样只影响「不补推历史通知」，不会出错。
4. 手动验证：`gh workflow run notify.yml` → `gh run watch`，或 Actions 页面点 Run workflow。

**必须知道的几个坑**

| 事项 | 说明 |
| --- | --- |
| 时区 | workflow 里的 `TZ: Asia/Shanghai` 别删。runner 是 UTC，删了免打扰 23:30–07:00 会错成北京时间 07:30–15:00 |
| 额度 | 公开仓库 Actions 免费不限量；私有仓库每月 2000 分钟，而 `*/5` 每轮至少按 1 分钟计（≈8600 分钟/月）会超，私有请改成 `*/30`（≈1440 分钟/月） |
| 延迟 | 官方 cron 最小 5 分钟，高峰期常延迟几分钟才启动，不是秒级实时 |
| 60 天无活动 | GitHub 会自动停用定时任务并发邮件；本仓库每轮都在更新 `state` 分支，所以不会触发 |
| `sources.json` | 已进仓库（只有公开栏目地址）；增删源直接改它提交即可 |
| 换令牌 | 只更新 Secret，不用动代码 |
| 并发 | workflow 已设 `concurrency`，同一时刻只会跑一个实例，避免同一条通知推两次 |

**想切回本机跑？** 先把云端那份数据库取回来，否则会重复推送：
```bash
git fetch origin state --depth=1 && git show FETCH_HEAD:notify.db > data/notify.db
```



```ini
# /etc/systemd/system/jlu-notify.service
[Unit]
Description=JLU notice push
[Service]
Type=oneshot
WorkingDirectory=/opt/jlu-notify
ExecStart=/usr/bin/python3 /opt/jlu-notify/cli.py run --once
```
```ini
# /etc/systemd/system/jlu-notify.timer
[Unit]
Description=Every 5 minutes
[Timer]
OnBootSec=1min
OnUnitActiveSec=5min
[Install]
WantedBy=timers.target
```
```bash
sudo systemctl enable --now jlu-notify.timer
```

### 手机 / 路由器（Termux、OpenWrt）

```bash
pkg install python && cd ~/jlu-notify && python3 cli.py run --loop
```
`--loop` 按 `level_a_minutes` 自己控节奏，进程常驻即可。

## 关于 OA（校内办公网）

`oa.jlu.edu.cn` 解析到 `10.100.18.18`，**纯内网地址**，校外（Wi-Fi、5G、Clash 全局）一律连不上。
手机企微能收到 OA 待办，是因为它走腾讯企微自己的内网应用代理，脚本无法复用那条通道。

两条路：

1. **校内常亮设备**（宿舍小主机、树莓派、实验室值班机）跑本程序 —— 最省事，直接通；
2. **先在校内探一次结构**（结构未知之前绝不臆造解析规则）：

```bash
pip3 install playwright && python3 -m playwright install chromium
python3 probe_oa.py          # 手动登录 OA，按回车，脚本 dump 列表页结构
python3 cli.py run --source "OA 办公网" --dry-run
```

`probe_oa.py` 把列表页存成 `data/oa_dump.html`，`sources/oa.py` 会优先读这个 dump。
拿到真实 DOM 后，把 `sources.json` 里 OA 那条 `"enabled": true` 即可。

## 请求量与礼貌

- 默认 92 个源、A 级 5 分钟 / B 级 30 分钟 → 平均**每分钟 3~4 个请求**，
  比一个新闻页自动刷新还轻。
- 每个请求前随机 sleep 0~1.5 秒，`workers` 默认 4，不形成尖峰。
- 只抓列表页，**不抓详情页正文**（详情链接已经够点进去了），流量极小。
- 全量 `discover.py` 约 600 个请求，是一次性操作，明确不放进定时任务。

## 合规红线

- 只轮询**公网无需登录的通知列表页**，等价于把「你手动刷网页」变成「程序定时刷」。
- **不**存学号密码、**不**自动提交登录表单、**不**绕验证码、**不**碰选课 / 成绩 / 教务内部接口。
- 抓取频率刻意压到很低，不并发轰炸、不构造压力。
- `config.json` / `sources.json` / `data/` 全在 `.gitignore` 里，webhook 与 appToken 不会误上传。

## 目录结构

```
jlu-notify/
├── cli.py            命令行：run / test-push / list-sources / stats
├── discover.py       一次性发现全校通知栏目，生成 sources.json
├── probe_oa.py       校内网探 OA 结构（手动登录）
├── login_once.py     需要登录的源：保存会话 cookie
├── parser.py         列表页解析（五种真实 DOM + 表格兜底）
├── store.py          SQLite：URL 去重、基线、游标、待推送队列
├── notifier.py       合并消息、免打扰、通道熔断、分发
├── common.py         urllib 直连、重试、日志、UTF-8 安全截断
├── config.py         配置加载与校验
├── channels/         wecom / wxpusher / pushplus
├── sources/          site（通用博达栏目）/ oa（内网）
├── tests/            夹具冒烟测试（93 项，不联网）
├── .github/workflows/ notify.yml（每 5 分钟推送）、test.yml（push 时跑测试）
├── sources.json      92 个源（进仓库，只有公开地址）
└── data/             notify.db、通道状态、dump（gitignore）
```

## 加一个自己的源

1. 浏览器确认栏目地址（右键看源码是不是 `<li>` 列表）；
2. 在 `sources.json` 的 `sources` 数组里加一条：

```json
{ "name": "物理学院", "url": "https://phy.jlu.edu.cn/xzgz/tzgg.htm",
  "level": "B", "type": "site", "enabled": true }
```

3. `python3 cli.py run --source 物理学院 --dry-run` 验证能抓出条目。

绝大多数吉大站点用 `type: "site"` 就够；若该栏目不是方括号日期格式，
加 `"detail_pattern"` 覆盖，或把 HTML 发出来调 `parser.py`。

## 常见问题

**Q：一条都没收到？**
先 `python3 cli.py test-push`。通了还收不到，就是没有新通知 ——
`python3 cli.py list-sources` 看游标，`run --dry-run` 看能不能抓到条目。

**Q：WxPusher 报「SPT 无效」或者根本没反应？**
1. `spt` 必须以 `SPT_` 开头、整串照抄（前后别带空格、别留中文说明）；
2. **不装客户端收不到**：手机 App 或桌面端至少登录一个（公众号已不是主通道）；
3. 令牌类错误会被熔断 1 小时，改完配置想立刻重试就先删 `data/channel_state.json`。

**Q：抓取失败 `HTTP 404` / 超时？**
校内域名有些只在校内可达（`bmb cw i jgdw jjw lab sjc tv xinfang xyk zzb`），
校外访问必然失败，把那些源的 `enabled` 改 `false` 即可。

**Q：想收到通知正文？**
本程序只推标题 + 链接，点进去看 —— 不抓正文就是为了不增加服务器压力。

**Q：Clash 全局模式下抓不到？**
不用管。`common.http_get` 对目标域名强制 `ProxyHandler({})`，绕过 TUN 的 fake-ip；
只对被墙域名才走系统代理。
