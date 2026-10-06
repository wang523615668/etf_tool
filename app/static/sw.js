// PWA Service Worker: 离线缓存壳 + 快速返回
// v3: 页面(navigate)改网络优先, 避免 HTML 更新被旧缓存挡住; 静态资源仍缓存优先
const CACHE = 'etf-assistant-v3';
const CORE = ['/', '/static/style.css', '/static/app.js', '/static/manifest.json', '/static/icon-192.png', '/static/icon-512.png'];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(CORE)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (e) => {
  e.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))).then(() => self.clients.claim()));
});

self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url);
  // API 请求不缓存（实时数据）
  if (url.pathname.startsWith('/api/')) return;
  if (e.request.method !== 'GET') return;
  // 页面导航: 网络优先, 离线回落缓存(保证 HTML/JS 改动即时可见)
  if (e.request.mode === 'navigate') {
    e.respondWith(
      fetch(e.request).then((resp) => {
        const clone = resp.clone();
        caches.open(CACHE).then((c) => c.put(e.request, clone));
        return resp;
      }).catch(() => caches.match(e.request).then((hit) => hit || caches.match('/')))
    );
    return;
  }
  // 其他静态资源: 缓存优先
  e.respondWith(
    caches.match(e.request).then((hit) => hit || fetch(e.request).then((resp) => {
      const clone = resp.clone();
      caches.open(CACHE).then((c) => c.put(e.request, clone));
      return resp;
    }).catch(() => caches.match('/')))
  );
});
