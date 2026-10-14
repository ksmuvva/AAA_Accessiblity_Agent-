// @body
return [...document.querySelectorAll('body *')].slice(0, 5000).filter(isVisible).map(idOf);
