<template>
  <section class="glass-console">
    <header class="console-topbar">
      <div>
        <h2>{{ pageTitle }}</h2>
        <p>{{ pageDescription }}</p>
      </div>
      <div class="console-tags">
        <a-tag :color="liveState?.deviceConnected ? 'success' : 'default'">{{ liveState?.deviceConnected ? '设备在线' : '设备离线' }}</a-tag>
        <a-tag :color="activeMode === 'navigation' ? 'processing' : 'success'">{{ activeModeLabel }}</a-tag>
        <a-tag :color="voiceModeTagColor">{{ voiceModeLabel }}</a-tag>
        <a-tag :color="aiTagColor">AI {{ aiStateLabel }}</a-tag>
      </div>
    </header>

    <div v-if="showCameraColumn || showControlColumn" class="console-layout">
      <div v-if="showCameraColumn" class="camera-column">
        <div class="camera-shell">
          <a-image
            v-if="snapshotSrc"
            :src="snapshotSrc"
            :preview="false"
            class="camera-image"
            :class="{ 'camera-image-rotated': snapshotRotated }"
            alt="实时画面"
          />
          <a-empty v-else description="暂无实时画面" />

          <div v-if="snapshotSrc && showAR" class="ar-overlay">
            <div class="ar-top">
              <button type="button" class="ar-back" @click="$emit('navigate', 'live')">‹ 返回</button>
              <span class="ar-mode">{{ arModeLabel }}</span>
            </div>
            <div class="ar-chips">
              <div class="ar-chip"><span>盲道覆盖率</span><strong>{{ coveragePct }}</strong></div>
              <div class="ar-chip"><span>红绿灯</span><strong>{{ trafficLights }}</strong></div>
              <div class="ar-chip is-orange"><span>转弯路口</span><strong>{{ routeTurns }}</strong></div>
            </div>
            <div v-if="currentGuidance" class="ar-guidance">{{ currentGuidance }}</div>
          </div>
        </div>

        <div v-if="showTranscript || showVisionEvents" class="camera-console">
          <div v-if="showTranscript" class="panel-card">
            <div class="panel-card-head">
              <strong>实时转写</strong>
              <a-tag :color="liveTranscripts.length > 0 ? 'processing' : 'default'">{{ liveTranscripts.length }} 条</a-tag>
            </div>
            <div ref="transcriptRef" class="panel-card-list">
              <div v-if="transcriptItems.length === 0" class="panel-empty">暂无实时转写</div>
              <template v-else>
                <article v-for="item in transcriptItems" :key="item.id" class="transcript-item" :class="`transcript-item-${item.role}`">
                  <div class="transcript-item-head">
                    <span>{{ transcriptRoleLabel(item.role) }}</span>
                    <time>{{ formatTerminalTime(item.createdAt) }}</time>
                  </div>
                  <p>{{ item.text }}</p>
                </article>
              </template>
            </div>
          </div>

          <div v-if="showVisionEvents" class="panel-card">
            <div class="panel-card-head">
              <strong>导航事件</strong>
              <a-tag :color="visionEvents.length > 0 ? 'processing' : 'default'">{{ visionEvents.length }} 条</a-tag>
            </div>
            <div class="panel-card-list">
              <div v-if="visionEvents.length === 0" class="panel-empty">暂无导航事件</div>
              <template v-else>
                <article v-for="event in recentVisionEvents" :key="visionEventKey(event)" class="vision-item">
                  <div class="vision-item-head">
                    <strong>{{ visionEventTitle(event) }}</strong>
                    <span>{{ formatTerminalTime(event.createdAt) }}</span>
                  </div>
                  <p>{{ event.guidanceText || event.error || event.state || '已记录' }}</p>
                </article>
              </template>
            </div>
          </div>
        </div>
      </div>

      <div v-if="showControlColumn" class="control-column">
        <div class="control-card">
          <div class="control-tabs" role="tablist">
            <button
              v-for="tab in controlTabs"
              :key="tab.key"
              type="button"
              class="control-tab"
              :class="{ 'control-tab-active': activeControlTab === tab.key }"
              @click="activeControlTab = tab.key"
            >
              {{ tab.label }}
            </button>
          </div>

          <div v-if="activeControlTab === 'nav'" class="control-tab-body">
            <div class="control-current">
              <span>当前位置</span>
              <strong>{{ originText || '未填写起点，默认当前位置' }}</strong>
            </div>

            <div class="field-row">
              <div>
                <label class="field-label">起点</label>
                <a-input v-model:value="originText" size="small" placeholder="例如：枣庄学院" />
              </div>
              <div>
                <label class="field-label">终点</label>
                <a-input v-model:value="destinationText" size="small" placeholder="例如：万达广场" @press-enter="planNavigationRoute" />
              </div>
            </div>

            <div class="btn-row">
              <a-button type="primary" size="small" :loading="navigationBusy" @click="planNavigationRoute" class="btn-primary-glow">
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
              <a-button size="small" @click="testSegmentBroadcast">
                <template #icon><SoundOutlined /></template>
                测试逐段
              </a-button>
              <a-button v-if="navigationLaunchUri" size="small" @click="openNavigationUri">
                <template #icon><EnvironmentOutlined /></template>
                打开高德
              </a-button>
            </div>

            <div class="field-row single">
              <div>
                <label class="field-label">语音口令</label>
                <a-input v-model:value="voiceCommandText" size="small" placeholder="例如：从学院去万达广场" @press-enter="submitNavigationVoice" />
              </div>
            </div>

            <div class="gps-row">
              <a-button
                size="small"
                :type="gpsEnabled ? 'primary' : 'default'"
                @click="$emit('toggle-gps')"
              >
                {{ gpsEnabled ? '关闭定位' : '开启定位' }}
              </a-button>
              <span class="gps-row-status">{{ gpsStatus }}</span>
              <span v-if="gpsFix" class="gps-row-fix">
                {{ Number(gpsFix.lng).toFixed(6) }}, {{ Number(gpsFix.lat).toFixed(6) }}
                <small v-if="gpsFix.accuracy">±{{ Math.round(gpsFix.accuracy) }}m</small>
              </span>
              <span v-if="gpsNavigationStatus?.triggered?.length" class="gps-row-fix">
                已触发 {{ gpsNavigationStatus.triggered.length }} 段播报
              </span>
            </div>

            <p v-if="navigationError" class="control-error">{{ navigationError }}</p>

            <div class="control-section">
              <div class="control-section-head">
                <strong>路况统计</strong>
                <span>{{ navigationStatusLabel }}</span>
              </div>
              <div class="route-stats">
                <div class="route-stat"><span>全程盲道覆盖率</span><strong class="highlight">{{ coveragePct }}</strong></div>
                <div class="route-stat"><span>途经红绿灯</span><strong>{{ trafficLights }}</strong></div>
                <div class="route-stat"><span>需转弯路口</span><strong>{{ routeTurns }}</strong></div>
                <div class="route-stat"><span>预计时长</span><strong>{{ routeDuration }}</strong></div>
              </div>
            </div>

            <div v-if="navigationPlan" class="route-summary">
              <div class="route-summary-head">
                <strong>路线结果</strong>
                <span>{{ navigationPlan.planning_result?.best_route?.name || navigationPlan.response_text || '-' }}</span>
              </div>
              <p>{{ navigationPlan.response_text || navigationPlan.planning_result?.broadcast_text }}</p>
            </div>

            <div v-if="routeSteps.length" class="route-steps">
              <div class="route-summary-head">
                <strong>路线步骤</strong>
                <span>共 {{ routeSteps.length }} 步</span>
              </div>
              <ol>
                <li v-for="step in routeSteps" :key="step.index">
                  <strong>{{ step.index }}</strong>
                  <span class="step-instruction">{{ step.instruction }}</span>
                  <span v-if="step.distance_meters" class="step-distance">{{ formatDistance(step.distance_meters) }}</span>
                </li>
              </ol>
            </div>
            <p v-else-if="navigationVoiceResult?.response_text" class="control-note">{{ navigationVoiceResult.response_text }}</p>
          </div>

          <div v-else-if="activeControlTab === 'obstacle'" class="control-tab-body">
            <div class="control-section">
              <div class="control-section-head">
                <strong>导航辅助</strong>
                <span>{{ visionStatusLabel }}</span>
              </div>
              <div class="action-grid">
                <button type="button" class="action-btn" @click="emitVisionCommand('start_blind_navigation')">
                  <CompassOutlined /><span>盲道</span>
                </button>
                <button type="button" class="action-btn" @click="emitVisionCommand('start_crossing')">
                  <AimOutlined /><span>过马路</span>
                </button>
                <button type="button" class="action-btn" @click="emitVisionCommand('detect_traffic_light')">
                  <ThunderboltOutlined /><span>红绿灯</span>
                </button>
                <button type="button" class="action-btn" @click="emitVisionCommand('stop')">
                  <PauseCircleOutlined /><span>停止</span>
                </button>
              </div>
            </div>

            <div class="control-section">
              <div class="control-section-head">
                <strong>障碍物手动上报</strong>
                <span>{{ gpsFix ? '已定位' : gpsStatus }}</span>
              </div>
              <div class="report-box">
                <a-input v-model:value="obstacleTypeText" size="small" placeholder="障碍物类型，如：电线杆、车辆" @press-enter="reportObstacle" />
                <a-input v-model:value="obstacleLngText" size="small" placeholder="经度（可手动填写）" />
                <a-input v-model:value="obstacleLatText" size="small" placeholder="纬度（可手动填写）" />
                <a-input v-model:value="obstacleDescText" size="small" placeholder="补充描述（可选）" @press-enter="reportObstacle" />
                <a-button size="small" :disabled="!gpsFix" @click="usePhoneLocation">
                  <template #icon><EnvironmentOutlined /></template>
                  使用手机定位
                </a-button>
                <a-button type="primary" size="small" :loading="obstacleReportBusy" @click="reportObstacle">
                  <template #icon><EnvironmentOutlined /></template>
                  手动上报
                </a-button>
              </div>
              <p v-if="obstacleReportError" class="control-error">{{ obstacleReportError }}</p>
              <p v-else-if="obstacleReportSuccess" class="control-note">{{ obstacleReportSuccess }}</p>
              <p class="control-note">可手动填写经纬度，或开启定位后使用手机当前位置；两项都不填也可上报，但不会进入地图热点。</p>
              <div class="obstacle-map-head">
                <strong>障碍物热点地图</strong>
                <span>{{ obstacleHotspots.length }} 个有位置热点</span>
              </div>
              <div ref="obstacleMapRef" class="obstacle-map" aria-label="障碍物热点地图"></div>
              <div v-if="obstacleHotspotsBusy" class="map-status">正在刷新热点...</div>
              <div v-else-if="obstacleHotspots.length === 0" class="map-status">暂无带坐标的障碍物热点</div>
              <div v-else class="obstacle-hotspot-list">
                <div v-for="hotspot in obstacleHotspots" :key="hotspot.hotspot_id" class="obstacle-hotspot-item">
                  <strong>{{ hotspot.obstacle_type }}</strong>
                  <span>{{ hotspot.report_count }} 次上报 · {{ hotspot.severity }}</span>
                  <small>{{ Number(hotspot.location.lng).toFixed(6) }}, {{ Number(hotspot.location.lat).toFixed(6) }}</small>
                </div>
              </div>
            </div>

            <p v-if="!liveState?.deviceConnected" class="control-error">设备当前离线，导航模式已切换，但不会收到新的实时画面。</p>
            <p v-else-if="visionState?.lastError" class="control-error">{{ visionState.lastError }}</p>
          </div>

          <div v-else class="control-tab-body">
            <div class="control-section">
              <div class="control-section-head">
                <strong>工作模式</strong>
                <span>导盲和回答问题二选一</span>
              </div>
              <div class="mode-switch-grid">
                <button type="button" class="mode-choice" :class="{ 'mode-choice-active': activeMode === 'navigation' }" @click="changeMode('navigation')">
                  <CompassOutlined class="mode-choice-icon" />
                  <span>导盲</span>
                  <small>盲道导航、过马路、红绿灯和找物品</small>
                </button>
                <button type="button" class="mode-choice" :class="{ 'mode-choice-active': activeMode === 'qa' }" @click="changeMode('qa')">
                  <MessageOutlined class="mode-choice-icon" />
                  <span>回答问题</span>
                  <small>实时转写、AI 回复和语音播放</small>
                </button>
              </div>
            </div>

            <div class="control-section">
              <div class="control-section-head">
                <strong>语音交互</strong>
                <span>说“聊天”或“导航模式”切换</span>
              </div>
              <div class="voice-mode-grid">
                <div class="voice-mode-card" :class="{ 'voice-mode-card-active': voiceMode === 'chat' }">
                  <MessageOutlined class="mode-choice-icon" />
                  <strong>千问聊天</strong>
                  <small>语音交给 Qwen Omni 多模态问答</small>
                </div>
                <div class="voice-mode-card" :class="{ 'voice-mode-card-active': voiceMode === 'navigation' }">
                  <EnvironmentOutlined class="mode-choice-icon" />
                  <strong>高德导航</strong>
                  <small>语音交给高德路线规划并逐段播报</small>
                </div>
              </div>
            </div>

            <div v-if="showAudio" class="control-section">
              <div class="control-section-head">
                <strong>AI 语音</strong>
                <span>AI 音频 {{ aiAudioChunksSeen }}</span>
              </div>
              <div class="audio-row">
                <a-button type="primary" size="small" :disabled="audioUnlocked" @click="$emit('unlock-audio')">
                  {{ audioUnlocked ? 'AI 语音已解锁' : '解锁 AI 语音' }}
                </a-button>
                <a-button size="small" @click="$emit('toggle-assistant-audio')">
                  {{ assistantAudioEnabled ? 'AI 语音开启' : 'AI 语音静音' }}
                </a-button>
              </div>
              <p class="control-note">{{ audioStatus }}</p>
            </div>
          </div>
        </div>
      </div>
    </div>

    <div v-if="showQuality" class="metric-grid">
      <div class="metric-cell">
        <span>FPS</span>
        <strong>{{ numberValue(streamQuality?.videoFps, 1) }}</strong>
      </div>
      <div class="metric-cell">
        <span>音频/s</span>
        <strong>{{ numberValue(streamQuality?.audioChunksPerSecond, 1) }}</strong>
      </div>
      <div class="metric-cell">
        <span>视频缺口</span>
        <strong>{{ streamQuality?.videoSeqGaps ?? 0 }}</strong>
      </div>
      <div class="metric-cell">
        <span>未完整帧</span>
        <strong>{{ streamQuality?.videoIncompleteFrames ?? 0 }}</strong>
      </div>
    </div>

    <div v-if="showSkillConsole" class="control-card">
      <div class="control-tab-body">
        <div class="control-section">
          <div class="control-section-head">
            <strong>AI 语音</strong>
            <span>AI 音频 {{ aiAudioChunksSeen }}</span>
          </div>
          <div class="audio-row">
            <a-button type="primary" size="small" :disabled="audioUnlocked" @click="$emit('unlock-audio')">
              {{ audioUnlocked ? 'AI 语音已解锁' : '解锁 AI 语音' }}
            </a-button>
            <a-button size="small" @click="$emit('toggle-assistant-audio')">
              {{ assistantAudioEnabled ? 'AI 语音开启' : 'AI 语音静音' }}
            </a-button>
          </div>
          <p class="control-note">{{ audioStatus }}</p>
        </div>
      </div>
    </div>

    <div v-if="showConsoles && !showConsolesUnderPreview" class="console-panels" :class="consolePanelClass">
      <div v-if="showTranscript" class="panel-card">
        <div class="panel-card-head">
          <strong>实时转写</strong>
          <a-tag :color="liveTranscripts.length > 0 ? 'processing' : 'default'">{{ liveTranscripts.length }} 条</a-tag>
        </div>
        <div ref="transcriptRef" class="panel-card-list">
          <div v-if="transcriptItems.length === 0" class="panel-empty">暂无实时转写</div>
          <template v-else>
            <article v-for="item in transcriptItems" :key="item.id" class="transcript-item" :class="`transcript-item-${item.role}`">
              <div class="transcript-item-head">
                <span>{{ transcriptRoleLabel(item.role) }}</span>
                <time>{{ formatTerminalTime(item.createdAt) }}</time>
              </div>
              <p>{{ item.text }}</p>
            </article>
          </template>
        </div>
      </div>

      <div v-if="showSkillConsole" class="panel-card skill-panel">
        <div class="panel-card-head">
          <strong>Live Skill Console</strong>
          <a-tag :color="skillEvents.length > 0 ? 'processing' : 'default'">{{ skillEvents.length }} calls</a-tag>
        </div>
        <div ref="terminalRef" class="skill-terminal">
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

      <div v-if="showVisionEvents" class="panel-card">
        <div class="panel-card-head">
          <strong>导航事件</strong>
          <a-tag :color="visionEvents.length > 0 ? 'processing' : 'default'">{{ visionEvents.length }} 条</a-tag>
        </div>
        <div class="panel-card-list">
          <div v-if="visionEvents.length === 0" class="panel-empty">暂无导航事件</div>
          <template v-else>
            <article v-for="event in recentVisionEvents" :key="visionEventKey(event)" class="vision-item">
              <div class="vision-item-head">
                <strong>{{ visionEventTitle(event) }}</strong>
                <span>{{ formatTerminalTime(event.createdAt) }}</span>
              </div>
              <p>{{ event.guidanceText || event.error || event.state || '已记录' }}</p>
            </article>
          </template>
        </div>
      </div>
    </div>

    <div v-if="showInfo" class="info-grid">
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
  SoundOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons-vue'
