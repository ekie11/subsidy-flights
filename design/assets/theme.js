/* ============================================================
   Переключатель темы: светлая по умолчанию, тёмная по кнопке.

   Подключается синхронно в <head>, до стилей страницы: тема ставится
   на <html> раньше первой отрисовки, без вспышки светлого экрана.
   Выбор хранится в localStorage под одним ключом на весь домен —
   витрина, оформление и табло /flights/ показывают одну тему.
   Кнопка — любой элемент с [data-theme-toggle]; aria-pressed="true"
   значит «тёмная включена».
   ============================================================ */
(function () {
  var KEY = 'theme';
  var root = document.documentElement;

  function saved() {
    try {
      var v = localStorage.getItem(KEY);
      return v === 'dark' || v === 'light' ? v : null;
    } catch (e) { return null; }
  }

  function apply(theme) {
    root.setAttribute('data-theme', theme);
    var dark = theme === 'dark';
    var btns = document.querySelectorAll('[data-theme-toggle]');
    for (var i = 0; i < btns.length; i++) {
      btns[i].setAttribute('aria-pressed', String(dark));
      btns[i].title = dark ? 'Включить светлую тему' : 'Включить тёмную тему';
    }
  }

  apply(saved() || 'light');

  document.addEventListener('DOMContentLoaded', function () {
    apply(root.getAttribute('data-theme') || 'light');
  });

  document.addEventListener('click', function (e) {
    var btn = e.target.closest && e.target.closest('[data-theme-toggle]');
    if (!btn) return;
    var next = root.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
    apply(next);
    try { localStorage.setItem(KEY, next); } catch (err) {}
  });

  // Тема, выбранная в соседней вкладке, применяется и здесь.
  window.addEventListener('storage', function (e) {
    if (e.key === KEY && (e.newValue === 'dark' || e.newValue === 'light')) apply(e.newValue);
  });
})();
