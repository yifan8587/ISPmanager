/** WebSSH 走子协议传 JWT，避免 access token 进入代理访问日志。noVNC 仍走 query。 */
export function wsAuthProtocols(storageKey = 'access_token') {
  const token = localStorage.getItem(storageKey) || ''
  return token ? ['access_token', token] : []
}
