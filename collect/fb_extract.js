// Read-only snapshot of a logged-out Facebook page. Runs in Dia's isolated world.
// Returns JSON.stringify({...}). Clicks nothing; facebook.py decides what to click.
(() => {
  const body = document.body ? document.body.innerText : '';
  const scripts = [...document.querySelectorAll('script')].map(s => s.textContent).join('\n');

  // Nearest "creation_time" to an id inside the page's embedded data (server-rendered posts only).
  function nearest(id, re) {
    let best = null, bestD = 1e9, i = scripts.indexOf(id);
    let guard = 0;
    while (i !== -1 && guard++ < 50) {
      const lo = Math.max(0, i - 30000), win = scripts.slice(lo, i + 30000);
      for (const m of win.matchAll(re)) {
        const d = Math.abs(lo + m.index - i);
        if (d < bestD) { bestD = d; best = m[1]; }
      }
      i = scripts.indexOf(id, i + id.length);
    }
    return best;
  }

  const PERMA = /facebook\.com\/(?:[^/?#]+\/posts\/(pfbid\w+|\d+)|permalink\.php\?story_fbid=(\w+)|photo\/?\?fbid=(\d+)|photo\.php\?fbid=(\d+)|[^/?#]+\/videos\/(\d+)|reel\/(\d+))/;
  const clean = h => h.replace(/([?&])__cft__\[0\]=[^&]*&?/, '$1').replace(/([?&])__tn__=[^&]*&?/, '$1').replace(/[?&]$/, '');

  const top = [...document.querySelectorAll('[role=article]')]
    .filter(a => !(a.parentElement && a.parentElement.closest('[role=article]')));
  const articles = [];
  for (const a of top) {
    let id = null, permalink = null, rawTime = '';
    for (const x of a.querySelectorAll('a[href]')) {
      const m = x.href.match(PERMA);
      if (!m) continue;
      if (!id) { id = m.slice(1).find(Boolean); permalink = clean(x.href); }
      const t = (x.innerText || '').trim().split('\n')[0];
      if (!rawTime && t && t.length < 40) rawTime = t;
    }
    if (!id) continue;  // placeholder skeletons and comment shells have no permalink
    const msg = a.querySelector('[data-ad-rendering-role="story_message"], [data-ad-preview="message"], [data-ad-comet-preview="message"]');
    // innerText of the message includes the "See more"/"See less" toggle; drop it, it is not the city's words.
    const msgText = msg ? msg.innerText.replace(/\s*(?:…\s*)?See (?:more|less)\s*$/, '') : null;
    const imgs = [...a.querySelectorAll('img')]
      .filter(i => /scontent|fbcdn/.test(i.src) && Math.max(i.naturalWidth || 0, i.width || 0) >= 120)
      .map(i => ({ src: i.src, alt: i.alt || null }));
    const seeMore = [...a.querySelectorAll('[role=button]')].some(b => (b.innerText || '').trim() === 'See more');
    const r = a.getBoundingClientRect();
    articles.push({
      id, permalink, raw_time: rawTime,
      text: msgText,
      fallback_text: msg ? null : (a.innerText || '').split(/\nAll reactions:|\nLike\n/)[0],
      images: imgs, see_more: seeMore,
      creation_time: nearest(id, /"creation_time":(\d+)/g),
      numeric_id: id.startsWith('pfbid') ? nearest(id, /"post_id":"(\d+)"/g) : id,
      rect: { x: r.x, y: r.y, w: r.width, h: r.height },
    });
  }
  return JSON.stringify({
    url: location.href,
    title: document.title,
    // Page header + Intro (name, followers, category, linked website): evidence the Page is official.
    intro: body.split(/\nPhotos\nSee all photos/)[0].slice(0, 900),
    visibility: document.visibilityState,
    dpr: window.devicePixelRatio,
    cookie_names: document.cookie.split(';').map(s => s.split('=')[0].trim()).filter(Boolean),
    login_button: /(^|\n)Log In(\n|$)/.test(body),
    create_account: /Create new account/.test(body),
    wall: /Log in or sign up for Facebook to connect/.test(body) || location.pathname.startsWith('/login'),
    closable_dialog: !!document.querySelector('[role=dialog] [aria-label="Close"]'),
    scroll_y: window.scrollY, scroll_h: document.documentElement.scrollHeight, inner_h: window.innerHeight,
    articles,
  });
})()
