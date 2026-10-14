(() => {
  if (window.__a11yInstr) return;
  // `paused` is set by probes that inject their own scripts (axe-core adds window listeners), so those aren't
  // recorded as the page's own.
  const I = (window.__a11yInstr = { timers: [], listeners: [], opens: 0, start: Date.now(), paused: false });
  const WATCH = new Set(['click', 'dblclick', 'mousedown', 'mouseup', 'mousemove', 'pointerdown', 'pointerup',
    'pointermove', 'touchstart', 'touchmove', 'touchend', 'gesturestart', 'devicemotion', 'deviceorientation',
    'keydown', 'keypress', 'keyup', 'dragstart', 'drop', 'wheel']);
  const addListener = EventTarget.prototype.addEventListener;
  EventTarget.prototype.addEventListener = function (type, fn, opts) {
    if (WATCH.has(type) && !I.paused && I.listeners.length < 4000) I.listeners.push({ type, target: this });
    return addListener.call(this, type, fn, opts);
  };
  const setT = window.setTimeout, setI = window.setInterval;
  window.setTimeout = function (fn, ms, ...rest) {
    if (ms >= 1000 && I.timers.length < 300) I.timers.push({ kind: 'timeout', ms, at: Date.now() - I.start });
    return setT.call(this, fn, ms, ...rest);
  };
  window.setInterval = function (fn, ms, ...rest) {
    if (I.timers.length < 300) I.timers.push({ kind: 'interval', ms, at: Date.now() - I.start });
    return setI.call(this, fn, ms, ...rest);
  };
  const open = window.open;
  window.open = function (...args) { I.opens++; return open.apply(this, args); };
})();
