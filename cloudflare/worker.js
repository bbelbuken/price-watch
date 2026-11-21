// price-watch Telegram bot – the button menu runs here, so it answers instantly.
// Free Cloudflare Worker + D1 (tables in schema.sql).
//
// Telegram → POST /telegram   : menu (answers right away)
// GitHub (Bearer INBOX_TOKEN):
//   GET  /watches              : all watches [{id, owner, pos, body, meta}]
//   POST /watches/import       : {"watches": [body…]} – only if the table is empty (first run)
//   POST /watches/put          : {"watches": [body…]} – update settings (after a new watch's setup)
//   POST /watches/delete       : {"ids": […]}
//   POST /watches/meta         : {"metas": {id: {…}}} – merge price-run info (cheapest, sellers)
//   GET  /jobs, POST /jobs/ack : work that needs Akakçe (new watch setup, prices now, summary)
//
// Worker settings:
//   DB              D1 binding                                                     (binding)
//   TELEGRAM_TOKEN  bot token                                                       (secret)
//   TELEGRAM_SECRET printed by `telegram_setup --webhook`                           (secret)
//   INBOX_TOKEN     same as the GitHub secret INBOX_TOKEN                           (secret)
//   GITHUB_TOKEN    fine-grained, this repo only, "Actions: Read and write"         (secret)
//   GITHUB_REPO     e.g. bbelbuken/price-watch                                      (text)
//   OWNERS          who may use the bot: me:<chat id>,gf:<chat id>                  (text)

const HOME = "🏠 Ana menü";
const BACK = "↩ Geri";
const MAX_WATCHES = 10;
const HOME_WORDS = new Set(["0", "menu", "menü", "/menu", "/start", "ana menu", "iptal", "/iptal"]);

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    if (url.pathname === "/telegram" && request.method === "POST") {
      if (request.headers.get("X-Telegram-Bot-Api-Secret-Token") !== env.TELEGRAM_SECRET) {
        return new Response("forbidden", { status: 403 });
      }
      const update = await request.json();
      try {
        await onUpdate(update, env, ctx);
      } catch (err) {
        console.log("update failed", err && err.stack);
      }
      return new Response("ok"); // always 200, or Telegram re-sends the same update forever
    }
    if (request.headers.get("Authorization") !== `Bearer ${env.INBOX_TOKEN}`) {
      return new Response("unauthorized", { status: 401 });
    }
    return api(url.pathname, request, env);
  },
};

// ---------------------------------------------------------------- GitHub API

async function api(path, request, env) {
  const body = request.method === "POST" ? await request.json() : {};
  if (path === "/watches") {
    const { results } = await env.DB.prepare("SELECT * FROM watches ORDER BY owner, pos").all();
    return Response.json({ watches: results.map(row) });
  }
  if (path === "/watches/import") {
    const { n } = await env.DB.prepare("SELECT COUNT(*) AS n FROM watches").first();
    if (n > 0) return Response.json({ imported: 0 });
    const ws = body.watches || [];
    await batch(env, ws.map((w, i) =>
      env.DB.prepare("INSERT INTO watches (id, owner, pos, body) VALUES (?1, ?2, ?3, ?4)")
        .bind(w.id, w.owner, i, JSON.stringify(w))));
    return Response.json({ imported: ws.length });
  }
  if (path === "/watches/put") {
    await batch(env, (body.watches || []).map((w) =>
      env.DB.prepare("UPDATE watches SET body = ?2 WHERE id = ?1").bind(w.id, JSON.stringify(w))));
    return Response.json({ ok: true });
  }
  if (path === "/watches/delete") {
    await batch(env, (body.ids || []).map((id) => env.DB.prepare("DELETE FROM watches WHERE id = ?1").bind(id)));
    return Response.json({ ok: true });
  }
  if (path === "/watches/meta") {
    const metas = body.metas || {};
    const { results } = await env.DB.prepare("SELECT id, meta FROM watches").all();
    await batch(env, results.filter((r) => metas[r.id]).map((r) =>
      env.DB.prepare("UPDATE watches SET meta = ?2 WHERE id = ?1")
        .bind(r.id, JSON.stringify({ ...JSON.parse(r.meta || "{}"), ...metas[r.id] }))));
    return Response.json({ ok: true });
  }
  if (path === "/jobs") {
    const { results } = await env.DB.prepare("SELECT id, body FROM jobs ORDER BY id LIMIT 50").all();
    return Response.json({ jobs: results.map((r) => ({ id: r.id, ...JSON.parse(r.body) })) });
  }
  if (path === "/jobs/ack") {
    const ids = (body.ids || []).map(Number).filter(Number.isInteger);
    await batch(env, ids.map((id) => env.DB.prepare("DELETE FROM jobs WHERE id = ?1").bind(id)));
    return Response.json({ ok: true });
  }
  return new Response("not found", { status: 404 });
}

