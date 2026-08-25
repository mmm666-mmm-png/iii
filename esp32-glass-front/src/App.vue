<template>
  <a-config-provider :theme="themeConfig">
    <div class="glass-shell">
      <aside class="glass-sidebar">
        <div class="glass-brand">
          <div class="glass-brand-mark">AI GLASS</div>
          <h1>智能导盲眼镜控制台</h1>
          <p>实时画面 · 导盲导航 · 语音问答</p>
        </div>

        <nav class="glass-nav" aria-label="功能页面">
          <button
            v-for="item in pageItems"
            :key="item.key"
            type="button"
            class="glass-nav-button"
            :class="{ 'glass-nav-button-active': activePage === item.key }"
            @click="setPage(item.key)"
          >
            <span>{{ item.label }}</span>
            <small>{{ item.description }}</small>
          </button>
        </nav>

        <div class="glass-status">
          <div class="glass-status-item">
            <span>设备连接</span>
            <strong :class="{ 'is-live': liveState?.deviceConnected }">{{ liveState?.deviceConnected ? '在线' : '等待连接' }}</strong>
          </div>
          <div class="glass-status-item">
            <span>AI 服务</span>
            <strong>{{ aiStatusText }}</strong>
          </div>
          <div class="glass-status-item">
            <span>视觉算法</span>
            <strong>{{ visionStatusText }}</strong>
          </div>
          <div class="glass-status-item">
            <span>数据同步</span>
            <strong>{{ sidebarSyncText }}</strong>
          </div>
        </div>

        <div class="glass-device">
          <div class="glass-device-row">
            <span>设备编号</span>
            <strong>{{ deviceIdText }}</strong>
          </div>
          <div class="glass-device-row">
            <span>固件版本</span>
            <strong>{{ firmwareText }}</strong>
          </div>
        </div>
      </aside>

      <main class="glass-main">
        <AdminHeader
          :live-state="liveState"
          :ai-state="aiState"
          :vision-state="effectiveVisionState"
          :refreshing="refreshing"
          :background-refreshing="backgroundRefreshing"
          :last-synced-at="lastSyncedAt"
          :active-page-label="activePageMeta.label"
          @refresh="refreshStatus({ manual: true })"
        />

        <LivePulseCard
          :page="activePage"
          :live-state="liveState"
          :ai-state="aiState"
          :vision-state="effectiveVisionState"
          :stream-quality="liveState?.quality"
          :snapshot-src="liveSnapshotSrc"
          :snapshot-rotated="shouldRotateSnapshot"
          :live-transcripts="liveTranscripts"
          :skill-events="liveSkillEvents"
          :vision-events="liveVisionEvents"
          :backend-http-base="backendHttpBase"
          :audio-unlocked="audioUnlocked"
          :assistant-audio-enabled="assistantAudioEnabled"
          :audio-status="audioStatus"
          :ai-audio-chunks-seen="aiAudioChunksSeen"
          :dashboard-error="dashboardError"
          :active-mode="activeWorkMode"
          :voice-mode="voiceMode"
          :gps-enabled="gpsEnabled"
          :gps-status="gpsStatus"
          :gps-fix="gpsFix"
          @mode-change="switchWorkMode"
          @unlock-audio="unlockAudio"
          @toggle-assistant-audio="toggleAssistantAudio"
          @vision-command="sendVisionCommand"
          @record-transcript="recordLiveTranscript"
          @toggle-gps="toggleGpsTracking"
          @navigate="setPage"
        />
      </main>
    </div>
  </a-config-provider>
</template>

<script setup>
// App.vue 是前端状态总线：负责连接 Go 后端 WebSocket、保存实时状态、
// 调度浏览器音频播放，并把控制命令传给子组件。
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import AdminHeader from './components/AdminHeader.vue'
import LivePulseCard from './components/LivePulseCard.vue'
import { formatRelativeTime } from './utils/format'

const configuredBackendOrigin = (import.meta.env.VITE_BACKEND_ORIGIN || '').trim().replace(/\/$/, '')
// 留空时使用当前页面同源；开发环境通常由 Vite proxy 转发到 Go 后端。
const backendHttpBase = configuredBackendOrigin || ''

// 四个页面：实时导航、障碍物管理、链路诊断、系统设置；hash 同步便于刷新保持位置。
const pageItems = [
  { key: 'live', label: '实时导航', description: '画面、转写、导盲/问答' },
  { key: 'navigation', label: '障碍物管理', description: '盲道、过马路、红绿灯、找物品' },
  { key: 'quality', label: '链路诊断', description: 'FPS、丢包、分片质量' },
  { key: 'ai', label: '系统设置', description: 'AI 语音、技能与服务信息' },
]

