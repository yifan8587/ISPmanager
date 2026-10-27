<template>
  <div class="webssh-terminal">
    <div ref="termEl" class="terminal-box"></div>
  </div>
</template>

<script setup>
import { ref, onMounted, onBeforeUnmount, nextTick } from 'vue'
import { ElMessage } from 'element-plus'
import { wsAuthProtocols } from '@/api/wsAuth'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import '@xterm/xterm/css/xterm.css'

const props = defineProps({
  wsUrl: { type: String, default: '' },
})

const emit = defineEmits(['connected', 'disconnected', 'status'])

const termEl = ref(null)
const connected = ref(false)
const connecting = ref(false)

let term = null
let fitAddon = null
let ws = null
let resizeObs = null

function initTerminal() {
  if (term || !termEl.value) return
  term = new Terminal({
    cursorBlink: true,
    fontSize: 14,
    fontFamily: 'Menlo, Monaco, Consolas, "Courier New", monospace',
    theme: { background: '#1e1e2e', foreground: '#cdd6f4' },
  })
  fitAddon = new FitAddon()
  term.loadAddon(fitAddon)
  term.open(termEl.value)
  fitAddon.fit()
  term.writeln('\x1b[36mWebSSH 终端 —— 填写连接信息并点击「连接」\x1b[0m')

  term.onData((d) => {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: 'input', data: d }))
    }
  })
  term.onResize(({ cols, rows }) => {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: 'resize', cols, rows }))
    }
  })
  resizeObs = new ResizeObserver(() => {
    try { fitAddon?.fit() } catch { /* ignore */ }
  })
  resizeObs.observe(termEl.value)
}

function connect(ssh) {
  if (!props.wsUrl) {
    ElMessage.warning('缺少 WebSocket 地址')
    return
  }
  if (!ssh?.host) {
    ElMessage.warning('请填写主机地址')
    return
  }
  disconnect(false)
  connecting.value = true
  ws = new WebSocket(props.wsUrl, wsAuthProtocols())

  ws.onopen = () => {
    ws.send(JSON.stringify({
      type: 'open',
      host: ssh.host,
      port: ssh.port,
      username: ssh.username,
      password: ssh.password,
    }))
  }
  ws.onmessage = (ev) => {
    let msg
    try { msg = JSON.parse(ev.data) } catch { return }
    if (msg.type === 'data') {
      term?.write(msg.data)
    } else if (msg.type === 'status') {
      connecting.value = false
      const color = msg.level === 'error' ? '31' : msg.level === 'success' ? '32' : '36'
      term?.writeln(`\x1b[${color}m[${msg.level}] ${msg.msg}\x1b[0m`)
      emit('status', msg)
      if (msg.level === 'success' && msg.msg.includes('SSH 连接成功')) {
        connected.value = true
        emit('connected')
        nextTick(() => { try { fitAddon?.fit() } catch { /* ignore */ } })
      }
      if (msg.level === 'error') ElMessage.error(msg.msg)
    } else if (msg.type === 'closed') {
      connected.value = false
      emit('disconnected')
      term?.writeln('\x1b[33m[会话已关闭]\x1b[0m')
    }
  }
  ws.onclose = () => {
    connected.value = false
    connecting.value = false
    emit('disconnected')
    term?.writeln('\x1b[33m[WebSocket 已断开]\x1b[0m')
  }
  ws.onerror = () => {
    connecting.value = false
    ElMessage.error('WebSocket 连接错误，请检查后端是否以 ASGI 启动')
  }
}

function disconnect(clearWs = true) {
  if (clearWs && ws) {
    ws.close()
    ws = null
  }
  connected.value = false
  connecting.value = false
}

function sendInput(data) {
  if (!connected.value || !ws || ws.readyState !== WebSocket.OPEN) {
    ElMessage.warning('请先建立 SSH 连接')
    return false
  }
  ws.send(JSON.stringify({ type: 'input', data }))
  return true
}

function sendCommand(command) {
  if (!connected.value || !ws || ws.readyState !== WebSocket.OPEN) {
    ElMessage.warning('请先建立 SSH 连接')
    return false
  }
  ws.send(JSON.stringify({ type: 'exec', command }))
  return true
}

function writeln(text) {
  term?.writeln(text)
}

function fit() {
  try { fitAddon?.fit() } catch { /* ignore */ }
}

onMounted(() => nextTick(() => initTerminal()))

onBeforeUnmount(() => {
  disconnect()
  if (resizeObs) { resizeObs.disconnect(); resizeObs = null }
  if (term) { term.dispose(); term = null }
})

defineExpose({
  connect,
  disconnect,
  sendInput,
  sendCommand,
  writeln,
  fit,
  connected,
  connecting,
})
</script>

<style scoped>
.webssh-terminal {
  height: 100%;
  min-height: 0;
  display: flex;
  flex-direction: column;
}
.terminal-box {
  flex: 1;
  height: 100%;
  min-height: 0;
  background: #1e1e2e;
  border-radius: 6px;
  padding: 4px;
}
</style>
