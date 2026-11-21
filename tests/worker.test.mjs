// Tests for cloudflare/worker.js – the Telegram menu. Run: node tests/worker.test.mjs
// D1 is replaced by Node's built-in SQLite with the real schema.sql; Telegram and GitHub are fakes.
import { readFileSync } from "node:fs";
import { DatabaseSync } from "node:sqlite";
import worker from "../cloudflare/worker.js";

// ---------------------------------------------------------------- fakes

function fakeD1() {
  const db = new DatabaseSync(":memory:");
  db.exec(readFileSync(new URL("../cloudflare/schema.sql", import.meta.url), "utf8"));
  const statement = (sql, args = []) => ({
    bind: (...a) => statement(sql, a),
    run: async () => db.prepare(sql).run(...args),
    first: async () => db.prepare(sql).get(...args) ?? null,
    all: async () => ({ results: db.prepare(sql).all(...args) }),
  });
  return {
    prepare: (sql) => statement(sql),
    batch: async (sts) => { for (const s of sts) await s.run(); },
    raw: db,
  };
}

let env, telegram, dispatches, pending;
const ME = 111, GF = 222;

function reset() {
  env = {
    DB: fakeD1(), TELEGRAM_TOKEN: "t", TELEGRAM_SECRET: "s3", INBOX_TOKEN: "i9",
    GITHUB_TOKEN: "g", GITHUB_REPO: "o/r", OWNERS: `me:${ME}, gf:${GF}`,
  };
  telegram = [];
  dispatches = 0;
  pending = [];
}

globalThis.fetch = async (url, init) => {
  const u = String(url);
  if (u.includes("api.github.com")) { dispatches++; return new Response(null, { status: 204 }); }
  const method = u.split("/").pop();
  telegram.push({ method, ...JSON.parse(init.body) });
  return Response.json({ ok: true, result: {} });
};

const ctx = { waitUntil: (p) => pending.push(p) };
let updateId = 0;

async function post(update, secret = "s3") {
  const res = await worker.fetch(new Request("https://w.dev/telegram", {
    method: "POST", headers: { "X-Telegram-Bot-Api-Secret-Token": secret }, body: JSON.stringify(update),
  }), env, ctx);
  await Promise.all(pending);
  return res;
}

const say = (text, chat = GF) => post({ update_id: ++updateId, message: { chat: { id: chat }, text } });
const tap = (data, chat = GF) =>
  post({ update_id: ++updateId, callback_query: { id: "cb", data, message: { message_id: 7, chat: { id: chat } } } });

/** The last screen the bot showed: {text, buttons: {label: data}, edited} */
function screen() {
  const m = [...telegram].reverse().find((c) => c.method === "sendMessage" || c.method === "editMessageText");
  const buttons = {};
  for (const row of m.reply_markup?.inline_keyboard || []) for (const b of row) buttons[b.text] = b.callback_data;
  return { text: m.text, buttons, edited: m.method === "editMessageText" };
}

async function press(part, chat = GF) {
  const label = Object.keys(screen().buttons).find((l) => l.includes(part));
  if (!label) throw new Error(`no button "${part}" in ${Object.keys(screen().buttons)}`);
  return tap(screen().buttons[label], chat);
}

const sql = (q, ...a) => env.DB.raw.prepare(q).all(...a);
const watch = (id) => {
  const r = sql("SELECT * FROM watches WHERE id = ?1", id)[0];
  return r && { ...r, body: JSON.parse(r.body), meta: JSON.parse(r.meta) };
};
const jobs = () => sql("SELECT body FROM jobs ORDER BY id").map((r) => JSON.parse(r.body));

function addWatch(id, owner, body = {}, meta = {}) {
  const pos = sql("SELECT COUNT(*) AS n FROM watches WHERE owner = ?1", owner)[0].n;
  env.DB.raw.prepare("INSERT INTO watches (id, owner, pos, body, meta) VALUES (?1, ?2, ?3, ?4, ?5)")
    .run(id, owner, pos, JSON.stringify({ id, owner, query: "iphone 17", ...body }), JSON.stringify(meta));
}

// ---------------------------------------------------------------- tiny test runner

const tests = [];
const test = (name, fn) => tests.push([name, fn]);
function eq(a, b, msg = "") {
  if (JSON.stringify(a) !== JSON.stringify(b)) throw new Error(`${msg}\n  got:  ${JSON.stringify(a)}\n  want: ${JSON.stringify(b)}`);
}
function ok(cond, msg) { if (!cond) throw new Error(msg); }

// ---------------------------------------------------------------- tests

test("strangers and wrong webhook secret get nothing", async () => {
  eq((await post({ update_id: 1, message: { chat: { id: GF }, text: "/menu" } }, "nope")).status, 403);
  await say("/menu", 999);
  eq(telegram.length, 0);
});