const initialHash = window.location.hash.replace('#', '')
const savedWorkMode = window.localStorage.getItem('ai-glass-work-mode')
const activePage = ref(pageItems.some((item) => item.key === initialHash) ? initialHash : 'live')
const activeWorkMode = ref(['navigation', 'qa'].includes(savedWorkMode) ? savedWorkMode : 'qa')
// 语音交互双模式：chat（千问聊天）/ navigation（高德导航），由 Go 后端 ai_state.voiceMode 同步。
const voiceMode = ref('chat')
// liveState 来自 /api/status 或 server_state；aiState/visionState 可能来自独立 WebSocket 事件。
const liveState = ref(null)
const aiState = ref(null)
const liveVisionState = ref(null)
const refreshing = ref(false)
const backgroundRefreshing = ref(false)
const dashboardError = ref('')
const lastSyncedAt = ref(null)
const liveSnapshotVersion = ref(Date.now())
// 直播帧使用 object URL，避免每帧都走 snapshot.jpg HTTP 请求。
const liveRawFrameUrl = ref('')
const liveVisionFrameUrl = ref('')
const liveSkillEvents = ref([])
const liveVisionEvents = ref([])
const liveTranscripts = ref([])
const audioUnlocked = ref(false)
const assistantAudioEnabled = ref(true)
const audioStatus = ref('浏览器 AI 语音尚未解锁。导盲提示由设备端播放，点击“解锁 AI 语音”后播放 AI 回复。')
const aiAudioChunksSeen = ref(0)

// 手机 GPS 定位上报：通过现有 /ws/view viewer 连接上行 gps_update 给 Go 转发。
const gpsEnabled = ref(false)
const gpsStatus = ref('未开启')
const gpsFix = ref(null)
let gpsWatchId = null

let refreshTimer = null
let snapshotTimer = null
let liveSocket = null
let liveReconnectTimer = null
let liveSocketStopped = false
let refreshInFlight = false
let latestRawFrameObjectUrl = ''
let latestVisionFrameObjectUrl = ''
// 浏览器 Web Audio 播放链路。必须由用户点击 unlockAudio 后才能启动。
let audioContext = null
let assistantAudioBus = null
let currentAssistantResponseId = ''
let lastNavigationGuidanceKey = ''

// Go 后端广播时先发 JSON 元数据，再发对应二进制 payload；
// 前端用 pendingPacketMetas 记录等待配对的元数据。
const pendingPacketMetas = []
const queuedAssistantAudio = []
const maxQueuedAssistantAudioChunks = 80
const audioFadeSamples = 24
const assistantAudioState = {
  // nextTime 是下一段音频在 AudioContext 时间轴上的开始时间，避免分片之间断裂。
  nextTime: 0,
  desiredLead: 0.08,
}

const themeConfig = {
  token: {
    colorPrimary: '#2563eb',
    colorSuccess: '#34d399',
    colorWarning: '#fb923c',
    colorError: '#f87171',
    borderRadius: 8,
    fontSize: 14,
  },
}

const effectiveVisionState = computed(() => liveVisionState.value || liveState.value?.vision || null)
const activePageMeta = computed(() => pageItems.find((item) => item.key === activePage.value) || pageItems[0])
const sidebarSyncText = computed(() => formatRelativeTime(lastSyncedAt.value))
const deviceIdText = computed(() => {
  const device = liveState.value?.device
  if (!device?.deviceId) return '等待握手'
  return device.deviceId
})
const firmwareText = computed(() => liveState.value?.device?.firmware || '—')
const aiStatusText = computed(() => {
  if (!aiState.value?.enabled) return '未启用'
  return aiState.value.connected ? '运行中' : '离线'
})
const visionStatusText = computed(() => {
  if (!effectiveVisionState.value?.enabled) return '未配置'
  return effectiveVisionState.value.connected ? (effectiveVisionState.value.state || '就绪') : '离线'
})
const qualityGapCount = computed(() => {
  const stats = liveState.value?.quality
  return Number(stats?.videoSeqGaps || 0) + Number(stats?.audioSeqGaps || 0) + Number(stats?.videoIncompleteFrames || 0)
})
const shouldUseVisionSnapshot = computed(() => {
  // 导航/寻物/红绿灯等状态下优先显示 Python worker 返回的标注图。
  const vision = effectiveVisionState.value
  return isActiveVisionState(vision?.state) && (vision?.annotatedAvailable || Boolean(liveVisionFrameUrl.value))
})
const liveSnapshotSrc = computed(() => {
  if (!liveState.value?.deviceConnected) return ''
  if (shouldUseVisionSnapshot.value) {
    return liveVisionFrameUrl.value || `${backendHttpBase}/vision-snapshot.jpg?t=${liveSnapshotVersion.value}`
  }
  return liveRawFrameUrl.value || `${backendHttpBase}/snapshot.jpg?t=${liveSnapshotVersion.value}`
})
const shouldRotateSnapshot = computed(() => !shouldUseVisionSnapshot.value)

