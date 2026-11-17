/* 同步版：iOS 的「运行 JavaScript」动作只给很短的等待时间，
   异步 fetch 来不及返回就报「无法在网页中运行JavaScript」。
   改成同步 XHR 后，脚本会阻塞到数据到手，再同步调 completion。 */
(function () {
  var H = (location.href.match(/\/https\/([0-9a-f]+)\//) || [])[1] ||
    '48714f71342f7a336d582f7e2857373750cd3d1004df80a0b5971c1b1a';
  var P = 'https://vpn.jlu.edu.cn/https/' + H +
    '/defaultroot/PortalInformation!jldxList.action?channelId=';
  var IDS = ['179577', '179578', '179579', '179581'];
  var W = 'https://wxpusher.zjiecode.com/api/send/message/simple-push';
  var SPT = 'SPT_Jt2GuBJBdKO9uTcP8Nw2nqEdcqe0';
  var SK = 'oa_seen_v1', MAXN = 10, MAXD = 3;

  // 同步请求：第三参数 false。跨域时不能带自定义 header（会触发预检，同步预检被禁止）
  function req(m, u, d) {
    var x = new XMLHttpRequest();
    x.open(m, u, false);
    x.send(d || null);
    return x.responseText;
  }

  function bar(t, c) {
    try {
      var o = document.getElementById('oab');
      if (o && o.parentNode) o.parentNode.removeChild(o);
      var d = document.createElement('div');
      d.id = 'oab';
      d.style.cssText = 'position:fixed;top:0;left:0;right:0;z-index:2147483647;padding:16px;' +
        'font:15px/1.5 -apple-system;color:#fff;white-space:pre-wrap;background:' + c;
      d.textContent = t;
      (document.body || document.documentElement).appendChild(d);
    } catch (e) {}
  }

  function grab(id) {
    var h = req('GET', P + id);
    if (h.length < 4096 || h.indexOf('wengine-vpn') > -1 ||
        (h.indexOf('/login') > -1 && h.indexOf('channelName') === -1)) return null;
    var nm = h.match(/channelName\s*=\s*'([^']+)'/);
    var doc = new DOMParser().parseFromString(h, 'text/html');
    var rows = [];
    doc.querySelectorAll('div.li').forEach(function (blk) {
      var a = blk.querySelector('a.font14') || blk.querySelector('a[href*="getInformation"]');
      if (!a) return;
      var t = (a.textContent || '').replace(/\s+/g, ' ').trim();
      var hr = a.getAttribute('href') || '';
      if (!t || !hr) return;
      var m = hr.match(/[?&]id=(\d+)/);
      var tm = blk.querySelector('.time');
      rows.push({
        k: 'oa:' + (m ? m[1] : t), t: t, u: new URL(hr, location.href).href,
        d: ((tm && tm.textContent) || '').replace(/\s+/g, ' ').trim(),
        b: nm ? nm[1] : id
      });
    });
    return { b: nm ? nm[1] : id, rows: rows };
  }

  function old(x) {
    var m = (x.d || '').match(/(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})/);
    if (!m) return false;
    return (Date.now() - new Date(+m[1], +m[2] - 1, +m[3]).getTime()) > MAXD * 86400000;
  }

  var out, bad = false, push = '';
  try {
    if (location.host !== 'vpn.jlu.edu.cn') {
      out = '请先在 Safari 打开 vpn.jlu.edu.cn 的 OA 页面';
      bad = true;
    } else {
      var all = [], cnt = [], miss = 0;
      IDS.forEach(function (id) {
        var g = grab(id);
        if (!g) { miss++; cnt.push(id + ':0'); return; }
        cnt.push(g.b + ':' + g.rows.length);
        g.rows.forEach(function (x) { all.push(x); });
      });
      var stat = cnt.join('  ');

      if (!all.length) {
        out = '取不到 OA 列表（登录可能已过期）\n' + stat;
        bad = true;
      } else {
        var raw = localStorage.getItem(SK);
        if (!raw) {
          localStorage.setItem(SK, JSON.stringify(all.map(function (x) { return x.k; })));
          out = '首次运行，已记住 ' + all.length + ' 条作为基线\n' + stat;
        } else {
          var seen = new Set(JSON.parse(raw));
          var fresh = all.filter(function (x) { return !seen.has(x.k); });
          if (!fresh.length) {
            out = '没有新通知（共查 ' + all.length + ' 条）\n' + stat;
          } else {
            var ok = fresh.filter(function (x) { return !old(x); });
            var oldN = fresh.length - ok.length;
            var top = ok.slice(0, MAXN);
            if (!top.length) {
              localStorage.setItem(SK, JSON.stringify(
                Array.from(seen).concat(fresh.map(function (x) { return x.k; }))));
              out = '抓到 ' + fresh.length + ' 条，但都超过 ' + MAXD + ' 天，不推';
            } else {
              push = top.map(function (x) {
                return '【' + x.b + '】' + x.t + '\n' + (x.d ? x.d + '\n' : '') + x.u;
              }).join('\n\n');
              if (ok.length > MAXN) push = '新通知 ' + ok.length + ' 条，先看前 ' + MAXN + ' 条：\n\n' + push;
              if (oldN > 0) push += '\n\n（另有 ' + oldN + ' 条超过 ' + MAXD + ' 天，已忽略）';
              out = '抓到新通知 ' + ok.length + ' 条，正在推送\n' + stat;
              try {
                req('POST', W, JSON.stringify({ spt: SPT, content: push }));
                localStorage.setItem(SK, JSON.stringify(
                  Array.from(seen).concat(fresh.map(function (x) { return x.k; }))));
                out = '已推送 ' + top.length + ' 条到微信\n' + stat;
              } catch (e3) {
                out = '微信推送失败，下次再试\n' + stat;
                bad = true;
              }
            }
          }
        }
      }
    }
  } catch (e) {
    out = '出错 ' + (e && e.message ? e.message : String(e));
    bad = true;
  }

  bar(out, !bad);
  completion(out);
})();
