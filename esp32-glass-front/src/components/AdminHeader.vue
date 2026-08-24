<template>
  <header class="surface-panel workspace-topbar">
    <div class="workspace-title-block">
      <div class="workspace-kicker">Console</div>
      <h2>{{ activePageLabel }}</h2>
      <p>状态会在后台静默同步；手动刷新只补拉最新状态，不清空当前画面和日志。</p>
    </div>

    <div class="workspace-toolbar">
      <a-space wrap>
        <a-tag :color="liveState?.deviceConnected ? 'processing' : 'default'" class="header-tag">
          <ApiOutlined />
          <span>{{ liveState?.deviceConnected ? '设备在线' : '设备离线' }}</span>
        </a-tag>
        <a-tag :color="aiTagColor" class="header-tag">
          <AudioOutlined />
          <span>AI {{ aiLabel }}</span>
        </a-tag>
        <a-tag :color="visionTagColor" class="header-tag">
          <CompassOutlined />
          <span>视觉 {{ visionLabel }}</span>
        </a-tag>
        <a-tag :color="backgroundRefreshing ? 'processing' : 'default'" class="header-tag">
          <HistoryOutlined />
          <span>{{ backgroundRefreshing ? '后台同步中' : `同步 ${formattedLastSynced}` }}</span>
        </a-tag>
      </a-space>

      <a-button type="primary" :loading="refreshing" @click="$emit('refresh')">
        <template #icon>
          <ReloadOutlined />
        </template>
        刷新状态
      </a-button>
    </div>
  </header>
</template>

<script setup>
// 顶部状态条：只展示父组件传入的设备、AI、视觉和同步状态。
import { computed } from 'vue'
import { ApiOutlined, AudioOutlined, CompassOutlined, HistoryOutlined, ReloadOutlined } from '@ant-design/icons-vue'

import { formatRelativeTime } from '../utils/format'

defineEmits(['refresh'])

const props = defineProps({
  liveState: {
    type: Object,
    default: null,
  },
  aiState: {
    type: Object,
    default: null,
  },
  visionState: {
    type: Object,
    default: null,
  },
  refreshing: {
    type: Boolean,
    default: false,
  },
  backgroundRefreshing: {
    type: Boolean,
    default: false,
  },
  lastSyncedAt: {
    type: [Date, String, null],
    default: null,
  },
  activePageLabel: {
    type: String,
    default: '实时交互',
  },
})

const formattedLastSynced = computed(() => formatRelativeTime(props.lastSyncedAt))
const aiLabel = computed(() => {
  // AI 未配置和已配置但离线要区分，便于判断是配置问题还是连接问题。
  if (!props.aiState?.enabled) return '未启用'
  return props.aiState.connected ? '在线' : '离线'
})
const aiTagColor = computed(() => {
  if (!props.aiState?.enabled) return 'default'
  return props.aiState.connected ? 'processing' : 'warning'
})
const visionLabel = computed(() => {
  // 视觉状态显示 Python worker 当前模式，例如 BLINDPATH_NAV、CROSSING。
  if (!props.visionState?.enabled) return '未配置'
  return props.visionState.connected ? (props.visionState.state || '就绪') : '离线'
})
const visionTagColor = computed(() => {
  if (!props.visionState?.enabled) return 'default'
  return props.visionState.connected ? 'success' : 'warning'
})
</script>