test("/menu, 0 and other home words show the main menu", async () => {
  for (const w of ["/menu", "0", "menü", "/start", "iptal"]) {
    await say(w);
    ok(screen().text.includes("Ana menü"), w);
  }
  eq(Object.keys(screen().buttons), ["➕ Yeni takip", "📋 Takiplerim", "📊 Özet gönder", "❓ Yardım"]);
});

test("adding a watch is instant; the Akakçe part goes to a GitHub job", async () => {
  await tap("a");
  ok(screen().edited && screen().text.includes("Ne takip edelim"), "prompt");
  await say("iphone 17 hedef 80000 hariç kılıf, pro");
  const w = watch("gf-1");
  eq(w.body, { id: "gf-1", owner: "gf", exclude: ["kılıf", "pro"], target: 80000, query: "iphone 17", include: ["iphone 17"] });
  eq(w.meta, { setup: "pending" });
  eq(jobs(), [{ type: "setup", owner: "gf", chat: String(GF), watch: "gf-1" }]);
  eq(dispatches, 1);
  ok(screen().text.includes("Eklendi") && screen().buttons["🏪 Satıcılar"] === "s:gf-1", "watch screen");
});

test("/ekle shortcut and a bad link", async () => {
  await say("/ekle iphone 16");
  ok(watch("gf-1") && screen().text.includes("Eklendi"), "shortcut");
  await tap("a");
  await say("https://www.amazon.com.tr/dp/123");
  ok(screen().text.includes("kategori linki değil"), "bad link");
  await say("https://www.akakce.com/power-supply/1000-w-power-supply.html");
  eq(watch("gf-2").body.url, "https://www.akakce.com/power-supply/1000-w-power-supply.html");
});

test("at most 10 watches per person", async () => {
  for (let i = 1; i <= 10; i++) addWatch(`gf-${i}`, "gf");
  await tap("a");
  await say("iphone 18");
  ok(screen().text.includes("En fazla 10"), "limit");
  eq(sql("SELECT COUNT(*) AS n FROM watches")[0].n, 10);
});

test("target: set by typing, wrong input keeps waiting, remove by button", async () => {
  addWatch("gf-1", "gf");
  await tap("t:gf-1");
  await say("çok ucuz");
  ok(screen().text.includes("anlamadım"), "asks again");
  await say("75.000");
  eq(watch("gf-1").body.target, 75000);
  await tap("t:gf-1");
  await press("Hedefi kaldır");
  eq(watch("gf-1").body.target, undefined);
});

test("0 cancels a half-typed input", async () => {
  addWatch("gf-1", "gf");
  await tap("t:gf-1");
  await say("0");
  await say("60000");
  eq(watch("gf-1").body.target, undefined);
  ok(screen().text.includes("Menüden seçebilirsin"), "nudge");
});

test("price range: set and remove min and max", async () => {
  addWatch("gf-1", "gf", { min_price: 50000 });
  await tap("r:gf-1");
  await press("En yükseği");
  await say("95k");
  eq(watch("gf-1").body.max_price, 95000);
  await tap("r-M:gf-1");
  await tap("r-m:gf-1");
  eq([watch("gf-1").body.min_price, watch("gf-1").body.max_price], [undefined, undefined]);
});

test("excluded words: add several, remove one, clear", async () => {
  addWatch("gf-1", "gf");
  await tap("x:gf-1");
  await press("Kelime ekle");
  await say("pro, kılıf, max");
  eq(watch("gf-1").body.exclude, ["kılıf", "max", "pro"]);
  await press("❌ max");
  eq(watch("gf-1").body.exclude, ["kılıf", "pro"]);
  await press("Hepsini temizle");
  eq(watch("gf-1").body.exclude, undefined);
});

test("sellers: instant from the price run's list, tap to add and remove", async () => {
  addWatch("gf-1", "gf", {}, { seller_options: [["Pttavm", 81997], ["Amazon Türkiye", 84999], ["Hepsiburada", 86900]] });
  await tap("s:gf-1");
  eq(Object.keys(screen().buttons).slice(0, 3), ["▫️ Pttavm · 81.997₺", "▫️ Amazon Türkiye · 84.999₺", "▫️ Hepsiburada · 86.900₺"]);
  await press("Amazon");
  await press("Hepsiburada");
  eq(watch("gf-1").body.sellers, ["Amazon Türkiye", "Hepsiburada"]);
  ok(screen().buttons["✅ Amazon Türkiye · 84.999₺"], "checked");
  await press("Amazon");
  eq(watch("gf-1").body.sellers, ["Hepsiburada"]);
  await press("Hepsini kaldır");
  eq(watch("gf-1").body.sellers, undefined);
  eq(jobs(), []);
});

