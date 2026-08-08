/**
 * UI-тест витрины: поднимает страницу в jsdom и проверяет, что интерфейс
 * реально работает — селекты заполнены, поиск фильтрует, календарь считает
 * места, пустое состояние предлагает ближайшие даты.
 *
 *   npm install jsdom
 *   node uitest.js path/to/index.html
 *
 * Без аргумента берёт data/index.html.
 */
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');

const file = process.argv[2] || path.join(__dirname, 'data', 'index.html');
let passed = 0, failed = 0;

function check(label, cond, detail = '') {
  if (cond) { passed++; console.log('  ok   ' + label); }
  else { failed++; console.log('  FAIL ' + label + (detail ? ' — ' + detail : '')); }
}

const dom = new JSDOM(fs.readFileSync(file, 'utf8'), {
  runScripts: 'dangerously',
  pretendToBeVisual: true,
});
const { window } = dom;
const errors = [];
window.addEventListener('error', e => errors.push(e.message));

window.addEventListener('load', () => {
  const doc = window.document;
  const $ = s => doc.querySelector(s);
  const $$ = s => [...doc.querySelectorAll(s)];
  const st = window.state;
  const DATA = window.DATA;

  console.log('\n1. Загрузка');
  check('ошибок JS нет', errors.length === 0, errors.join('; '));
  check('данные встроены в страницу', Array.isArray(DATA) && DATA.length > 0,
        String(DATA && DATA.length));

  console.log('\n2. Форма поиска');
  check('список «Откуда» заполнен', $$('#from option').length > 5,
        String($$('#from option').length));
  check('города сгруппированы по ДФО', $$('#from optgroup').length === 2,
        String($$('#from optgroup').length));
  check('маршрут по умолчанию есть в данных',
        DATA.some(f => f.o === st.from && f.d === st.to), st.from + '-' + st.to);
  check('дата по умолчанию — та, где есть места',
        DATA.some(f => f.dt === st.date && f.q > 0), st.date);
  check('границы календаря выставлены', !!$('#date').min && !!$('#date').max);
  check('коды аэропортов подписаны в полях',
        $('#fromCode').textContent === st.from && $('#toCode').textContent === st.to,
        `${$('#fromCode').textContent}/${$('#toCode').textContent} вместо ${st.from}/${st.to}`);

  console.log('\n3. Выдача');
  const rows = $$('.flights tbody tr');
  check('рейсы отрисованы', rows.length > 0, String(rows.length));
  check('заголовок маршрута заполнен', $('#routeTitle').textContent.includes('→'),
        $('#routeTitle').textContent);
  const shown = DATA.filter(f => f.o === st.from && f.d === st.to && f.dt === st.date && f.q >= 1);
  check('показаны только рейсы с местами на выбранную дату',
        rows.length === shown.length, `в DOM ${rows.length}, ожидалось ${shown.length}`);
  check('в демо-режиме кнопка покупки отключена',
        !window.META.demo || $$('.buy.off').length === rows.length);

  console.log('\n4. Пассажиры');
  const before = rows.length;
  st.adults = 5; window.renderPax(); window.search();
  const after = $$('.flights tbody tr').length;
  check('увеличение числа пассажиров отсекает рейсы с малым числом мест',
        after < before, `было ${before}, стало ${after}`);
  check('счётчик пассажиров подписан верно',
        $('#paxLabel').textContent.includes('5'), $('#paxLabel').textContent);
  st.infants = 9; window.renderPax();
  check('младенцев нельзя больше, чем взрослых', st.infants <= st.adults, String(st.infants));
  st.adults = 1; st.infants = 0; window.renderPax(); window.search();

  console.log('\n5. Пустое состояние');
  // 9 взрослых + 1 ребёнок = 10 мест: заведомо больше, чем есть на любом рейсе.
  st.adults = 9; st.children = 1; window.renderPax(); window.search();
  const empty = $('.empty');
  check('показан экран «мест нет»', !!empty && empty.textContent.includes('Мест на эту дату нет'),
        empty ? empty.textContent.slice(0, 60) : 'нет .empty');
  check('предложены ближайшие даты или честно сказано, что их нет',
        !!empty && ($$('.jump button').length > 0
                    || empty.textContent.includes('свободных мест сейчас нет')));
  check('есть кнопка подписки', !!$('.watch'));
  st.adults = 1; st.children = 0; window.renderPax(); window.search();

  console.log('\n6. Календарь');
  const days = $$('.day:not(.void)');
  check('календарь отрисован', days.length >= 28, String(days.length));
  const need = st.adults + st.children;
  const byDate = {};
  DATA.filter(f => f.o === st.from && f.d === st.to)
      .forEach(f => { byDate[f.dt] = (byDate[f.dt] || 0) + (f.q >= need ? f.q : 0); });
  const sample = Object.keys(byDate).find(d => byDate[d] > 0);
  const dayNum = Number(sample.slice(8));
  const cell = days.find(d => Number(d.querySelector('.n').textContent) === dayNum
                              && !d.classList.contains('unknown'));
  check('в ячейке дня стоит верное число мест',
        cell && cell.querySelector('.q').textContent === String(byDate[sample]),
        cell ? `${cell.querySelector('.q').textContent} вместо ${byDate[sample]}` : 'ячейка не найдена');
  check('дни вне мониторинга не кликаются',
        $$('.day.unknown').every(d => d.disabled));
  check('выбранный день подсвечен', $$('.day.sel').length === 1,
        String($$('.day.sel').length));

  console.log('\n7. Переключения');
  const t0 = $('#routeTitle').textContent;
  $('#swap').dispatchEvent(new window.Event('click'));
  check('кнопка «поменять местами» работает',
        $('#routeTitle').textContent !== t0,
        `${t0} -> ${$('#routeTitle').textContent}`);
  check('коды в полях обновились после свапа',
        $('#fromCode').textContent === st.from && $('#toCode').textContent === st.to);
  $('#swap').dispatchEvent(new window.Event('click'));

  st.sort = 'seats'; window.search();
  const seats = $$('.flights tbody .seats').map(e => parseInt(e.textContent));
  check('сортировка по числу мест работает',
        seats.every((v, i, a) => i === 0 || a[i - 1] >= v), seats.join(','));
  st.sort = 'price'; window.search();
  st.sort = 'time'; window.search();

  const cat = $$('.cat')[1];
  cat.dispatchEvent(new window.Event('click'));
  check('переключение категории меняет памятку',
        $('#note').textContent.includes(window.CATEGORIES[1].title),
        $('#note').textContent.slice(0, 50));

  console.log('\n8. Направления под мониторингом');
  const pops = $$('.pop');
  check('карточки направлений отрисованы', pops.length === window.ROUTES.length,
        `${pops.length} карточек при ${window.ROUTES.length} маршрутах`);
  check('в карточке есть минимальная цена',
        pops[0].textContent.includes('₽') || pops[0].textContent.includes('уточняется'),
        pops[0].textContent.trim().slice(0, 60));
  const other = window.ROUTES.find(r => r.origin !== st.from || r.destination !== st.to);
  if (other) {
    window.goRoute(other.origin, other.destination);
    check('клик по карточке переключает направление',
          st.from === other.origin && st.to === other.destination,
          st.from + '-' + st.to);
    check('переключение ведёт на дату, где места есть',
          DATA.some(f => f.o === st.from && f.d === st.to && f.dt === st.date && f.q > 0),
          st.date);
  }

  console.log('\n9. Нехоженое направление');
  st.from = 'PKC'; st.to = 'AER'; window.search();
  check('честно сообщаем, что направление не отслеживается',
        $('#board').textContent.includes('пока не отслеживаем'),
        $('#board').textContent.slice(0, 60));

  // ---- Читаемость: ловим «белое по белому».
  // Строка поиска лежит внутри синего блока, где текст белый, но фон у неё
  // белый — стоит забыть явный color, и значения полей исчезают.
  console.log('\n10. Читаемость текста');

  const css = [...doc.querySelectorAll('style')].map(s => s.textContent).join('\n');
  const rootBlock = css.match(/:root\{([\s\S]*?)\}/);
  const palette = {};
  if (rootBlock) {
    for (const m of rootBlock[1].matchAll(/--([a-z0-9-]+)\s*:\s*(#[0-9a-fA-F]{3,8})/g)) {
      palette['--' + m[1]] = m[2];
    }
  }
  const resolveVar = v => {
    const m = /var\((--[a-z0-9-]+)\)/.exec(v || '');
    return m ? palette[m[1]] : v;
  };
  const lin = c => (c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4));
  // Принимает и #hex, и rgb(): getComputedStyle возвращает то одно, то другое.
  const lum = value => {
    const s = String(value || '').trim();
    let rgb = null;
    const m = /^rgba?\((\d+)[,\s]+(\d+)[,\s]+(\d+)/.exec(s);
    if (m) {
      rgb = [+m[1] / 255, +m[2] / 255, +m[3] / 255];
    } else {
      let h = s.replace('#', '');
      if (h.length === 3) h = [...h].map(c => c + c).join('');
      if (h.length !== 6 || /[^0-9a-fA-F]/.test(h)) return null;
      rgb = [0, 2, 4].map(i => parseInt(h.slice(i, i + 2), 16) / 255);
    }
    return 0.2126 * lin(rgb[0]) + 0.7152 * lin(rgb[1]) + 0.0722 * lin(rgb[2]);
  };
  const contrast = (a, b) => {
    const [la, lb] = [lum(a), lum(b)];
    if (la === null || lb === null) return null;
    const [hi, lo] = la > lb ? [la, lb] : [lb, la];
    return (hi + 0.05) / (lo + 0.05);
  };

  check('палитра прочитана из :root', Object.keys(palette).length > 8,
        String(Object.keys(palette).length));

  // Элементы, лежащие на белом фоне.
  [['#from', 'город отправления'], ['#to', 'город назначения'],
   ['#date', 'дата'], ['#paxLabel', 'пассажиры']].forEach(([sel, name]) => {
    const el = $(sel);
    const color = resolveVar(el && window.getComputedStyle(el).color);
    const r = contrast(color, '#ffffff');
    check(`${name} читается на белом поле`, r !== null && r >= 4.5,
          `${color} даёт контраст ${r ? r.toFixed(2) : '?'}`);
  });

  // Текст на кнопке действия — на янтарной заливке.
  const findBtn = $('.btn-find');
  const btnColor = resolveVar(window.getComputedStyle(findBtn).color);
  const rBtn = contrast(btnColor, palette['--cta']);
  check('надпись на кнопке читается на янтарном',
        rBtn !== null && rBtn >= 4.5,
        `${btnColor} на ${palette['--cta']} даёт ${rBtn ? rBtn.toFixed(2) : '?'}`);

  console.log('\n' + '='.repeat(52));
  console.log(`пройдено: ${passed}   провалено: ${failed}`);
  process.exit(failed ? 1 : 0);
});
