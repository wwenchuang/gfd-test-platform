import { createApp } from 'vue'
import { createPinia } from 'pinia'

import App from './App.vue'
import { router } from './router'
import { loadBusinessLines } from './utils/businessLines'
import './styles/tokens.css'
import './styles/app.css'

async function bootstrap(): Promise<void> {
  await Promise.race([
    loadBusinessLines(),
    new Promise<void>(resolve => window.setTimeout(resolve, 800)),
  ])
  createApp(App).use(createPinia()).use(router).mount('#app')
}

void bootstrap()