async function batch(env, statements) {
  if (statements.length) await env.DB.batch(statements);
}

function row(r) {
  return { id: r.id, owner: r.owner, pos: r.pos, body: JSON.parse(r.body), meta: JSON.parse(r.meta || "{}") };
}

// ---------------------------------------------------------------- storage

async function myWatches(env, owner) {
  const { results } = await env.DB.prepare("SELECT * FROM watches WHERE owner = ?1 ORDER BY pos").bind(owner).all();
  return results.map(row);
}

async function getWatch(env, owner, id) {
  const r = await env.DB.prepare("SELECT * FROM watches WHERE id = ?1 AND owner = ?2").bind(id, owner).first();
  return r ? row(r) : null;
}

async function saveBody(env, w) {
  await env.DB.prepare("UPDATE watches SET body = ?2 WHERE id = ?1").bind(w.id, JSON.stringify(w.body)).run();
}

async function getMenu(env, owner) {
  const r = await env.DB.prepare("SELECT body FROM menus WHERE owner = ?1").bind(owner).first();
  return r ? JSON.parse(r.body) : {};
}

async function setMenu(env, owner, menu) {
  await env.DB.prepare("INSERT OR REPLACE INTO menus (owner, body) VALUES (?1, ?2)")
    .bind(owner, JSON.stringify(menu)).run();
}

async function addJob(env, ctx, job) {
  await env.DB.prepare("INSERT INTO jobs (body, created) VALUES (?1, ?2)")
    .bind(JSON.stringify(job), Date.now()).run();
  ctx.waitUntil(startRun(env));
}

async function startRun(env) {
  const res = await fetch(
    `https://api.github.com/repos/${env.GITHUB_REPO}/actions/workflows/price-watch.yml/dispatches`,
    {
      method: "POST",
      headers: {
        Authorization: `Bearer ${env.GITHUB_TOKEN}`,
        Accept: "application/vnd.github+json",
        "User-Agent": "price-watch-bot",
      },
      body: JSON.stringify({ ref: "main", inputs: { mode: "commands" } }),
    },
  );
  if (res.status !== 204) console.log("GitHub dispatch failed", res.status, await res.text());
}

// ---------------------------------------------------------------- Telegram

