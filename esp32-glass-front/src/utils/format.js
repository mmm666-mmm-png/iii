// 前端展示格式化工具：只处理显示文本，不包含业务状态判断。
export function formatDateTime(value) {
  if (!value) {
    return '-'
  }

  const date = value instanceof Date ? value : new Date(value)
  if (Number.isNaN(date.getTime())) {
    return '-'
  }

  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  }).format(date)
}

export function formatRelativeTime(value) {
  // 后台同步时间使用相对时间，超过 1 小时回退到具体日期时间。
  if (!value) {
    return '尚未同步'
  }

  const date = value instanceof Date ? value : new Date(value)
  if (Number.isNaN(date.getTime())) {
    return '尚未同步'
  }

  const diffSeconds = Math.max(0, Math.round((Date.now() - date.getTime()) / 1000))
  if (diffSeconds < 5) {
    return '刚刚'
  }
  if (diffSeconds < 60) {
    return `${diffSeconds} 秒前`
  }
  const diffMinutes = Math.round(diffSeconds / 60)
  if (diffMinutes < 60) {
    return `${diffMinutes} 分钟前`
  }
  return formatDateTime(date)
}

export function formatDuration(totalSeconds) {
  const seconds = Number(totalSeconds || 0)
  if (!Number.isFinite(seconds) || seconds <= 0) {
    return '0 秒'
  }

  const hours = Math.floor(seconds / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)
  const remainder = seconds % 60

  if (hours > 0) {
    return `${hours} 小时 ${minutes} 分`
  }
  if (minutes > 0) {
    return `${minutes} 分 ${remainder} 秒`
  }
  return `${remainder} 秒`
}

export function formatBytes(value) {
  // 链路诊断中的字节数格式化。
  const bytes = Number(value || 0)
  if (!Number.isFinite(bytes) || bytes <= 0) {
    return '-'
  }
  if (bytes < 1024) {
    return `${bytes} B`
  }
  if (bytes < 1024 * 1024) {
    return `${(bytes / 1024).toFixed(1)} KB`
  }
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`
}

export function riskTagColor(risk) {
  // 预留给视觉风险标签使用，映射到 Ant Design Vue tag color。
  switch (risk) {
    case 'low':
    case 'stable':
      return 'success'
    case 'medium':
    case 'warning':
    case 'attention':
      return 'warning'
    case 'high':
      return 'error'
    default:
      return 'default'
  }
}

export function statusTagColor(status) {
  // 通用状态色，给技能/任务/连接状态复用。
  switch (status) {
    case 'running':
    case 'active':
      return 'processing'
    case 'closed':
    case 'completed':
      return 'success'
    case 'failed':
      return 'error'
    default:
      return 'default'
  }
}
