/*
 * Поиск по запросу — Cloudflare Worker.
 *
 * Витрина (webapp.py) — статика: в ней только маршруты, которые сборщик
 * опрашивает по расписанию. Для любого другого направления браузер зовёт
 * этот воркер, а он — партнёрский getFaresByFOP_Ex3. Напрямую из браузера
 * нельзя: API отдаётся по http (со страницы на https браузер такой запрос
 * заблокирует) и без CORS.
 *
 *   GET /api/search?from=IKT&to=UUD&date=2026-11-15
 *   → {"ok":true,"flights":[{o,d,dt,tm,ar,fn,al,fc,q,p,url,pc}, ...]}
 *
 * Формат строк тот же, что DATA в витрине (datajson.rows_to_data), чтобы
 * фронт клал ответ в тот же массив без перекладки.
 *
 * Лимит партнёра — 1000 запросов на продажу в месяц, поэтому ответы
 * кэшируются (CACHE_TTL), а с одного IP — не больше RATE_LIMIT поисков в
 * минуту. Один поиск = по запросу на каждую категорию (mla и aaa).
 *
 * Переменные (wrangler.toml / секреты):
 *   PARTNER_ID       — боевой PartnerID (секрет: wrangler secret put PARTNER_ID)
 *   PERSON_ID        — необязательно, дописывается в BookURL, как в parser.py
 *   API_URL          — необязательно, по умолчанию fares.biletdv.ru
 *   ALLOWED_ORIGINS  — через запятую: https://xn--1-9sbmlp7byd.xn--p1ai,...
 *   CACHE_TTL        — секунды, по умолчанию 1200 (20 минут)
 */

const DEFAULT_API = "http://fares.biletdv.ru/SASSirenaFares.asmx/getFaresByFOP_Ex3";
const CATEGORIES = ["mla", "aaa"];
// Те же коды, что config.SUBSIDY_FARE_CODES; ненулевой MRID — тоже субсидия.
const SUBSIDY_FARE_CODES = new Set(["PZZSOC", "PSOCDO", "UBDOWZZ", "RBDOWD"]);
const RATE_LIMIT = 10;
const RATE_WINDOW_MS = 60_000;
const MAX_DAYS_AHEAD = 365;

const IATA_RE = /^[A-Z]{3}$/;
const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

/* ---------------------------------------------------------------- парсер */

const XML_ENTITIES = { amp: "&", lt: "<", gt: ">", quot: '"', apos: "'" };
const unescapeXml = s => String(s ?? "").replace(/&(amp|lt|gt|quot|apos);/g, (_, e) => XML_ENTITIES[e]);

function attrs(tagText) {
  const out = {};
  for (const m of tagText.matchAll(/([\w:]+)\s*=\s*"([^"]*)"/g)) {
    out[m[1].toLowerCase()] = unescapeXml(m[2]).trim();
  }
  return out;
}

// "15.10.2026T10:10" → ["2026-10-15", "10:10"]
function splitDeparture(v) {
  const m = /(\d{2})\.(\d{2})\.(\d{4})\D*(\d{1,2}):(\d{2})?/.exec(v || "");
  if (!m) return ["", ""];
  return [`${m[3]}-${m[2]}-${m[1]}`, m[5] ? `${m[4].padStart(2, "0")}:${m[5]}` : ""];
}
const timeOf = v => {
  const m = /(\d{1,2}):(\d{2})/.exec(v || "");
  return m ? `${m[1].padStart(2, "0")}:${m[2]}` : "";
};

function isSubsidized(fareCode, mrid) {
  const codes = fareCode.toUpperCase().split("/").filter(Boolean);
  if (codes.length && codes.every(c => SUBSIDY_FARE_CODES.has(c))) return true;
  return mrid !== "" && mrid !== "0";
}

function withPersonId(url, personId) {
  if (!url || !personId || /personid=/i.test(url)) return url;
  return url + (url.includes("?") ? "&" : "?") + "PersonID=" + encodeURIComponent(personId);
}

