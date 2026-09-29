#!/usr/bin/env python3
"""
Бэкенд для макета витрины из design/: та же вёрстка и анимации, но вместо
вымышленных рейсов — реальные данные сборщика (как webapp.py, только для
другой разметки).

Часть полей макета (модель самолёта, длительность перелёта, пересадки)
партнёрский API не отдаёт вообще — эти узлы вырезаны, а не заполнены
выдумкой. Маршруты в форме поиска ограничены тем, что реально в config.ROUTES:
остальные комбинации городов из макета к реальным данным не относятся.

    python design_app.py                      # data/design/index.html
    python design_app.py --out /var/www/html/design/index.html

design/checkout.html в шаблонизации не участвует: он не знает о базе данных,
а принимает выбранный рейс через параметры адреса (уже общий контракт с этим
файлом, см. правки в нём же — date вместо day, без dur/stops/plane).
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

import cities
import config
from datajson import rows_to_data as _rows_to_data, to_json as _json
from db import Database

TEMPLATE_PATH = Path(__file__).resolve().parent / "design" / "index.html"
DEFAULT_OUT = config.DATA_DIR / "design" / "index.html"


def _replace_once(html: str, old: str, new: str, label: str) -> str:
    count = html.count(old)
    if count != 1:
        raise RuntimeError(
            f"design/index.html: ожидался ровно один блок «{label}», найдено {count}. "
            "Шаблон изменился — обновите design_app.py."
        )
    return html.replace(old, new, 1)


# Единственный фрагмент макета, который не может быть посчитан на клиенте:
# метатег для роботов должен быть в исходном HTML, а не дописан JS-ом после
# загрузки страницы (краулер JS не выполняет). Логика — та же, что в
# webapp.py.build(): noindex только в демо-режиме.
ROBOTS_LINE = '<meta name="robots" content="noindex,nofollow">\n'

# Фильтр «Без пересадок» осмыслен только когда известно, прямой рейс или нет.
# Партнёрский API такого признака не даёт, поэтому чип убираем целиком —
# оставлять рабочую с виду, но фактически бесполезную кнопку нечестно.
DIRECT_CHIP = '<button class="chip" type="button" data-filter="direct" aria-pressed="false">Без пересадок</button>\n        '


NEW_SCRIPT = r"""<script>
document.addEventListener('DOMContentLoaded', function () {
  'use strict';
  var $ = function (s, r) { return (r || document).querySelector(s); };
  var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };

  var reduceMQ = window.matchMedia('(prefers-reduced-motion: reduce)');
  function M() { return (!reduceMQ.matches && window.Motion) ? window.Motion : null; }
  var EASE = [0.16, 1, 0.3, 1];
  var SPRING = { type: 'spring', stiffness: 320, damping: 34 };
  function play(target, keyframes, options) {
    var m = M();
    if (!m || !target || (target.length === 0)) return Promise.resolve();
    var c = m.animate(target, keyframes, options || { duration: 0.45, ease: EASE });
    return new Promise(function (res) { c.then ? c.then(res, res) : res(); });
  }

  /* ---------- данные бэкенда ---------- */
  var DATA = __DATA_JSON__;
  var ROUTES = __ROUTES_JSON__;
  var META = __META_JSON__;
  var AIRPORTS = __AIRPORTS_JSON__;
  var CATEGORIES = __CATEGORIES_JSON__;
  var AIRLINES = __AIRLINES_JSON__;
  var LOW = __LOW_JSON__;

  var MONTHS = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля', 'августа',
    'сентября', 'октября', 'ноября', 'декабря'];
  var MONTHS_N = ['Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь', 'Июль', 'Август',
    'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь'];
  var DOW = ['пн', 'вт', 'ср', 'чт', 'пт', 'сб', 'вс'];
  var SEAT_FORMS = ['место', 'места', 'мест'];
  var nf = new Intl.NumberFormat('ru-RU');

  function cityName(c) { return (AIRPORTS[c] || {}).city || c; }
  function airlineName(c) { return AIRLINES[c] || c; }
  function fmtPrice(p) { return p ? nf.format(p) + ' ₽' : '—'; }
  // Для узкой клетки календаря: 10 800 → «10,8к», 9 500 → «9,5к».
  function shortPrice(p) { return (Math.round(p / 100) / 10).toLocaleString('ru-RU') + 'к'; }
  function fmtDate(iso) { var d = new Date(iso + 'T00:00:00'); return d.getDate() + ' ' + MONTHS[d.getMonth()]; }
  function plural(n, f) {
    var a = Math.abs(n) % 100, b = a % 10;
    if (a > 10 && a < 20) return f[2];
    if (b > 1 && b < 5) return f[1];
    if (b === 1) return f[0];
    return f[2];
  }
  function timeToMin(tm) { var m = /^(\d{1,2}):(\d{2})/.exec(tm || ''); return m ? (+m[1]) * 60 + (+m[2]) : 0; }
  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
    });
  }

  var state = { from: '', to: '', date: '', pax: 1, cats: ['dfo'], filters: { avail: false, evening: false } };
  function routeText() { return cityName(state.from) + ' →\u00a0' + cityName(state.to); }
  function dateText(iso) { return fmtDate(iso); }
  function seatsNeeded() { return state.pax; }
  function matchesRoute(f) { return f.o === state.from && f.d === state.to; }

  /* Льгот можно выбрать несколько: рейс подходит, если подходит хотя бы под
     одну выбранную. Категория без fare_codes (пока таких все, см. cities.py)
     подходит под любой субсидированный тариф — квота общая. */
  function selectedCats() { return CATEGORIES.filter(function (c) { return state.cats.indexOf(c.id) >= 0; }); }
  function catFits(c, f) {
    if (c.psg && f.pc && c.psg !== f.pc) return false;
    return !(c.fare_codes || []).length || c.fare_codes.indexOf(f.fc) >= 0;
  }
  function catsFor(f) { return selectedCats().filter(function (c) { return catFits(c, f); }); }
  function matchCategory(f) { return catsFor(f).length > 0; }
  function matches(f) { return matchesRoute(f) && matchCategory(f); }

  /* ---------- поля поиска ---------- */
  var fFrom = $('#from'), fTo = $('#to'), fDate = $('#date'), fPax = $('#pax');

  function originsList() {
    var seen = {}, out = [];
    ROUTES.forEach(function (r) { if (!seen[r.origin]) { seen[r.origin] = true; out.push(r.origin); } });
    return out;
  }
  function destinationsFor(origin) {
    return ROUTES.filter(function (r) { return r.origin === origin; }).map(function (r) { return r.destination; });
  }
  function fillSelect(sel, codes, selected) {
    sel.innerHTML = codes.map(function (c) {
      return '<option value="' + c + '"' + (c === selected ? ' selected' : '') + '>' + esc(cityName(c)) + '</option>';
    }).join('');
  }

  function showFields() {
    $('[data-show="from"]').textContent = cityName(fFrom.value);
    $('[data-show="from-code"]').textContent = fFrom.value;
    $('[data-show="to"]').textContent = cityName(fTo.value);
    $('[data-show="to-code"]').textContent = fTo.value;
    $('[data-show="date"]').textContent = fDate.value ? dateText(fDate.value) : 'Выберите дату';
    var p = parseInt(fPax.value, 10);
    $('[data-show="pax"]').textContent = p + ' ' + plural(p, ['пассажир', 'пассажира', 'пассажиров']);
  }
  fPax.addEventListener('change', showFields);
  fFrom.addEventListener('change', function () {
    var dests = destinationsFor(fFrom.value);
    fillSelect(fTo, dests, dests[0]);
    showFields();
  });
  fTo.addEventListener('change', showFields);
  fDate.addEventListener('input', showFields);
  fDate.addEventListener('change', showFields);
  fDate.addEventListener('click', function () { try { fDate.showPicker(); } catch (e) {} });

  var toast = $('#toast'), toastTimer = null;
  function showToast(msg) {
    toast.textContent = msg; toast.hidden = false;
    play(toast, { opacity: [0, 1], y: [12, 0] }, { duration: 0.3, ease: EASE });
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () {
      play(toast, { opacity: 0, y: 8 }, { duration: 0.2, ease: 'easeIn' }).then(function () { toast.hidden = true; toast.style.opacity = ''; toast.style.transform = ''; });
    }, 3200);
  }

  var swapRot = 0;
  $('#swap').addEventListener('click', function () {
    var rev = ROUTES.some(function (r) { return r.origin === fTo.value && r.destination === fFrom.value; });
    if (!rev) { showToast('Обратное направление сейчас не отслеживается.'); return; }
    var a = fFrom.value;
    fFrom.value = fTo.value;
    fillSelect(fTo, destinationsFor(fFrom.value), a);
    showFields();
    swapRot += 180;
    play($('#swap i'), { rotate: swapRot }, SPRING);
    play($$('.f-from .v, .f-to .v'), { opacity: [0, 1], x: [6, 0] }, { duration: 0.3, ease: EASE });
  });

  var searchErr = $('#search-err');
  function setSearchError(msg) {
    searchErr.textContent = msg || '';
    searchErr.hidden = !msg;
    if (msg) play(searchErr, { opacity: [0, 1], y: [-4, 0] }, { duration: 0.25, ease: EASE });
  }
  $('#search').addEventListener('submit', function (e) {
    e.preventDefault();
    if (!fDate.value) return setSearchError('Выберите дату вылета.');
    if (fDate.value < META.date_from || fDate.value > META.date_to) {
      return setSearchError('Мониторим даты с ' + dateText(META.date_from) + ' по ' + dateText(META.date_to) + '.');
    }
    setSearchError('');
    state.from = fFrom.value; state.to = fTo.value; state.date = fDate.value; state.pax = parseInt(fPax.value, 10);
    renderAll(true);
    goTo($('#rejsy'), $('#res-title'));
  });

  /* ---------- категории ---------- */
  var docText = $('[data-doc]');
  function renderNote() {
    var cs = selectedCats();
    if (!cs.length) return;
    docText.innerHTML = cs.map(function (c) {
      return '<b>' + esc(c.title) + '.</b> ' + c.requirements.map(esc).join('. ') + '.';
    }).join('<br>') + (cs.length > 1 ? '<br>Документы нужны только по той льготе, по которой покупаете билет.' : '');
  }
  function renderCats() {
    $$('.cat').forEach(function (x) { x.setAttribute('aria-pressed', String(state.cats.indexOf(x.dataset.cat) >= 0)); });
  }
  /* Последнюю выбранную льготу снять нельзя — иначе непонятно, что искать. */
  $$('.cat').forEach(function (b) {
    b.addEventListener('click', function () {
      var id = b.dataset.cat, on = state.cats.indexOf(id) >= 0;
      if (on && state.cats.length === 1) { showToast('Нужна хотя бы одна льгота.'); return; }
      state.cats = CATEGORIES.map(function (c) { return c.id; }).filter(function (x) {
        return x === id ? !on : state.cats.indexOf(x) >= 0;
      });
      renderCats();
      renderNote();
      play(docText, { opacity: [0, 1], y: [4, 0] }, { duration: 0.35, ease: EASE });
      if (state.date) { renderAll(false); renderBoard(); }
    });
  });

  /* ---------- рейсы ---------- */
  var flightsBox = $('#flights');
  var emptyFilter = $('#empty-filter'), emptyOff = $('#empty-off'), filtersBox = $('.filters');
  var cards = [], currentFlights = [];

  function flightsForDate() {
    return DATA.filter(function (f) { return matches(f) && f.dt === state.date; })
      .sort(function (a, b) { return (a.tm || '').localeCompare(b.tm || ''); });
  }
  function cardHtml(f) {
    var need = seatsNeeded();
    var st = f.q === 0 ? 'none' : (f.q < need ? 'short' : (f.q <= LOW ? 'low' : 'ok'));
    var canBuy = (st === 'ok' || st === 'low') && !!f.url;
    var seatsHtml = st === 'none'
      ? '<b data-seats>Мест нет</b>'
      : '<b data-seats>' + f.q + '</b><span class="u" data-unit>' + plural(f.q, SEAT_FORMS) + ' по ' + fmtPrice(f.p) + '</span>';
    var fit = catsFor(f);
    var trend = st === 'short' ? 'Не хватит на всех пассажиров'
      : (fit.length < state.cats.length ? 'По льготе: ' + fit.map(function (c) { return c.short; }).join(', ') : '');
    return '<li class="flight" data-state="' + st + '" data-seats="' + f.q + '" data-dep="' + timeToMin(f.tm) + '">' +
      '<div class="fl-main"><div class="leg">' +
      '<div class="pt"><b class="num">' + esc(f.tm || '—') + '</b><span data-code="from">' + esc(f.o) + '</span></div>' +
      '<div class="path"><i aria-hidden="true"></i></div>' +
      '<div class="pt"><b class="num">' + esc(f.ar || '—') + '</b><span data-code="to">' + esc(f.d) + '</span></div>' +
      '</div><div class="carrier"><b>' + esc(airlineName(f.al)) + '</b>' + esc(f.fn || '—') + '</div></div>' +
      '<div class="count" aria-live="off">' + seatsHtml + (trend ? '<span class="trend" data-trend>' + trend + '</span>' : '') + '</div>' +
      '<div class="act">' +
      '<button class="btn p" type="button" data-buy' + (canBuy ? '' : ' hidden') + '>Купить у партнёра</button>' +
      '<button class="btn s" type="button" data-notify' + (canBuy ? ' hidden' : '') + '>Сообщить о местах</button>' +
      '</div></li>';
  }
  function onBuy(f) {
    if (!f || !f.url) return;
    var next = (f.ar && f.tm && f.ar < f.tm) ? '1' : '0';
    var q = new URLSearchParams({
      from: cityName(state.from), to: cityName(state.to),
      fromAp: state.from, toAp: state.to,
      date: state.date, dep: f.tm || '', arr: f.ar || '', next: next,
      carrier: airlineName(f.al), num: f.fn || '',
      seats: f.q, pax: state.pax, cat: catsFor(f)[0].id,
      cats: catsFor(f).map(function (c) { return c.id; }).join(','), price: Math.round(f.p || 0)
    });
    window.location.href = 'checkout.html?' + q.toString();
  }
  function onNotify(f) {
    ctxFlight = f ? (f.fn || '') : '';
    syncContext();
    if (!subdone.hidden) { subdone.hidden = true; subform.hidden = false; }
    goTo($('#podpiska'), contact);
  }
  function updateResultsIntro() {
    var el = $('#rejsy .sec-h p');
    var n = currentFlights.length;
    el.textContent = (n ? (n + ' ' + plural(n, ['рейс', 'рейса', 'рейсов']) + ' в этот день. ') : '') +
      'Вместо цены показываем число свободных мест: тариф по субсидии одинаковый на всех рейсах.';
  }
  function renderFlights() {
    currentFlights = flightsForDate();
    flightsBox.innerHTML = currentFlights.map(cardHtml).join('');
    cards = $$('.flight', flightsBox);
    $$('[data-buy]', flightsBox).forEach(function (b, i) { b.addEventListener('click', function () { onBuy(currentFlights[i]); }); });
    $$('[data-notify]', flightsBox).forEach(function (b, i) { b.addEventListener('click', function () { onNotify(currentFlights[i]); }); });
    updateResultsIntro();
  }

  /* ---------- фильтры (FLIP) ---------- */
  function matchesFilter(c) {
    var f = state.filters;
    if (f.avail && !(+c.dataset.seats >= state.pax && +c.dataset.seats > 0)) return false;
    if (f.evening && +c.dataset.dep < 18 * 60) return false;
    return true;
  }
  function fillEmptyOff() {
    var h3 = $('#empty-off h3'), p = $('#empty-off p');
    var d = state.date;
    if (d < META.date_from || d > META.date_to) {
      h3.textContent = 'Эту дату мы пока не отслеживаем';
      p.textContent = 'Мониторинг идёт с ' + dateText(META.date_from) + ' по ' + dateText(META.date_to) + '. Выберите другую дату в календаре.';
    } else {
      h3.textContent = 'Мест на эту дату нет';
      p.textContent = 'На ' + dateText(d) + ' субсидированных мест по маршруту ' + routeText() + ' нет. Посмотрите соседние даты в календаре ниже.';
    }
  }
  var running = [];
  var flipToken = 0;
  function stopRunning() { running.forEach(function (c) { try { c.stop(); } catch (e) {} }); running = []; }
  function track(c) { if (c) running.push(c); return c; }
  var SHOWN = { opacity: 1, scale: 1, y: 0 };
  function applyFilters(animate) {
    var token = ++flipToken;
    var hasFlights = cards.length > 0;
    filtersBox.hidden = !hasFlights;
    emptyOff.hidden = hasFlights;
    if (!hasFlights) {
      fillEmptyOff();
      emptyFilter.hidden = true;
      return;
    }
    var want = new Map();
    cards.forEach(function (c) { want.set(c, matchesFilter(c)); });
    var anyShown = cards.some(function (c) { return want.get(c); });
    function finish() { emptyFilter.hidden = anyShown; }
    var m = animate ? M() : null;
    stopRunning();
    if (!m) {
      cards.forEach(function (c) { c.hidden = !want.get(c); c.style.opacity = ''; c.style.transform = ''; });
      finish();
      return;
    }
    var leaving = cards.filter(function (c) { return !c.hidden && !want.get(c); });
    var entering = cards.filter(function (c) { return c.hidden && want.get(c); });
    var staying = cards.filter(function (c) { return !c.hidden && want.get(c); });
    var first = new Map();
    staying.forEach(function (c) { first.set(c, c.getBoundingClientRect().top); });
    var out = Promise.resolve();
    if (leaving.length) {
      var lc = track(m.animate(leaving, { opacity: 0, scale: 0.98 }, { duration: 0.16, ease: 'easeIn' }));
      out = new Promise(function (res) { lc.then(res, res); });
    }
    out.then(function () {
      if (token !== flipToken) return;
      leaving.forEach(function (c) { c.hidden = true; m.animate(c, SHOWN, { duration: 0 }); });
      entering.forEach(function (c) { c.hidden = false; });
      finish();
      staying.forEach(function (c) {
        var dy = first.get(c) - c.getBoundingClientRect().top;
        track(m.animate(c, { y: [dy, 0], opacity: 1, scale: 1 }, { duration: 0.45, ease: EASE }));
      });
      if (entering.length) track(m.animate(entering, { opacity: [0, 1], y: [10, 0], scale: 1 }, { duration: 0.4, ease: EASE, delay: m.stagger(0.05) }));
      var emptyShown = [emptyFilter].filter(function (e) { return !e.hidden; });
      if (emptyShown.length) m.animate(emptyShown, { opacity: [0, 1], y: [8, 0] }, { duration: 0.35, ease: EASE });
    });
  }
  $$('[data-filter]').forEach(function (chip) {
    chip.addEventListener('click', function () {
      var k = chip.dataset.filter;
      if (k === 'all') { state.filters = { avail: false, evening: false }; }
      else { state.filters[k] = !state.filters[k]; }
      syncChips();
      applyFilters(true);
    });
  });
  $('[data-reset]').addEventListener('click', function () {
    state.filters = { avail: false, evening: false };
    syncChips(); applyFilters(true);
  });
  function syncChips() {
    var any = state.filters.avail || state.filters.evening;
    $$('[data-filter]').forEach(function (c) {
      var k = c.dataset.filter;
      c.setAttribute('aria-pressed', String(k === 'all' ? !any : !!state.filters[k]));
    });
  }

  /* ---------- календарь ---------- */
  var calGrid = $('.cal-grid');
  function renderCalendar() {
    var need = seatsNeeded(), by = {}, minP = {};
    DATA.filter(matches).forEach(function (f) {
      var fits = f.q >= need;
      by[f.dt] = (by[f.dt] || 0) + (fits ? f.q : 0);
      // Цена «от» — только среди рейсов, где мест хватает на всех пассажиров.
      if (fits && f.p > 0 && !(minP[f.dt] <= f.p)) minP[f.dt] = f.p;
    });
    var ym = state.date.slice(0, 7).split('-').map(Number);
    var y = ym[0], m = ym[1];
    $('#cal-route').textContent = MONTHS_N[m - 1] + ' ' + y + ', ' + routeText();

    var first = new Date(y, m - 1, 1), start = (first.getDay() + 6) % 7;
    var daysInMonth = new Date(y, m, 0).getDate();
    var html = DOW.map(function (d) { return '<span class="dow" aria-hidden="true">' + d + '</span>'; }).join('');
    for (var i = 0; i < start; i++) html += '<span class="pad" aria-hidden="true"></span>';
    for (var d = 1; d <= daysInMonth; d++) {
      var iso = y + '-' + String(m).padStart(2, '0') + '-' + String(d).padStart(2, '0');
      var known = Object.prototype.hasOwnProperty.call(by, iso);
      var q = by[iso];
      var cls = !known ? '' : (q === 0 ? 'no' : (q <= LOW ? 'low' : 'ok'));
      var sel = iso === state.date;
      var p = q ? minP[iso] : 0;
      var label = d + ' ' + MONTHS[m - 1] + ', ' + (!known ? 'вне мониторинга' : (q ? q + ' ' + plural(q, SEAT_FORMS) : 'мест нет')) +
        (p ? ', от ' + fmtPrice(p) : '');
      var priceHtml = p ? '<span class="p" aria-hidden="true"><span class="f">от ' + fmtPrice(p) + '</span><span class="k">' + shortPrice(p) + '</span></span>' : '';
      html += '<button class="day' + (cls ? ' ' + cls : '') + '" type="button" data-day="' + d + '" data-iso="' + iso + '"' +
        (known ? '' : ' disabled') + ' aria-pressed="' + sel + '" aria-label="' + esc(label) + '">' +
        '<span class="d">' + d + '</span><span class="s">' + (!known ? '' : (q ? q : 'нет')) + '</span>' + priceHtml + '</button>';
    }
    calGrid.innerHTML = html;
    wireCalendarDays();
  }
  function wireCalendarDays() {
    var days = $$('.day', calGrid);
    days.forEach(function (btn) {
      btn.addEventListener('click', function () {
        state.date = btn.dataset.iso;
        fDate.value = state.date;
        showFields();
        play(btn, { scale: [0.94, 1] }, SPRING);
        renderAll(true);
        goTo($('#rejsy'), $('#res-title'));
      });
    });
    var enabledDays = days.filter(function (b) { return !b.disabled; });
    enabledDays.forEach(function (b) { b.tabIndex = b.dataset.iso === state.date ? 0 : -1; });
    calGrid.onkeydown = function (e) {
      var cur = e.target.closest('.day'); if (!cur) return;
      var d = +cur.dataset.day, target = null;
      var byDay = function (n) { return enabledDays.filter(function (b) { return +b.dataset.day === n; })[0]; };
      if (e.key === 'ArrowRight') target = byDay(d + 1);
      else if (e.key === 'ArrowLeft') target = byDay(d - 1);
      else if (e.key === 'ArrowDown') target = byDay(d + 7);
      else if (e.key === 'ArrowUp') target = byDay(d - 7);
      else if (e.key === 'Home') target = enabledDays[0];
      else if (e.key === 'End') target = enabledDays[enabledDays.length - 1];
      else return;
      e.preventDefault();
      if (!target) return;
      enabledDays.forEach(function (b) { b.tabIndex = b === target ? 0 : -1; });
      target.focus();
    };
  }

  /* ---------- направления на табло ---------- */
  function renderBoard() {
    var box = $('.board ul');
    if (!box) return;
    box.innerHTML = ROUTES.map(function (r) {
      var recs = DATA.filter(function (f) { return f.o === r.origin && f.d === r.destination && matchCategory(f); });
      var best = recs.length ? recs.reduce(function (a, b) { return b.q > a.q ? b : a; }) : null;
      if (!best || best.q === 0) {
        return '<li class="brow no"><span class="d">—</span><span class="r">' + esc(cityName(r.origin)) + ' →&nbsp;' + esc(cityName(r.destination)) + '</span><span class="seats"><b>нет</b></span></li>';
      }
      var cls = best.q <= LOW ? 'low' : 'ok';
      var dd = best.dt.slice(8, 10) + '.' + best.dt.slice(5, 7);
      return '<li class="brow ' + cls + '"><span class="d">' + dd + '</span><span class="r">' + esc(cityName(r.origin)) + ' →&nbsp;' + esc(cityName(r.destination)) +
        '<span>' + esc(best.fn || '') + ' · вылет ' + esc(best.tm || '—') + '</span></span>' +
        '<span class="seats"><b>' + best.q + '</b><span>' + plural(best.q, SEAT_FORMS) + '</span></span></li>';
    }).join('');
  }

  /* ---------- проверено N минут назад ---------- */
  function renderStamp() {
    var el = $('.stamp'); if (!el) return;
    var gen = META.generated ? new Date(META.generated) : null;
    var txt;
    if (!gen || isNaN(gen.getTime())) {
      txt = 'Обновляем каждый час';
    } else {
      var mins = Math.max(0, Math.round((Date.now() - gen.getTime()) / 60000));
      txt = mins < 1 ? 'Проверено только что'
        : 'Проверено ' + mins + ' ' + plural(mins, ['минуту', 'минуты', 'минут']) + ' назад';
    }
    el.innerHTML = '<span class="live" aria-hidden="true"></span>' + txt;
  }

  /* ---------- подписка ---------- */
  var ctx = $('[data-ctx]'), contact = $('#contact'), err = $('#contact-err');
  var subform = $('#subform'), subdone = $('#subdone');
  var ctxFlight = '';
  function syncContext() {
    ctx.textContent = routeText() + ', ' + dateText(state.date) + (ctxFlight ? ', рейс ' + ctxFlight : '');
  }
  $$('[data-notify]').forEach(function (btn) {
    if (btn.closest('#flights')) return;
    btn.addEventListener('click', function () { onNotify(null); });
  });
  function validContact(v) {
    return /^@[A-Za-z][A-Za-z0-9_]{4,31}$/.test(v) || /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(v);
  }
  function setErr(msg) {
    err.textContent = msg || ''; err.hidden = !msg;
    contact.setAttribute('aria-invalid', msg ? 'true' : 'false');
    if (msg) play(err, { opacity: [0, 1], y: [-4, 0] }, { duration: 0.25, ease: EASE });
  }
  contact.addEventListener('input', function () { if (!err.hidden && validContact(contact.value.trim())) setErr(''); });
  subform.addEventListener('submit', function (e) {
    e.preventDefault();
    var v = contact.value.trim();
    if (!v) { setErr('Введите Telegram или почту.'); contact.focus(); return; }
    if (!validContact(v)) { setErr('Проверьте адрес. Ник в Telegram начинается с @, у почты есть @ и точка.'); contact.focus(); return; }
    setErr('');
    $('[data-done-text]').textContent = 'Напишем на ' + v + ', как только появятся места: ' + ctx.textContent + '.';
    subform.hidden = true; subdone.hidden = false;
    subdone.focus({ preventScroll: true });
    play(subdone, { opacity: [0, 1], y: [8, 0] }, { duration: 0.4, ease: EASE });
  });
  $('#subedit').addEventListener('click', function () {
    subdone.hidden = true; subform.hidden = false;
    play(subform, { opacity: [0, 1] }, { duration: 0.3, ease: EASE });
    contact.focus();
  });

  /* ---------- общие ---------- */
  function renderAll(animate) {
    $('#res-title').textContent = routeText() + ', ' + dateText(state.date);
    ctxFlight = '';
    syncContext();
    renderFlights();
    applyFilters(animate);
    renderCalendar();
    if (animate) {
      var m = M();
      var counts = $$('.flight:not([hidden]) .count', flightsBox);
      if (m) m.animate(counts, { opacity: [0, 1], y: [-6, 0] }, { duration: 0.35, ease: EASE, delay: m.stagger(0.05) });
    }
  }
  function goTo(section, focusEl) {
    var smooth = !reduceMQ.matches;
    if (focusEl) focusEl.focus({ preventScroll: true });
    section.scrollIntoView({ behavior: smooth ? 'smooth' : 'auto', block: 'start' });
  }
  $('a[href="#privacy"]').addEventListener('click', function (e) { e.preventDefault(); showToast('Страница политики появится к запуску.'); });

  function init() {
    var origins = originsList();
    state.from = origins[0] || '';
    var dests = destinationsFor(state.from);
    state.to = dests[0] || '';
    fillSelect(fFrom, origins, state.from);
    fillSelect(fTo, dests, state.to);

    var onRoute = DATA.filter(matches);
    var withSeats = onRoute.filter(function (f) { return f.q > 0; }).map(function (f) { return f.dt; }).sort();
    state.date = withSeats[0] || META.date_from;
    fDate.value = state.date;
    fDate.min = META.date_from; fDate.max = META.date_to;

    renderCats();
    renderNote();
    renderStamp();
    renderBoard();
    renderAll(false);
    showFields();
  }

  /* ---------- меню ---------- */
  var menuBtn = $('.menu-btn'), menu = $('#menu');
  function setMenu(open, returnFocus) {
    menuBtn.setAttribute('aria-expanded', String(open));
    menuBtn.querySelector('i').className = 'ph ' + (open ? 'ph-x' : 'ph-list');
    if (open) {
      menu.hidden = false;
      play(menu, { opacity: [0, 1], y: [-6, 0] }, { duration: 0.22, ease: EASE });
      var first = menu.querySelector('a'); if (first) first.focus();
    } else if (!menu.hidden) {
      play(menu, { opacity: 0, y: -4 }, { duration: 0.14, ease: 'easeIn' }).then(function () {
        if (menuBtn.getAttribute('aria-expanded') === 'false') { menu.hidden = true; menu.style.opacity = ''; menu.style.transform = ''; }
      });
      if (returnFocus) menuBtn.focus();
    }
  }
  menuBtn.addEventListener('click', function () { setMenu(menuBtn.getAttribute('aria-expanded') !== 'true'); });
  menu.addEventListener('click', function (e) { if (e.target.closest('a')) setMenu(false); });
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape' && menuBtn.getAttribute('aria-expanded') === 'true') setMenu(false, true); });
  document.addEventListener('click', function (e) {
    if (menuBtn.getAttribute('aria-expanded') === 'true' && !menu.contains(e.target) && !menuBtn.contains(e.target)) setMenu(false);
  });
  window.matchMedia('(min-width: 901px)').addEventListener('change', function (q) { if (q.matches) { setMenu(false); menu.hidden = true; } });

  /* Подсветка текущего раздела в навигации (IntersectionObserver, без слушателя прокрутки). */
  if ('IntersectionObserver' in window) {
    var links = $$('.topnav a, .menu a');
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) {
        if (!en.isIntersecting) return;
        var id = '#' + en.target.id;
        links.forEach(function (a) { if (a.getAttribute('href') === id) a.setAttribute('aria-current', 'true'); else a.removeAttribute('aria-current'); });
      });
    }, { rootMargin: '-45% 0px -50% 0px' });
    ['rejsy', 'calendar', 'kak', 'podpiska'].forEach(function (id) { var s = document.getElementById(id); if (s) io.observe(s); });
  }

  init();

  /* ---------- появление при прокрутке ---------- */
  (function reveals() {
    var m = M(); if (!m) return;
    var vh = window.innerHeight;
    function below(el) { return el.getBoundingClientRect().top > vh * 0.92; }
    function once(root, fn) {
      var stop = m.inView(root, function () { fn(); if (stop) stop(); }, { amount: 0.2 });
    }
    m.animate('.brow .seats', { opacity: [0, 1], y: [-8, 0] }, { duration: 0.45, ease: EASE, delay: m.stagger(0.07, { startDelay: 0.15 }) });

    var list = $('#flights');
    if (below(list)) {
      var items = $$('.flight:not([hidden])');
      m.animate(items, { opacity: 0, y: 16 }, { duration: 0 });
      once(list, function () {
        if (flipToken > 1) return;
        var vis = items.filter(function (c) { return !c.hidden; });
        track(m.animate(vis, { opacity: 1, y: 0 }, { duration: 0.55, ease: EASE, delay: m.stagger(0.07) }));
      });
    }
    var grid = $('.cal-grid');
    if (below(grid)) {
      var ds = $$('.day');
      m.animate(ds, { opacity: 0, y: 6 }, { duration: 0 });
      once(grid, function () { m.animate(ds, { opacity: 1, y: 0 }, { duration: 0.4, ease: EASE, delay: m.stagger(0.012) }); });
    }
    var rw = $('.route-wrap');
    if (below(rw)) {
      var line = $('.route-line'), mks = $$('.route .mk'), txt = $$('.route h3, .route p');
      var vertical = window.matchMedia('(max-width: 760px)').matches;
      m.animate(line, vertical ? { scaleY: 0 } : { scaleX: 0 }, { duration: 0 });
      m.animate(mks, { opacity: 0, scale: 0.6 }, { duration: 0 });
      m.animate(txt, { opacity: 0, y: 10 }, { duration: 0 });
      once(rw, function () {
        m.animate(line, vertical ? { scaleY: 1 } : { scaleX: 1 }, { duration: 0.9, ease: EASE });
        m.animate(mks, { opacity: 1, scale: 1 }, { type: 'spring', stiffness: 360, damping: 26, delay: m.stagger(0.28) });
        m.animate(txt, { opacity: 1, y: 0 }, { duration: 0.5, ease: EASE, delay: m.stagger(0.14, { startDelay: 0.1 }) });
      });
    }
  })();
});
</script>"""


def build(out_path: Path | str | None = None, db: Database | None = None) -> Path:
    db = db or Database()
    rows = db.current_state()
    runs = db.last_runs(limit=1)
    last_run = runs[0] if runs else None
    is_demo = bool(last_run and last_run["dry_run"])

    data = _rows_to_data(rows)
    routes = [{"origin": r.origin, "destination": r.destination} for r in config.ROUTES]
    meta = {
        "generated": None,
        "date_from": config.DATE_FROM.isoformat(),
        "date_to": config.DATE_TO.isoformat(),
        "demo": is_demo,
        "partner": config.PARTNER_ID,
    }
    if last_run and last_run["finished_at"]:
        meta["generated"] = last_run["finished_at"]

    html = TEMPLATE_PATH.read_text(encoding="utf-8")

    html = _replace_once(
        html, ROBOTS_LINE, ROBOTS_LINE if is_demo else "", "метатег robots"
    )
    html = _replace_once(html, DIRECT_CHIP, "", "фильтр «Без пересадок»")

    if not is_demo:
        demo_match = re.search(
            r'<aside class="demo"[^>]*>.*?</aside>\n?', html, re.S
        )
        if not demo_match:
            raise RuntimeError("design/index.html: не найден демо-баннер для удаления")
        html = html[: demo_match.start()] + html[demo_match.end():]

    script_match = re.search(r"<script>\ndocument\.addEventListener\('DOMContentLoaded'.*?</script>", html, re.S)
    if not script_match:
        raise RuntimeError("design/index.html: не найден основной <script> блок — шаблон изменился")

    new_script = (
        NEW_SCRIPT
        .replace("__DATA_JSON__", _json(data))
        .replace("__ROUTES_JSON__", _json(routes))
        .replace("__META_JSON__", _json(meta))
        .replace("__AIRPORTS_JSON__", _json(cities.as_json_dict()))
        .replace("__CATEGORIES_JSON__", _json(cities.CATEGORIES))
        .replace("__AIRLINES_JSON__", _json(cities.AIRLINES))
        .replace("__LOW_JSON__", str(config.ALERTS.low_seats_threshold))
    )
    html = html[: script_match.start()] + new_script + html[script_match.end():]

    out = Path(out_path) if out_path else DEFAULT_OUT
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Сборка макета витрины (design/) на реальных данных")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    path = build(args.out)
    print(f"design-витрина готова: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
