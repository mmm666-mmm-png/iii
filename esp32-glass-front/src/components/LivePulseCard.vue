<template>
  <section class="surface-panel live-surface">
    <div class="surface-head">
      <div>
        <h2>{{ pageTitle }}</h2>
        <p>{{ pageDescription }}</p>
      </div>
      <a-space wrap>
        <a-tag :color="liveState?.deviceConnected ? 'success' : 'default'">
          {{ liveState?.deviceConnected ? '设备在线' : '设备离线' }}
        </a-tag>
        <a-tag :color="activeMode === 'navigation' ? 'processing' : 'success'">
          {{ activeModeLabel }}
        </a-tag>
        <a-tag :color="aiTagColor">AI {{ aiStateLabel }}</a-tag>
      </a-space>
    </div>

    <div v-if="showPreviewSection" class="live-grid" :class="{ 'live-grid-single': !showPreview }">
      <div v-if="showPreview" class="live-preview-panel">
        <div class="preview-shell classic-preview">
          <a-image
            v-if="snapshotSrc"
            :src="snapshotSrc"
            :preview="false"
            class="preview-image"
            :class="{ 'preview-image-rotated': snapshotRotated }"
            alt="直播快照"
          />
          <a-empty v-else description="暂无实时画面" />
        </div>

        <section
          v-if="showConsolesUnderPreview"
          class="live-console-wrap live-console-grid live-console-under-preview"
          :class="consoleGridClass"
        >
          <div v-if="showTranscript" class="live-transcript-panel">
            <div class="live-transcript-head">
              <strong>实时转写</strong>
              <a-tag :color="liveTranscripts.length > 0 ? 'processing' : 'default'">{{ liveTranscripts.length }} lines</a-tag>
            </div>
            <div ref="transcriptRef" class="live-transcript-list">
              <div v-if="transcriptItems.length === 0" class="live-transcript-empty">
                暂无实时转写
              </div>
              <template v-else>
                <article
                  v-for="item in transcriptItems"
                  :key="item.id"
                  class="live-transcript-item"
                  :class="`live-transcript-item-${item.role}`"
                >
                  <div class="live-transcript-item-head">
                    <span>{{ transcriptRoleLabel(item.role) }}</span>
                    <time>{{ formatTerminalTime(item.createdAt) }}</time>
                  </div>
                  <p>{{ item.text }}</p>
                </article>
              </template>
            </div>
          </div>

          <div v-if="showVisionEvents" class="vision-event-panel">
            <div class="live-transcript-head">
              <strong>导航事件</strong>
              <a-tag :color="visionEvents.length > 0 ? 'processing' : 'default'">{{ visionEvents.length }} events</a-tag>
            </div>
            <div class="vision-event-list">
              <div v-if="visionEvents.length === 0" class="live-transcript-empty">暂无导航事件</div>
              <template v-else>
                <article v-for="event in recentVisionEvents" :key="visionEventKey(event)" class="vision-event-item">
                  <div>
                    <strong>{{ visionEventTitle(event) }}</strong>
                    <span>{{ formatTerminalTime(event.createdAt) }}</span>
                  </div>
                  <p>{{ event.guidanceText || event.error || event.state || '已记录' }}</p>
                </article>
              </template>
            </div>
          </div>
        </section>
      </div>

      <div class="live-side-panel">
        <div v-if="showModeSelector" class="mode-switch-panel">
          <div class="mode-switch-head">
            <div>
              <strong>工作模式</strong>
              <span>导盲和回答问题二选一，主画面保持原始实时画面。</span>
            </div>
            <a-tag>{{ activeModeLabel }}</a-tag>
          </div>

          <div class="mode-switch-actions">
            <button
              type="button"
              class="mode-choice"
              :class="{ 'mode-choice-active': activeMode === 'navigation' }"
              @click="changeMode('navigation')"
            >
              <CompassOutlined class="mode-choice-icon" />
              <span>导盲</span>
              <small>盲道导航、过马路、红绿灯和找物品</small>
            </button>
            <button
              type="button"
              class="mode-choice"
              :class="{ 'mode-choice-active': activeMode === 'qa' }"
              @click="changeMode('qa')"
            >
              <MessageOutlined class="mode-choice-icon" />
              <span>回答问题</span>
              <small>实时转写、AI 回复和语音播放</small>
            </button>
          </div>
        </div>

        <div v-if="showMetrics" class="metric-strip">
          <div class="metric-cell">
            <span>观看端</span>
            <strong>{{ viewerValue }}</strong>
          </div>
          <div class="metric-cell">
            <span>视频帧</span>
            <strong>{{ liveState?.videoFramesSeen ?? 0 }}</strong>
          </div>
          <div class="metric-cell">
            <span>音频块</span>
            <strong>{{ liveState?.audioChunksSeen ?? 0 }}</strong>
          </div>
          <div class="metric-cell">
            <span>AI 桥接</span>
            <strong>{{ aiStateLabel }}</strong>
          </div>
        </div>

        <div v-if="showNavigation" class="navigation-console">
          <div class="navigation-console-head">
            <div>
              <strong>导航控制</strong>
              <span>{{ visionStatusLabel }}</span>
            </div>
            <a-tag :color="visionTagColor">{{ visionModeLabel }}</a-tag>
          </div>

          <div class="navigation-actions">
            <a-button type="primary" size="small" @click="emitVisionCommand('start_blind_navigation')">
              <template #icon><CompassOutlined /></template>
              盲道
            </a-button>
            <a-button size="small" @click="emitVisionCommand('start_crossing')">
              <template #icon><AimOutlined /></template>
              过马路
            </a-button>
            <a-button size="small" @click="emitVisionCommand('detect_traffic_light')">
              <template #icon><ThunderboltOutlined /></template>
              红绿灯
            </a-button>
            <a-button danger size="small" @click="emitVisionCommand('stop')">
              <template #icon><PauseCircleOutlined /></template>
              停止
            </a-button>
          </div>

          <div class="navigation-search-row">
            <a-input
              v-model:value="targetText"
              size="small"
              placeholder="物品名称"
              @press-enter="findObject"
            />
            <a-button size="small" @click="findObject">
              <template #icon><SearchOutlined /></template>
              查找
            </a-button>
          </div>

          <div class="navigation-route-panel">
            <div class="navigation-route-head">
              <strong>高德导航</strong>
              <a-space wrap>
                <a-tag :color="navigationStatusColor">{{ navigationStatusLabel }}</a-tag>
                <a-tag :color="navigationSourceColor">{{ navigationSourceLabel }}</a-tag>
              </a-space>
            </div>
            <div class="navigation-route-fields">
              <a-input v-model:value="originText" size="small" placeholder="起点，例如：枣庄学院" />
              <a-input
                v-model:value="destinationText"
                size="small"
                placeholder="终点，例如：万达广场"
                @press-enter="planNavigationRoute"
              />
            </div>
            <div class="navigation-route-actions">
              <a-button type="primary" size="small" :loading="navigationBusy" @click="planNavigationRoute">
                <template #icon><EnvironmentOutlined /></template>
                开始导航
              </a-button>
              <a-button size="small" :loading="navigationVoiceBusy" @click="submitNavigationVoice">
                <template #icon><SoundOutlined /></template>
                语音解析
              </a-button>
              <a-button size="small" :loading="navigationBroadcastBusy" @click="broadcastRoute">
                <template #icon><SoundOutlined /></template>
                播报路线
              </a-button>
              <a-button v-if="navigationLaunchUri" size="small" @click="openNavigationUri">
                <template #icon><EnvironmentOutlined /></template>
                打开高德
              </a-button>
            </div>
            <div class="navigation-voice-row">
              <a-input
                v-model:value="voiceCommandText"
                size="small"
                placeholder="语音口令，例如：从学院去万达广场"
                @press-enter="submitNavigationVoice"
              />
            </div>
            <p v-if="navigationError" class="navigation-error">{{ navigationError }}</p>
            <div v-if="navigationPlan" class="navigation-route-summary">
              <div class="navigation-route-summary-head">
                <strong>路线结果</strong>
                <span>{{ navigationPlan.planning_result?.best_route?.name || navigationPlan.response_text || '-' }}</span>
              </div>
              <div v-if="navigationPlan.planning_result?.best_route" class="navigation-route-metrics">
                <div>
                  <span>距离</span>
                  <strong>{{ formatDistance(navigationPlan.planning_result.best_route.total_distance_meters) }}</strong>
                </div>
                <div>
                  <span>时间</span>
                  <strong>{{ formatDuration(navigationPlan.planning_result.best_route.total_duration_seconds) }}</strong>
                </div>
                <div>
                  <span>得分</span>
                  <strong>{{ numberValue(navigationPlan.planning_result.best_route.score, 1) }}</strong>
                </div>
                <div>
                  <span>盲道</span>
                  <strong>{{ numberValue(navigationPlan.planning_result.best_route.blind_path_coverage_pct, 1) }}%</strong>
                </div>
              </div>
              <p class="navigation-route-source">
                数据源：{{ navigationPlan.planning_result?.data_sources?.amap ? '高德API' : '本地兜底' }}
                <span v-if="navigationPlan.planning_result?.best_route?.source"> / {{ navigationPlan.planning_result.best_route.source }}</span>
              </p>
              <p>{{ navigationPlan.response_text || navigationPlan.planning_result?.broadcast_text }}</p>
            </div>
            <div v-if="routeSteps.length" class="navigation-route-steps">
              <div class="navigation-route-summary-head">
                <strong>路线步骤</strong>
                <span>共 {{ routeSteps.length }} 步</span>
              </div>
              <ol>
                <li v-for="step in routeSteps" :key="step.index">
                  <strong>{{ step.index }}</strong>
                  <span class="navigation-step-instruction">{{ step.instruction }}</span>
                  <span v-if="step.distance_meters" class="navigation-step-distance">{{ formatDistance(step.distance_meters) }}</span>
                </li>
              </ol>
            </div>
            <p v-else-if="navigationVoiceResult?.response_text" class="navigation-route-voice-result">
              {{ navigationVoiceResult.response_text }}
            </p>
          </div>

          <p v-if="visionState?.lastGuidance">{{ visionState.lastGuidance }}</p>
          <p v-if="!liveState?.deviceConnected" class="navigation-error">设备当前离线，导航模式已切换，但不会收到新的实时画面。</p>
          <p v-else-if="visionState?.lastError" class="navigation-error">{{ visionState.lastError }}</p>
        </div>

        <div v-if="showQuality" class="quality-panel">
          <div class="quality-panel-head">
            <strong>链路质量</strong>
            <a-tag>{{ qualityLabel }}</a-tag>
          </div>
          <div class="quality-grid">
            <div class="quality-cell">
              <span>FPS</span>
              <strong>{{ numberValue(streamQuality?.videoFps, 1) }}</strong>
            </div>
            <div class="quality-cell">
              <span>音频/s</span>
              <strong>{{ numberValue(streamQuality?.audioChunksPerSecond, 1) }}</strong>
            </div>
            <div class="quality-cell">
              <span>视频缺口</span>
              <strong>{{ streamQuality?.videoSeqGaps ?? 0 }}</strong>
            </div>
            <div class="quality-cell">
              <span>未完整帧</span>
              <strong>{{ streamQuality?.videoIncompleteFrames ?? 0 }}</strong>
            </div>
          </div>
        </div>

        <div v-if="showAudio" class="audio-control-panel">
          <div class="audio-control-actions">
            <a-button
              type="primary"
              size="small"
              :disabled="audioUnlocked"
              @click="$emit('unlock-audio')"
            >
              {{ audioUnlocked ? 'AI 语音已解锁' : '解锁 AI 语音' }}
            </a-button>
            <a-button size="small" @click="$emit('toggle-assistant-audio')">
              {{ assistantAudioEnabled ? 'AI 语音开启' : 'AI 语音静音' }}
            </a-button>
            <a-tag :color="aiAudioChunksSeen > 0 ? 'processing' : 'default'">
              AI 音频 {{ aiAudioChunksSeen }}
            </a-tag>
          </div>
          <p>{{ audioStatus }}</p>
        </div>
      </div>
    </div>

    <section v-if="showInfo" class="live-info-wrap">
      <div class="info-table">
        <div class="info-row">
          <span>设备</span>
          <strong>{{ deviceLabel }}</strong>
        </div>
        <div class="info-row">
          <span>最近在线</span>
          <strong>{{ lastSeenLabel }}</strong>
        </div>
        <div class="info-row">
          <span>AI 模型</span>
          <strong>{{ aiModelLabel }}</strong>
        </div>
        <div class="info-row">
          <span>服务技能</span>
          <strong>{{ skillLabel }}</strong>
        </div>
        <div class="info-row">
          <span>运行说明</span>
          <strong>{{ aiNote }}</strong>
        </div>
      </div>
    </section>

    <section v-if="showConsoles && !showConsolesUnderPreview" class="live-console-wrap live-console-grid" :class="consoleGridClass">
      <div v-if="showTranscript" class="live-transcript-panel">
        <div class="live-transcript-head">
          <strong>实时转写</strong>
          <a-tag :color="liveTranscripts.length > 0 ? 'processing' : 'default'">{{ liveTranscripts.length }} lines</a-tag>
        </div>
        <div ref="transcriptRef" class="live-transcript-list">
          <div v-if="transcriptItems.length === 0" class="live-transcript-empty">
            暂无实时转写
          </div>
          <template v-else>
            <article
              v-for="item in transcriptItems"
              :key="item.id"
              class="live-transcript-item"
              :class="`live-transcript-item-${item.role}`"
            >
              <div class="live-transcript-item-head">
                <span>{{ transcriptRoleLabel(item.role) }}</span>
                <time>{{ formatTerminalTime(item.createdAt) }}</time>
              </div>
              <p>{{ item.text }}</p>
            </article>
          </template>
        </div>
      </div>

      <div v-if="showSkillConsole" class="skill-terminal-panel live-skill-terminal-panel">
        <div class="skill-terminal-head">
          <strong>Live Skill Console</strong>
          <a-tag :color="skillEvents.length > 0 ? 'processing' : 'default'">{{ skillEvents.length }} calls</a-tag>
        </div>
        <div ref="terminalRef" class="skill-terminal live-skill-terminal">
          <div
            v-for="line in terminalLines"
            :key="line.id"
            class="skill-terminal-line"
            :class="`skill-terminal-line-${line.kind}`"
          >
            {{ line.text }}
          </div>
        </div>
      </div>

      <div v-if="showVisionEvents" class="vision-event-panel">
        <div class="live-transcript-head">
          <strong>导航事件</strong>
          <a-tag :color="visionEvents.length > 0 ? 'processing' : 'default'">{{ visionEvents.length }} events</a-tag>
        </div>
        <div class="vision-event-list">
          <div v-if="visionEvents.length === 0" class="live-transcript-empty">暂无导航事件</div>
          <template v-else>
            <article v-for="event in recentVisionEvents" :key="visionEventKey(event)" class="vision-event-item">
              <div>
                <strong>{{ visionEventTitle(event) }}</strong>
                <span>{{ formatTerminalTime(event.createdAt) }}</span>
              </div>
              <p>{{ event.guidanceText || event.error || event.state || '已记录' }}</p>
            </article>
          </template>
        </div>
      </div>
    </section>

    <a-alert
      v-if="dashboardError"
      :message="dashboardError"
      type="error"
      show-icon
      class="inline-alert"
    />
  </section>
