() => {
  if (!window.__a11yFocusLog) {
    window.__a11yFocusLog = [];
    window.addEventListener('focus', (e) => { if (e.target && e.target.nodeType === 1) window.__a11yFocusLog.push(e.target); }, true);
  }
  window.__a11yFocusLog.length = 0;
}