function setPage(page) {
  // 页面切换只影响控制台显示，不会重置 WebSocket 或实时日志。
  if (!pageItems.some((item) => item.key === page)) return
  activePage.value = page
  window.history.replaceState(null, '', `#${page}`)
}

function onHashChange() {
  const next = window.location.hash.replace('#', '')
  if (pageItems.some((item) => item.key === next)) {
    activePage.value = next
  }
}

function apiUrl(path) {
  // 统一拼接后端地址，兼容同源部署和独立前端域名部署。
  return `${backendHttpBase}${path}`
}

async function fetchJSON(path) {
  // 所有轮询接口都禁用缓存，确保状态面板拿到最新设备/AI/视觉状态。
  const response = await fetch(apiUrl(path), { cache: 'no-store' })
  if (!response.ok) {
    const message = await response.text()
    throw new Error(message || `Request failed: ${response.status}`)
  }
  return response.json()
}

async function refreshStatus(options = {}) {
  // 手动刷新会显示 loading 和错误；后台刷新尽量静默，不打断画面和日志。
  const manual = Boolean(options.manual)
  if (refreshInFlight) return
  refreshInFlight = true
  if (manual) {
    refreshing.value = true
    dashboardError.value = ''
  } else {
    backgroundRefreshing.value = true
  }

  try {
    const state = await fetchJSON('/api/status')
    liveState.value = state
    if (state?.vision) {
      liveVisionState.value = state.vision
      syncWorkModeFromState({ vision: state.vision })
    }
    try {
      const vision = await fetchJSON('/api/vision/status')
      if (vision?.enabled) {
        liveVisionState.value = vision
        syncWorkModeFromState({ vision })
      }
    } catch {
      // Vision worker is optional; keep the previous state so refresh stays invisible.
    }
    lastSyncedAt.value = new Date()
    if (manual) dashboardError.value = ''
  } catch (error) {
    if (manual) {
      dashboardError.value = error instanceof Error ? error.message : '状态同步失败。'
    }
  } finally {
    refreshInFlight = false
    refreshing.value = false
    backgroundRefreshing.value = false
  }
}

async function setAssistantMode(mode, options = {}) {
  // Go 后端用 AI mode 控制 DashScope 是否接收麦克风输入。
  // navigation 模式会暂停 AI 问答，qa 模式恢复普通问答。
  if (!['navigation', 'qa'].includes(mode)) return false
  try {
    const response = await fetch(apiUrl('/api/ai/mode'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mode }),
    })
    const body = await response.json().catch(() => ({}))
    if (!response.ok) {
      throw new Error(body.error || `AI 模式切换失败：${response.status}`)
    }
    if (body.ai) {
      aiState.value = body.ai
    }
    return true
  } catch (error) {
    if (!options.silent) {
      dashboardError.value = error instanceof Error ? error.message : 'AI 模式切换失败。'
    }
    return false
  }
}

async function sendVisionCommand(payload, options = {}) {
  // 所有导航按钮最终都发到 /api/vision/control，由 Go 转发给 Python worker。
  dashboardError.value = ''
  const command = String(payload?.command || '')
  const syncMode = options.syncMode !== false
  if (syncMode && isNavigationCommand(command)) {
    // 启动视觉导航前先把 AI 切到 navigation，避免环境声触发大模型闲聊。
    activeWorkMode.value = 'navigation'
    window.localStorage.setItem('ai-glass-work-mode', 'navigation')
    await setAssistantMode('navigation', { silent: true })
  }
  try {
    const response = await fetch(apiUrl('/api/vision/control'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload || {}),
    })
    const body = await response.json().catch(() => ({}))
    if (!response.ok) {
      throw new Error(body.error || `视觉控制失败：${response.status}`)
    }
    await refreshStatus()
    liveSnapshotVersion.value = Date.now()
    if (syncMode && isStopNavigationCommand(command)) {
      activeWorkMode.value = 'qa'
      window.localStorage.setItem('ai-glass-work-mode', 'qa')
      await setAssistantMode('qa', { silent: true })
    }
    return true
  } catch (error) {
    if (syncMode && isNavigationCommand(command)) {
      activeWorkMode.value = 'qa'
      window.localStorage.setItem('ai-glass-work-mode', 'qa')
      await setAssistantMode('qa', { silent: true })
    }
    dashboardError.value = error instanceof Error ? error.message : '视觉控制请求失败。'
    return false
  }
}