</template>

<script setup>
// LivePulseCard 只负责展示和发出用户操作事件，不直接请求后端。
// 后端状态、音频播放和视觉控制都由 App.vue 统一处理。
import { computed, onBeforeUnmount, onMounted, nextTick, ref, watch } from 'vue'
import {
  AimOutlined,
  EnvironmentOutlined,
  CompassOutlined,
  MessageOutlined,
  PauseCircleOutlined,
  SearchOutlined,
  SoundOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons-vue'

import { formatDateTime } from '../utils/format'

const props = defineProps({
  // Go 后端 /api/status 或 server_state 的汇总状态。
  liveState: {
    type: Object,
    default: null,
  },
  page: {
    type: String,
    default: 'live',
  },
  aiState: {
    type: Object,
    default: null,
  },
  visionState: {
    type: Object,
    default: null,
  },
  backendHttpBase: {
    type: String,
    default: '',
  },
  streamQuality: {
    type: Object,
    default: null,
  },
  snapshotSrc: {
    type: String,
    default: '',
  },
  snapshotRotated: {
    type: Boolean,
    default: true,
  },
  skillEvents: {
    type: Array,
    default: () => [],
  },
  visionEvents: {
    type: Array,
    default: () => [],
  },
  liveTranscripts: {
    type: Array,
    default: () => [],
  },
  audioUnlocked: {
    type: Boolean,
    default: false,
  },
  assistantAudioEnabled: {
    type: Boolean,
    default: true,
  },
  audioStatus: {
    type: String,
    default: '',
  },
  aiAudioChunksSeen: {
    type: Number,
    default: 0,
  },
  dashboardError: {
    type: String,
    default: '',
  },
  activeMode: {
    type: String,
    default: 'qa',
  },
})

const emit = defineEmits(['unlock-audio', 'toggle-assistant-audio', 'vision-command', 'mode-change', 'record-transcript'])

const deviceLabel = computed(() => {
  // 设备 hello 中的 deviceId/firmware 用于确认当前连接的是哪块板子。
  const device = props.liveState?.device
  if (!device) {
    return '等待设备握手'
  }
  return `${device.deviceId || '未知设备'} / ${device.firmware || '未知固件'}`
})

const lastSeenLabel = computed(() => formatDateTime(props.liveState?.lastDeviceSeen))
const viewerValue = computed(() => props.liveState?.viewerCount ?? 0)
const terminalRef = ref(null)
const transcriptRef = ref(null)
const targetText = ref('')
const originText = ref('')
const destinationText = ref('')
const voiceCommandText = ref('')
const navigationStatus = ref(null)
const navigationPlan = ref(null)
const navigationVoiceResult = ref(null)
const navigationBusy = ref(false)
const navigationVoiceBusy = ref(false)
const navigationBroadcastBusy = ref(false)
const navigationBroadcastResult = ref(null)
const navigationError = ref('')
const lastNavigationSpeechKey = ref('')
const navigationLaunchUri = computed(() => {
  return (
    navigationPlan.value?.planning_result?.navigation?.uri
    || navigationVoiceResult.value?.planning_result?.navigation?.uri
    || ''
  )
})
const routeSteps = computed(() => {
  const planning = navigationPlan.value?.planning_result
    || navigationVoiceResult.value?.planning_result
    || navigationBroadcastResult.value?.planning_result
  const steps = planning?.turn_by_turn
    || navigationBroadcastResult.value?.turn_by_turn
  return Array.isArray(steps) ? steps : []
})
const lastAutoNavigationTranscriptId = ref('')
let navigationStatusTimer = null
const transcriptItems = computed(() => props.liveTranscripts.slice(-10))
const recentVisionEvents = computed(() => props.visionEvents.slice(-8).reverse())
const pageTitle = computed(() => {
  // 页面标题由父级 page 控制，同一个组件复用在实时/诊断视图。
  const titles = {
    live: '实时交互',
    navigation: '导盲控制',
    ai: 'AI 日志',
    quality: '链路诊断',
  }
  return titles[props.page] || '实时交互'
})
const pageDescription = computed(() => {
  const descriptions = {
    live: '实时画面与实时转写在同一页，右侧选择导盲或回答问题。',
    navigation: '控制盲道导航、过马路、红绿灯检测和物品查找。',
    ai: '查看 AI 回复、技能调用和语音播放状态。',
    quality: '观察 UDP 分片、帧率、音频块速率和链路缺口。',
  }
  return descriptions[props.page] || descriptions.live
})
const showPreview = computed(() => ['live', 'navigation', 'quality'].includes(props.page))
const showMetrics = computed(() => props.page === 'live')
const showInfo = computed(() => props.page === 'live')
const showModeSelector = computed(() => props.page === 'live')
const showNavigation = computed(() => props.page === 'navigation' || (props.page === 'live' && props.activeMode === 'navigation'))
const showQuality = computed(() => props.page === 'quality')
const showAudio = computed(() => props.page === 'ai' || props.page === 'live')
const showTranscript = computed(() => ['live', 'ai'].includes(props.page))
const showSkillConsole = computed(() => props.page === 'ai')
const showVisionEvents = computed(() => props.page === 'navigation' || (props.page === 'live' && props.activeMode === 'navigation'))
const showPreviewSection = computed(() => showPreview.value || showMetrics.value || showInfo.value || showModeSelector.value || showNavigation.value || showQuality.value || showAudio.value)
const showConsoles = computed(() => showTranscript.value || showSkillConsole.value || showVisionEvents.value)
// 实时页把日志放在预览下方，诊断/AI 页则使用独立区域。
const showConsolesUnderPreview = computed(() => props.page === 'live' && showConsoles.value)
const consolePanelCount = computed(() => [showTranscript.value, showSkillConsole.value, showVisionEvents.value].filter(Boolean).length)
const consoleGridClass = computed(() => `console-panel-count-${consolePanelCount.value}`)

const terminalLines = computed(() => {
  // 把 ai_skill 事件格式化成类似终端的行，便于查看技能参数、摘要和结果。
  if (props.skillEvents.length === 0) {
    return [
      {
        id: 'empty',
        kind: 'result',
        text: '[--:--:--] waiting for skill calls...',
      },
    ]
  }

  return props.skillEvents.flatMap((event, index) => {
    const failed = event.status === 'failed'
    const duration = Number(event.durationMs || 0)
    const durationText = duration > 0 ? ` (${duration}ms)` : ''
    const result = compactJSON(event.result)
    const lines = [
      {
        id: `${index}-cmd`,
        kind: 'command',
        text: `[${formatTerminalTime(event.createdAt)}] $ call ${event.name || 'skill'} ${compactJSON(event.args)}`,
      },
      {
        id: `${index}-summary`,
        kind: failed ? 'error' : 'success',
        text: `${failed ? 'failed' : 'ok'} ${event.summary || event.error || '技能调用已记录。'}${durationText}`,
      },
    ]

    if (result !== '{}') {
      lines.push({
        id: `${index}-result`,
        kind: 'result',
        text: `result ${result}`,
      })
    }
    return lines
  })
})

watch(
  () => terminalLines.value.map((line) => line.text).join('\n'),
  async () => {
    // 技能日志新增时自动滚到底部。
    await nextTick()
    if (terminalRef.value) {
      terminalRef.value.scrollTop = terminalRef.value.scrollHeight
    }
  },
  { flush: 'post', immediate: true },
)

watch(
  () => transcriptItems.value.map((item) => `${item.role}:${item.text}`).join('\n'),
  async () => {
    // 实时转写新增时自动滚到底部。
    await nextTick()
    if (transcriptRef.value) {
      transcriptRef.value.scrollTop = transcriptRef.value.scrollHeight
    }
  },
  { flush: 'post', immediate: true },
)

onMounted(() => {
  lastAutoNavigationTranscriptId.value = findLatestFinalUserTranscript(transcriptItems.value)?.id || ''
  refreshNavigationStatus()
  navigationStatusTimer = window.setInterval(refreshNavigationStatus, 5000)
})

onBeforeUnmount(() => {
  if (navigationStatusTimer) {
    window.clearInterval(navigationStatusTimer)
    navigationStatusTimer = null
  }
})

const aiStateLabel = computed(() => {
  if (!props.aiState?.enabled) {
    return '未启用'
  }
  return props.aiState.connected ? '在线' : '离线'
})

const activeModeLabel = computed(() => props.activeMode === 'navigation' ? '导盲模式' : '回答问题')

const aiModelLabel = computed(() => {
  if (!props.aiState?.enabled) {
    return '未配置'
  }
  return props.aiState.model || '-'
})

const skillLabel = computed(() => {
  const skills = props.aiState?.skills || []
  if (skills.length === 0) {
    return '未加载'
  }
  return skills.map((item) => item.displayName || item.name).join(' / ')
})

const aiTagColor = computed(() => {
  if (!props.aiState?.enabled) {
    return 'default'
  }
  return props.aiState.connected ? 'processing' : 'warning'
})

const aiNote = computed(() => {
  if (!props.aiState?.enabled) {
    return '未配置 DashScope 桥接'
  }
  if (props.aiState.connected) {
    return `${props.aiState.model || '-'} / ${props.aiState.voice || '-'}`
  }
  return props.aiState.lastError || '等待建立连接'
})

const visionModeLabel = computed(() => props.visionState?.state || 'IDLE')
const visionStatusLabel = computed(() => {
  // 视觉 worker 未配置、未连接、已就绪三种状态分别显示。
  if (!props.visionState?.enabled) {
    return '未配置 Python worker'
  }
  if (!props.visionState.connected) {
    return props.visionState.lastError || '等待 worker 连接'
  }
  return props.visionState.ready ? '视觉 worker 已就绪' : 'worker 连接中'
})

const visionTagColor = computed(() => {
  if (!props.visionState?.enabled) {
    return 'default'
  }
  if (!props.visionState.connected) {
    return 'warning'
  }
  return props.visionState.state && props.visionState.state !== 'IDLE' && props.visionState.state !== 'CHAT'
    ? 'processing'
    : 'success'
})

const qualityLabel = computed(() => {
  // 质量标签把视频缺口、音频缺口和未完整帧合并成一个简短状态。
  const stats = props.streamQuality
  if (!stats?.datagramsSeen) {
    return '等待数据'
  }
  const gaps = Number(stats.videoSeqGaps || 0) + Number(stats.audioSeqGaps || 0) + Number(stats.videoIncompleteFrames || 0)
  return gaps > 0 ? `${gaps} gaps` : '稳定'
})

const navigationStatusLabel = computed(() => {
  if (navigationBusy.value) {
    return '规划中'
  }
  if (navigationVoiceBusy.value) {
    return '解析中'
  }
  if (!navigationStatus.value?.success) {
    return '未连接'
  }
  return navigationStatus.value.data?.status || '在线'
})

const navigationStatusColor = computed(() => {
  if (navigationBusy.value || navigationVoiceBusy.value) {
    return 'processing'
  }
  if (!navigationStatus.value?.success) {
    return 'warning'
  }
  return 'success'
})

const navigationSourceLabel = computed(() => {
  const planning = navigationPlan.value?.planning_result || navigationVoiceResult.value?.planning_result
  const amapConfigured = Boolean(navigationStatus.value?.data?.amap_configured)
  if (planning?.data_sources?.amap) {
    return 'Amap API'
  }
  if (planning) {
    return amapConfigured ? 'Amap fallback' : 'Local fallback'
  }
  if (!navigationStatus.value?.success) {
    return 'Amap unknown'
  }
  if (amapConfigured) {
    return 'Amap ready'
  }
  return 'Amap not configured'
})

const navigationSourceColor = computed(() => {
  const planning = navigationPlan.value?.planning_result || navigationVoiceResult.value?.planning_result
  const amapConfigured = Boolean(navigationStatus.value?.data?.amap_configured)
  if (planning?.data_sources?.amap) {
    return 'processing'
  }
  if (planning) {
    return amapConfigured ? 'warning' : 'default'
  }
  if (!navigationStatus.value?.success) {
    return 'warning'
  }
  return amapConfigured ? 'success' : 'default'
})

function emitVisionCommand(command, target = '') {
  // 只抛事件给父组件；父组件负责同步 AI 模式和调用后端。
  emit('vision-command', { command, target })
}

function changeMode(mode) {
  emit('mode-change', mode)
}

function findObject() {
  // 空目标不触发请求，避免 Python worker 进入无意义寻物状态。
  const target = targetText.value.trim()
  if (!target) {
    return
  }
  emitVisionCommand('find_object', target)
}

async function requestJSON(path, options = {}) {
  const base = props.backendHttpBase || window.location.origin
  const url = new URL(path, base).toString()
  const response = await fetch(url, {
    cache: 'no-store',
    ...options,
  })
  const body = await response.json().catch(() => ({}))
  if (!response.ok) {
    throw new Error(body.detail || body.error || `Request failed: ${response.status}`)
  }
  return body
}

async function refreshNavigationStatus() {
  try {
    navigationStatus.value = await requestJSON('/api/navigation/status')
  } catch {
    navigationStatus.value = null
  }
}

function syncNavigationFields(payload = {}) {
  if (payload.origin_desc && payload.origin_desc !== '当前位置') {
    originText.value = payload.origin_desc
  }
  if (payload.destination_desc) {
    destinationText.value = payload.destination_desc
  }
}

function findLatestFinalUserTranscript(items) {
  for (let index = items.length - 1; index >= 0; index -= 1) {
    const item = items[index]
    if (item.role === 'user' && item.final && item.text?.trim()) {
      return item
    }
  }
  return null
}

function buildNavigationVoiceText() {
  const origin = originText.value.trim()
  const destination = destinationText.value.trim()
  if (origin && destination) {
    return `从${origin}到${destination}导航`
  }
  if (destination) {
    return `导航到${destination}`
  }
  return ''
}

async function planNavigationRoute() {
  const origin = originText.value.trim()
  const destination = destinationText.value.trim()
  if (!origin && !destination) {
    navigationError.value = '请填写起点或终点'
    return
  }

  navigationBusy.value = true
  navigationError.value = ''
  try {
    const body = await requestJSON('/api/navigation/plan', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        origin_text: origin,
        destination_text: destination,
      }),
    })
    const data = body.data || {}
    navigationPlan.value = data
    navigationVoiceResult.value = data
    syncNavigationFields(data)
    appendNavigationTranscript(data)
    speakNavigationBroadcast(data)
    await refreshNavigationStatus()
  } catch (error) {
    navigationError.value = error instanceof Error ? error.message : '路线规划失败'
  } finally {
    navigationBusy.value = false
  }
}