import L from 'leaflet'
import 'leaflet/dist/leaflet.css'

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
  voiceMode: {
    type: String,
    default: 'chat',
  },
  gpsEnabled: {
    type: Boolean,
    default: false,
  },
  gpsStatus: {
    type: String,
    default: '未开启',
  },
  gpsFix: {
    type: Object,
    default: null,
  },
  gpsNavigationStatus: {
    type: Object,
    default: null,
  },
})

const emit = defineEmits(['unlock-audio', 'toggle-assistant-audio', 'vision-command', 'mode-change', 'record-transcript', 'toggle-gps', 'navigate'])

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
const obstacleTypeText = ref('')
const obstacleLngText = ref('')
const obstacleLatText = ref('')
const obstacleDescText = ref('')
const obstacleReportBusy = ref(false)
const obstacleReportError = ref('')
const obstacleReportSuccess = ref('')
const obstacleHotspots = ref([])
const obstacleHotspotsBusy = ref(false)
const obstacleMapRef = ref(null)
let obstacleMap = null
let obstacleMarkerLayer = null
let obstacleHotspotTimer = null
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
  // 页面标题由父级 page 控制，同一个组件复用在实时/障碍物/诊断/系统设置视图。
  const titles = {
    live: '实时导航',
    navigation: '障碍物管理',
    ai: '系统设置',
    quality: '链路诊断',
  }
  return titles[props.page] || '实时导航'
})
const pageDescription = computed(() => {
  const descriptions = {
    live: '实时画面与转写同页，右侧导航控制与辅助设置。',
    navigation: '盲道、过马路、红绿灯检测和物品查找。',
    ai: 'AI 语音、技能调用与服务信息。',
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
  refreshObstacleHotspots()
  obstacleHotspotTimer = window.setInterval(refreshObstacleHotspots, 10000)
  nextTick(initObstacleMap)
})

onBeforeUnmount(() => {
  if (navigationStatusTimer) {
    window.clearInterval(navigationStatusTimer)
    navigationStatusTimer = null
  }
  if (obstacleHotspotTimer) {
    window.clearInterval(obstacleHotspotTimer)
    obstacleHotspotTimer = null
  }
  if (obstacleMap) {
    obstacleMap.remove()
    obstacleMap = null
  }
})

const aiStateLabel = computed(() => {
  if (!props.aiState?.enabled) {
    return '未启用'
  }
  return props.aiState.connected ? '在线' : '离线'
})

const activeModeLabel = computed(() => props.activeMode === 'navigation' ? '导盲模式' : '回答问题')

const voiceModeLabel = computed(() => (props.voiceMode === 'navigation' ? '高德导航' : '千问聊天'))

const voiceModeTagColor = computed(() => (props.voiceMode === 'navigation' ? 'processing' : 'success'))

// —— 暗色控制台新增：控制列、AR 覆盖层与路况统计 ——
const activeControlTab = ref(props.page === 'navigation' ? 'obstacle' : 'nav')
const controlTabs = [
  { key: 'nav', label: '导航控制' },
  { key: 'obstacle', label: '障碍物管理' },
  { key: 'assist', label: '辅助设置' },
]

watch(activeControlTab, async (tab) => {
  if (tab === 'obstacle') {
    await nextTick()
    initObstacleMap()
    refreshObstacleHotspots()
  }
})

const showCameraColumn = computed(() => showPreview.value)
const showControlColumn = computed(() => ['live', 'navigation'].includes(props.page))
const showAR = computed(() => ['live', 'navigation'].includes(props.page))

const arModeLabel = computed(() => (props.activeMode === 'navigation' ? '导航模式' : '问答模式'))

const activeRoutePlanning = computed(() => {
  return (
    navigationPlan.value?.planning_result
    || navigationVoiceResult.value?.planning_result
    || navigationBroadcastResult.value?.planning_result
    || null
  )
})

const coveragePct = computed(() => {
  const cov = activeRoutePlanning.value?.best_route?.blind_path_coverage_pct
  return cov == null ? '--' : `${numberValue(cov, 0)}%`
})

const routeTurns = computed(() => (routeSteps.value.length ? `${routeSteps.value.length} 个` : '--'))

const trafficLights = computed(() => {
  const count = routeSteps.value.filter((step) => /红绿灯|信号灯|交通灯/.test(step.instruction || '')).length
  return count ? `${count} 个` : '--'
})

const routeDuration = computed(() => {
  const seconds = Number(activeRoutePlanning.value?.best_route?.total_duration_seconds || 0)
  if (!seconds) return '--'
  if (seconds >= 3600) {
    const hours = Math.floor(seconds / 3600)
    const minutes = Math.round((seconds % 3600) / 60)
    return `${hours} 小时 ${minutes} 分`
  }
  if (seconds >= 60) return `${Math.round(seconds / 60)} 分钟`
  return `${seconds} 秒`
})

const currentGuidance = computed(() => {
  const last = props.visionState?.lastGuidance
  if (last) return last
  const latest = recentVisionEvents.value.find((item) => item.guidanceText)
  return latest?.guidanceText || ''
})

const standaloneConsoleCount = computed(() => [showTranscript.value, showSkillConsole.value, showVisionEvents.value].filter(Boolean).length)
const consolePanelClass = computed(() => `panels-count-${standaloneConsoleCount.value}`)

watch(
  () => props.page,
  (page) => {
    // 切换页面时重置右侧控制列默认标签：障碍物管理页默认展示“障碍物管理”。
    if (page === 'navigation') {
      activeControlTab.value = 'obstacle'
    } else if (page === 'live') {
      activeControlTab.value = 'nav'
    }
  },
)

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

function initObstacleMap() {
  if (!obstacleMapRef.value || obstacleMap) return
  obstacleMap = L.map(obstacleMapRef.value, { zoomControl: true }).setView([34.812, 117.327], 15)
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 19,
    attribution: '&copy; OpenStreetMap contributors',
  }).addTo(obstacleMap)
  obstacleMarkerLayer = L.layerGroup().addTo(obstacleMap)
  renderObstacleMarkers()
}

