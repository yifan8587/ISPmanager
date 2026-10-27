import { ElMessage } from 'element-plus'

/**
 * 浏览器文件对话框对复合后缀 `.tar.gz` 支持很差：
 * Chrome / Edge / Linux GTK 通常只认最后一段 `.gz`，导致 .tar.gz 灰掉或点不了。
 * 用 `.gz` + gzip/tar MIME，再在选择后按文件名校验。
 */
export const ARCHIVE_ACCEPT = [
  '.gz',
  '.tgz',
  '.tar',
  'application/gzip',
  'application/x-gzip',
  'application/gzip-compressed',
  'application/x-tar',
  'application/x-gtar',
  'application/x-compressed-tar',
  'application/x-compressed',
].join(',')

export function isArchiveFilename(name = '') {
  const lower = String(name || '').trim().toLowerCase()
  return lower.endsWith('.tar.gz') || lower.endsWith('.tgz') || lower.endsWith('.tar')
}

export function beforeArchiveUpload(file) {
  if (!file) {
    ElMessage.error('未选择文件')
    return false
  }
  if (!isArchiveFilename(file.name)) {
    ElMessage.error('请选择 .tar.gz / .tgz / .tar 压缩包（当前文件无法识别）')
    return false
  }
  return true
}