async function submitNavigationVoice() {
  const text = voiceCommandText.value.trim() || buildNavigationVoiceText()
  if (!text) {
    navigationError.value = '请先输入语音命令'
    return
  }

  navigationVoiceBusy.value = true
  navigationError.value = ''
  try {
    const body = await requestJSON('/api/navigation/voice', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text }),
    })
    const data = body.data || {}
    navigationVoiceResult.value = data
    if (data.planning_result) {
      navigationPlan.value = {
        origin_desc: data.origin_desc || originText.value,
        destination_desc: data.destination_desc || destinationText.value,
        response_text: data.response_text,
        planning_result: data.planning_result,
      }
    }
    syncNavigationFields(data)
    appendNavigationTranscript(data)
    speakNavigationBroadcast(data)
    await refreshNavigationStatus()
  } catch (error) {
    navigationError.value = error instanceof Error ? error.message : '语音解析失败'
  } finally {
    navigationVoiceBusy.value = false
  }
}

async function broadcastRoute() {
  const origin = originText.value.trim()
  const destination = destinationText.value.trim()
  if (!origin && !destination) {
    navigationError.value = '请先填写起点或终点'
    return
  }

  navigationBroadcastBusy.value = true
  navigationError.value = ''
  try {
    const body = await requestJSON('/api/navigation/broadcast', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        origin_text: origin,
        destination_text: destination,
      }),
    })
    const data = body.data || {}
    navigationBroadcastResult.value = data
    if (data.planning_result) {
      navigationPlan.value = {
        origin_desc: data.origin_desc || origin,
        destination_desc: data.destination_desc || destination,
        response_text: data.response_text,
        planning_result: data.planning_result,
      }
      navigationVoiceResult.value = data
    }
    syncNavigationFields(data)
    appendNavigationTranscript(data)
    await refreshNavigationStatus()
  } catch (error) {
    navigationError.value = error instanceof Error ? error.message : '路线播报失败'
  } finally {
    navigationBroadcastBusy.value = false
  }
}

