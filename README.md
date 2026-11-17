# 吉大通知 → 企业微信 / 微信

把吉林大学各单位的「通知公告」抓下来，合并成一条消息推到**企业微信群机器人**、**WxPusher** 或**个人微信**。
不用装 App、不用交学号密码、不自动登录 —— 就是一份低频只读的轮询脚本 + 三个可选推送通道。

```
OA 四个板块 + 校内栏目页  →  按块解析  →  SQLite 去重  →  降噪  →  合并成 1 条  →  企微群 + 微信
```

## 它做什么

| 能力 | 说明 |
| --- | --- |
| 主力是 OA | 电子校务平台（`oa.jlu.edu.cn`）的**校内通知 / 学生天地 / 校内快讯 / 教学信息** 四个板块，服务端渲染、免登录，30 条/页 |
| 兜底覆盖 | 学校机构设置页的 118 个单位站点里，自动测出 92 个可解析的「通知公告」栏目（`sources.json` 里默认只开 4 个 A 级源，其余留着一键启用） |
| 多通道 | 企业微信群机器人（20 条/分，无日上限）、WxPusher（SPT 极简 / appToken 标准）、PushPlus，互不影响 |
| 不重复 | 以详情页 URL / OA 条目 id 为唯一键，同一条通知被三个站点转载也只推一次 |
| 不轰炸 | 首次运行只建基线不推送；多条合并成一条；23:30–07:00 不打扰，早上合并补发 |
| 有降噪 | 过期条目、噪音关键词、死链（404/410）直接丢弃；每个源每轮最多推 4 条，其余进晚间汇总 |
| 分级轮询 | A 级（OA 四个板块/学校通知/本科生院）5 分钟，B 级（其余院系）30 分钟 |
| 直连校内 | 强制 `ProxyHandler({})`，绕过 Clash / TUN 的 fake-ip，不需要配 NO_PROXY |

## 覆盖范围（2026-11 实测）

- 学校机构设置页列出 **118** 个单位站点，其中 **109 个公网可直连**；
  余下 9 个（`bmb cw jgdw jjw lab sjc xinfang xyk zzb`）只能内网访问或已下线。
- 109 个可直连站点里 **92 个**的通知栏目能解析出条目，全部记在 `sources.json` 里
  （95 条记录 = 4 个 OA 板块 + 91 个站点源；默认启用 8 个：4 个 OA + 4 个公网 A 级）。
  其余 87 个站点源都在文件里，把 `"enabled": false` 改回 `true` 就能启用。
