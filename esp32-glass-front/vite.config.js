import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  // 开发环境代理到 Go 后端，避免浏览器跨域和 WebSocket 地址手动切换。
  const proxyTarget = env.VITE_DEV_PROXY_TARGET || 'http://127.0.0.1:8888'

  return {
    plugins: [vue()],
    build: {
      rollupOptions: {
        output: {
          manualChunks(id) {
            // 把 antd/vue 拆成稳定 chunk，提高二次构建和浏览器缓存命中率。
            if (!id.includes('node_modules')) {
              return
            }
            if (id.includes('ant-design-vue') || id.includes('@ant-design/icons-vue')) {
              return 'antd'
            }
            if (id.includes('/vue/')) {
              return 'vue'
            }
          },
        },
      },
    },
    server: {
      // 监听所有网卡（IPv4/IPv6），保证通过 127.0.0.1、localhost 以及局域网 IP 都能访问。
      // 默认只绑定 ::1，会导致 127.0.0.1 和局域网设备连接被拒绝（“无响应、无状态码”）。
      host: true,
      // 本地开发时 /api、/snapshot.jpg、/ws 都转发到 Go 服务端。
      proxy: {
        '/api': {
          target: proxyTarget,
          changeOrigin: true,
        },
        '/snapshot.jpg': {
          target: proxyTarget,
          changeOrigin: true,
        },
        '/healthz': {
          target: proxyTarget,
          changeOrigin: true,
        },
        '/ws': {
          target: proxyTarget,
          ws: true,
          changeOrigin: true,
        },
      },
    },
  }
})