function renderObstacleMarkers() {
  if (!obstacleMarkerLayer) return
  obstacleMarkerLayer.clearLayers()
  const points = obstacleHotspots.value
    .map((hotspot) => ({
      hotspot,
      lat: Number(hotspot.location?.lat),
      lng: Number(hotspot.location?.lng),
    }))
    .filter(({ lat, lng }) => Number.isFinite(lat) && Number.isFinite(lng))

  points.forEach(({ hotspot, lat, lng }) => {
    const marker = L.circleMarker([lat, lng], {
      radius: Math.min(14, 7 + Number(hotspot.report_count || 1)),
      color: hotspot.severity === 'high' ? '#dc2626' : '#f97316',
      fillColor: hotspot.severity === 'high' ? '#ef4444' : '#fb923c',
      fillOpacity: 0.8,
      weight: 2,
    })
    const popup = L.DomUtil.create('div')
    const title = L.DomUtil.create('strong', '', popup)
    title.textContent = hotspot.obstacle_type || 'unknown'
    const detail = L.DomUtil.create('div', '', popup)
    detail.textContent = `${hotspot.report_count || 0} 次上报 · ${hotspot.severity || 'medium'}`
    marker.bindPopup(popup).addTo(obstacleMarkerLayer)
  })

  if (points.length && obstacleMap) {
    const bounds = L.latLngBounds(points.map(({ lat, lng }) => [lat, lng]))
    obstacleMap.fitBounds(bounds, { padding: [24, 24], maxZoom: 17 })
  }
}

