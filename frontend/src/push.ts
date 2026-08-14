import { api } from './api'

export type PushPermission = 'granted' | 'denied' | 'default' | 'unsupported'

function urlBase64ToUint8Array(value: string): BufferSource {
  const padding = '='.repeat((4 - (value.length % 4)) % 4)
  const base64 = (value + padding).replace(/-/g, '+').replace(/_/g, '/')
  const raw = atob(base64)
  const output = new Uint8Array(raw.length)
  for (let i = 0; i < raw.length; i += 1) output[i] = raw.charCodeAt(i)
  return output
}

export function isPushSupported(): boolean {
  return (
    typeof window !== 'undefined' &&
    window.isSecureContext &&
    'Notification' in window &&
    'serviceWorker' in navigator &&
    'PushManager' in window
  )
}

export function currentPushPermission(): PushPermission {
  if (!isPushSupported()) return 'unsupported'
  return Notification.permission
}

async function getRegistration(): Promise<ServiceWorkerRegistration | null> {
  if (!('serviceWorker' in navigator)) return null
  const existing = await navigator.serviceWorker.getRegistration()
  if (existing) return existing
  return Promise.race([
    navigator.serviceWorker.ready,
    new Promise<null>((resolve) => {
      window.setTimeout(() => resolve(null), 4000)
    }),
  ])
}

async function subscribeWith(registration: ServiceWorkerRegistration): Promise<void> {
  const { public_key: publicKey } = await api.vapidPublicKey()
  const subscription = await registration.pushManager.subscribe({
    userVisibleOnly: true,
    applicationServerKey: urlBase64ToUint8Array(publicKey),
  })
  const json = subscription.toJSON()
  const endpoint = json.endpoint
  const p256dh = json.keys?.p256dh
  const auth = json.keys?.auth
  if (!endpoint || !p256dh || !auth) {
    throw new Error('push subscription is missing keys')
  }
  await api.savePushSubscription({ endpoint, keys: { p256dh, auth } })
}

/**
 * 許可済みなら購読を作り、未設定なら `request` のときだけダイアログを出す。
 * SW 未登録・非 HTTPS・拒否は静かに終わる。
 */
export async function ensurePushSubscription(
  options: { request?: boolean } = {},
): Promise<PushPermission> {
  if (!isPushSupported()) return 'unsupported'
  let permission: NotificationPermission = Notification.permission
  if (permission === 'denied') return 'denied'
  if (permission === 'default') {
    if (!options.request) return 'default'
    permission = await Notification.requestPermission()
  }
  if (permission !== 'granted') return permission
  try {
    const registration = await getRegistration()
    if (!registration?.pushManager) return 'granted'
    await subscribeWith(registration)
  } catch (error) {
    console.warn('push subscribe failed', error)
  }
  return 'granted'
}