async function dispatchNavigationVoice(text, { auto = false, transcriptId = '' } = {}) {
  const commandText = (text || '').trim()
  if (!commandText) {
    if (!auto) {
      navigationError.value = '璇峰厛杈撳叆璇煶鍛戒护'
    }
    return false
  }

  const dispatchKey = transcriptId || commandText
  if (auto && lastAutoNavigationTranscriptId.value === dispatchKey) {
    return false
  }
  if (navigationVoiceBusy.value) {
    return false
  }

  if (auto) {
    lastAutoNavigationTranscriptId.value = dispatchKey
    voiceCommandText.value = commandText
  }

  navigationVoiceBusy.value = true
  navigationError.value = ''
  try {
    const body = await requestJSON('/api/navigation/voice', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: commandText }),
    })
    const data = body.data || {}
    navigationVoiceResult.value = data
    if (data.planning_result) {
      navigationPlan.value = {
        origin_desc: data.origin_desc || originText.value,
        destination_desc: data.destination_desc || destinationText.value,
        response_text: data.response_text,
        planning_result: data.planning_result,
      }
    }
    syncNavigationFields(data)
    await refreshNavigationStatus()
    return true
  } catch (error) {
    if (!auto) {
      navigationError.value = error instanceof Error ? error.message : '璇煶瑙ｆ瀽澶辫触'
    }
    return false
  } finally {
    navigationVoiceBusy.value = false
  }
}