test("sellers not known yet: a job prepares them", async () => {
  addWatch("gf-1", "gf");
  await tap("s:gf-1");
  ok(screen().text.includes("hazırlanıyor"), "waiting text");
  eq(jobs(), [{ type: "sellers", owner: "gf", chat: String(GF), watch: "gf-1" }]);
});

test("prices now and summary are jobs", async () => {
  addWatch("gf-1", "gf");
  await tap("p:gf-1");
  await tap("o");
  await say("/ozet");
  eq(jobs().map((j) => j.type), ["show", "summary", "summary"]);
  eq(dispatches, 3);
});

test("delete asks first", async () => {
  addWatch("gf-1", "gf");
  await tap("w:gf-1");
  await press("Takibi sil");
  ok(watch("gf-1") && screen().text.includes("silinsin mi"), "asks");
  await press("Geri");
  ok(watch("gf-1"), "still there");
  await tap("d:gf-1");
  await press("Evet");
  eq(watch("gf-1"), undefined);
  ok(screen().text.includes("Silindi"), "confirmed");
});

test("nobody can see or touch someone else's watch", async () => {
  addWatch("me-1", "me", { query: "9070 xt" });
  for (const d of ["w:me-1", "d!:me-1", "t-:me-1", "x0:me-1", "s0:me-1", "p:me-1"]) {
    await tap(d, GF);
    ok(screen().text.includes("artık yok"), d);
  }
  ok(watch("me-1"), "untouched");
  await tap("l", GF);
  ok(!JSON.stringify(screen().buttons).includes("9070"), "not in her list");
  eq(jobs(), []);
});

test("menu memory is per person", async () => {
  addWatch("gf-1", "gf");
  await tap("t:gf-1", GF);
  await say("60000", ME); // my typing doesn't answer her prompt
  eq(watch("gf-1").body.target, undefined);
  await say("60000", GF);
  eq(watch("gf-1").body.target, 60000);
});

test("every screen has 🏠 and short button data; back walks up", async () => {
  addWatch("gf-1", "gf", { exclude: ["kılıf", "pro"], sellers: ["Amazon"], target: 80000, max_price: 99000 },
    { seller_options: [["Amazon Türkiye", 1]] });
  for (const d of ["h", "l", "a", "y", "o", "w:gf-1", "t:gf-1", "r:gf-1", "rm:gf-1", "x:gf-1", "x+:gf-1",
    "s:gf-1", "p:gf-1", "d:gf-1"]) {
    await tap(d);
    const data = Object.values(screen().buttons);
    ok(data.every((x) => Buffer.byteLength(x) <= 64), `long data on ${d}`);
    if (d !== "h") ok(data.includes("h"), `no home on ${d}`);
  }
  await tap("x:gf-1");
  eq(screen().buttons["↩ Geri"], "w:gf-1");
  await press("Geri");
  eq(screen().buttons["↩ Geri"], "l");
  await press("Geri");
  eq(screen().buttons["↩ Geri"], "h");
});

test("GitHub API: auth, import only once, meta merge, jobs ack", async () => {
  const api = (path, body) => worker.fetch(new Request(`https://w.dev${path}`, {
    method: body ? "POST" : "GET", headers: { Authorization: "Bearer i9" }, body: body && JSON.stringify(body),
  }), env, ctx).then((r) => r.json());
  eq((await worker.fetch(new Request("https://w.dev/watches"), env, ctx)).status, 401);
  await api("/watches/import", { watches: [{ id: "me-1", owner: "me", query: "9070 xt" }] });
  eq((await api("/watches/import", { watches: [{ id: "x", owner: "me", query: "y" }] })).imported, 0);
  await api("/watches/meta", { metas: { "me-1": { cheapest: 61900 } } });
  await api("/watches/meta", { metas: { "me-1": { shown_fp: "abc" } } });
  const [w] = (await api("/watches")).watches;
  eq([w.id, w.meta], ["me-1", { cheapest: 61900, shown_fp: "abc" }]);
  await api("/watches/put", { watches: [{ ...w.body, min_price: 30000 }] });
  eq(watch("me-1").body.min_price, 30000);
  await tap("o", ME);
  const { jobs: list } = await api("/jobs");
  eq(list.map((j) => j.type), ["summary"]);
  await api("/jobs/ack", { ids: list.map((j) => j.id) });
  eq(jobs(), []);
  await api("/watches/delete", { ids: ["me-1"] });
  eq(watch("me-1"), undefined);
});

// ---------------------------------------------------------------- run

let failed = 0;
for (const [name, fn] of tests) {
  reset();
  try {
    await fn();
    console.log(`ok    ${name}`);
  } catch (err) {
    failed++;
    console.log(`FAIL  ${name}\n      ${err.message}`);
  }
}
console.log(`\n${tests.length - failed} passed, ${failed} failed`);
process.exitCode = failed ? 1 : 0;
