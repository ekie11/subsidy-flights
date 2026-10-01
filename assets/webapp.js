const $ = (s,r=document)=>r.querySelector(s);
const $$ = (s,r=document)=>[...r.querySelectorAll(s)];
const state = {from:'',to:'',date:'',adults:1,children:0,infants:0,cats:['dfo'],sort:'time',calMonth:''};

const nf = new Intl.NumberFormat('ru-RU');
const MONTHS=['января','февраля','марта','апреля','мая','июня','июля','августа',
              'сентября','октября','ноября','декабря'];
const MONTHS_N=['Январь','Февраль','Март','Апрель','Май','Июнь','Июль','Август',
                'Сентябрь','Октябрь','Ноябрь','Декабрь'];
const DOW=['пн','вт','ср','чт','пт','сб','вс'];

const seatsNeeded = ()=> state.adults + state.children;   // младенцы летят на руках
const cityName = c => (AIRPORTS[c]||{}).city || c;
const airlineName = c => (typeof AIRLINES!=='undefined' && AIRLINES[c]) || c;

/* Всё, что пришло от партнёра (номер рейса, ссылка), экранируем перед
   вставкой в разметку: кавычка в BookURL иначе ломала бы атрибут href. */
const esc = v => String(v??'').replace(/[&<>"']/g,
  ch=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
const ICON_NEXT = '<svg class="ico" viewBox="0 0 24 24" aria-hidden="true" focusable="false">'
  +'<path d="m9 18 6-6-6-6"/></svg>';

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

  // Дату по умолчанию — ту, где места есть по выбранной льготе: у каждой
  // льготы свои места, и «есть по любой» открывал бы пустой экран.
  const dates=[...new Set(DATA.map(f=>f.dt))].sort();
  state.date = dates.find(d=>DATA.some(f=>f.dt===d&&matches(f)&&f.q>0)) || dates[0] || META.today;
  $('#date').value=state.date;
  // С поиском по запросу дата не ограничена окном сборщика: любой день
  // вперёд партнёр отдаст сам.
  if(META.searchApi){ $('#date').min=META.today; }
  else if(dates.length){ $('#date').min=dates[0]; $('#date').max=dates[dates.length-1]; }
  state.calMonth=state.date.slice(0,7);

  $('#from').onchange=e=>{state.from=e.target.value;search()};
  $('#to').onchange=e=>{state.to=e.target.value;search()};
  $('#date').onchange=e=>{state.date=e.target.value;state.calMonth=state.date.slice(0,7);search()};
  $('#swap').onclick=()=>{[state.from,state.to]=[state.to,state.from];
    $('#from').value=state.from;$('#to').value=state.to;search()};
  $('#find').onclick=search;
  $('#sort').onchange=e=>{state.sort=e.target.value;search()};

  $('#paxBtn').onclick=e=>{e.stopPropagation();setPax(!$('#paxPop').classList.contains('open'))};
  document.addEventListener('click',()=>setPax(false));
  $('#paxPop').onclick=e=>e.stopPropagation();
  document.addEventListener('keydown',e=>{
    if(e.key!=='Escape') return;
    if($('#watch').classList.contains('open')){ closeWatch(); return; }
    if($('#paxPop').classList.contains('open')){ setPax(false); $('#paxBtn').focus(); }
  });
  $$('.step').forEach(b=>b.onclick=()=>{
    state[b.dataset.k]+=(+b.dataset.d);
    renderPax(); search();          // корректность состава чинит clampPax()
  });

  $$('.cat').forEach(b=>b.onclick=()=>toggleCat(b.dataset.id));

  $('#watchClose').onclick=closeWatch;
  $('#watchSave').onclick=submitWatch;
  $('#watchInput').onkeydown=e=>{ if(e.key==='Enter') submitWatch(); };
  $('#watch').onclick=e=>{ if(e.target===e.currentTarget) closeWatch(); };
  $('#watch').addEventListener('keydown',trapFocus);

  $$('footer a[data-o]').forEach(a=>a.onclick=e=>{
    e.preventDefault(); goRoute(a.dataset.o,a.dataset.d);
  });

  // Время обновления — в часовом поясе читателя, а не в UTC.
  const upd=$('#updated'), when=new Date(META.generated);
  if(upd && !isNaN(when)){
    upd.textContent=when.toLocaleString('ru-RU',{day:'numeric',month:'long',
      hour:'2-digit',minute:'2-digit'});
  }

  renderPax(); renderCats(); renderNote(); renderPopular(); search();
}

/* ---------- льготы ---------- */
/* Льгот можно выбрать несколько — у человека их бывает больше одной
   (житель ДФО до 23 лет, многодетная пенсионерка). Рейс показываем, если
   он подходит хотя бы под одну из выбранных. Пока у категорий нет своих
   кодов тарифа (cities.CATEGORIES → fare_codes пустые), подходит любой
   субсидированный рейс: квота общая. Последнюю льготу снять нельзя —
   иначе непонятно, что искать. */
function selectedCats(){ return CATEGORIES.filter(c=>state.cats.includes(c.id)); }
function catFits(c,f){
  if(c.psg && f.pc && c.psg!==f.pc) return false;   // у каждой категории свои места
  return !(c.fare_codes||[]).length || c.fare_codes.includes(f.fc);
}
function catsFor(f){ return selectedCats().filter(c=>catFits(c,f)); }
function matchCategory(f){ return catsFor(f).length>0; }

function toggleCat(id){
  const on=state.cats.includes(id);
  if(on && state.cats.length===1) return;
  state.cats = on ? state.cats.filter(x=>x!==id)
                  : CATEGORIES.map(c=>c.id).filter(x=>x===id||state.cats.includes(x));
  renderCats(); renderNote(); renderPopular(); search();
}
function renderCats(){
  $$('.cat').forEach(b=>{
    const on=state.cats.includes(b.dataset.id);
    b.classList.toggle('on',on); b.setAttribute('aria-pressed',String(on));
  });
}

/* ---------- поиск ---------- */
function onRoute(f){ return f.o===state.from && f.d===state.to; }
function matches(f){ return onRoute(f) && matchCategory(f); }

/* ---------- поиск по запросу ---------- */
/* Маршрутов и дат, которых нет в DATA (их не опрашивает сборщик), спрашиваем
   у воркера (worker/index.js) и докладываем ответ в тот же DATA. Состояние
   держим по ключу маршрут+дата: 'loading' | 'done' | 'error'. В демо-режиме
   не ходим — там всё равно фикстура и покупка выключена. */
const live={};
const liveKey=()=>state.from+'-'+state.to+'-'+state.date;
function needLive(){
  return !!META.searchApi && !META.demo && !live[liveKey()]
    && state.date>=META.today && !DATA.some(f=>onRoute(f)&&f.dt===state.date);
}
async function liveSearch(){
  const key=liveKey(), {from,to,date}=state;
  live[key]='loading';
  try{
    const u=new URL(META.searchApi);
    u.search=new URLSearchParams({from,to,date}).toString();
    const res=await fetch(u);
    const body=await res.json().catch(()=>({}));
    if(!res.ok || !body.ok) throw new Error(body.error||res.status);
    (body.flights||[]).forEach(f=>{
      // lv — найдено по запросу: в «Направления под мониторингом» не
      // попадает, это разовая проверка, а не отслеживание.
      if(f.o===from && f.d===to) DATA.push({...f,lv:1});
    });
    live[key]='done';
  } catch(e){
    live[key]='error';
  }
  if(liveKey()===key) search();
}

function search(){
  clampPax();
  if(needLive()) liveSearch();
  const need=seatsNeeded();
  const onDate=DATA.filter(f=>matches(f)&&f.dt===state.date);
  const fit=onDate.filter(f=>f.q>=need);
  renderBoard(fit,onDate,need);
  renderCalendar();
  $('#fromCode').textContent=state.from;
  $('#toCode').textContent=state.to;
  $('#routeTitle').textContent=cityName(state.from)+' → '+cityName(state.to);
  $('#routeSub').textContent=fmtDate(state.date)+', нужно '+need+' '+plural(need,'место','места','мест');
  $('#status').textContent = fit.length
    ? `${fmtDate(state.date)}: ${fit.length} ${plural(fit.length,'рейс','рейса','рейсов')} с местами`
    : `${fmtDate(state.date)}: мест нет`;
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
  const lv=live[liveKey()];
  if(lv==='loading'){
    box.innerHTML=`<div class="empty"><div class="big">Ищем места…</div>
      <div class="sm">Спрашиваем систему бронирования, это несколько секунд.</div></div>`;
    return;
  }
  if(lv==='error' && !onDate.length){
    box.innerHTML=`<div class="empty"><div class="big">Не получилось проверить места</div>
      <div class="sm">Система бронирования сейчас не ответила. Попробуйте через минуту.</div>
      <button class="watch" onclick="retryLive()">Проверить ещё раз</button></div>`;
    return;
  }
  if(!DATA.some(onRoute) && lv!=='done'){
    box.innerHTML=`<div class="empty"><div class="big">Это направление мы пока не отслеживаем</div>
      <div class="sm">Сейчас в мониторинге: ${esc(ROUTES.map(r=>cityName(r.origin)+' → '+cityName(r.destination)).join(', '))}.
      Напишите, какое направление добавить, и мы поставим его на отслеживание.</div>
      <a class="watch" href="mailto:harhanovk@gmail.com?subject=${encodeURIComponent('Добавить направление '+cityName(state.from)+' → '+cityName(state.to))}">Предложить направление</a></div>`;
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
  /* Под какие из выбранных льгот подходит рейс — пишем, только когда это
     не «все выбранные»: иначе строка повторяла бы шапку на каждом рейсе. */
  const fit=catsFor(f);
  const catNote = fit.length<state.cats.length
    ? `<small class="fit">по льготе: ${esc(fit.map(c=>c.short).join(', '))}</small>` : '';
  const btn = META.demo
    ? `<span class="buy off">демо</span>`
    : (f.url ? `<a class="buy" href="${esc(f.url)}" target="_blank" rel="noopener nofollow">Выбрать</a>`
             : `<button class="watch" onclick="openWatch()">Следить</button>`);
  return `<tr>
    <td><div class="time">${esc(f.tm||'—')}${f.ar?`<small>прилёт ${esc(f.ar)}</small>`:''}</div></td>
    <td class="fl"><b>${esc(f.fn||'—')}</b><span>${esc(airlineName(f.al)||'')}</span></td>
    <td><div class="seats ${cls}">${f.q}<small>${f.q?'мест свободно':'нет мест'}</small></div></td>
    <td><div class="price">${f.p?nf.format(f.p)+' ₽':'—'}<small>субсидированный</small>${catNote}</div></td>
    <td>${btn}</td></tr>`;
}

function nearestDates(need){
  const by={};
  DATA.filter(f=>matches(f)&&f.q>=need).forEach(f=>by[f.dt]=(by[f.dt]||0)+f.q);
  return Object.entries(by).map(([dt,q])=>({dt,q}))
    .sort((a,b)=>Math.abs(new Date(a.dt)-new Date(state.date))
                -Math.abs(new Date(b.dt)-new Date(state.date))).slice(0,4);
}

function retryLive(){ delete live[liveKey()]; search(); }

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
    if(f.lv) return;
    const k=f.o+'|'+f.d;
    if(!agg[k]) agg[k]={o:f.o,d:f.d,seats:0,min:Infinity,days:new Set()};
    if(!matchCategory(f)) return;      // направление остаётся в списке, но без чужих мест
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
      <div class="q ${cls}">${txt}</div></div><span class="ch">${ICON_NEXT}</span></button>`;
  }).join('');
}

/* ---------- календарь ---------- */
function renderCalendar(){
  const need=seatsNeeded(), by={};
  DATA.filter(matches).forEach(f=>{
    if(!by[f.dt]) by[f.dt]={q:0,p:Infinity};
    by[f.dt].q+=(f.q>=need?f.q:0);
    if(f.q>=need && f.p>0) by[f.dt].p=Math.min(by[f.dt].p,f.p);
  });
  const [y,m]=state.calMonth.split('-').map(Number);
  $('#calTitle').textContent=MONTHS_N[m-1]+' '+y;

  const first=new Date(y,m-1,1), start=(first.getDay()+6)%7;
  const days=new Date(y,m,0).getDate();
  let cells=DOW.map(d=>`<div class="dow">${d}</div>`).join('');
  for(let i=0;i<start;i++) cells+='<div class="day void"></div>';
  for(let d=1;d<=days;d++){
    const iso=`${y}-${String(m).padStart(2,'0')}-${String(d).padStart(2,'0')}`;
    const cell=by[iso];
    const known=Object.prototype.hasOwnProperty.call(by,iso);
    const q=cell?cell.q:0;
    const cls=!known?'unknown':(q===0?'none':(q<=LOW?'low':'has'));
    const sel=iso===state.date?' sel':'';
    const priceHtml = (q && cell.p<Infinity)
      ? `<span class="p">${nf.format(cell.p)} ₽</span>` : '';
    const label=`${d} ${MONTHS[m-1]}: `+(!known?'вне мониторинга'
      :(q?`${q} ${plural(q,'место','места','мест')}`+(cell.p<Infinity?`, от ${nf.format(cell.p)} ₽`:''):'мест нет'));
    cells+=`<button class="day ${cls}${sel}" aria-label="${label}"${sel?' aria-current="date"':''} ${known?`onclick="goDate('${iso}')"`:'disabled'}>
      <span class="n">${d}</span>
      <span class="q">${known?(q?q:'—'):'·'}</span>${priceHtml}</button>`;
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
  const cs=selectedCats();
  if(!cs.length) return;
  const hint=$('#catHint');
  if(hint) hint.textContent = cs.length===1
    ? `Что взять с собой по льготе «${cs[0].short}»`
    : `Что взять с собой по льготам: ${cs.map(c=>`«${c.short}»`).join(', ')}`;
  const blocks=cs.map(c=>`<b>${esc(c.title)}.</b> Что попросят показать при посадке:
    <ul>${c.requirements.map(r=>`<li>${esc(r)}</li>`).join('')}</ul>`).join('');
  const tail = cs.length>1
    ? `Выбрано несколько льгот — показываем рейсы, подходящие хотя бы под одну.
       Оформить билет можно по любой из них: документы нужны только для той, по которой покупаете.`
    : `Наличие мест одинаково для всех льготных категорий: субсидированная квота на рейсе общая.
       Если подходите под несколько льгот, выберите их все.`;
  $('#note').innerHTML=blocks+`<div style="margin-top:9px">${tail}</div>`;
}
function setPax(open){
  $('#paxPop').classList.toggle('open',open);
  $('#paxBtn').setAttribute('aria-expanded',String(open));
}

/* Окно подписки ведёт себя как диалог: фокус заходит внутрь, Tab не уходит
   на страницу под затемнением, Esc и клик по фону закрывают, фокус
   возвращается на кнопку, которая окно открыла. */
let watchOpener=null;
function openWatch(){
  watchOpener=document.activeElement;
  $('#watch').classList.add('open'); $('#watchMsg').textContent='';
  $('#watchInput').focus();
}
function closeWatch(){
  $('#watch').classList.remove('open');
  if(watchOpener && watchOpener.focus) watchOpener.focus();
}
function trapFocus(e){
  if(e.key!=='Tab') return;
  const f=$$('#watch input, #watch button:not(:disabled)');
  const first=f[0], last=f[f.length-1];
  if(e.shiftKey && document.activeElement===first){ e.preventDefault(); last.focus(); }
  else if(!e.shiftKey && document.activeElement===last){ e.preventDefault(); first.focus(); }
}

/* Бэкенд подписки (subscribe_api.py) — отдельный процесс, который есть
   не на каждом деплое: на GitHub Pages (статика) его нет и не будет,
   на VPS он включается явно через SUBSIDY_SUBSCRIBE_ENABLED (см. README
   → «Деплой на VPS»). META.subscribeApi отражает именно это, а не
   демо/боевой режим сбора — кнопка не должна врать, что подписка работает,
   там, где отправлять её физически некуда. */
async function submitWatch(){
  const input=$('#watchInput'), msg=$('#watchMsg'), btn=$('#watchSave');
  const contact=input.value.trim();
  if(!contact){ msg.textContent='Укажите e-mail или @telegram.'; return; }

  if(META.demo){
    msg.textContent='Прототип: подписка не отправляется. На проде здесь вызов /api/subscribe.';
    return;
  }
  if(!META.subscribeApi){
    msg.textContent='Подписка пока не подключена на этом сайте.';
    return;
  }

  btn.disabled=true; msg.textContent='Отправляем…';
  try{
    const res=await fetch('/api/subscribe',{
      method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({contact, origin:state.from, destination:state.to}),
    });
    const body=await res.json().catch(()=>({}));
    if(res.ok && body.ok){
      msg.textContent='Готово — напишем, как только места появятся.';
      input.value='';
    } else {
      msg.textContent=body.error || 'Не получилось отправить, попробуйте позже.';
    }
  } catch{
    msg.textContent='Не получилось отправить, попробуйте позже.';
  } finally {
    btn.disabled=false;
  }
}

document.addEventListener('DOMContentLoaded',init);

/* Экспорт в window для автотеста uitest.js: объявления const/let в классическом
   скрипте живут в скрипт-скоупе и снаружи не видны. На работу страницы не влияет. */
Object.assign(window,{DATA,ROUTES,META,AIRPORTS,CATEGORIES,LOW,state,
  search,renderPax,live,liveSearch,toggleCat,matchCategory,renderCalendar,renderPopular,goDate,goRoute,nearestDates,
  openWatch,closeWatch,submitWatch,clampPax});
