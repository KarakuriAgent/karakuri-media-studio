/* 生成 SW（vite-plugin-pwa generateSW）から importScripts される。 */
self.addEventListener('push', (event) => {
  let data = { title: 'Karakuri Media Studio', body: '', url: '/', tag: '' }
  try {
    if (event.data) data = { ...data, ...event.data.json() }
  } catch {
    /* 壊れた payload は既定のタイトルだけ出す */
  }
  const title = data.title || 'Karakuri Media Studio'
  event.waitUntil(
    self.registration.showNotification(title, {
      body: data.body || '',
      tag: data.tag || undefined,
      data: { url: data.url || '/' },
    }),
  )
})

self.addEventListener('notificationclick', (event) => {
  event.notification.close()
  const raw = (event.notification.data && event.notification.data.url) || '/'
  const target = new URL(raw, self.location.origin).href
  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((clients) => {
      for (const client of clients) {
        if ('focus' in client) return client.focus()
      }
      if (self.clients.openWindow) return self.clients.openWindow(target)
      return undefined
    }),
  )
})
