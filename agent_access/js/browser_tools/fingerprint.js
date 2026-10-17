() => {
  // A coarse identity for a page state: enough to tell whether replaying steps reached the state that was labelled
  // (a panel or menu open, a tab selected, a box checked, a field filled, a dialog shown), not so fine that a clock or
  // an animation changes it.
  const visible = (el) => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
  const text = (el) => (el.innerText || el.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 80);
  const shown = (selector) => [...document.querySelectorAll(selector)].filter(visible);
  return {
    url: location.href,
    title: document.title,
    headings: shown('h1,h2,h3,h4,h5,h6,[role="heading"]').map(text).slice(0, 20),
    controls: shown('input,select,textarea,button,dialog[open],[role="dialog"]').length,
    links: shown('a[href]').length,
    // State that lives in attributes and values rather than in new headings or controls.
    states: shown('[aria-expanded="true"],[aria-selected="true"],[aria-pressed="true"],[aria-checked="true"],details[open]')
      .map((el) => `${el.tagName}:${text(el).slice(0, 40)}`).slice(0, 30),
    values: shown('input,select,textarea')
      .map((el) => (el.type === 'checkbox' || el.type === 'radio' ? String(el.checked) : String(el.value).slice(0, 40)))
      .slice(0, 40),
  };
}