async function switchWorkMode(mode) {
  // 工作模式切换是前端、Go AI 输入暂停、Python 视觉状态三者的同步动作。
  if (!['navigation', 'qa'].includes(mode)) return
  const previousMode = activeWorkMode.value
  activeWorkMode.value = mode
  window.localStorage.setItem('ai-glass-work-mode', mode)

  if (mode === 'navigation') {
    await setAssistantMode('navigation', { silent: true })
    const ok = await sendVisionCommand({ command: 'start_blind_navigation' }, { syncMode: false })
    if (!ok) {
      activeWorkMode.value = previousMode
      window.localStorage.setItem('ai-glass-work-mode', previousMode)
      await setAssistantMode(previousMode, { silent: true })
    }
    return
  }

  await setAssistantMode('qa', { silent: true })
  if (effectiveVisionState.value?.enabled && effectiveVisionState.value?.connected) {
    await sendVisionCommand({ command: 'stop' }, { syncMode: false })
  }
}

function normalizeVisionCommand(command) {
  // 和 Go 后端保持一致，允许按钮传 kebab-case 或 snake_case。
  return String(command || '').trim().toLowerCase().replace(/-/g, '_')
}

function isNavigationCommand(command) {
  return [
    'start_blind_navigation',
    'blind_navigation',
    'blind',
    'start_blind',
    'start_crossing',
    'crossing',
    'crosswalk',
    'detect_traffic_light',
    'traffic_light',
    'find_object',
    'item_search',
    'search_item',
  ].includes(normalizeVisionCommand(command))
}

function isStopNavigationCommand(command) {
  return ['stop', 'stop_navigation', 'idle', 'chat', 'reset', 'clear'].includes(normalizeVisionCommand(command))
}

function isActiveVisionState(state) {
  const value = String(state || '').trim().toUpperCase()
  return Boolean(value && !['IDLE', 'CHAT'].includes(value))
}

function syncWorkModeFromState({ ai = null, vision = null } = {}) {
  // 后端状态可能由语音命令改变，前端需要根据 ai/vision 事件反向同步按钮状态。
  if (vision && isActiveVisionState(vision.state)) {
    activeWorkMode.value = 'navigation'
    window.localStorage.setItem('ai-glass-work-mode', 'navigation')
    return
  }
  if (ai?.mode === 'navigation' || ai?.inputPaused) {
    activeWorkMode.value = 'navigation'
    window.localStorage.setItem('ai-glass-work-mode', 'navigation')
    return
  }
  if (ai?.mode === 'qa' && !isActiveVisionState(effectiveVisionState.value?.state)) {
    activeWorkMode.value = 'qa'
    window.localStorage.setItem('ai-glass-work-mode', 'qa')
  }
}

function viewerWsUrl() {
  // HTTP/HTTPS 自动映射为 WS/WSS，支持反向代理部署。
  const base = backendHttpBase || window.location.origin
  const url = new URL('/ws/view', base)
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:'
  return url.toString()
}

