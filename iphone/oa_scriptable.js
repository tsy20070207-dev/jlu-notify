// Variables used by Scriptable.
// These must be at the very top of the file. Do not edit.
// icon-color: blue; icon-glyph: bell;

/**
 * 吉大 OA 通知 → 微信（wxpusher）
 *
 * 为什么用 Scriptable：iOS 快捷指令的「在活跃标签页中运行 JavaScript」动作
 * 在分享扩展里承载不了真实工作，反复报「无法在网页中运行JavaScript」。
 * Scriptable 的 WebView 是普通 WKWebView，cookie 会留在 App 自己的
 * WKWebsiteDataStore 里，首次登录 WebVPN 之后就能一直复用，脚本长度也不受限。
 *
 * 首次运行：会弹出 WebVPN 登录页，登录一次（有验证码也在里面填）。
 * 之后运行：直接抓取，只有会话过期时才会再让你登录。
 */

const HASH = '48714f71342f7a336d582f7e2857373750cd3d1004df80a0b5971c1b1a';
const BOARDS = [
  ['179577', '校内通知'],
  ['179578', '校内快讯'],
  ['179579', '教学信息'],
  ['179581', '学生天地'],
];
const SPT = 'SPT_Jt2GuBJBdKO9uTcP8Nw2nqEdcqe0';
const MAX_PUSH = 10;        // 一次最多推几条
const MAX_AGE_DAYS = 3;     // 超过这么多天的旧公告不推
const SEEN_MAX = 3000;

const fm = FileManager.local();
const SEEN_PATH = fm.joinPath(fm.documentsDirectory(), 'oa_seen.json');

function loadSeen() {
  if (!fm.fileExists(SEEN_PATH)) return null;
  try {
    return JSON.parse(fm.readString(SEEN_PATH));
  } catch (e) {
    return null;
  }
}

function saveSeen(keys) {
  fm.writeString(SEEN_PATH, JSON.stringify(Array.from(new Set(keys)).slice(-SEEN_MAX)));
}

// 在 WebView 里用同步 XHR 抓四个板块并解析，只把结果传回来（传 HTML 太大）
function grabScript() {
  const ids = JSON.stringify(BOARDS.map((b) => b[0]));
  return `
  (function () {
    var ids = ${ids};
    var prefix = '/https/${HASH}/defaultroot/PortalInformation!jldxList.action?channelId=';
    var out = [];
    for (var i = 0; i < ids.length; i++) {
      var x = new XMLHttpRequest();
      try {
        x.open('GET', prefix + ids[i], false);
        x.send();
      } catch (e) {
        out.push({ id: ids[i], board: ids[i], rows: [], err: String(e) });
        continue;
      }
      var h = x.responseText || '';
      // 注意：WebVPN 会给每个页面注入 wengine-vpn 的脚本/样式，
      // 拿它判断登录态会把正常页面全判成过期。
      // 列表页一定带 channelName，登录页没有，这才是可靠判据。
      if (h.length < 4096 || h.indexOf('channelName') === -1) {
        out.push({ id: ids[i], board: ids[i], rows: [], expired: true });
        continue;
      }
      var nm = h.match(/channelName\\s*=\\s*'([^']+)'/);
      var doc = new DOMParser().parseFromString(h, 'text/html');
      var rows = [];
      var blocks = doc.querySelectorAll('div.li');
      for (var j = 0; j < blocks.length; j++) {
        var blk = blocks[j];
        var a = blk.querySelector('a.font14') || blk.querySelector('a[href*="getInformation"]');
        if (!a) continue;
        var t = (a.textContent || '').replace(/\\s+/g, ' ').trim();
        var hr = a.getAttribute('href') || '';
        if (!t || !hr) continue;
        var m = hr.match(/[?&]id=(\\d+)/);
        var tm = blk.querySelector('.time');
        rows.push({
          key: 'oa:' + (m ? m[1] : t),
          title: t,
          url: new URL(hr, location.href).href,
          date: ((tm && tm.textContent) || '').replace(/\\s+/g, ' ').trim(),
          board: nm ? nm[1] : ids[i]
        });
      }
      out.push({ id: ids[i], board: nm ? nm[1] : ids[i], rows: rows, expired: false });
    }
    return JSON.stringify(out);
  })()
  `;
}

async function canFetch(wv) {
  const r = await wv.evaluateJavaScript(`
    (function () {
      var x = new XMLHttpRequest();
      try {
        x.open('GET', '/https/${HASH}/defaultroot/PortalInformation!jldxList.action?channelId=179577', false);
        x.send();
      } catch (e) {
        return JSON.stringify({ ok: false, err: String(e) });
      }
      var h = x.responseText || '';
      return JSON.stringify({ ok: h.indexOf('channelName') > -1 && h.length > 4096, len: h.length });
    })()
  `);
  return JSON.parse(r);
}