async function refreshObstacleHotspots() {
  obstacleHotspotsBusy.value = true
  try {
    const body = await requestJSON('/api/obstacle/hotspots')
    obstacleHotspots.value = Array.isArray(body?.data?.hotspots) ? body.data.hotspots : []
    renderObstacleMarkers()
  } catch {
    // 地图数据不可用时保留上一次结果，不影响导航和手动上报。
  } finally {
    obstacleHotspotsBusy.value = false
  }
}

async function reportObstacle() {
  // 手动上报：优先使用手填坐标，否则回退到手机定位；坐标可全部缺省。
  obstacleReportError.value = ''
  obstacleReportSuccess.value = ''

  const obstacleType = obstacleTypeText.value.trim()
  if (!obstacleType) {
    obstacleReportError.value = '请先填写障碍物类型。'
    return
  }

  const manualLng = obstacleLngText.value.trim()
  const manualLat = obstacleLatText.value.trim()
  if ((manualLng && !manualLat) || (!manualLng && manualLat)) {
    obstacleReportError.value = '经度和纬度需要同时填写。'
    return
  }
  const fix = props.gpsFix
  const lng = manualLng ? Number(manualLng) : fix?.lng
  const lat = manualLat ? Number(manualLat) : fix?.lat
  if (manualLng && (!Number.isFinite(lng) || !Number.isFinite(lat) || lng < -180 || lng > 180 || lat < -90 || lat > 90)) {
    obstacleReportError.value = '请输入有效的经纬度。'
    return
  }

  const deviceId = props.liveState?.device?.deviceId || ''
  obstacleReportBusy.value = true
  try {
    await requestJSON('/api/obstacle/report', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        device_id: deviceId,
        ...(lng != null && lat != null ? { lng, lat } : {}),
        obstacle_type: obstacleType,
        severity: 'medium',
        confidence: 0.9,
        description: obstacleDescText.value.trim(),
      }),
    })
    obstacleReportSuccess.value = '障碍物已上报。'
    obstacleTypeText.value = ''
    obstacleLngText.value = ''
    obstacleLatText.value = ''
    obstacleDescText.value = ''
    await refreshObstacleHotspots()
  } catch (error) {
    obstacleReportError.value = error instanceof Error ? error.message : '障碍物上报失败。'
  } finally {
    obstacleReportBusy.value = false
  }
}