function connectLiveSocket() {
  // 连接浏览器 viewer WebSocket。关闭后自动 3 秒重连，除非组件卸载。
  liveSocketStopped = false
  if (liveSocket && (liveSocket.readyState === WebSocket.OPEN || liveSocket.readyState === WebSocket.CONNECTING)) {
    return
  }

  const socket = new WebSocket(viewerWsUrl())
  socket.binaryType = 'arraybuffer'
  liveSocket = socket

  socket.addEventListener('message', (event) => {
    if (typeof event.data !== 'string') {
      // 二进制 payload 必须与前面收到的 JSON meta 配对。
      handleBinaryPacket(event.data)
      return
    }

    let payload = null
    try {
      payload = JSON.parse(event.data)
    } catch {
      return
    }

    if (payload?.type === 'server_state') {
      // server_state 是 Go 后端周期心跳，包含设备、质量、视觉摘要。
      liveState.value = payload
      if (payload.vision) {
        liveVisionState.value = payload.vision
        syncWorkModeFromState({ vision: payload.vision })
      }
      lastSyncedAt.value = new Date()
      return
    }
    if (payload?.type === 'ai_state') {
      aiState.value = payload
      if (payload.voiceMode === 'chat' || payload.voiceMode === 'navigation') {
        voiceMode.value = payload.voiceMode
      }
      syncWorkModeFromState({ ai: payload })
      return
    }
    if (payload?.type === 'ai_skill') {
      liveSkillEvents.value = [...liveSkillEvents.value, payload].slice(-60)
      return
    }
    if (payload?.type === 'vision_state') {
      liveVisionState.value = payload
      syncWorkModeFromState({ vision: payload })
      return
    }
    if (payload?.type === 'vision_event') {
      // 视觉事件包含导航控制结果或 guidanceText；导航语音由设备端播放，前端只展示。
      liveVisionEvents.value = [...liveVisionEvents.value, payload].slice(-80)
      if (payload.guidanceText) {
        handleNavigationGuidance(payload.guidanceText, payload.createdAt)
        recordLiveTranscript({
          role: 'assistant',
          text: `[导航] ${payload.guidanceText}`,
          final: true,
          responseId: `vision-${payload.createdAt || Date.now()}`,
        })
      }
      return
    }
    if (payload?.type === 'ai_transcript') {
      // 导盲模式下不显示 AI 助手回复，避免和导航提示混在一起；用户转写仍保留。
      if (activeWorkMode.value !== 'qa' && payload.role !== 'user') {
        return
      }
      recordLiveTranscript(payload)
      return
    }
    if (payload?.type === 'ai_event') {
      handleAIEvent(payload)
      return
    }
    if (payload?.type === 'video' || payload?.type === 'vision_video') {
      // 这里只入队 meta，真正图片 bytes 会紧随其后到达。
      pendingPacketMetas.push(payload)
      liveSnapshotVersion.value = Date.now()
      return
    }
    if (payload?.type === 'audio' || payload?.type === 'ai_audio') {
      pendingPacketMetas.push(payload)
    }
  })

  socket.addEventListener('close', () => {
    pendingPacketMetas.length = 0
    if (liveSocket === socket) {
      liveSocket = null
    }
    if (!liveSocketStopped) {
      liveReconnectTimer = window.setTimeout(connectLiveSocket, 3000)
    }
  })

  socket.addEventListener('error', () => socket.close())
}

function sendGpsUpdate(lat, lng, accuracy) {
  // 通过 HTTP POST /api/gps/update 上报 GPS（WGS-84），Go 转发给 Python worker 做路段匹配与逐段播报。
  // 注意：不走 WebSocket，因为 Go 的 handleViewerWS 是纯接收端；GPS 走 /api/gps/update HTTP 端点。
  fetch(apiUrl('/api/gps/update'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ lat, lng, accuracy }),
  }).catch(() => {})
  gpsFix.value = { lat, lng, accuracy, updatedAt: new Date() }
  gpsStatus.value = `定位中 (${Number(lng).toFixed(6)}, ${Number(lat).toFixed(6)})`
}

function startGpsTracking() {
  // 手机浏览器 navigator.geolocation.watchPosition 持续上报经纬度。
  if (!('geolocation' in navigator)) {
    gpsStatus.value = '当前浏览器不支持定位'
    gpsEnabled.value = false
    return
  }
  gpsStatus.value = '等待定位授权…'
  gpsWatchId = navigator.geolocation.watchPosition(
    (position) => {
      const { latitude, longitude, accuracy } = position.coords
      gpsStatus.value = '定位中'
      sendGpsUpdate(latitude, longitude, accuracy || 0)
    },
    (error) => {
      gpsStatus.value = `定位失败: ${error.message || error.code}`
      gpsEnabled.value = false
      gpsWatchId = null
    },
    { enableHighAccuracy: true, maximumAge: 2000, timeout: 15000 }
  )
}

function stopGpsTracking() {
  if (gpsWatchId != null && 'geolocation' in navigator) {
    navigator.geolocation.clearWatch(gpsWatchId)
  }
  gpsWatchId = null
  gpsFix.value = null
  gpsStatus.value = '未开启'
}

function toggleGpsTracking() {
  if (gpsEnabled.value) {
    stopGpsTracking()
    gpsEnabled.value = false
  } else {
    gpsEnabled.value = true
    startGpsTracking()
  }
}

