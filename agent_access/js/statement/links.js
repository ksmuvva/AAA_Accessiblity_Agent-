() => [...document.querySelectorAll('a[href]')].map((a) => ({
  text: (a.innerText || a.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 100), href: a.href,
}))
