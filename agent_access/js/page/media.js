// @body
const PLAYER = /youtube|youtu\.be|vimeo|wistia|brightcove|jwplayer|dailymotion|soundcloud|spotify|podbean|libsyn|twitch|kaltura|vidyard/i;
const media = (el) => ({
  selector: cssPath(el), tag: el.tagName.toLowerCase(),
  src: (el.currentSrc || el.src || el.querySelector('source')?.src || '').slice(0, 120),
  autoplay: el.autoplay, muted: el.muted, controls: el.controls, loop: el.loop, paused: el.paused,
  duration: Number.isFinite(el.duration) ? Math.round(el.duration) : null,
  tracks: [...el.querySelectorAll('track')].map((t) => ({ kind: t.kind || 'subtitles', srclang: t.srclang, label: t.label })),
  visible: isVisible(el),
});
const animated = [...document.querySelectorAll('body *')].slice(0, 5000).filter((el) => {
  const s = getComputedStyle(el);
  return isVisible(el) && s.animationName !== 'none' && s.animationIterationCount === 'infinite' && s.animationPlayState !== 'paused';
});
return {
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
