/*
 * 吉林大学 OA 四个板块抓取（在 Safari 里跑，依赖已登录的 WebVPN 会话）
 *
 * 由 iphone/build_shortcut.py 塞进「在活跃的 Safari 标签页中运行 JavaScript」动作里。
 * 单独放一份在这里，方便改；改完重新跑 build_shortcut.py 即可。
 *
 * 前提：这个页面必须是 vpn.jlu.edu.cn 域名下的页面（否则 fetch 跨域失败）。
 * 推送直接在 JS 里做——wxpusher 的接口对跨域全开，省掉在快捷指令里拼变量的麻烦。
 */

const IDS = ['179577', '179578', '179579', '179581'];

// WebVPN 把主机名编码进了路径，同一个站点复用同一串，四个板块只要换 channelId
const PREFIX = 'https://vpn.jlu.edu.cn/https/' +
  '48714f71342f7a336d582f7e2857373750cd3d1004df80a0b5971c1b1a' +
  '/defaultroot/PortalInformation!jldxList.action?channelId=';

// wxpusher 的「简单推送」令牌，和 config.json 里 channels.wxpusher.spt 是同一个
const PUSH_SPT = 'SPT_Jt2GuBJBdKO9uTcP8Nw2nqEdcqe0';
const PUSH_URL = 'https://wxpusher.zjiecode.com/api/send/message/simple-push';

const SEEN_KEY = 'oa_seen_v1';
const MAX_PUSH = 10;        // 一次最多推几条
const MAX_AGE_DAYS = 3;     // 超过这个天数的旧通知不推（防止首次误推一堆老公告）

function grab(id) {
  return fetch(PREFIX + id, { credentials: 'include' }).then(function (res) {
    return res.text().then(function (html) {
      // 登录过期：VPN 网关会直接回登录页，抓不到通知
      const expired = html.length < 4096 ||
        html.indexOf('wengine-vpn') > -1 ||
        html.indexOf('/login') > -1 && html.indexOf('channelName') === -1;
      if (expired) {
        return { id: id, board: id, rows: [], expired: true };
      }
      const nm = html.match(/channelName\s*=\s*'([^']+)'/);
      const board = nm ? nm[1] : id;
      const doc = new DOMParser().parseFromString(html, 'text/html');
      const rows = [];
      doc.querySelectorAll('div.li').forEach(function (blk) {
        const a = blk.querySelector('a.font14') || blk.querySelector('a[href*="getInformation"]');
        if (!a) return;
        const title = (a.textContent || '').replace(/\s+/g, ' ').trim();
        const href = a.getAttribute('href') || '';
        if (!title || !href) return;
        const idm = href.match(/[?&]id=(\d+)/);
        const t = blk.querySelector('.time');
        rows.push({
          key: 'oa:' + (idm ? idm[1] : title),
          title: title,
          url: new URL(href, location.href).href,
          date: ((t && t.textContent) || '').replace(/\s+/g, ' ').trim(),
          board: board
        });
      });
      return { id: id, board: board, rows: rows, expired: false };
    });
  });
}

function parseDate(s) {
  const m = (s || '').match(/(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})/);
  if (!m) return null;
  return new Date(+m[1], +m[2] - 1, +m[3]);
}

function isOld(item) {
  const d = parseDate(item.date);
  if (!d) return false;                    // 日期读不出来就按新的算，宁可多推
  return (Date.now() - d.getTime()) > MAX_AGE_DAYS * 86400000;
}

function pushWechat(text) {
  return fetch(PUSH_URL, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'omit',
    body: JSON.stringify({ spt: PUSH_SPT, content: text })
  }).then(function (res) {
    return res.json();
  }).then(function (j) {
    return !!(j && j.code === 1000);
  }).catch(function () {
    return false;
  });
}

function saveSeen(keys) {
  const merged = new Set(keys);
  localStorage.setItem(SEEN_KEY, JSON.stringify(Array.from(merged).slice(-3000)));
}

Promise.all(IDS.map(function (id) {
  return grab(id).catch(function () { return { id: id, board: id, rows: [], expired: true }; });
})).then(function (groups) {
  const all = [];
  const counts = [];
  let expired = false;
  groups.forEach(function (g) {
    if (g.expired) expired = true;
    counts.push(g.board + ' ' + g.rows.length);
    g.rows.forEach(function (x) { all.push(x); });
  });

  if (expired && !all.length) {
    pushWechat('⚠️ **吉大 OA 登录已过期**\n请在 Safari 里打开 vpn.jlu.edu.cn 重新登录，然后点一次「OA通知」。');
    completion('⚠️ VPN 或 OA 登录已过期\n请在 Safari 里打开 vpn.jlu.edu.cn 重新登录，然后再点一次。');
    return;
  }

  const raw = localStorage.getItem(SEEN_KEY);
  if (!raw) {
    // 第一次跑：只记基线，不推（否则一上来就是几十条旧公告）
    saveSeen(all.map(function (x) { return x.key; }));
    completion('✅ 首次运行，已记住 ' + all.length + ' 条作为基线，从这里开始只推新的。\n' +
      counts.join('｜'));
    return;
  }

  const seen = new Set(JSON.parse(raw));
  const fresh = all.filter(function (x) { return !seen.has(x.key); });

  if (!fresh.length) {
    completion('没有新通知（共查 ' + all.length + ' 条）\n' + counts.join('｜'));
    return;
  }

  const usable = fresh.filter(function (x) { return !isOld(x); });
  const oldCount = fresh.length - usable.length;

  if (!usable.length) {
    saveSeen(Array.from(seen).concat(fresh.map(function (x) { return x.key; })));
    completion('抓到 ' + fresh.length + ' 条，但都超过 ' + MAX_AGE_DAYS + ' 天了，不推。');
    return;
  }

  const top = usable.slice(0, MAX_PUSH);
  const body = top.map(function (x) {
    return '**【' + x.board + '】** ' + x.title + '\n' + (x.date ? x.date + '\n' : '') + x.url;
  }).join('\n\n');

  let text = body;
  if (usable.length > MAX_PUSH) {
    text = '新通知 ' + usable.length + ' 条，先看前 ' + MAX_PUSH + ' 条：\n\n' + body;
  }
  if (oldCount > 0) {
    text += '\n\n（另有 ' + oldCount + ' 条超过 ' + MAX_AGE_DAYS + ' 天，已忽略）';
  }

  pushWechat(text).then(function (ok) {
    if (ok) {
      // 只有推送成功才记为已读，失败的话下次还会重试
      saveSeen(Array.from(seen).concat(fresh.map(function (x) { return x.key; })));
      completion('✅ 已推送 ' + top.length + ' 条到微信' +
        (usable.length > MAX_PUSH ? '（共 ' + usable.length + ' 条新的，剩下的下次/晚间汇总再说）' : ''));
    } else {
      completion('❌ 微信推送失败（网络问题），下次再点会重试。\n\n' + body);
    }
  });
}).catch(function (err) {
  // 任何未预料的异常都要兜住：不调 completion 的话，快捷指令只会甩一句
  // 「脚本完成时必须调用函数 completion(result)」，什么线索都没有
  completion('❌ 脚本出错：' + (err && err.message ? err.message : String(err)));
});
