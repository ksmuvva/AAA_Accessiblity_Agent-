(sel) => {
  const d = document.querySelector(sel), a = document.activeElement;
  return !!d && (!a || a === document.body || d.contains(a));
}
