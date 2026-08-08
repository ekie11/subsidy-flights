#!/usr/bin/env python3
"""
Витрина поиска субсидированных билетов — статический сайт из данных сборщика.

Генерирует один самодостаточный HTML: данные из SQLite вшиваются в страницу
как JSON, поиск и фильтрация работают на клиенте. Бэкенда нет — страницу
раздаёт любой nginx, хостинг стоит копейки, а падать в рантайме нечему.

Раскладка повторяет привычную схему большого метапоиска: сплошной синий
экран сверху, крупный заголовок по центру, ряд пилюль-переключателей,
белая строка поиска из сегментов и контрастная кнопка действия. Ниже —
белая страница с серыми карточками. Пользователь такую страницу читает
без обучения, и это экономит ему усилия.

    python webapp.py                      # data/index.html
    python webapp.py --out /var/www/html/index.html

Пересобирать после каждого прогона collector.py:
    python collector.py --live --site

Фирменные цвета — переменные --brand и --cta в начале CSS.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import cities
import config
from db import Database


# ==========================================================================
# Стили
# ==========================================================================

CSS = """
/* ПАЛИТРА
   Построена по трём правилам, а не на вкус:

   1. 60-30-10. 60% площади — светлый нейтральный фон, 30% — глубокая
      индиговая подложка (шапка, герой, плашки), 10% — янтарный акцент
      только на действиях. Ограниченная палитра читается спокойнее.
   2. Насыщенный цвет не занимает больших площадей. Индиго тёмный и
      умеренно насыщенный: заливка в пол-экрана из яркого тона утомляет глаз.
   3. Нейтральные не серые, а подкрашенные тоном бренда (низкая насыщенность
      того же синего). Чистый серый выглядит безжизненно и «спорит» с цветом.

   Синий выбран не только эстетически: в исследованиях доверия к сервисам
   бронирования именно тёмно-синее окружение повышало намерение забронировать
   через рост доверия. Янтарь — тёплый комплемент к синему, поэтому кнопка
   действия видна и на светлом фоне, и на тёмном герое.

   Все цвета проверены на контраст WCAG AA (см. вывод selftest-контраста).
   Меняются здесь и больше нигде. */
:root{
  --brand:#1e3163;--brand-dk:#16244a;--brand-soft:#eef1fa;
  --cta:#f5a524;--cta-dk:#e0930f;--cta-ink:#3a2600;
  --ink:#141a29;--ink-2:#3f4a60;--dim:#5b6577;--faint:#98a0b0;
  --line:#e5e8f0;--grey:#f5f7fb;--grey-2:#e9edf5;--bg:#fff;
  /* Светофор наличия мест. Затемнён до 4.5:1 на белом — эти же цвета идут
     в мелкие подписи, где послабление для крупного текста не действует. */
  --green:#12784a;--amber:#96600a;--red:#be3229;
  --r:12px;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
  font:16px/1.45 ui-sans-serif,-apple-system,"Segoe UI",Roboto,Helvetica,sans-serif;
  -webkit-font-smoothing:antialiased}
.wrap{max-width:1120px;margin:0 auto;padding:0 20px}
button{font:inherit;cursor:pointer;color:inherit}
input,select{font:inherit;color:inherit}
h1,h2,h3{letter-spacing:-.02em}

/* ---------- синий экран ---------- */
/* Едва заметный вертикальный переход: плоская заливка такой площади выглядит
   картонной, а сильный градиент — крикливым. Разница между концами ~6% света. */