/**
 * Разбор FlightsSearchResponse. Логика — как в parser.py: маршрут берём
 * запрошенный (города), пересадки склеиваем, места — по самому
 * загруженному сегменту. Регулярками, а не DOM: в воркере нет DOMParser,
 * а формат ответа плоский и стабильный.
 */
export function parseFares(xml, { origin, destination, date, psg, personId = "" }) {
  if (!xml || !xml.trim()) throw new Error("пустой ответ");
  const success = /<isSuccess>\s*([^<]*)<\/isSuccess>/i.exec(xml);
  if (success && success[1].trim().toLowerCase() === "false") {
    const err = /<ErrorText>([^<]*)<\/ErrorText>/i.exec(xml);
    throw new Error("API: " + (err ? unescapeXml(err[1]).trim() : "isSuccess=false"));
  }
  if (!/FlightsSearchResponse|Result/i.test(xml)) throw new Error("неизвестный формат ответа");

  const fallbackUrlMatch = /<\/Offers>\s*<BookURL>([^<]*)<\/BookURL>/i.exec(xml);
  const fallbackUrl = fallbackUrlMatch ? unescapeXml(fallbackUrlMatch[1]).trim() : "";

  const flights = [];
  const seen = new Set();
  for (const pm of xml.matchAll(/<Proposal\b([^>]*)>([\s\S]*?)<\/Proposal>/gi)) {
    const pa = attrs(pm[1]);
    const body = pm[2];
    const segs = [...body.matchAll(/<Flight\b([^>]*?)\/?>/gi)].map(m => attrs(m[1]));
    const out = segs.filter(s => (s.direction ?? "0") === "0" || s.direction === "");
    const legs = out.length ? out : segs;
    if (!legs.length) continue;
    const first = legs[0], last = legs[legs.length - 1];

    const fareCode = [...new Set(legs.map(s => s.farecode).filter(Boolean))].join("/");
    const mrid = (legs.map(s => s.mrid).find(v => v && v !== "0")) || "";
    if (!isSubsidized(fareCode, mrid)) continue;

    const [dt, tm] = splitDeparture(first.departure);
    const fn = legs.map(s => `${s.code || ""} ${s.num || ""}`.trim()).join(" + ");
    const urlMatch = /<BookURL>([^<]*)<\/BookURL>/i.exec(body);
    const row = {
      o: origin,
      d: destination,
      dt: dt || date,
      tm,
      ar: timeOf(last.arrival),
      fn,
      al: first.code || "",
      fc: fareCode,
      q: Math.min(...legs.map(s => parseInt(s.availqty, 10) || 0)),
      p: parseFloat(String(pa.total || pa.fare || "0").replace(/\s/g, "").replace(",", ".")) || 0,
      url: withPersonId(urlMatch ? unescapeXml(urlMatch[1]).trim() : fallbackUrl, personId),
      pc: psg,
    };
    const key = [row.dt, row.fn, row.fc, mrid, psg].join("|");
    if (seen.has(key)) continue;
    seen.add(key);
    flights.push(row);
  }
  return flights;
}

/* ------------------------------------------------------------- проверки */

export function validate(params, now = new Date()) {
  const from = (params.get("from") || "").toUpperCase();
  const to = (params.get("to") || "").toUpperCase();
  const date = params.get("date") || "";
  if (!IATA_RE.test(from) || !IATA_RE.test(to)) return { error: "from/to — трёхбуквенные коды городов" };
  if (from === to) return { error: "from и to совпадают" };
  if (!DATE_RE.test(date)) return { error: "date — в формате ГГГГ-ММ-ДД" };
  const d = new Date(date + "T00:00:00Z");
  if (isNaN(d) || d.toISOString().slice(0, 10) !== date) return { error: "неверная дата" };
  const today = new Date(now.toISOString().slice(0, 10) + "T00:00:00Z");
  const days = (d - today) / 86_400_000;
  if (days < 0 || days > MAX_DAYS_AHEAD) return { error: "дата вне окна продаж" };
  return { from, to, date };
}