function usePhoneLocation() {
  if (!props.gpsFix) return
  obstacleLngText.value = Number(props.gpsFix.lng).toFixed(6)
  obstacleLatText.value = Number(props.gpsFix.lat).toFixed(6)
  obstacleReportError.value = ''
  obstacleReportSuccess.value = ''
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
    speakNavigationSummary(data)
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
    speakNavigationSummary(data)
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

function speakNavigationSteps(data = {}) {
  const steps = data.planning_result?.turn_by_turn || data.turn_by_turn || []
  const normalizedSteps = Array.isArray(steps)
    ? steps.filter((step) => String(step?.instruction || '').trim())
    : []
  const summary = data.planning_result?.broadcast_text || data.response_text || ''
  const speechKey = `${data.planning_result?.request_id || ''}-${summary}-${normalizedSteps.map((step) => step.instruction).join('|')}`
  if (!summary && normalizedSteps.length === 0 || speechKey === lastNavigationSpeechKey.value) {
    return
  }

  lastNavigationSpeechKey.value = speechKey
  if (!window.speechSynthesis || typeof window.SpeechSynthesisUtterance === 'undefined') {
    return
  }

  try {
    window.speechSynthesis.cancel()
    const voices = window.speechSynthesis.getVoices?.() || []
    const chineseVoice = voices.find((voice) => String(voice.lang || '').toLowerCase().startsWith('zh'))
    const texts = normalizedSteps.length
      ? normalizedSteps.map((step, index) => `第${step.index || index + 1}步，${step.instruction}`)
      : [summary]
    texts.forEach((text) => {
      const utterance = new SpeechSynthesisUtterance(text)
      utterance.lang = 'zh-CN'
      utterance.rate = 0.96
      utterance.pitch = 1
      if (chineseVoice) utterance.voice = chineseVoice
      window.speechSynthesis.speak(utterance)
    })
  } catch {
    // ignore browser TTS failures
  }
}

function speakNavigationSummary(data = {}) {
  const text = data.planning_result?.broadcast_text || data.response_text || ''
  if (!text || !window.speechSynthesis || typeof window.SpeechSynthesisUtterance === 'undefined') return
  window.speechSynthesis.cancel()
  const utterance = new SpeechSynthesisUtterance(text)
  utterance.lang = 'zh-CN'
  utterance.rate = 0.96
  window.speechSynthesis.speak(utterance)
}

function testSegmentBroadcast() {
  const fakeSteps = [
    { index: 1, instruction: '从校门出发，沿学院路向东直行', distance_meters: 180 },
    { index: 2, instruction: '前方路口右转进入明德路', distance_meters: 95 },
    { index: 3, instruction: '沿明德路直行，经过一处斑马线', distance_meters: 240 },
    { index: 4, instruction: '前方五十米左转，终点就在右侧', distance_meters: 50 },
  ]
  const data = {
    response_text: '开始测试逐段导航播报',
    turn_by_turn: fakeSteps,
    planning_result: { request_id: `fake-${Date.now()}`, turn_by_turn: fakeSteps },
  }
  navigationBroadcastResult.value = data
  navigationPlan.value = data
  appendNavigationTranscript(data)
  speakNavigationSteps(data)
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
