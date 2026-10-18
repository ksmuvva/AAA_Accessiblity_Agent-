// Installed before any page script runs. The run-wide route can't see what a page sends while it is being unloaded
// (beacons, keepalive fetches, fetchLater) or from a SharedWorker, so those are switched off here.
(() => {
  Navigator.prototype.sendBeacon = () => false;
  const fetch = window.fetch;
  window.fetch = function (input, init) {
    const method = ((init && init.method) || (input && input.method) || 'GET').toUpperCase();
    if (init && init.keepalive && !['GET', 'HEAD', 'OPTIONS'].includes(method)) {
      return Promise.reject(new TypeError('blocked by the accessibility audit'));
    }
    return fetch.apply(this, arguments);
  };
  for (const name of ['fetchLater', 'SharedWorker', 'WebTransport']) { try { delete window[name]; } catch (e) {} }
})();
