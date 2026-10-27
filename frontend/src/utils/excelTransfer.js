import axios from 'axios'

function authHeaders() {
  const token = localStorage.getItem('access_token')
  return token ? { Authorization: `Bearer ${token}` } : {}
}

function cleanParams(params = {}) {
  const out = {}
  Object.entries(params).forEach(([key, value]) => {
    if (key === 'page' || key === 'page_size') return
    if (value === '' || value === null || value === undefined) return
    out[key] = value
  })
  return out
}

function filenameFromDisposition(header, fallback) {
  if (!header) return fallback
  const utf = /filename\*=UTF-8''([^;]+)/i.exec(header)
  if (utf) {
    try {
      return decodeURIComponent(utf[1])
    } catch {
      return fallback
    }
  }
  const ascii = /filename="?([^"]+)"?/i.exec(header)
  return ascii ? ascii[1] : fallback
}

async function blobError(blob, fallback) {
  const text = await blob.text()
  try {
    const data = JSON.parse(text)
    return data.detail || data.file || data.message || fallback
  } catch {
    return text || fallback
  }
}

export async function downloadExcel(url, params = {}, fallbackName = 'export.xlsx') {
  const res = await axios.get(url, {
    params: cleanParams(params),
    responseType: 'blob',
    timeout: 0,
    headers: authHeaders(),
  })
  const type = res.headers['content-type'] || ''
  if (type.includes('application/json')) {
    throw new Error(await blobError(res.data, '下载失败'))
  }
  const filename = filenameFromDisposition(res.headers['content-disposition'], fallbackName)
  const href = URL.createObjectURL(res.data)
  const a = document.createElement('a')
  a.href = href
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(href)
}

export async function uploadExcel(url, file) {
  const form = new FormData()
  form.append('file', file)
  const res = await axios.post(url, form, {
    timeout: 0,
    headers: authHeaders(),
  })
  return res.data
}
