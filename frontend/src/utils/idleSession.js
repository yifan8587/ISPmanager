export const IDLE_MS = 30 * 60 * 1000
const ACTIVITY_KEY = 'auth_last_activity'
const TOKEN_KEY = 'access_token'
const REPORT_WINDOW_MS = 60 * 1000

let expireHandler = null
let started = false
let expiring = false

export function markActivity() {
  localStorage.setItem(ACTIVITY_KEY, String(Date.now()))
}

export function clearActivity() {
  localStorage.removeItem(ACTIVITY_KEY)
}

export function isIdleExpired() {
  if (!localStorage.getItem(TOKEN_KEY)) return false
  const seen = Number(localStorage.getItem(ACTIVITY_KEY) || 0)
  if (!seen) return true
  return Date.now() - seen > IDLE_MS
}

export function shouldReportActivity() {
  const seen = Number(localStorage.getItem(ACTIVITY_KEY) || 0)
  return seen > 0 && Date.now() - seen <= REPORT_WINDOW_MS
}

export function setExpireHandler(handler) {
  expireHandler = handler
}

export function expireSession() {
  if (expiring || !localStorage.getItem(TOKEN_KEY)) return
  expiring = true
  try {
    if (expireHandler) expireHandler()
    else {
      localStorage.removeItem(TOKEN_KEY)
      localStorage.removeItem('refresh_token')
      localStorage.removeItem('user')
      clearActivity()
    }
  } finally {
    window.setTimeout(() => { expiring = false }, 1500)
  }
}

export function startIdleWatch() {
  if (started || typeof window === 'undefined') return
  started = true
  let lastNote = 0
  const onUserAction = () => {
    if (!localStorage.getItem(TOKEN_KEY)) return
    if (isIdleExpired()) {
      expireSession()
      return
    }
    const now = Date.now()
    if (now - lastNote < 5000) return
    lastNote = now
    markActivity()
  }
  ;['pointerdown', 'keydown', 'wheel', 'touchstart'].forEach((name) => {
    window.addEventListener(name, onUserAction, { passive: true })
  })
  const tick = () => {
    if (isIdleExpired()) expireSession()
  }
  window.setInterval(tick, 15000)
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') tick()
  })
  tick()
}
