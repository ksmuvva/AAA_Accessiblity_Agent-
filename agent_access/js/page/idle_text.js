// @body
// The visible lines that speak of a session or time limit, and the visible controls that could extend it (English only).
const LIMIT = /session|expir|time ?out|inactiv|idle|still there|signed? ?out|log(ged)? ?out|more time/i;
const EXTEND = /extend|more time|continue|stay|keep me|still here|i'?m here|renew/i;
const name = (el) => (label(el) || el.value || '').trim();
return {
  warnings: (document.body?.innerText || '').split('\n').map((l) => l.trim()).filter((l) => l.length < 200 && LIMIT.test(l)),
  controls: [...document.querySelectorAll('button, a[href], input[type=button], input[type=submit], [role=button]')]
    .filter((el) => isVisible(el) && EXTEND.test(name(el))).map((el) => name(el).slice(0, 60)).slice(0, 5),
};