/* ------------------------------------------------------------ лимиты IP */

// Память изолята: сбрасывается при перезапуске, у разных дата-центров своя.
// Это защита от случайного шквала, а не строгий учёт — строгий даёт кэш.
const hits = new Map();
export function rateLimited(ip, now = Date.now()) {
  const list = (hits.get(ip) || []).filter(t => now - t < RATE_WINDOW_MS);
  list.push(now);
  hits.set(ip, list);
  if (hits.size > 10_000) hits.clear();
  return list.length > RATE_LIMIT;
}

/* --------------------------------------------------------------- запрос */

async function fetchCategory(env, ctx, q, psg) {
  const [y, m, d] = q.date.split("-");
  const url = new URL(env.API_URL || DEFAULT_API);
  url.searchParams.set("depCity", q.from);
  url.searchParams.set("destCity", q.to);
  // depDate — ДДММ без года, как в fetcher.py.
  url.searchParams.set("depDate", d + m);
  url.searchParams.set(psg, "1");
  url.searchParams.set("PartnerID", env.PARTNER_ID || "KirillTest");

  // Ключ кэша — без PartnerID в адресе, чтобы он не утёк в логи кэша.
  const cacheKey = new Request(`https://cache.local/fares/${q.from}-${q.to}/${q.date}/${psg}`);
  const cache = caches.default;
  const hit = await cache.match(cacheKey);
  if (hit) return { flights: await hit.json(), cached: true };

  const resp = await fetch(url.toString(), {
    headers: { "User-Agent": "subsidy-search-worker/1.0", Accept: "application/xml, text/xml" },
  });
  if (!resp.ok) throw new Error(`партнёр ответил HTTP ${resp.status}`);
  const xml = new TextDecoder("utf-8").decode(await resp.arrayBuffer());
  const flights = parseFares(xml, {
    origin: q.from, destination: q.to, date: q.date, psg, personId: env.PERSON_ID || "",
  });

  // Пустой ответ тоже кэшируем: «мест нет» — такой же результат, иначе
  // популярный пустой маршрут съест лимит повторными поисками.
  const ttl = parseInt(env.CACHE_TTL || "1200", 10);
  ctx.waitUntil(cache.put(cacheKey, new Response(JSON.stringify(flights), {
    headers: { "Content-Type": "application/json", "Cache-Control": `max-age=${ttl}` },
  })));
  return { flights, cached: false };
}

function corsHeaders(env, origin) {
  const allowed = (env.ALLOWED_ORIGINS || "").split(",").map(s => s.trim()).filter(Boolean);
  const ok = origin && (allowed.includes(origin) || allowed.includes("*"));
  return ok ? { "Access-Control-Allow-Origin": origin, Vary: "Origin" } : { Vary: "Origin" };
}

function json(body, status, extra) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8", ...extra },
  });
}

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    const cors = corsHeaders(env, request.headers.get("Origin"));

    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: {
        ...cors, "Access-Control-Allow-Methods": "GET", "Access-Control-Max-Age": "86400" } });
    }
    if (url.pathname !== "/api/search") return json({ ok: false, error: "not found" }, 404, cors);
    if (request.method !== "GET") return json({ ok: false, error: "только GET" }, 405, cors);

    const q = validate(url.searchParams);
    if (q.error) return json({ ok: false, error: q.error }, 400, cors);

    const ip = request.headers.get("CF-Connecting-IP") || "unknown";
    if (rateLimited(ip)) return json({ ok: false, error: "слишком много поисков, подождите минуту" }, 429, cors);

    try {
      const parts = await Promise.all(CATEGORIES.map(psg => fetchCategory(env, ctx, q, psg)));
      return json({
        ok: true,
        flights: parts.flatMap(p => p.flights),
        cached: parts.every(p => p.cached),
      }, 200, { ...cors, "Cache-Control": "no-store" });
    } catch (err) {
      return json({ ok: false, error: "партнёр недоступен, попробуйте позже" }, 502, cors);
    }
  },
};