function appendNavigationTranscript(data = {}) {
  const text = data.planning_result?.broadcast_text || data.response_text || ''
  if (!text) {
    return
  }
  emit('record-transcript', {
    role: 'assistant',
    text: `[导航] ${text}`,
    final: true,
    responseId: `navigation-${data.planning_result?.request_id || Date.now()}`,
  })
}

function speakNavigationBroadcast(data = {}) {
  const text = data.planning_result?.broadcast_text || data.response_text || ''
  const speechKey = `${data.planning_result?.request_id || ''}-${text}`
  if (!text || speechKey === lastNavigationSpeechKey.value) {
    return
  }

  lastNavigationSpeechKey.value = speechKey
  if (!window.speechSynthesis || typeof window.SpeechSynthesisUtterance === 'undefined') {
    return
  }

  try {
    window.speechSynthesis.cancel()
    const utterance = new SpeechSynthesisUtterance(text)
    utterance.lang = 'zh-CN'
    utterance.rate = 0.96
    utterance.pitch = 1
    const voices = window.speechSynthesis.getVoices?.() || []
    const chineseVoice = voices.find((voice) => String(voice.lang || '').toLowerCase().startsWith('zh'))
    if (chineseVoice) {
      utterance.voice = chineseVoice
    }
    window.speechSynthesis.speak(utterance)
  } catch {
    // ignore browser TTS failures
  }
}