async function grabAll(wv) {
  return JSON.parse(await wv.evaluateJavaScript(grabScript()));
}

async function pushWechat(text) {
  const req = new Request('https://wxpusher.zjiecode.com/api/send/message/simple-push');
  req.method = 'POST';
  req.headers = { 'Content-Type': 'application/json' };
  req.body = JSON.stringify({ spt: SPT, content: text });
  try {
    const j = await req.loadJSON();
    return !!(j && j.code === 1000);
  } catch (e) {
    return false;
  }
}

function ageDays(item) {
  const m = (item.date || '').match(/(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})/);
  if (!m) return 0;            // 读不出日期就当新的，宁可多推
  const d = new Date(+m[1], +m[2] - 1, +m[3]);
  return (Date.now() - d.getTime()) / 86400000;
}

// 前台（在 Scriptable 里点运行）弹框；后台（快捷指令自动化触发）弹不出来，
// 所以改成发本地通知，并把结果写进快捷指令输出，方便排查
async function finish(title, body) {
  const msg = body || '';
  if (config.runsInApp) {
    const a = new Alert();
    a.title = title;
    a.message = msg;
    a.addAction('好');
    await a.present();
    return;
  }
  const n = new Notification();
  n.title = title;
  n.body = msg;
  n.sound = 'default';
  await n.schedule();
  Script.setShortcutOutput(title + '\n' + msg);
}

async function main() {
  const wv = new WebView();
  await wv.loadURL('https://vpn.jlu.edu.cn/');

  let probe = await canFetch(wv);
  if (!probe.ok) {
    // 只有在前台运行时才能把登录页交给用户点；后台（自动化）弹不出 WebView，
    // 只能发通知让他自己打开 Scriptable 登录
    if (config.runsInApp) {
      await wv.present();
      await wv.loadURL('https://vpn.jlu.edu.cn/');
      probe = await canFetch(wv);
    }
    if (!probe.ok) {
      await finish('需要重新登录 WebVPN',
        'Scriptable 里的 WebVPN 会话过期了（页面 ' + probe.len + ' 字节）。\n' +
        '打开 Scriptable 手动运行一次「OA通知」，在弹窗里登录即可。');
      return;
    }
  }

  const groups = await grabAll(wv);
  const all = [];
  const counts = [];
  let expired = false;
  groups.forEach((g) => {
    if (g.expired) expired = true;
    counts.push(g.board + ' ' + g.rows.length);
    g.rows.forEach((x) => all.push(x));
  });
  const stat = counts.join('｜');

  if (expired && !all.length) {
    await finish('OA 登录已过期', '请在 Scriptable 里重新登录 WebVPN 后再运行。\n' + stat);
    return;
  }

  const seenArr = loadSeen();
  if (!seenArr) {
    saveSeen(all.map((x) => x.key));
    await finish('首次运行，已建立基线',
      '已记住 ' + all.length + ' 条作为基线，之后只推新的。\n' + stat);
    return;
  }

  const seen = new Set(seenArr);
  const fresh = all.filter((x) => !seen.has(x.key));
  if (!fresh.length) {
    await finish('没有新通知', '共查 ' + all.length + ' 条\n' + stat);
    return;
  }

  const usable = fresh.filter((x) => ageDays(x) <= MAX_AGE_DAYS);
  const oldCount = fresh.length - usable.length;

  if (!usable.length) {
    saveSeen(seenArr.concat(fresh.map((x) => x.key)));
    await finish('没有要推的新通知',
      '抓到 ' + fresh.length + ' 条，但都超过 ' + MAX_AGE_DAYS + ' 天了。');
    return;
  }

  const top = usable.slice(0, MAX_PUSH);
  let text = top.map((x) =>
    '【' + x.board + '】' + x.title + '\n' + (x.date ? x.date + '\n' : '') + x.url
  ).join('\n\n');
  if (usable.length > MAX_PUSH) {
    text = '新通知 ' + usable.length + ' 条，先看前 ' + MAX_PUSH + ' 条：\n\n' + text;
  }
  if (oldCount > 0) {
    text += '\n\n（另有 ' + oldCount + ' 条超过 ' + MAX_AGE_DAYS + ' 天，已忽略）';
  }

  const ok = await pushWechat(text);
  if (ok) {
    // 只有推送成功才记为已读，失败的话下次还会重试
    saveSeen(seenArr.concat(fresh.map((x) => x.key)));
    await finish('已推送到微信', '推送了 ' + top.length + ' 条\n' + stat);
  } else {
    await finish('微信推送失败', '网络或 wxpusher 出错，下次运行会重试。\n' + stat);
  }
}

await main();
