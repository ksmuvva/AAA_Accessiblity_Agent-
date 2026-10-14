(sel) => {
  const root = sel ? document.querySelector(sel) : document;
  return !!root && !!root.querySelector('[required], [aria-required="true"]');
}
