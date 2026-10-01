// Автотест воркера поиска: node worker/test.mjs
// Сеть и кэш Cloudflare подменяются; парсер гоняется на настоящих ответах
// партнёра из fixtures/.
import { readFileSync } from "node:fs";
import worker, { parseFares, validate, rateLimited } from "./index.js";

let failed = 0;
const check = (name, ok, extra = "") => {
  console.log(`${ok ? "ok  " : "FAIL"} ${name}${ok ? "" : "  " + extra}`);
  if (!ok) failed++;
};

const full = readFileSync(new URL("../fixtures/biletdv_mla_KHV-MOW_1510.xml", import.meta.url), "utf8");
const empty = readFileSync(new URL("../fixtures/biletdv_empty.xml", import.meta.url), "utf8");

/* ---- парсер ---- */
const rows = parseFares(full, { origin: "KHV", destination: "MOW", date: "2026-10-15", psg: "mla" });
check("рейсы из фикстуры разобраны", rows.length === 3, JSON.stringify(rows.length));
const r = rows[0];
check("поля строки как в DATA витрины",
  r.o === "KHV" && r.d === "MOW" && r.dt === "2026-10-15" && r.tm === "10:10" && r.ar === "11:10"
  && r.fn === "SU 1719" && r.al === "SU" && r.fc === "PZZSOC" && r.q === 9 && r.p === 8099
  && r.pc === "mla", JSON.stringify(r));
check("BookURL раскодирован из &amp;", r.url.includes("&iguid=") && !r.url.includes("&amp;"), r.url);
check("PersonID дописывается",
  parseFares(full, { origin: "KHV", destination: "MOW", date: "2026-10-15", psg: "mla", personId: "42" })[0]
    .url.endsWith("&PersonID=42"));
check("пустой ответ → []", parseFares(empty, { origin: "KHV", destination: "MOW", date: "2026-10-15", psg: "aaa" }).length === 0);

const noSubsidy = full.replace(/FareCode="PZZSOC" MRID="43834"/g, 'FareCode="YFLEX" MRID="0"');
check("тариф без признаков субсидии отброшен",
  parseFares(noSubsidy, { origin: "KHV", destination: "MOW", date: "2026-10-15", psg: "mla" }).length === 0);

let threw = false;
try { parseFares("<FlightsSearchResponse><isSuccess>false</isSuccess><ErrorText>bad</ErrorText></FlightsSearchResponse>", {}); }
catch (e) { threw = /bad/.test(e.message); }
check("isSuccess=false → ошибка", threw);

const transfer = full.replace(
  /(<Flight Code="SU"[^>]*Num="1719"[^>]*\/>)/,
  '$1<Flight Code="SU" Num="1402" Origin="SVO" Destination="LED" Departure="15.10.2026T13:00" Arrival="15.10.2026T14:30" FareCode="PZZSOC" MRID="43834" AvailQty="2" Direction="0" />');
const t = parseFares(transfer, { origin: "KHV", destination: "LED", date: "2026-10-15", psg: "mla" })[0];
check("пересадка: рейсы склеены, места по худшему сегменту, прилёт последнего",
  t.fn === "SU 1719 + SU 1402" && t.q === 2 && t.ar === "14:30", JSON.stringify(t));

/* ---- проверка параметров ---- */
const now = new Date("2026-10-01T12:00:00Z");
const P = s => new URLSearchParams(s);
check("валидный запрос", !validate(P("from=ikt&to=UUD&date=2026-11-15"), now).error);
check("плохой код города", !!validate(P("from=IK&to=UUD&date=2026-11-15"), now).error);
check("одинаковые города", !!validate(P("from=UUD&to=UUD&date=2026-11-15"), now).error);
check("прошедшая дата", !!validate(P("from=IKT&to=UUD&date=2026-09-30"), now).error);
check("несуществующая дата", !!validate(P("from=IKT&to=UUD&date=2026-02-30"), now).error);
check("дальше года", !!validate(P("from=IKT&to=UUD&date=2027-12-01"), now).error);

/* ---- лимит по IP ---- */
let limited = false;
for (let i = 0; i < 11; i++) limited = rateLimited("1.2.3.4", 1000 + i);
check("11-й поиск за минуту отклонён", limited);
check("через минуту снова можно", !rateLimited("1.2.3.4", 1000 + 61_000));

/* ---- обработчик целиком ---- */
const store = new Map();
globalThis.caches = { default: {
  match: async req => store.has(req.url) ? new Response(store.get(req.url)) : undefined,
  put: async (req, resp) => { store.set(req.url, await resp.text()); },
} };
const calls = [];
globalThis.fetch = async url => {
  calls.push(url);
  const u = new URL(url);
  return new Response(u.searchParams.has("mla") ? full : empty, { status: 200 });
};
const ctx = { pending: [], waitUntil(p) { this.pending.push(p); } };
const env = { PARTNER_ID: "REAL123", ALLOWED_ORIGINS: "https://xn--1-9sbmlp7byd.xn--p1ai" };
const date = new Date(Date.now() + 10 * 86_400_000).toISOString().slice(0, 10);
const req = (qs, origin = "https://xn--1-9sbmlp7byd.xn--p1ai") => new Request(
  `https://w.example/api/search?${qs}`, { headers: { Origin: origin, "CF-Connecting-IP": "9.9.9.9" } });

let res = await worker.fetch(req(`from=IKT&to=UUD&date=${date}`), env, ctx);
let body = await res.json();
await Promise.all(ctx.pending);
check("200 и рейсы обеих категорий", res.status === 200 && body.ok && body.flights.length === 3, JSON.stringify(body).slice(0, 200));
check("в запросе к партнёру — маршрут, ДДММ и PartnerID",
  calls.length === 2 && calls.every(c => c.includes("depCity=IKT") && c.includes("destCity=UUD")
    && c.includes("depDate=" + date.slice(8, 10) + date.slice(5, 7)) && c.includes("PartnerID=REAL123")),
  calls.join("\n"));
check("маршрут в ответе — запрошенный", body.flights.every(f => f.o === "IKT" && f.d === "UUD"));
check("CORS для своего домена", res.headers.get("Access-Control-Allow-Origin") === "https://xn--1-9sbmlp7byd.xn--p1ai");
check("PartnerID не попал в ключи кэша", [...store.keys()].every(k => !k.includes("REAL123")));

res = await worker.fetch(req(`from=IKT&to=UUD&date=${date}`), env, ctx);
body = await res.json();
check("повторный поиск — из кэша, без запроса к партнёру", calls.length === 2 && body.cached === true);

res = await worker.fetch(req(`from=IKT&to=UUD&date=${date}`, "https://evil.example"), env, ctx);
check("чужой сайт без CORS-заголовка", !res.headers.get("Access-Control-Allow-Origin"));

res = await worker.fetch(req("from=IKT&to=UUD&date=bad"), env, ctx);
check("кривая дата → 400", res.status === 400);

globalThis.fetch = async () => new Response("oops", { status: 503 });
store.clear();
res = await worker.fetch(req(`from=OVB&to=UUD&date=${date}`), env, ctx);
check("партнёр лёг → 502 без подробностей", res.status === 502 && !(await res.text()).includes("REAL123"));

console.log(failed ? `\n${failed} проверок упало` : "\nвсе проверки прошли");
process.exit(failed ? 1 : 0);
