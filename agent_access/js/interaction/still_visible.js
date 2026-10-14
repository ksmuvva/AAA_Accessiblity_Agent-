// @body
for (const el of document.querySelectorAll('body *')) if (__ids.map.get(el) === arg) return isVisible(el);
return false;
