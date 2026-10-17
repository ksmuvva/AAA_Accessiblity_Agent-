() => {
  // A coarse identity for a page state: enough to tell whether replaying steps reached the state that was labelled
  // (a panel open, a dialog shown, a step of a flow), not so fine that a clock or an animation changes it.
  const visible = (el) => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
  const text = (el) => (el.innerText || el.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 80);
  return {
    url: location.href,
    title: document.title,
    headings: [...document.querySelectorAll('h1,h2,h3,h4,h5,h6,[role="heading"]')].filter(visible).map(text).slice(0, 20),
    controls: [...document.querySelectorAll('input,select,textarea,button,dialog[open],[role="dialog"]')].filter(visible).length,
  };
}