async function unlockAudio() {
  // 浏览器策略要求用户手势后才能播放音频；点击按钮后恢复 AudioContext。
  ensureAudioGraph()
  if (!audioContext) return
  if (audioContext.state !== 'running') {
    await audioContext.resume()
  }
  audioUnlocked.value = true
  audioStatus.value = queuedAssistantAudio.length > 0
    ? `语音已解锁，正在播放缓存的 ${queuedAssistantAudio.length} 段 AI 音频。`
    : '语音已解锁，AI 回复会在浏览器播放；导盲提示由设备端播放。'
  flushQueuedAssistantAudio()
}

function toggleAssistantAudio() {
  assistantAudioEnabled.value = !assistantAudioEnabled.value
  updateAssistantAudioGain()
  audioStatus.value = assistantAudioEnabled.value ? '语音播报已开启。' : '语音播报已静音。'
}

function ensureAudioGraph() {
  // 创建 Web Audio 图：PCM 分片 -> Gain -> Compressor -> speakers。
  if (audioContext) return
  const AudioContextClass = window.AudioContext || window.webkitAudioContext
  if (!AudioContextClass) {
    audioStatus.value = '当前浏览器不支持 Web Audio，无法播放 AI 语音。'
    return
  }
  audioContext = new AudioContextClass()
  assistantAudioBus = createAssistantAudioBus()
  updateAssistantAudioGain()
}

function createAssistantAudioBus() {
  // 压缩器只用于防止分片音量峰值过大，不改变导航设备端预录语音。
  const gainNode = audioContext.createGain()
  const compressorNode = audioContext.createDynamicsCompressor()
  gainNode.gain.value = assistantAudioEnabled.value ? 1 : 0
  compressorNode.threshold.value = -18
  compressorNode.knee.value = 12
  compressorNode.ratio.value = 2.4
  compressorNode.attack.value = 0.004
  compressorNode.release.value = 0.09
  gainNode.connect(compressorNode)
  compressorNode.connect(audioContext.destination)
  return { input: gainNode, gainNode }
}

function updateAssistantAudioGain() {
  if (assistantAudioBus) {
    assistantAudioBus.gainNode.gain.value = assistantAudioEnabled.value ? 1 : 0
  }
}

function resetAssistantPlayback(statusText = '') {
  // 新一轮回复或用户开口时重建音频总线，清空旧分片，避免残留播放。
  assistantAudioState.nextTime = 0
  queuedAssistantAudio.length = 0
  if (assistantAudioBus) {
    try {
      assistantAudioBus.input.disconnect()
    } catch {
      // Already disconnected.
    }
  }
  if (audioContext) {
    assistantAudioBus = createAssistantAudioBus()
    updateAssistantAudioGain()
  }
  if (statusText) audioStatus.value = statusText
}

function handleAIEvent(payload) {
  // 根据 Go 后端转发的 AI 生命周期事件维护浏览器播放队列。
  if (payload.event === 'input_audio_buffer.speech_started') {
    currentAssistantResponseId = ''
    resetAssistantPlayback('检测到用户说话，已清空待播放 AI 语音。')
    return
  }
  if (payload.event === 'response.created') {
    currentAssistantResponseId = payload.responseId || ''
    resetAssistantPlayback('正在接收新的 AI 语音回复。')
    return
  }
  if (payload.event === 'response.done' || payload.event === 'response.audio.done') {
    if (!payload.responseId || payload.responseId === currentAssistantResponseId) {
      currentAssistantResponseId = ''
    }
  }
}

function handleBinaryPacket(buffer) {
  // 后端约定：每个 video/vision_video/ai_audio 二进制包前都有一条 JSON meta。
  if (pendingPacketMetas.length === 0) return
  const meta = pendingPacketMetas.shift()
  if (meta?.type === 'video' || meta?.type === 'vision_video') {
    updateLiveFrame(meta.type, buffer)
    liveSnapshotVersion.value = Date.now()
    return
  }
  if (meta?.type !== 'ai_audio') return
  aiAudioChunksSeen.value += 1
  playAssistantAudioChunk(buffer, meta)
}