function looksLikeNavigationVoice(text) {
  const value = String(text || '').trim()
  if (!value) {
    return false
  }
  return /(导航|去|到|前往|出发|路线|带我|从.+到.+)/.test(value)
}

function openNavigationUri() {
  const uri = navigationLaunchUri.value
  if (!uri) {
    navigationError.value = '暂无可打开的高德导航链接'
    return
  }
  window.open(uri, '_blank', 'noopener,noreferrer')
}

function formatDistance(value) {
  const meters = Number(value || 0)
  if (meters >= 1000) {
    return `${(meters / 1000).toFixed(1)} km`
  }
  return `${meters.toFixed(0)} m`
}

function formatDuration(value) {
  const seconds = Number(value || 0)
  if (seconds >= 3600) {
    const hours = Math.floor(seconds / 3600)
    const minutes = Math.round((seconds % 3600) / 60)
    return `${hours}h${minutes}m`
  }
  if (seconds >= 60) {
    return `${Math.round(seconds / 60)} min`
  }
  return `${seconds.toFixed(0)} s`
}

function numberValue(value, digits = 0) {
  const numeric = Number(value || 0)
  return numeric.toFixed(digits)
}

function visionEventTitle(event) {
  if (event.command) {
    return `控制：${event.command}`
  }
  if (event.event === 'guidance') {
    return `提示：${event.state || '-'}`
  }
  return event.event || 'vision'
}