async function tg(env, method, payload) {
  const res = await fetch(`https://api.telegram.org/bot${env.TELEGRAM_TOKEN}/${method}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return res.json().catch(() => ({ ok: false }));
}

function escapeHtml(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

async function showScreen(env, chat, screen, editId) {
  const payload = {
    chat_id: chat,
    text: escapeHtml(screen.text),
    parse_mode: "HTML",
    reply_markup: { inline_keyboard: screen.rows.filter((r) => r.length).map((r) =>
      r.map(([text, data]) => ({ text, callback_data: data }))) },
  };
  if (editId) {
    const res = await tg(env, "editMessageText", { ...payload, message_id: editId });
    if (res.ok || String(res.description).includes("not modified")) return;
  }
  await tg(env, "sendMessage", payload);
}

function owners(env) {
  const map = {};
  for (const pair of String(env.OWNERS || "").split(",")) {
    const [owner, chat] = pair.split(":").map((s) => s.trim());
    if (owner && chat) map[chat] = owner;
  }
  return map;
}

async function onUpdate(update, env, ctx) {
  const cq = update.callback_query;
  const msg = cq ? cq.message : update.message;
  const chat = msg && msg.chat && String(msg.chat.id);
  const owner = owners(env)[chat];
  if (!owner) return; // strangers get nothing
  if (cq) {
    ctx.waitUntil(tg(env, "answerCallbackQuery", { callback_query_id: cq.id }));
    const screen = await onButton(cq.data || "", owner, chat, env, ctx);
    await showScreen(env, chat, screen, msg.message_id);
  } else if (msg.text) {
    await showScreen(env, chat, await onText(msg.text, owner, chat, env, ctx));
  }
}

// ---------------------------------------------------------------- formatting

function lira(x) {
  return String(Math.round(x)).replace(/\B(?=(\d{3})+(?!\d))/g, ".") + "₺";
}

const FOLD = { "ı": "i", "İ": "i", "ç": "c", "Ç": "c", "ş": "s", "Ş": "s", "ğ": "g", "Ğ": "g", "ü": "u", "Ü": "u", "ö": "o", "Ö": "o" };
function fold(s) {
  return String(s).replace(/[ıİçÇşŞğĞüÜöÖ]/g, (c) => FOLD[c]).toLowerCase();
}

function compact(s) {
  return fold(s).replace(/[^a-z0-9]+/g, "");
}

function label(b) {
  if (b.name || b.query) return String(b.name || b.query);
  const slug = String(b.url || "").split("?")[0].replace(/\/$/, "").split("/").pop().split(",")[0];
  return slug.replace(/\.html$/, "").replace(/-/g, " ");
}

function parsePrice(text) {
  let t = fold(text).trim().replace("₺", "").replace("tl", "").trim();
  const mult = t.endsWith("k") ? 1000 : 1;
  t = t.replace(/k$/, "").trim();
  t = t.includes(",") ? t.replace(/\./g, "").replace(",", ".") : t.replace(/\./g, "");
  if (!/^\d+(\.\d+)?$/.test(t)) return null;
  const v = Number(t) * mult;
  return v > 0 ? v : null;
}

function parseAdd(args) {
  const w = {};
  let rest = args;
  const ex = rest.match(/\b(hari[cç])\s+(.+)$/i);
  if (ex) {
    w.exclude = splitWords(ex[2]);
    rest = rest.slice(0, ex.index);
  }
  rest = rest.replace(/\b(hedef|min|max)\s+([\d.,]+k?)/gi, (_, key, value) => {
    const p = parsePrice(value);
    if (p) w[{ hedef: "target", min: "min_price", max: "max_price" }[fold(key)]] = Math.round(p);
    return "";
  }).trim().replace(/\s+/g, " ");
  if (/^(https?:\/\/|(www\.)?akakce\.com)/i.test(rest)) w.url = rest.startsWith("http") ? rest : "https://" + rest;
  else if (rest) { w.query = rest; w.include = [rest]; }
  return w;
}

function splitWords(text) {
  return String(text).split(",").map((s) => s.trim()).filter(Boolean);
}

function isCategoryUrl(u) {
  return /^https?:\/\/(www\.)?akakce\.com\/[^?#]+\.html/i.test(u) && !/\/en-ucuz-|-fiyati,/.test(u);
}

function chosen(b, seller) {
  return (b.sellers || []).some((c) => compact(c) && compact(seller).includes(compact(c)));
}

// ---------------------------------------------------------------- screens

const nav = (back) => [...(back ? [[BACK, back]] : []), [HOME, "h"]];
const withNote = (note, text) => (note ? `${note}\n\n${text}` : text);

function homeScreen(note) {
  return {
    text: withNote(note, "🏠 Ana menü – ne yapmak istersin?"),
    rows: [[["➕ Yeni takip", "a"], ["📋 Takiplerim", "l"]], [["📊 Özet gönder", "o"], ["❓ Yardım", "y"]]],
  };
}

function listScreen(ws, note) {
  if (!ws.length) return { text: withNote(note, "Henüz takibin yok."), rows: [[["➕ Yeni takip", "a"]], nav(null)] };
  return {
    text: withNote(note, "📋 Takiplerin – birine dokun:"),
    rows: [
      ...ws.map((w) => [[label(w.body) + (w.meta.cheapest ? ` · ${lira(w.meta.cheapest)}` : ""), `w:${w.id}`]]),
      [["➕ Yeni takip", "a"]],
      nav("h"),
    ],
  };
}

function summaryText(w) {
  const b = w.body;
  return [
    `🔎 ${label(b)}` + (w.meta.cheapest ? ` · en ucuz ${lira(w.meta.cheapest)}` : ""),
    `🎯 Hedef: ${b.target ? lira(b.target) : "yok"}`,
    `💰 Aralık: ${b.min_price ? lira(b.min_price) : "0₺"} – ${b.max_price ? lira(b.max_price) : "sınırsız"}`,
    `🚫 Hariç: ${(b.exclude || []).join(", ") || "yok"}`,
    `🏪 Satıcılar: ${(b.sellers || []).join(", ") || "sadece genel liste"}`,
  ].join("\n");
}

function watchScreen(w, note) {
  const id = w.id;
  return {
    text: withNote(note, summaryText(w)),
    rows: [
      [["🎯 Hedef", `t:${id}`], ["💰 Fiyat aralığı", `r:${id}`]],
      [["🚫 Hariç kelimeler", `x:${id}`], ["🏪 Satıcılar", `s:${id}`]],
      [["📊 Şimdiki fiyatlar", `p:${id}`], ["🗑 Takibi sil", `d:${id}`]],
      nav("l"),
    ],
  };
}

function targetScreen(w, note) {
  const b = w.body;
  return {
    text: withNote(note, `🎯 ${label(b)} – hedef fiyat: ${b.target ? lira(b.target) : "yok"}\n` +
      "Fiyat bu rakama ya da altına inince listede 🔥 görünür.\n\nYeni hedefi yaz (ör. 60000)."),
    rows: [...(b.target ? [[["❌ Hedefi kaldır", `t-:${w.id}`]]] : []), nav(`w:${w.id}`)],
  };
}

function rangeScreen(w, note) {
  const b = w.body;
  const id = w.id;
  return {
    text: withNote(note, `💰 ${label(b)} – fiyat aralığı\n` +
      `En düşük: ${b.min_price ? lira(b.min_price) : "yok"} (altındakiler aksesuar sayılır)\n` +
      `En yüksek: ${b.max_price ? lira(b.max_price) : "yok"}`),
    rows: [
      [["✏️ En düşüğü değiştir", `rm:${id}`], ...(b.min_price ? [["❌ Kaldır", `r-m:${id}`]] : [])],
      [["✏️ En yükseği değiştir", `rM:${id}`], ...(b.max_price ? [["❌ Kaldır", `r-M:${id}`]] : [])],
      nav(`w:${id}`),
    ],
  };
}

function excludeScreen(w, note) {
  const words = w.body.exclude || [];
  const rows = [];
  for (let j = 0; j < words.length; j += 3) {
    rows.push(words.slice(j, j + 3).map((word, k) => [`❌ ${word}`, `x-:${w.id}:${j + k}`]));
  }
  rows.push([["➕ Kelime ekle", `x+:${w.id}`], ...(words.length ? [["🧹 Hepsini temizle", `x0:${w.id}`]] : [])]);
  rows.push(nav(`w:${w.id}`));
  return {
    text: withNote(note, `🚫 ${label(w.body)} – adında bu kelimeler geçen ürünler gösterilmez.\n` +
      (words.length ? "Kaldırmak için kelimeye dokun." : "Henüz kelime yok.")),
    rows,
  };
}

function sellersScreen(w, note) {
  const options = w.meta.seller_options;
  if (!options) {
    return {
      text: withNote(note, `🏪 ${label(w.body)} – satıcı listesi hazırlanıyor, ~1 dakika içinde haber veririm.`),
      rows: [nav(`w:${w.id}`)],
    };
  }
  return {
    text: withNote(note, `🏪 ${label(w.body)} – satıcılar (en ucuz teklifleri)\n` +
      "Dokunarak seç ✅ / kaldır ▫️. Seçtiklerinin en ucuz 5'i genel listenin altına gelir."),
    rows: [
      ...options.slice(0, 24).map(([name, price], i) =>
        [[`${chosen(w.body, name) ? "✅" : "▫️"} ${name} · ${lira(price)}`, `s~:${w.id}:${i}`]]),
      ...((w.body.sellers || []).length ? [[["🧹 Hepsini kaldır", `s0:${w.id}`]]] : []),
      nav(`w:${w.id}`),
    ],
  };
}

const prompt = (text, back) => ({ text, rows: [nav(back)] });

const HELP = "🤖 Fiyat botu\n\nHer şey menüden: /menu (ya da 0 yaz).\n" +
  "🏠 Ana menü → ➕ Yeni takip · 📋 Takiplerim · 📊 Özet\n" +
  "Bir takibin içinde: 🎯 hedef fiyat · 💰 fiyat aralığı · 🚫 hariç kelimeler · " +
  "🏪 satıcılar (seçtiklerinin en ucuz 5'i genel listenin altına gelir) · 🗑 sil.\n" +
  "Eklediğin her şeyi aynı yerden kaldırabilirsin. Her ekranda ↩ Geri ve 🏠 Ana menü var.\n\n" +
  "Değişikliklerden sonra güncel liste en geç 10 dakika içinde bir kez gelir.\n" +
  "Kısayol: /ekle iphone 17 hedef 60000 hariç kılıf, pro";

// ---------------------------------------------------------------- input

async function onButton(data, owner, chat, env, ctx) {
  const menu = await getMenu(env, owner);
  if (menu.await) await setMenu(env, owner, {}); // a tap cancels half-typed input
  const [action, id, arg] = data.split(":");
  if (action === "h") return homeScreen();
  if (action === "l") return listScreen(await myWatches(env, owner));
  if (action === "a") {
    await setMenu(env, owner, { await: "add" });
    return prompt("➕ Ne takip edelim? Yaz, ör. iphone 17\n(Akakçe kategori linki de olur.)", "h");
  }
  if (action === "o") {
    await addJob(env, ctx, { type: "summary", owner, chat });
    return { text: "📋 Özet hazırlanıyor, ~1 dakika içinde gelir.", rows: [nav(null)] };
  }
  if (action === "y") return { text: HELP, rows: [nav(null)] };

  const w = await getWatch(env, owner, id);
  if (!w) return homeScreen("Bu takip artık yok.");
  const b = w.body;
  const change = async (screen) => { await saveBody(env, w); return screen; };

  switch (action) {
    case "w": return watchScreen(w);
    case "t": await setMenu(env, owner, { await: "target", watch: id }); return targetScreen(w);
    case "t-": delete b.target; return change(watchScreen(w, "✅ Hedef kaldırıldı"));
    case "r": return rangeScreen(w);
    case "rm":
    case "rM":
      await setMenu(env, owner, { await: action === "rm" ? "min" : "max", watch: id });
      return prompt(`💰 ${label(b)} – ${action === "rm" ? "en düşük" : "en yüksek"} fiyatı yaz (ör. 40000).`, `r:${id}`);
    case "r-m": delete b.min_price; return change(rangeScreen(w, "✅ En düşük fiyat kaldırıldı"));
    case "r-M": delete b.max_price; return change(rangeScreen(w, "✅ En yüksek fiyat kaldırıldı"));
    case "x": return excludeScreen(w);
    case "x+":
      await setMenu(env, owner, { await: "exclude", watch: id });
      return prompt(`🚫 ${label(b)} – gizlenecek kelimeleri yaz, virgülle ayır (ör. kılıf, pro).`, `x:${id}`);
    case "x-": {
      const word = (b.exclude || [])[Number(arg)];
      if (word === undefined) return excludeScreen(w);
      b.exclude = b.exclude.filter((x) => x !== word);
      if (!b.exclude.length) delete b.exclude;
      return change(excludeScreen(w, `✅ '${word}' kaldırıldı`));
    }
    case "x0": delete b.exclude; return change(excludeScreen(w, "✅ Hariç kelimeler temizlendi"));
    case "s":
      if (!w.meta.seller_options) await addJob(env, ctx, { type: "sellers", owner, chat, watch: id });
      return sellersScreen(w);
    case "s~": {
      const option = (w.meta.seller_options || [])[Number(arg)];
      if (!option) return sellersScreen(w);
      const name = option[0];
      const before = b.sellers || [];
      const rest = before.filter((c) => !(compact(c) && compact(name).includes(compact(c))));
      b.sellers = rest.length === before.length ? [...before, name] : rest;
      if (!b.sellers.length) delete b.sellers;
      return change(sellersScreen(w, rest.length === before.length ? `✅ ${name} eklendi` : `✅ ${name} çıkarıldı`));
    }
    case "s0": delete b.sellers; return change(sellersScreen(w, "✅ Satıcı listesi kaldırıldı"));
    case "p":
      await addJob(env, ctx, { type: "show", owner, chat, watch: id });
      return { text: `📊 ${label(b)} – şimdiki en ucuz 5, ~1 dakika içinde gelir.`, rows: [nav(`w:${id}`)] };
    case "d":
      return { text: `🗑 ${label(b)} takibi silinsin mi?`, rows: [[["✅ Evet, sil", `d!:${id}`], [BACK, `w:${id}`]], [[HOME, "h"]]] };
    case "d!":
      await env.DB.prepare("DELETE FROM watches WHERE id = ?1").bind(id).run();
      return listScreen(await myWatches(env, owner), `🗑 Silindi: ${label(b)}`);
  }
  return homeScreen();
}

async function onText(text, owner, chat, env, ctx) {
  const t = text.trim();
  const menu = await getMenu(env, owner);
  if (HOME_WORDS.has(fold(t))) {
    if (menu.await) await setMenu(env, owner, {});
    return homeScreen();
  }
  if (t.startsWith("/")) {
    if (menu.await) await setMenu(env, owner, {});
    const [first, ...restWords] = t.split(/\s+/);
    const cmd = fold(first.slice(1).split("@")[0]);
    const args = restWords.join(" ");
    if (cmd === "ekle") return args ? addWatch(args, owner, chat, env, ctx) : onButton("a", owner, chat, env, ctx);
    if (cmd === "liste") return listScreen(await myWatches(env, owner));
    if (cmd === "ozet") return onButton("o", owner, chat, env, ctx);
    if (cmd === "yardim" || cmd === "help") return { text: HELP, rows: [nav(null)] };
    return homeScreen();
  }
  if (!menu.await) return homeScreen("Menüden seçebilirsin 👇");
  await setMenu(env, owner, {});
  if (menu.await === "add") return addWatch(t, owner, chat, env, ctx);

  const w = await getWatch(env, owner, menu.watch);
  if (!w) return homeScreen("Bu takip artık yok.");
  if (["target", "min", "max"].includes(menu.await)) {
    const price = parsePrice(t);
    if (!price) {
      await setMenu(env, owner, menu); // keep waiting
      return prompt("Fiyatı anlamadım, sadece rakam yaz (ör. 60000).", menu.await === "target" ? `t:${w.id}` : `r:${w.id}`);
    }
    const key = { target: "target", min: "min_price", max: "max_price" }[menu.await];
    w.body[key] = Math.round(price);
    await saveBody(env, w);
    const note = `✅ ${{ target: "Hedef", min: "En düşük fiyat", max: "En yüksek fiyat" }[menu.await]}: ${lira(price)}`;
    return menu.await === "target" ? watchScreen(w, note) : rangeScreen(w, note);
  }
  if (menu.await === "exclude") {
    const words = splitWords(t);
    if (!words.length) {
      await setMenu(env, owner, menu);
      return prompt("Kelime yazmadın. Ör. kılıf, pro", `x:${w.id}`);
    }
    w.body.exclude = [...new Set([...(w.body.exclude || []), ...words])].sort();
    await saveBody(env, w);
    return excludeScreen(w, `✅ Hariç: ${w.body.exclude.join(", ")}`);
  }
  return homeScreen();
}

async function addWatch(text, owner, chat, env, ctx) {
  const mine = await myWatches(env, owner);
  if (mine.length >= MAX_WATCHES) return homeScreen(`En fazla ${MAX_WATCHES} takip olabilir. Önce birini sil.`);
  const fields = parseAdd(text);
  if (!fields.query && !fields.url) {
    await setMenu(env, owner, { await: "add" });
    return prompt("Neyi takip edelim? Ör. iphone 17", "h");
  }
  if (fields.url && !isCategoryUrl(fields.url)) {
    await setMenu(env, owner, { await: "add" });
    return prompt("Bu bir Akakçe kategori linki değil. Akakçe'de kategoriyi açıp adresi kopyala, ya da ürün adını yaz.", "h");
  }
  const { results } = await env.DB.prepare("SELECT id FROM watches").all();
  const ids = new Set(results.map((r) => r.id));
  let n = 1;
  while (ids.has(`${owner}-${n}`)) n++;
  const body = { id: `${owner}-${n}`, owner, ...fields };
  const pos = mine.length ? Math.max(...mine.map((w) => w.pos)) + 1 : 0;
  await env.DB.prepare("INSERT INTO watches (id, owner, pos, body, meta) VALUES (?1, ?2, ?3, ?4, ?5)")
    .bind(body.id, owner, pos, JSON.stringify(body), JSON.stringify({ setup: "pending" })).run();
  await addJob(env, ctx, { type: "setup", owner, chat, watch: body.id });
  return watchScreen({ id: body.id, body, meta: {} },
    `✅ Eklendi: ${label(body)} – Akakçe'ye bakıyorum, en ucuz 5 ~1 dakika içinde gelir.`);
}
