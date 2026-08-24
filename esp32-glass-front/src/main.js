import { createApp } from 'vue'
import {
  Alert,
  Button,
  ConfigProvider,
  Empty,
  Image,
  Input,
  Layout,
  Space,
  Tag,
} from 'ant-design-vue'
import 'ant-design-vue/dist/reset.css'

import App from './App.vue'
import './style.css'

// Ant Design Vue 按组件注册，减少打包体积；全局样式在 style.css 中统一覆盖。
const app = createApp(App)

;[
  Alert,
  Button,
  ConfigProvider,
  Empty,
  Image,
  Input,
  Layout,
  Space,
  Tag,
].forEach((component) => {
  app.use(component)
})

app.mount('#app')