function visionEventKey(event) {
  return `${event.createdAt || ''}-${event.event || ''}-${event.guidanceText || event.command || ''}`
}

watch(
  transcriptItems,
  async (items) => {
    const latestUserTranscript = findLatestFinalUserTranscript(items)
    if (!latestUserTranscript) {
      return
    }
    if (!showNavigation.value && !looksLikeNavigationVoice(latestUserTranscript.text)) {
      return
    }
    if (lastAutoNavigationTranscriptId.value === latestUserTranscript.id) {
      return
    }
    await dispatchNavigationVoice(latestUserTranscript.text, {
      auto: true,
      transcriptId: latestUserTranscript.id,
    })
  },
  { flush: 'post' },
)

function compactJSON(value) {
  // 技能结果可能很大，控制台只展示压缩后的单行摘要。
  if (value === null || value === undefined) {
    return '{}'
  }
  if (typeof value !== 'object') {
    return String(value)
  }

  const text = JSON.stringify(value)
  if (!text || text === 'null') {
    return '{}'
  }
  if (text.length <= 240) {
    return text
  }
  return `${text.slice(0, 237)}...`
}

function transcriptRoleLabel(role) {
  return role === 'user' ? '用户' : '助手'
}

function formatTerminalTime(value) {
  const date = value instanceof Date ? value : new Date(value)
  if (Number.isNaN(date.getTime())) {
    return '--:--:--'
  }
  return new Intl.DateTimeFormat('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).format(date)
}
</script>
