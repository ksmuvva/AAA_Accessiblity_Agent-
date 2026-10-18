// @body
const PLAYER = /youtube|youtu\.be|vimeo|wistia|brightcove|jwplayer|dailymotion|soundcloud|spotify|podbean|libsyn|twitch|kaltura|vidyard/i;
const CAPTION = /^(captions|subtitles)$/;
const GAP = 30;  // seconds with no caption worth naming
// A caption track loads its cues only once enabled: enable it hidden (nothing is shown) and wait up to 3 s, in frames
// (the page's timer instrumentation would count a setTimeout), with the media's length; modes are restored at the end.
const captionTracks = [...document.querySelectorAll('video track, audio track')].filter((t) => CAPTION.test(t.kind));
const modes = captionTracks.map((t) => t.track.mode);
captionTracks.forEach((t) => { if (t.track.mode === 'disabled') t.track.mode = 'hidden'; });
const loading = () => captionTracks.some((t) => t.readyState < 2
  || (t.parentElement.readyState < 1 && t.parentElement.networkState === HTMLMediaElement.NETWORK_LOADING));
const loaded = (async () => {
  const start = performance.now();
  while (loading() && performance.now() - start < 3000) await new Promise((resolve) => requestAnimationFrame(resolve));
})();
const cues = (t, duration) => {
  const spans = [...(t.track.cues || [])].map((c) => [c.startTime, c.endTime]).sort((a, b) => a[0] - b[0]);
  const gaps = [];
  let end = 0;
  for (const [s, e] of spans) { if (s - end >= GAP) gaps.push([end, s]); end = Math.max(end, e); }
  if (spans.length && duration && duration - end >= GAP) gaps.push([end, duration]);
  return { src: t.src.slice(0, 120), state: ['none', 'loading', 'loaded', 'error'][t.readyState], cues: spans.length,
    gaps: gaps.slice(0, 5).map((g) => g.map(Math.round)) };
};
const media = (el) => ({
  selector: cssPath(el), tag: el.tagName.toLowerCase(),
  src: (el.currentSrc || el.src || el.querySelector('source')?.src || '').slice(0, 120),
  autoplay: el.autoplay, muted: el.muted, controls: el.controls, loop: el.loop, paused: el.paused,
  duration: Number.isFinite(el.duration) ? Math.round(el.duration) : null,
  tracks: [...el.querySelectorAll('track')].map((t) => ({ kind: t.kind || 'subtitles', srclang: t.srclang, label: t.label,
    ...(CAPTION.test(t.kind) ? cues(t, Number.isFinite(el.duration) ? el.duration : null) : {}) })),
  visible: isVisible(el),
});
const animated = [...document.querySelectorAll('body *')].slice(0, 5000).filter((el) => {
  const s = getComputedStyle(el);
  return isVisible(el) && s.animationName !== 'none' && s.animationIterationCount === 'infinite' && s.animationPlayState !== 'paused';
});
return loaded.then(() => { const out = {
  videos: [...document.querySelectorAll('video')].map(media),
  audio: [...document.querySelectorAll('audio')].map(media),
  player_iframes: [...document.querySelectorAll('iframe')].filter((f) => PLAYER.test(f.src)).map((f) => ({
    selector: cssPath(f), src: f.src.slice(0, 120), title: f.title || null })),
  other_iframes: document.querySelectorAll('iframe').length,
  objects: document.querySelectorAll('object, embed').length,
  canvas: document.querySelectorAll('canvas').length,
  infinite_animations: animated.slice(0, 10).map((el) => ({ selector: cssPath(el), animation: getComputedStyle(el).animationName })),
  marquee_or_blink: document.querySelectorAll('marquee, blink').length,
  gifs: [...document.querySelectorAll('img')].filter((i) => /\.gif(\?|$)/i.test(i.currentSrc || i.src)).map((i) => cssPath(i)).slice(0, 10),
};
captionTracks.forEach((t, i) => { t.track.mode = modes[i]; });
return out; });