function updateLiveFrame(type, buffer) {
  // 每次替换 object URL 前释放旧 URL，避免长时间直播造成内存增长。
  const nextUrl = URL.createObjectURL(new Blob([buffer], { type: 'image/jpeg' }))
  if (type === 'vision_video') {
    if (latestVisionFrameObjectUrl) URL.revokeObjectURL(latestVisionFrameObjectUrl)
    latestVisionFrameObjectUrl = nextUrl
    liveVisionFrameUrl.value = nextUrl
    return
  }

  if (latestRawFrameObjectUrl) URL.revokeObjectURL(latestRawFrameObjectUrl)
  latestRawFrameObjectUrl = nextUrl
  liveRawFrameUrl.value = nextUrl
}

function revokeLiveFrameUrls() {
  if (latestRawFrameObjectUrl) URL.revokeObjectURL(latestRawFrameObjectUrl)
  if (latestVisionFrameObjectUrl) URL.revokeObjectURL(latestVisionFrameObjectUrl)
  latestRawFrameObjectUrl = ''
  latestVisionFrameObjectUrl = ''
  liveRawFrameUrl.value = ''
  liveVisionFrameUrl.value = ''
}

function playAssistantAudioChunk(buffer, meta) {
  // 导盲模式下设备端负责提示音，浏览器不播放 AI 回复语音。
  if (activeWorkMode.value !== 'qa') {
    audioStatus.value = '导盲模式中，AI 回复语音已暂停。'
    return
  }
  if (currentAssistantResponseId && meta.responseId && meta.responseId !== currentAssistantResponseId) return
  if (!currentAssistantResponseId && meta.responseId) currentAssistantResponseId = meta.responseId

  if (!audioContext || audioContext.state !== 'running') {
    queueAssistantAudio(buffer, meta)
    audioStatus.value = `已收到 ${aiAudioChunksSeen.value} 段 AI 音频，点击“解锁 AI 语音”后播放。`
    return
  }
  if (!assistantAudioEnabled.value) {
    audioStatus.value = 'AI 语音已静音，音频包仍在接收。'
    return
  }
  scheduleAssistantAudio(buffer, meta)
}

function queueAssistantAudio(buffer, meta) {
  // 未解锁音频时缓存少量分片；超过上限丢最旧，避免内存无限增长。
  queuedAssistantAudio.push({ buffer: buffer.slice(0), meta: { ...meta } })
  while (queuedAssistantAudio.length > maxQueuedAssistantAudioChunks) queuedAssistantAudio.shift()
}

function flushQueuedAssistantAudio() {
  if (!audioContext || audioContext.state !== 'running' || !assistantAudioEnabled.value) return
  while (queuedAssistantAudio.length > 0) {
    const item = queuedAssistantAudio.shift()
    scheduleAssistantAudio(item.buffer, item.meta)
  }
}

function scheduleAssistantAudio(buffer, meta) {
  // Go 后端发来的 ai_audio 是 PCM16 little-endian，采样率在 meta.sampleRate 中。
  if (!audioContext || !assistantAudioBus) return
  const pcm = new Int16Array(buffer)
  if (pcm.length === 0) return
  const sampleRate = Number(meta.sampleRate || 24000)
  const channel = pcm16ToFloat32(pcm)
  applyChunkEnvelope(channel)
  const audioBuffer = audioContext.createBuffer(1, channel.length, sampleRate)
  audioBuffer.copyToChannel(channel, 0)
  scheduleAudioBuffer(audioBuffer)
  audioStatus.value = `正在播放 AI 语音：${meta.bytes || buffer.byteLength} B / ${sampleRate} Hz。`
}

function scheduleAudioBuffer(audioBuffer) {
  // 按 AudioContext 时间轴连续排程，desiredLead 给网络/调度抖动留一点缓冲。
  const source = audioContext.createBufferSource()
  source.buffer = audioBuffer
  source.connect(assistantAudioBus.input)
  const now = audioContext.currentTime
  if (assistantAudioState.nextTime < now + assistantAudioState.desiredLead) {
    assistantAudioState.nextTime = now + assistantAudioState.desiredLead
  }
  source.start(assistantAudioState.nextTime)
  assistantAudioState.nextTime += audioBuffer.duration
}

function pcm16ToFloat32(samples) {
  const channel = new Float32Array(samples.length)
  for (let index = 0; index < samples.length; index += 1) {
    channel[index] = Math.max(-1, Math.min(1, samples[index] / 32768))
  }
  return channel
}

function applyChunkEnvelope(channel) {
  // 给每个分片首尾做很短淡入淡出，降低 PCM 分片边界的爆音。
  const fadeSamples = Math.min(audioFadeSamples, Math.floor(channel.length / 8))
  for (let index = 0; index < fadeSamples; index += 1) {
    const gain = (index + 1) / (fadeSamples + 1)
    channel[index] *= gain
    channel[channel.length - 1 - index] *= gain
  }
}