- **主力换成了 OA**：`oa.jlu.edu.cn` 的电子校务平台有 4 个板块
  （`channelId` = 179577 校内通知 / 179578 学生天地 / 179579 校内快讯 / 179581 教学信息），
  列表页是服务端渲染的 `div.li` 块，免登录、30 条/页，日期只写「今天/昨天/前天」
  或 `2026-11-13`，详情链接 `PortalInformation!getInformation.action?id=<id>&channelId=<ch>`。
  OA 是内网源，只能由校园网那台机器抓（见下文「OA 怎么跑」）。
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
| `python3 cli.py digest --dry-run` | 预览「今天所有通知」的汇总消息（不发送） |
| `python3 cli.py digest` | 立刻发一份今天的汇总（不受 22:30 限制） |
| `python3 cli.py digest --date 2026-11-15` | 补看/补发某一天的汇总 |
| `python3 discover.py --write` | 重新发现全部通知栏目（联网，约 1 分钟） |
| `python3 oa_check.py` | 摸 OA 四个板块：要不要登录、板块叫什么、抓到几条（只读，不改任何东西） |
| `python3 oa_check.py --write-sources --save-fixtures` | 把板块名写回 `sources.json`，同时把页面存成离线夹具 |
| `python3 oa_watch.py --status` | 确认这台机器能不能连 OA（TCP 探测，不产生访问日志） |
| `python3 oa_watch.py` | 校园网机器上跑一轮 OA（launchd / 计划任务每 15 分钟调） |
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
    "max_run_seconds": 240    // 单轮时间上限：到点还没轮上的源本轮跳过（不算失败）
  },
  "push": {
    "merge": true,            // 多条合并成一条（关掉逐条推会被风控限流）
    "max_items": 8,           // 一条消息最多列几条，其余下轮继续
    "max_chars": 1800,
    "title_prefix": "吉大通知",
    "first_run_baseline_only": true,
    "max_per_run": 60,
    "max_age_days": 3,        // 发布日期早于「今天-N 天」的丢弃，0=不限（治旧通知乱推）
    "per_source_limit": 4,    // 每个源每轮最多推几条，0=不限；超额的不丢，进 22:30 汇总
    "title_skip_keywords": [],// 标题含这些词就丢，例 ["招标","采购","中标","讣告"]
    "check_links": true       // 发之前 HEAD 抽检，404/410 的丢掉；连不上不算死链
  },
  "digest": {
    "enabled": true,          // 每天固定时间再发一条「当日全部通知」汇总
    "at": "22:30",            // 北京时间；必须早于上面 quiet_hours 的起点
    "max_items": 40,          // 汇总最多取多少条
    "max_chars": 1700,        // 正文预算（还会与通道 max_chars 取较小值）
    "send_empty": false       // 当天 0 条时要不要也发一条「今天没通知」
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

### 每日汇总（`digest`）

实时推送照旧；另外每天到 `digest.at`（默认 **22:30 北京时间**）之后，
**随下一轮抓取**把当天所有通知拼成一条汇总再推一次：

- 前面若干条带链接，剩下的降级成「仅标题」清单，一天再多条也不会漏；
- 同一天只发一次（`meta.digest_last_date`），重复触发 / 手动点 Run 都不会重发；
- 发失败不写标记，下一轮 5 分钟后自动重试（直到 23:30 进免打扰）；
- 当天 0 条新通知就跳过，不打扰；
- 22:30 之后才出现的新通知不会进当天汇总（会被单条推给你），想覆盖更全就调早/调晚。

不需要额外定时器：它挂在现有的「每 5 分钟一轮」里，GitHub Actions 那边什么都不用改。
想看效果：`python3 cli.py digest --dry-run`（只打印不发）。

## 部署

> 三选一：**GitHub Actions（推荐，电脑关机也能收）**、macOS launchd、Linux systemd。
> 同一条通知两边各有一份数据库，同时开会重复推送。

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

仓库自带 `.github/workflows/notify.yml`，定时在 GitHub 的免费机器上跑
`cli.py run --once`，你的电脑可以随便关。默认 cron 是 15 分钟一次的**兜底**；
要 5 分钟节奏就按下面「兜底：外部 5 分钟触发」加一个外部定时器。和上面的 macOS launchd、下面的 Linux systemd
**三选一**，同时开两套会重复推送（各有一份数据库）。

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
| 额度 | 公开仓库 Actions 免费不限量；私有仓库每月 2000 分钟，`*/15` 每轮至少按 1 分钟计（≈2900 分钟/月）刚好够，想保险可放成 `*/30`（≈1440 分钟/月） |
| 延迟 | cron 最小 5 分钟；官方文档说「整点是负载高峰，可能延迟甚至丢队列」。默认 cron 是 `*/15` **只当兜底**，主力是外部定时器打 `workflow_dispatch`（见下）。⚠️ 实测本仓库的 schedule 注册后 **2 小时 19 分、9 个触发点一次都没下发**，别把定时寄托在它身上 |
| 60 天无活动 | GitHub 会自动停用定时任务并发邮件；本仓库每轮都在更新 `state` 分支，所以不会触发 |
| `sources.json` | 已进仓库（只有公开栏目地址）；增删源直接改它提交即可 |
| 换令牌 | 只更新 Secret，不用动代码 |
| 并发 | workflow 已设 `concurrency`，同一时刻只会跑一个实例，避免同一条通知推两次 |

**触发器分两层，先搞清楚谁负责什么**

| 层 | 谁干 | 跑在哪 |
| --- | --- | --- |
| 执行层：抓取 → 去重 → 推送 | GitHub 的免费机器 | 云端，跟你的电脑无关 |
| 触发层：每 N 分钟喊一声「开始干活」 | 外部定时器（cron-job.org） | 云端，跟你的电脑无关 |

令牌在 Secret 里、去重库在 `state` 分支里，**所以电脑开不开机都不影响通知**。
唯一的问题是「谁按时喊那一声」—— 实测 GitHub 自带的 `schedule` 在本仓库不干活
（注册后 2 小时 19 分、9 个触发点，0 次），所以主力靠外部定时器。

**① 一把只开一个门的钥匙（fine-grained PAT）**

直达链接：<https://github.com/settings/personal-access-tokens/new>

| 字段 | 值 |
| --- | --- |
| Token name | `jlu-notify-cron` |
| Expiration | 90 days |
| Resource owner | 你的账号 |
| Repository access | Only select repositories → 只勾 `jlu-notify` |
| Repository permissions | 只把 **Actions** 改成 **Read and write**，其余不动 |

生成后那串 `github_pat_...` 只显示一次，立刻复制。

**② cron-job.org 建任务**

注册（要邮箱收验证码）→ **CREATE CRONJOB**：

| 字段 | 值 |
| --- | --- |
| URL | `https://api.github.com/repos/<owner>/<repo>/actions/workflows/notify.yml/dispatches` |
| Schedule | Every 5 minutes（或 Custom `*/5 * * * *`） |
| Request method | `POST` |
| Request body | `{"ref":"main"}` |
| Headers | `Authorization: Bearer github_pat_...`<br>`Accept: application/vnd.github+json`<br>`Content-Type: application/json` |

存盘后点 **TEST RUN**，状态码 **204** 就是成功（204 = GitHub 收下，没有返回体）。

已知限制（官方 FAQ）：执行超时 30 秒；自定义 `User-Agent` 会被忽略；
连续失败 25 次会自动停用该任务（可开邮件通知）；免费版最小间隔 1 分钟。

**③ 两条触发撞车也不会双推**：`concurrency` 串行化，后一轮发现没新通知直接退出。

**自检三行**：

```bash
gh run list -R <owner>/<repo> --limit 5                                                    # 最近几轮（看是不是每 5 分钟一轮 workflow_dispatch）
gh api repos/<owner>/<repo>/branches/state --jq .commit.commit.committer.date              # 数据库最后更新时间
gh workflow run test-push.yml -R <owner>/<repo>                                            # 手动发一条测试推送，验证通道
```

**通道自测**：Actions 页面 → 左侧 **测试推送** → **Run workflow**，
手机应立即收到「吉大通知 · 通道自测」。日志里 `[wxpusher] OK 已送达 WxPusher` 只代表
WxPusher 接口收下了；**没装 WxPusher 接收端（App / 桌面客户端）就收不到**，公众号不是主通道。

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

## OA 怎么跑（主力源）

`oa.jlu.edu.cn` 解析到 `10.100.18.18`，**纯内网地址**：GitHub Actions 在公网永远连不上，
校外 Wi-Fi / 5G 也不行。所以分工是：

- **校园网那台机器**（宿舍小主机、实验室值班机、常开的笔记本）跑 `oa_watch.py`，负责 4 个 OA 板块；
- **GitHub Actions** 只管公网源 —— workflow 里设了 `JLU_NOTIFY_SKIP_CAMPUS=1`，
  `cli.py` 会自动跳过 `"needs_campus": true` 的源，不会每轮 4 个源全失败。

### 第一次部署（只做一遍）

```bash
python3 oa_watch.py --status          # 1. 这台机器能不能连 OA（TCP 探测）
python3 oa_check.py                   # 2. 摸四个板块：要登录吗？板块叫什么？抓到几条？
python3 oa_check.py --write-sources --save-fixtures   # 3. 板块名写回 sources.json + 存离线夹具
python3 oa_watch.py --dry-run         # 4. 抓一轮看看（首次建基线，不会响手机）
```

`oa_check.py` 第 2 步的输出就是结论：四个板块都显示「免登录 + 30 条」就可以直接跑；
显示 `HTTP 403` / 跳登录页，就走下面的「需要登录怎么办」。

### 挂到定时任务

```bash
# cron 每 15 分钟一次（A 级 5 分钟对云端公网源有意义，OA 用 15 分钟足够）
*/15 * * * * cd ~/jlu-notify && /usr/bin/python3 oa_watch.py >> data/oa_watch.log 2>&1
```

`oa_watch.py` 先做一次 TCP 探测：连不上就 `exit 0` 静默退出（出门用 4G 时不会刷红），
连得上才 `cli.py run --once --force --source "OA·…"`，只跑 OA 的那几个源。

### 需要登录怎么办

用 `login_once.py` 手动登录一次存 cookie，再给源加一行
`"cookie_file": "data/oa_session.json"`，`sources/oa.py` 会带上这个 Cookie 请求头。
**不**自动登录、**不**绕验证码。

### 四个板块是怎么抓的

列表页 `PortalInformation!jldxList.action?channelId=<ch>`，服务端渲染、30 条/页，
翻页是 `?1=1&channelId=<ch>&startPage=2`。单条的 DOM：

```html
<DIV class="li rel"><I></I>
  <A class="font14" href="PortalInformation!getInformation.action?id=72089970&channelId=179577">标题</A>
  ... <A class="column" href="...">商学与管理学院</A>
  ... <SPAN class="time">今天&nbsp;&nbsp; 10:39</SPAN>
</DIV>
```

时间只有「今天 / 昨天 / 前天 / 2026-11-13」三种写法，前两种按本地日期换算。
踩过的坑：切块不能按 class 前缀匹配 `div.li`（页头有 `class="link_r"`，会多切出假块），
必须用 `\bli\b`；所以不要拿 `probe_oa.py` 那套 Playwright 的思路重写 —— 当初用无头浏览器
是因为结构未知，现在用 urllib + 正则就够，不用装 Chromium。

## 请求量与礼貌

- 默认启用 8 个源（4 个 OA 板块 + 4 个公网 A 级），OA 每源抓 2 页（60 条），
  一轮约 12 个请求；校园网机器 15 分钟一轮、云端 15 分钟一轮 → 平均**每分钟不到 2 个请求**，
  比一个新闻页自动刷新还轻。
- 每个请求前随机 sleep 0~1.5 秒，`workers` 默认 4，不形成尖峰。
- 只抓列表页，**不抓详情页正文**；只有 `check_links` 会对「本轮真要发的 ≤8 条」做一次 HEAD。
- 全量 `discover.py` 约 600 个请求，是一次性操作，明确不放进定时任务。

## 合规红线

- 只轮询**公网无需登录的通知列表页**，等价于把「你手动刷网页」变成「程序定时刷」。
- **不**存学号密码、**不**自动提交登录表单、**不**绕验证码、**不**碰选课 / 成绩 / 教务内部接口。
- 抓取频率刻意压到很低，不并发轰炸、不构造压力。
- `config.json` / `sources.json` / `data/` 全在 `.gitignore` 里，webhook 与 appToken 不会误上传。

## 目录结构

```
jlu-notify/
├── cli.py            命令行：run / test-push / list-sources / stats / digest
├── discover.py       一次性发现全校通知栏目，生成 sources.json
├── oa_check.py       摸 OA 四个板块（免登录？板块名？条数？）+ 写回 sources.json
├── oa_watch.py       校园网侧守护：连得上 OA 才跑一轮，连不上静默退出
├── probe_oa.py       早期探针（Playwright，结构未知时用；现在不必要）
├── login_once.py     需要登录的源：保存会话 cookie
├── filters.py        降噪闸门：过期 / 噪音词 / 单源限流 / 死链
├── parser.py         列表页解析（五种真实 DOM + 表格兜底 + 标题长度治理）
├── store.py          SQLite：URL 去重、基线、游标、待推送队列、隐藏标记
├── notifier.py       合并消息、免打扰、通道熔断、分发
├── common.py         urllib 直连、重试、日志、UTF-8 安全截断
├── config.py         配置加载与校验
├── channels/         wecom / wxpusher / pushplus
├── sources/          site（通用博达栏目）/ oa（OA 四板块，channelId 驱动）
├── iphone/           纯手机方案：oa.js（浏览器里跑）+ build_shortcut.py + 签好的快捷指令
├── tests/            夹具冒烟测试（132 项，不联网）
├── .github/workflows/ notify.yml（定时推送）、test-push.yml（手动测试推送）、test.yml（push 时跑测试）
├── sources.json      95 条源（4 个 OA + 91 个站点，进仓库，只有公开地址）
└── data/             notify.db、通道状态、dump（gitignore）
```

## 手机端：iPhone 快捷指令（不依赖服务器）

OA 那四个板块要登录态，云端 IP 抓不到；校内服务器又不可能 24 小时开着。所以给 OA 单独做了一条纯手机路线：

**原理**：快捷指令里放一个「在活跃的 Safari 标签页中运行 JavaScript」动作，脚本在**已经登录 WebVPN 的 Safari 页面**里跑，用页面自身的 cookie 去 fetch 四个板块，解析完直接调 wxpusher 接口推微信。会话、跨域、令牌全在浏览器里解决，不需要任何后端。

**用法**（一次性配置，之后就是一个按钮）：

1. 双击 `iphone/OA通知.shortcut` 导入（已签名，iOS 15+ 可直接添加）；
2. iPhone 上 Safari 登录一次 `vpn.jlu.edu.cn`（勾「记住设备」，能撑很久）；
3. 点「OA通知」→ 第一次只记基线不推送，之后有新通知直接进微信。

**跑起来但一条没推？** 看快捷指令最后弹出的文字：

| 弹窗 | 含义 |
|---|---|
| `✅ 首次运行，已记住 120 条作为基线` | 正常，之后只推新的 |
| `没有新通知（共查 120 条）` | 正常，四个板块都抓到了 |
| `⚠️ VPN 或 OA 登录已过期` | Safari 里的 WebVPN 会话掉了，重登一次 |
| `❌ 微信推送失败` | 网络问题，没记已读，下次会重试 |

**改脚本后要重新签名**（`shortcuts sign` 需要 macOS，只在改代码时用一次）：

```bash
python3 iphone/build_shortcut.py      # 生成 + 签名，产出 iphone/OA通知.shortcut
```

脚本主体在 `iphone/oa.js`，改完重跑上面这条命令、把新文件 Airdrop 到手机重新导入即可。
已读记录存在 Safari 的 `localStorage['oa_seen_v1']`（上限 3000 条），只有推送成功才写，
所以推送失败不会丢通知。

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
