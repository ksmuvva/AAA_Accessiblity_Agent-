() => [...document.querySelectorAll('a[href]')].map((a) => ({
  href: a.href,
  text: (a.innerText || a.getAttribute('aria-label') || '').trim().replace(/\s+/g, ' ').slice(0, 80),
}))
