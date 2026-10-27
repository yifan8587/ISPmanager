/** 管理端以 GB 展示和填写。库内与下发 POP / vpopmanager 仍使用 MB。 */

export function mbToGb(mb) {
  const n = Number(mb)
  if (!Number.isFinite(n) || n <= 0) return 0
  const gb = Math.round((n / 1024) * 100) / 100
  return Number.isInteger(gb) ? gb : gb
}

export function gbToMb(gb) {
  const n = Number(gb)
  if (!Number.isFinite(n) || n <= 0) return 0
  return Math.round(n * 1024)
}

export function ramText(mb) {
  const gb = mbToGb(mb)
  return gb ? `${gb} GB` : '0 GB'
}

export function ramAmount(key, value) {
  if (key !== 'ram_mb' || value == null || value === '') return value
  return mbToGb(value)
}