.blue{background:linear-gradient(180deg,#223770 0%,#1a2b56 100%);color:#fff}
.topbar{display:flex;align-items:center;gap:16px;height:64px}
.logo{display:flex;align-items:center;gap:10px;font-weight:800;font-size:20px;
  letter-spacing:-.02em}
.logo .mark{width:32px;height:32px;border-radius:9px;background:#fff;color:var(--brand);
  display:grid;place-items:center;font-size:17px}
.topnav{margin-left:auto;display:flex;gap:26px;font-size:15px;font-weight:600}
.topnav a{color:#fff;text-decoration:none;opacity:.92}
.topnav a:hover{opacity:1}

.hero{padding:46px 0 30px;text-align:center}
.hero h1{margin:0 auto 26px;font-size:clamp(28px,4vw,46px);line-height:1.08;
  font-weight:800;max-width:16em}

/* пилюли-категории */
.cats{display:flex;flex-wrap:wrap;justify-content:center;gap:8px;margin-bottom:26px}
.cat{border:0;background:rgba(255,255,255,.16);color:#fff;border-radius:var(--r);
  padding:13px 20px;font-size:15px;font-weight:600;line-height:1.2}
.cat:hover{background:rgba(255,255,255,.26)}
.cat.on{background:#fff;color:var(--ink)}

/* строка поиска
   color здесь обязателен: строка лежит внутри синего блока, где текст белый,
   а фон у неё белый — без явного цвета значения полей наследуют белый
   и полностью исчезают. */
.search{background:#fff;color:var(--ink);border-radius:10px;display:grid;
  grid-template-columns:1fr 1fr 1fr 1fr auto;align-items:stretch;
  box-shadow:0 2px 10px rgba(0,0,0,.10);text-align:left}
.field{position:relative;min-width:0;padding:12px 18px;border-right:1px solid var(--line);
  display:flex;flex-direction:column;justify-content:center;min-height:70px}
.field:hover{background:#fafbfc}
.field:focus-within{background:#fafbfc}
.field .lab{font-size:13px;color:var(--dim);margin-bottom:1px}
.control{width:100%;height:26px;border:0;background:transparent;color:var(--ink);
  outline:none;padding:0;font-size:17px;font-weight:700;letter-spacing:-.01em;
  appearance:none;cursor:pointer;border-radius:0;text-overflow:ellipsis}
/* Код аэропорта прижат вправо, а длинное название города обрезается, не заезжая
   под него. В поле «Откуда» отступ больше: там же сидит кнопка обмена. */
.code{position:absolute;right:18px;top:50%;transform:translateY(-4px);font-size:13px;
  color:var(--faint);font-weight:600;pointer-events:none;text-transform:uppercase}
#from{padding-right:84px}
#to{padding-right:48px}
.field:first-child .code{right:52px}
.swap{position:absolute;right:-15px;top:50%;transform:translateY(-50%);z-index:4;
  width:30px;height:30px;border:1px solid var(--line);border-radius:50%;background:#fff;
  color:var(--brand);font-size:14px;line-height:1;padding:0;
  box-shadow:0 1px 3px rgba(0,0,0,.10)}
.swap:hover{background:var(--brand-soft)}
/* Текст на золотой заливке — тёмный: белый по золоту не проходит по контрасту. */
.btn-find{border:0;background:var(--cta);color:var(--cta-ink);font-weight:800;font-size:18px;
  padding:0 40px;border-radius:0 10px 10px 0;letter-spacing:-.01em;white-space:nowrap}
.btn-find:hover{background:var(--cta-dk)}

/* пассажиры */
.pax-btn{text-align:left}
.pax-sub{font-size:13px;color:var(--dim);margin-top:1px}
.pax-pop{position:absolute;top:calc(100% + 8px);left:0;width:310px;background:#fff;
  border-radius:var(--r);box-shadow:0 6px 24px rgba(0,0,0,.16);padding:8px 18px 14px;
  display:none;z-index:20;color:var(--ink)}
.pax-pop.open{display:block}
.pax-row{display:flex;align-items:center;gap:12px;padding:12px 0;
  border-bottom:1px solid var(--line)}
.pax-row:last-of-type{border-bottom:0}
.pax-row .t{flex:1}
.pax-row .t b{display:block;font-size:15px;font-weight:600}
.pax-row .t span{font-size:13px;color:var(--dim)}
.step{width:32px;height:32px;border:0;border-radius:50%;background:var(--grey);
  color:var(--brand);font-size:17px;line-height:1;font-weight:700}
.step:hover:not(:disabled){background:var(--grey-2)}
.step:disabled{color:var(--faint);cursor:not-allowed}
.step-val{width:20px;text-align:center;font-weight:700}
.pax-hint{font-size:13px;color:var(--dim);padding-top:12px;border-top:1px solid var(--line)}

.underbar{display:flex;flex-wrap:wrap;gap:14px;align-items:center;
  padding:14px 0 34px;font-size:15px;font-weight:600}
.underbar .right{margin-left:auto;font-weight:400;opacity:.85}

/* ---------- белая страница ---------- */
.strip{background:#fff;border-radius:var(--r);box-shadow:0 1px 4px rgba(0,0,0,.07);
  display:flex;align-items:center;gap:14px;padding:18px 24px;margin:-22px auto 0;
  max-width:760px;position:relative;z-index:2;font-size:16px}
.strip .ic{width:22px;color:var(--dim);flex:none;text-align:center}
.strip a{margin-left:auto;color:var(--brand);font-weight:700;text-decoration:none;
  white-space:nowrap}
.page{padding:44px 0 0}
.sec{margin-bottom:44px}
.sec-h{display:flex;align-items:baseline;flex-wrap:wrap;gap:12px;margin-bottom:16px}
.sec-h h2{margin:0;font-size:24px;font-weight:800}
.sec-h .sub{color:var(--dim);font-size:15px}
.sec-h .right{margin-left:auto}

.note{background:var(--grey);border-radius:var(--r);padding:18px 22px;font-size:15px;
  color:var(--ink-2)}
.note b{color:var(--ink)}
.note ul{margin:8px 0 0;padding-left:20px}
.note li{margin:3px 0}

/* ---------- результаты ---------- */
.sortsel{border:0;background:var(--grey);border-radius:999px;padding:9px 16px;
  font-size:14px;font-weight:600;color:var(--ink-2);outline:none}
.flights{width:100%;border-collapse:separate;border-spacing:0 10px}
.flights thead{display:none}
.flights td{background:#fff;padding:16px 18px;vertical-align:middle;
  border-top:1px solid var(--line);border-bottom:1px solid var(--line)}
.flights td:first-child{border-left:1px solid var(--line);
  border-radius:var(--r) 0 0 var(--r);padding-left:22px}
.flights td:last-child{border-right:1px solid var(--line);
  border-radius:0 var(--r) var(--r) 0;text-align:right;padding-right:22px}
.flights tr:hover td{background:#fafbfc}
.time{font-size:23px;font-weight:800;letter-spacing:-.02em}
.time small{display:block;font-size:13px;font-weight:400;color:var(--dim);
  letter-spacing:0;margin-top:2px}
.fl b{font-weight:700;font-size:16px}
.fl span{display:block;font-size:13px;color:var(--dim);margin-top:2px}
.seats{font-size:23px;font-weight:800;letter-spacing:-.02em}
.seats.ok{color:var(--green)}.seats.low{color:var(--amber)}.seats.no{color:var(--red)}
.seats small{display:block;font-size:12px;font-weight:500;color:var(--dim);margin-top:2px}
.price{font-size:23px;font-weight:800;white-space:nowrap;letter-spacing:-.02em}
.price small{display:block;font-size:12px;font-weight:400;color:var(--dim);margin-top:2px}
.buy{display:inline-block;background:var(--cta);color:var(--cta-ink);border:0;border-radius:10px;
  padding:13px 26px;font-weight:800;font-size:16px;text-decoration:none;white-space:nowrap}
.buy:hover{background:var(--cta-dk)}
.buy.off{background:var(--grey);color:var(--faint);cursor:default;font-weight:600}
.watch{background:var(--brand);border:0;border-radius:10px;padding:13px 24px;color:#fff;
  font-weight:800;font-size:16px;white-space:nowrap}
.watch:hover{background:var(--brand-dk)}

.empty{background:var(--grey);border-radius:var(--r);padding:44px 28px;text-align:center}
.empty .big{font-size:22px;font-weight:800;margin-bottom:8px}
.empty .sm{color:var(--dim);max-width:38em;margin:0 auto 22px;font-size:15px}
.jump{display:flex;flex-wrap:wrap;gap:10px;justify-content:center;margin-bottom:22px}
.jump button{background:#fff;border:0;border-radius:10px;padding:12px 20px;text-align:left}
.jump button:hover{box-shadow:0 2px 8px rgba(0,0,0,.10)}
.jump b{display:block;font-size:16px;font-weight:700}
.jump span{font-size:13px;color:var(--green);font-weight:700}

/* ---------- направления ---------- */
.pops{display:grid;grid-template-columns:repeat(3,1fr);gap:14px}
.pop{background:var(--grey);border:0;border-radius:var(--r);padding:16px 20px;
  text-align:left;display:flex;align-items:center;gap:14px}
.pop:hover{background:var(--grey-2)}
.pop .txt{flex:1;min-width:0}
.pop b{font-size:16px;font-weight:700}
.pop .p{color:var(--dim);font-weight:400}
.pop .q{font-size:13px;font-weight:700;margin-top:3px}
.pop .q.ok{color:var(--green)}.pop .q.low{color:var(--amber)}.pop .q.no{color:var(--dim)}
.pop .ch{color:var(--faint);font-size:13px}

/* ---------- календарь ---------- */
.cal{background:var(--grey);border-radius:var(--r);padding:20px 22px 22px}
.cal-top{display:flex;align-items:center;gap:12px;margin-bottom:14px}
.cal-top h3{margin:0;font-size:18px;font-weight:700}
.cal-top .nav{margin-left:auto;display:flex;gap:8px}
.cal-top .nav button{width:34px;height:34px;border:0;border-radius:50%;background:#fff;
  color:var(--ink-2)}
.cal-top .nav button:hover{color:var(--brand)}
.cal-grid{display:grid;grid-template-columns:repeat(7,1fr);gap:6px}
.dow{font-size:12px;color:var(--faint);text-align:center;padding-bottom:2px}
.day{border:0;border-radius:10px;background:#fff;padding:9px 4px;min-height:60px;
  display:flex;flex-direction:column;align-items:center;justify-content:center;gap:2px}
.day .n{font-size:14px;color:var(--dim)}
.day .q{font-size:15px;font-weight:800}
.day.void,.day.unknown{background:transparent;cursor:default}
.day.unknown .q{color:var(--faint);font-weight:400}
.day.none .q{color:var(--faint);font-weight:400}
.day.has{background:#e7f3ec}.day.has .q{color:var(--green)}
.day.low{background:#fdf3e2}.day.low .q{color:var(--amber)}
.day.sel{box-shadow:inset 0 0 0 2px var(--brand)}
.day:not(.void):not(:disabled):hover{filter:brightness(.97)}
.legend{display:flex;flex-wrap:wrap;gap:18px;margin-top:14px;font-size:13px;color:var(--dim)}
.legend i{display:inline-block;width:12px;height:12px;border-radius:4px;margin-right:7px;
  vertical-align:-2px}

/* ---------- подвал ---------- */
.demo-bar{background:#fff8e6;border-bottom:1px solid #ffe3a3;color:#7a5200;
  font-size:14px;padding:11px 0}
.demo-bar b{color:#5c3d00}
.how{background:var(--grey);border-radius:var(--r);padding:22px 24px;font-size:15px;
  color:var(--ink-2)}
.how b{color:var(--ink)}
footer{border-top:1px solid var(--line);margin-top:20px;padding:28px 0 40px;
  color:var(--dim);font-size:14px}
.fcols{display:grid;grid-template-columns:repeat(4,1fr);gap:24px;margin-bottom:24px}
.fcols h4{margin:0 0 10px;font-size:15px;font-weight:700;color:var(--ink)}
.fcols div p{margin:5px 0}

/* ---------- модалка ---------- */
.modal{position:fixed;inset:0;background:rgba(0,0,0,.45);display:none;place-items:center;
  padding:20px;z-index:50}
.modal.open{display:grid}
.modal-box{background:#fff;border-radius:16px;max-width:420px;width:100%;padding:26px}
.modal-box h3{margin:0 0 8px;font-size:21px;font-weight:800}
.modal-box p{margin:0 0 18px;color:var(--dim);font-size:15px}
.modal-box input{width:100%;height:50px;border:0;background:var(--grey);border-radius:10px;
  padding:0 14px;margin-bottom:8px;outline:none}
.modal-actions{display:flex;gap:8px;margin-top:12px}
.modal-actions button{flex:1;height:50px;border:0;border-radius:10px;background:var(--grey);
  font-weight:700}
.modal-actions .primary{background:var(--cta);color:var(--cta-ink)}
.modal-actions .primary:hover{background:var(--cta-dk)}

@media (max-width:900px){
  .hero{padding:26px 0 22px}
  .search{grid-template-columns:1fr 1fr}
  .field{border-right:0;border-bottom:1px solid var(--line)}
  .swap{right:-15px}
  .btn-find{grid-column:1/-1;border-radius:0 0 10px 10px;padding:18px 0}
  .pops{grid-template-columns:1fr}
  .fcols{grid-template-columns:1fr 1fr}
  .flights td{display:block;padding:10px 18px;border:0}
  .flights td:first-child{border-radius:var(--r) var(--r) 0 0;padding-top:16px}
  .flights td:last-child{border-radius:0 0 var(--r) var(--r);text-align:left;
    padding-bottom:16px}
  .flights td:nth-child(2){display:none}
  .flights tr{box-shadow:0 0 0 1px var(--line)}
  .buy,.watch{display:block;width:100%;text-align:center}
}
"""


# ==========================================================================
# Клиентская логика
# ==========================================================================

JS = r"""
const $ = (s,r=document)=>r.querySelector(s);
const $$ = (s,r=document)=>[...r.querySelectorAll(s)];
const state = {from:'',to:'',date:'',adults:1,children:0,infants:0,cat:'dfo',sort:'time',calMonth:''};

const nf = new Intl.NumberFormat('ru-RU');
const MONTHS=['января','февраля','марта','апреля','мая','июня','июля','августа',
              'сентября','октября','ноября','декабря'];
const MONTHS_N=['Январь','Февраль','Март','Апрель','Май','Июнь','Июль','Август',
                'Сентябрь','Октябрь','Ноябрь','Декабрь'];
const DOW=['пн','вт','ср','чт','пт','сб','вс'];

const seatsNeeded = ()=> state.adults + state.children;   // младенцы летят на руках
const cityName = c => (AIRPORTS[c]||{}).city || c;

function fmtDate(iso){
  const d=new Date(iso+'T00:00:00');
  return d.getDate()+' '+MONTHS[d.getMonth()];
}
function plural(n,a,b,c){
  const m=n%100, k=n%10;
  if(m>=11&&m<=14) return c;
  if(k===1) return a;
  if(k>=2&&k<=4) return b;
  return c;
}

/* ---------- инициализация ---------- */
function fillSelect(sel, selected){
  const dfo=[],rest=[];
  Object.entries(AIRPORTS).forEach(([code,a])=>(a.dfo?dfo:rest).push([code,a]));
  const grp=(title,list)=>{
    if(!list.length) return '';
    return `<optgroup label="${title}">`+list
      .sort((a,b)=>a[1].city.localeCompare(b[1].city,'ru'))
      .map(([c,a])=>`<option value="${c}"${c===selected?' selected':''}>${a.city}</option>`)
      .join('')+'</optgroup>';
  };
  sel.innerHTML = grp('Дальний Восток',dfo)+grp('Другие города',rest);
}

function init(){
  const first = ROUTES[0] || {origin:'KHV',destination:'MOW'};
  state.from=first.origin; state.to=first.destination;
  fillSelect($('#from'),state.from);
  fillSelect($('#to'),state.to);

  const dates=[...new Set(DATA.map(f=>f.dt))].sort();
  state.date = dates.find(d=>DATA.some(f=>f.dt===d&&f.q>0)) || dates[0] || META.today;
  $('#date').value=state.date;
  if(dates.length){ $('#date').min=dates[0]; $('#date').max=dates[dates.length-1]; }
  state.calMonth=state.date.slice(0,7);

  $('#from').onchange=e=>{state.from=e.target.value;search()};
  $('#to').onchange=e=>{state.to=e.target.value;search()};
  $('#date').onchange=e=>{state.date=e.target.value;state.calMonth=state.date.slice(0,7);search()};
  $('#swap').onclick=()=>{[state.from,state.to]=[state.to,state.from];
    $('#from').value=state.from;$('#to').value=state.to;search()};
  $('#find').onclick=search;
  $('#sort').onchange=e=>{state.sort=e.target.value;search()};

  $('#paxBtn').onclick=e=>{e.stopPropagation();$('#paxPop').classList.toggle('open')};
  document.addEventListener('click',()=>$('#paxPop').classList.remove('open'));
  $('#paxPop').onclick=e=>e.stopPropagation();
  $$('.step').forEach(b=>b.onclick=()=>{
    state[b.dataset.k]+=(+b.dataset.d);
    renderPax(); search();          // корректность состава чинит clampPax()
  });

  $$('.cat').forEach(b=>b.onclick=()=>{
    $$('.cat').forEach(x=>x.classList.remove('on'));
    b.classList.add('on'); state.cat=b.dataset.id; renderNote();
  });

  $('#watchClose').onclick=()=>$('#watch').classList.remove('open');
  $('#watchSave').onclick=()=>{
    const v=$('#watchInput').value.trim();
    $('#watchMsg').textContent = v
      ? 'Прототип: подписка не отправляется. На проде здесь вызов /api/subscribe.'
      : 'Укажите e-mail или Telegram.';
  };

  renderPax(); renderNote(); renderPopular(); search();
}

/* ---------- поиск ---------- */
function matches(f){ return f.o===state.from && f.d===state.to; }

function search(){
  clampPax();
  const need=seatsNeeded();
  const onDate=DATA.filter(f=>matches(f)&&f.dt===state.date);
  const fit=onDate.filter(f=>f.q>=need);
  renderBoard(fit,onDate,need);
  renderCalendar();
  $('#fromCode').textContent=state.from;
  $('#toCode').textContent=state.to;
  $('#routeTitle').textContent=cityName(state.from)+' → '+cityName(state.to);
  $('#routeSub').textContent=fmtDate(state.date)+', '+need+' '+plural(need,'место','места','мест');
}

function sortFlights(list){
  const s=state.sort;
  return [...list].sort((a,b)=>
    s==='seats' ? b.q-a.q :
    s==='price' ? (a.p||1e9)-(b.p||1e9) :
    (a.tm||'').localeCompare(b.tm||''));
}

function renderBoard(fit,onDate,need){
  const box=$('#board');
  if(!DATA.some(matches)){
    box.innerHTML=`<div class="empty"><div class="big">Это направление мы пока не отслеживаем</div>
      <div class="sm">Сейчас в мониторинге: ${ROUTES.map(r=>cityName(r.origin)+' → '+cityName(r.destination)).join(', ')}.
      Напишите, какое направление добавить — поставим на отслеживание.</div></div>`;
    return;
  }
  if(!fit.length){
    const alt=nearestDates(need);
    const why = onDate.length
      ? `На ${fmtDate(state.date)} места есть, но меньше ${need} — на всех не хватит.`
      : `На ${fmtDate(state.date)} субсидированных мест нет.`;
    box.innerHTML=`<div class="empty">
      <div class="big">Мест на эту дату нет</div>
      <div class="sm">${why}${alt.length?' Зато они есть на соседних датах:':''}</div>
      ${alt.length?`<div class="jump">${alt.map(a=>
        `<button onclick="goDate('${a.dt}')"><b>${fmtDate(a.dt)}</b>
         <span>${a.q} ${plural(a.q,'место','места','мест')}</span></button>`).join('')}</div>`
       :'<div class="sm">В отслеживаемом окне свободных мест сейчас нет.</div>'}
      <button class="watch" onclick="openWatch()">Сообщить, когда появятся места</button>
      </div>`;
    return;
  }
  box.innerHTML=`<table class="flights"><thead><tr>
      <th>Вылет</th><th>Рейс</th><th>Мест</th><th>Цена</th><th></th>
    </tr></thead><tbody>${sortFlights(fit).map(rowHtml).join('')}</tbody></table>`;
}

function rowHtml(f){
  const cls=f.q===0?'no':(f.q<=LOW?'low':'ok');
  const btn = META.demo
    ? `<span class="buy off">демо</span>`
    : (f.url ? `<a class="buy" href="${f.url}" target="_blank" rel="noopener nofollow">Выбрать</a>`
             : `<button class="watch" onclick="openWatch()">Следить</button>`);
  return `<tr>
    <td><div class="time">${f.tm||'—'}${f.ar?`<small>прилёт ${f.ar}</small>`:''}</div></td>
    <td class="fl"><b>${f.fn||'—'}</b><span>тариф ${f.fc||'—'}</span></td>
    <td><div class="seats ${cls}">${f.q}<small>${f.q?'мест свободно':'нет мест'}</small></div></td>
    <td><div class="price">${f.p?nf.format(f.p)+' ₽':'—'}<small>субсидированный</small></div></td>
    <td>${btn}</td></tr>`;
}

function nearestDates(need){
  const by={};
  DATA.filter(f=>matches(f)&&f.q>=need).forEach(f=>by[f.dt]=(by[f.dt]||0)+f.q);
  return Object.entries(by).map(([dt,q])=>({dt,q}))
    .sort((a,b)=>Math.abs(new Date(a.dt)-new Date(state.date))
                -Math.abs(new Date(b.dt)-new Date(state.date))).slice(0,4);
}

function goDate(dt){ state.date=dt; state.calMonth=dt.slice(0,7); $('#date').value=dt; search(); }

function goRoute(o,d){
  state.from=o; state.to=d;
  $('#from').value=o; $('#to').value=d;
  const withSeats=DATA.filter(f=>f.o===o&&f.d===d&&f.q>=seatsNeeded()).map(f=>f.dt).sort();
  if(withSeats.length){ goDate(withSeats[0]); } else { search(); }
  $('#results').scrollIntoView?.({behavior:'smooth',block:'start'});
}

/* ---------- направления ---------- */
function renderPopular(){
  const agg={};
  DATA.forEach(f=>{
    const k=f.o+'|'+f.d;
    if(!agg[k]) agg[k]={o:f.o,d:f.d,seats:0,min:Infinity,days:new Set()};
    agg[k].seats+=f.q;
    if(f.q>0){ agg[k].days.add(f.dt); if(f.p>0) agg[k].min=Math.min(agg[k].min,f.p); }
  });
  const list=Object.values(agg).sort((a,b)=>b.seats-a.seats).slice(0,9);
  $('#pops').innerHTML=list.map(r=>{
    const cls=r.seats===0?'no':(r.days.size<=2?'low':'ok');
    const txt=r.seats===0?'мест сейчас нет'
      :`${r.days.size} ${plural(r.days.size,'дата','даты','дат')} с местами`;
    return `<button class="pop" onclick="goRoute('${r.o}','${r.d}')">
      <div class="txt"><b>${cityName(r.o)} — ${cityName(r.d)}</b>
      <span class="p"> ${r.min<Infinity?'от '+nf.format(r.min)+' ₽':'цена уточняется'}</span>
      <div class="q ${cls}">${txt}</div></div><span class="ch">›</span></button>`;
  }).join('');
}

/* ---------- календарь ---------- */
function renderCalendar(){
  const need=seatsNeeded(), by={};
  DATA.filter(matches).forEach(f=>{by[f.dt]=(by[f.dt]||0)+(f.q>=need?f.q:0)});
  const [y,m]=state.calMonth.split('-').map(Number);
  $('#calTitle').textContent=MONTHS_N[m-1]+' '+y;

  const first=new Date(y,m-1,1), start=(first.getDay()+6)%7;
  const days=new Date(y,m,0).getDate();
  let cells=DOW.map(d=>`<div class="dow">${d}</div>`).join('');
  for(let i=0;i<start;i++) cells+='<div class="day void"></div>';
  for(let d=1;d<=days;d++){
    const iso=`${y}-${String(m).padStart(2,'0')}-${String(d).padStart(2,'0')}`;
    const q=by[iso];
    const known=Object.prototype.hasOwnProperty.call(by,iso);
    const cls=!known?'unknown':(q===0?'none':(q<=LOW?'low':'has'));
    const sel=iso===state.date?' sel':'';
    cells+=`<button class="day ${cls}${sel}" ${known?`onclick="goDate('${iso}')"`:'disabled'}>
      <span class="n">${d}</span>
      <span class="q">${known?(q?q:'—'):'·'}</span></button>`;
  }
  $('#calGrid').innerHTML=cells;
  $('#calPrev').onclick=()=>shiftMonth(-1);
  $('#calNext').onclick=()=>shiftMonth(1);
}
function shiftMonth(k){
  let [y,m]=state.calMonth.split('-').map(Number);
  m+=k; if(m<1){m=12;y--} if(m>12){m=1;y++}
  state.calMonth=`${y}-${String(m).padStart(2,'0')}`; renderCalendar();
}

/* ---------- пассажиры и памятка ---------- */
/* Состав пассажиров держим корректным в самом состоянии, а не в обработчике
   кнопок: иначе любой другой источник (ссылка с параметрами, восстановление
   сессии) сможет протащить 9 младенцев на одного взрослого. */
function clampPax(){
  state.adults  = Math.max(1, Math.min(9, state.adults|0));
  state.children= Math.max(0, Math.min(8, state.children|0));
  state.infants = Math.max(0, Math.min(state.adults, state.infants|0));
}

function renderPax(){
  clampPax();
  ['adults','children','infants'].forEach(k=>{ $('#v-'+k).textContent=state[k] });
  const total=state.adults+state.children+state.infants;
  $('#paxLabel').textContent=total+' '+plural(total,'пассажир','пассажира','пассажиров');
  $$('.step').forEach(b=>{
    const k=b.dataset.k,d=+b.dataset.d;
    b.disabled = d<0 ? (k==='adults'?state[k]<=1:state[k]<=0)
                     : (k==='adults'?state[k]>=9:state[k]>=(k==='infants'?state.adults:8));
  });
}
function renderNote(){
  const c=CATEGORIES.find(x=>x.id===state.cat);
  if(!c) return;
  $('#note').innerHTML=`<b>${c.title}.</b> Что попросят показать при посадке:
    <ul>${c.requirements.map(r=>`<li>${r}</li>`).join('')}</ul>
    <div style="margin-top:9px">Наличие мест одинаково для всех льготных категорий:
    субсидированная квота на рейсе общая.</div>`;
}
function openWatch(){ $('#watch').classList.add('open'); $('#watchMsg').textContent=''; }

document.addEventListener('DOMContentLoaded',init);

/* Экспорт в window для автотеста uitest.js: объявления const/let в классическом
   скрипте живут в скрипт-скоупе и снаружи не видны. На работу страницы не влияет. */
Object.assign(window,{DATA,ROUTES,META,AIRPORTS,CATEGORIES,LOW,state,
  search,renderPax,renderCalendar,renderPopular,goDate,goRoute,nearestDates,
  openWatch,clampPax});
"""


# ==========================================================================
# Сборка страницы
# ==========================================================================

def _rows_to_data(rows) -> list[dict]:
    out = []
    for r in rows:
        out.append({
            "o": r["origin"] or "",
            "d": r["destination"] or "",
            "dt": r["depart_date"] or "",
            "tm": r["depart_time"] or "",
            "ar": r["arrive_time"] or "",
            "fn": r["flight_number"] or "",
            "fc": r["fare_code"] or "",
            "q": int(r["avail_qty"] or 0),
            "p": float(r["price"] or 0),
            "url": r["book_url"] or "",
        })
    return out


def _json(obj) -> str:
    # </script> внутри данных сломал бы страницу — экранируем слэш.
    return json.dumps(obj, ensure_ascii=False).replace("</", "<\\/")


def build(out_path: Path | str | None = None, db: Database | None = None) -> Path:
    db = db or Database()
    rows = db.current_state()
    runs = db.last_runs(limit=1)
    last_run = runs[0] if runs else None
    is_demo = bool(last_run and last_run["dry_run"])

    data = _rows_to_data(rows)
    routes = [{"origin": o, "destination": d}
              for o, d in sorted({(x["o"], x["d"]) for x in data})]

    meta = {
        "generated": datetime.now(timezone.utc).isoformat(timespec="minutes"),
        "today": datetime.now(timezone.utc).date().isoformat(),
        "demo": is_demo,
        "partner": config.PARTNER_ID,
    }

    # В демо-режиме страница закрыта от индексации: публичная выдача с
    # вымышленными рейсами и ценами под именем ИП — прямой репутационный риск.
    robots = ('<meta name="robots" content="noindex,nofollow">\n'
              if is_demo else "")

    head = f"""<!DOCTYPE html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Субсидированные авиабилеты с Дальнего Востока — есть ли места</title>
<meta name="description" content="Показываем, есть ли свободные места по субсидированным
тарифам на рейсах с Дальнего Востока. Обновляется автоматически каждые 15 минут.">
{robots}<style>{CSS}</style></head><body>"""

    demo_bar = ("<div class='demo-bar'><div class='wrap'><b>Демо-режим.</b> "
                "Данные из тестовой фикстуры, а не от партнёрского API: рейсы, места "
                "и цены вымышлены, переход к покупке отключён.</div></div>"
                ) if is_demo else ""

    cats_html = "".join(
        f"<button class='cat{' on' if c['id'] == 'dfo' else ''}' data-id='{c['id']}'>"
        f"{c['short']}</button>"
        for c in cities.CATEGORIES
    )

    pax_rows = "".join(
        f"""<div class="pax-row"><div class="t"><b>{title}</b><span>{hint}</span></div>
        <button class="step" data-k="{key}" data-d="-1">−</button>
        <span class="step-val" id="v-{key}">0</span>
        <button class="step" data-k="{key}" data-d="1">+</button></div>"""
        for key, title, hint in (
            ("adults", "Взрослые", "от 12 лет"),
            ("children", "Дети", "2–11 лет, отдельное место"),
            ("infants", "Младенцы", "до 2 лет, на руках"),
        )
    )

    body = f"""
{demo_bar}
<div class="blue">
  <div class="wrap">
    <div class="topbar">
      <div class="logo"><span class="mark">✈</span>субсидия</div>
      <nav class="topnav">
        <a href="#results">Рейсы</a><a href="#cal">Календарь мест</a>
        <a href="#how">Как это работает</a>
      </nav>
    </div>

    <div class="hero">
      <h1>Тут видно, есть ли места по субсидии</h1>
      <div class="cats">{cats_html}</div>
    </div>

    <div class="search">
      <div class="field">
        <span class="lab">Откуда</span>
        <select class="control" id="from"></select>
        <span class="code" id="fromCode"></span>
        <button class="swap" id="swap" title="Поменять местами">⇄</button>
      </div>
      <div class="field">
        <span class="lab">Куда</span>
        <select class="control" id="to"></select>
        <span class="code" id="toCode"></span>
      </div>
      <div class="field">
        <span class="lab">Когда</span>
        <input class="control" type="date" id="date">
      </div>
      <div class="field">
        <span class="lab">Пассажиры</span>
        <button class="control pax-btn" id="paxBtn"><span id="paxLabel">1 пассажир</span></button>
        <span class="pax-sub">субсидированный тариф</span>
        <div class="pax-pop" id="paxPop">{pax_rows}
          <div class="pax-hint">Младенцу на руках отдельное место не нужно —
            в поиске мест он не учитывается.</div>
        </div>
      </div>
      <button class="btn-find" id="find">Найти места</button>
    </div>

    <div class="underbar">
      <span>Квота тает за часы — мы проверяем наличие каждые 15 минут</span>
      <span class="right">обновлено {meta['generated'].replace('T', ' ')} UTC</span>
    </div>
  </div>
</div>

<div class="wrap">
  <div class="strip">
    <span class="ic">◎</span>
    <span>Показываем живое наличие мест, а не тариф, которого уже нет</span>
    <a href="#how">Узнать больше</a>
  </div>
</div>

<div class="wrap page">
  <div class="sec" id="results">
    <div class="sec-h">
      <h2 id="routeTitle">—</h2><div class="sub" id="routeSub"></div>
      <div class="right"><select class="sortsel" id="sort">
        <option value="time">по времени вылета</option>
        <option value="seats">где больше мест</option>
        <option value="price">сначала дешевле</option>
      </select></div>
    </div>
    <div id="board"></div>
  </div>

  <div class="sec">
    <div class="sec-h"><h2>Кому положена субсидия</h2>
      <div class="sub">выберите категорию в синем блоке выше</div></div>
    <div class="note" id="note"></div>
  </div>

  <div class="sec">
    <div class="sec-h"><h2>Направления под мониторингом</h2>
      <div class="sub">нажмите, чтобы открыть ближайшую дату с местами</div></div>
    <div class="pops" id="pops"></div>
  </div>

  <div class="sec" id="cal">
    <div class="sec-h"><h2>Календарь мест</h2>
      <div class="sub">сколько свободно по дням на выбранном направлении</div></div>
    <div class="cal">
      <div class="cal-top">
        <h3 id="calTitle">—</h3>
        <div class="nav"><button id="calPrev">‹</button><button id="calNext">›</button></div>
      </div>
      <div class="cal-grid" id="calGrid"></div>
      <div class="legend">
        <span><i style="background:#e7f3ec"></i>места есть</span>
        <span><i style="background:#fdf3e2"></i>мест мало</span>
        <span><i style="background:#fff;box-shadow:inset 0 0 0 1px #e5e8f0"></i>мест нет</span>
        <span><i style="background:#f5f7fb"></i>вне мониторинга</span>
      </div>
    </div>
  </div>

  <div class="sec how" id="how">
    <b>Как это работает.</b> Мы не продаём билеты и не берём с вас денег.
    Каждые 15 минут опрашиваем систему бронирования, храним историю наличия мест
    и показываем её здесь. Покупка — на сайте авиакассы-партнёра по нашей ссылке,
    цена для вас ровно та же.
  </div>
</div>

<div class="modal" id="watch"><div class="modal-box">
  <h3>Сообщить, когда появятся места</h3>
  <p>Пришлём уведомление, как только субсидированные места вернутся в продажу
     на выбранном направлении.</p>
  <input id="watchInput" placeholder="E-mail или @telegram">
  <div id="watchMsg" style="font-size:13px;color:#e03131;min-height:19px"></div>
  <div class="modal-actions">
    <button id="watchClose">Отмена</button>
    <button class="primary" id="watchSave">Подписаться</button>
  </div>
</div></div>

<footer><div class="wrap">
  <div class="fcols">
    <div><h4>Субсидии</h4><p>Жителям ДФО</p><p>Молодёжи до 23 лет</p>
      <p>Пенсионерам</p><p>Многодетным семьям</p></div>
    <div><h4>Направления</h4><p>Хабаровск — Москва</p><p>Хабаровск — Петербург</p>
      <p>Владивосток — Москва</p></div>
    <div><h4>Помощь</h4><p>Как купить по субсидии</p><p>Какие нужны документы</p>
      <p>Что если мест нет</p></div>
    <div><h4>О сервисе</h4><p>Партнёр: БилетДВ</p><p>ИП Харханов К.</p>
      <p>harhanovk@gmail.com</p></div>
  </div>
  Сервис информационный: наличие мест показываем мы, билеты продаёт партнёр.
</div></footer>

<script>
const DATA={_json(data)};
const ROUTES={_json(routes)};
const META={_json(meta)};
const AIRPORTS={_json(cities.as_json_dict())};
const CATEGORIES={_json(cities.CATEGORIES)};
const LOW={config.ALERTS.low_seats_threshold};
{JS}
</script></body></html>"""

    out = Path(out_path or (config.DATA_DIR / "index.html"))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(head + body, encoding="utf-8")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Сборка витрины поиска")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    path = build(args.out)
    print(f"витрина готова: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