function handleNavigationGuidance(text, createdAt = '') {
  // 导航提示只更新状态文案；实际语音由 Go 服务端匹配预录 wav 并下发到设备。
  const guidance = cleanSpeechText(text)
  if (!guidance) return

  const key = `${createdAt || Date.now()}-${guidance}`
  if (key === lastNavigationGuidanceKey) return

  lastNavigationGuidanceKey = key
  audioStatus.value = `导航提示已交给设备端：${guidance}`
}

function cleanSpeechText(text) {
  return String(text || '')
    .replace(/^\[导航\]\s*/, '')
    .replace(/[.。…]+$/g, '')
    .trim()
}

function recordLiveTranscript(payload) {
  // 合并助手 partial/final，保持实时转写列表稳定，不让同一回复产生大量碎片。
  const text = String(payload?.text || '').trim()
  if (!text) return

  const role = payload.role === 'user' ? 'user' : 'assistant'
  const final = Boolean(payload.final)
  const responseId = payload.responseId || ''
  const now = new Date().toISOString()
  const items = [...liveTranscripts.value]

  if (role === 'assistant' && !final) {
    const draftIndex = findLastTranscriptIndex(items, (item) => item.role === 'assistant' && !item.final)
    if (draftIndex >= 0) {
      const draft = items[draftIndex]
      items[draftIndex] = { ...draft, text: `${draft.text}${text}`, responseId: draft.responseId || responseId, createdAt: now }
    } else {
      items.push(createTranscriptItem(role, text, final, responseId, now))
    }
    liveTranscripts.value = items.slice(-24)
    return
  }

  if (role === 'assistant' && final) {
    let targetIndex = responseId
      ? findLastTranscriptIndex(items, (item) => item.role === 'assistant' && item.responseId === responseId)
      : -1
    if (targetIndex < 0) {
      targetIndex = findLastTranscriptIndex(items, (item) => item.role === 'assistant' && !item.final)
    }
    if (targetIndex >= 0) {
      items[targetIndex] = { ...items[targetIndex], text, final: true, responseId: responseId || items[targetIndex].responseId, createdAt: now }
    } else {
      items.push(createTranscriptItem(role, text, final, responseId, now))
    }
    liveTranscripts.value = items.slice(-24)
    return
  }

  items.push(createTranscriptItem(role, text, final, responseId, now))
  liveTranscripts.value = items.slice(-24)
}

function createTranscriptItem(role, text, final, responseId, createdAt) {
  return {
    id: `${createdAt}-${Math.random().toString(36).slice(2)}`,
    role,
    text,
    final,
    responseId,
    createdAt,
  }
}

function findLastTranscriptIndex(items, predicate) {
  for (let index = items.length - 1; index >= 0; index -= 1) {
    if (predicate(items[index])) return index
  }
  return -1
}

function numberValue(value, digits = 0) {
  return Number(value || 0).toFixed(digits)
}

watch(activeWorkMode, (mode) => {
  // 切入导盲模式时立即停止浏览器 AI 语音，避免覆盖设备导航提示。
  if (mode === 'navigation') {
    resetAssistantPlayback('导盲模式中，AI 回复语音已暂停。')
  }
})

onMounted(() => {
  // 启动时同步后端模式、建立 WebSocket，并开启轻量状态轮询作为兜底。
  setAssistantMode(activeWorkMode.value, { silent: true })
  connectLiveSocket()
  refreshStatus()
  refreshTimer = window.setInterval(() => refreshStatus(), 7000)
  snapshotTimer = window.setInterval(() => {
    if (liveState.value?.deviceConnected) liveSnapshotVersion.value = Date.now()
  }, 5000)
  window.addEventListener('hashchange', onHashChange)
})

onBeforeUnmount(() => {
  // 组件卸载时关闭定时器、WebSocket 和 object URL，避免热更新/页面跳转泄漏。
  if (refreshTimer) window.clearInterval(refreshTimer)
  if (snapshotTimer) window.clearInterval(snapshotTimer)
  if (liveReconnectTimer) window.clearTimeout(liveReconnectTimer)
  window.removeEventListener('hashchange', onHashChange)
  liveSocketStopped = true
  if (liveSocket) {
    liveSocket.close()
    liveSocket = null
  }
  stopGpsTracking()
  revokeLiveFrameUrls()
})
</script>
