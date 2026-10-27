<template>
  <div class="login-container">
    <div class="login-bg">
      <div class="bg-circle circle1"></div>
      <div class="bg-circle circle2"></div>
      <div class="bg-circle circle3"></div>
    </div>
    <div class="login-card">
      <div class="login-header">
        <div class="logo">
          <el-icon size="40" color="#409EFF"><Monitor /></el-icon>
        </div>
        <h1>ISP Manager</h1>
        <p>互联网服务提供商管理系统</p>
      </div>
      <el-alert
        v-if="sessionExpired"
        class="idle-alert"
        type="warning"
        :closable="false"
        show-icon
        title="登录已超时，请重新登录"
      />
      <el-form
        ref="formRef"
        :model="form"
        :rules="rules"
        class="login-form"
        @keyup.enter="handleLogin"
      >
        <el-form-item prop="username">
          <el-input
            v-model="form.username"
            placeholder="请输入用户名"
            size="large"
            :prefix-icon="User"
            clearable
          />
        </el-form-item>
        <el-form-item prop="password">
          <el-input
            v-model="form.password"
            type="password"
            placeholder="请输入密码"
            size="large"
            :prefix-icon="Lock"
            show-password
            clearable
          />
        </el-form-item>
        <el-form-item>
          <el-button
            type="primary"
            size="large"
            class="login-btn"
            :loading="loading"
            @click="handleLogin"
          >
            {{ loading ? '登录中...' : '立即登录' }}
          </el-button>
        </el-form-item>
      </el-form>
      <div class="login-footer">
        <span>© 2024 ISP Manager · 专业网络资源管理平台</span>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { User, Lock, Monitor } from '@element-plus/icons-vue'
import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const route = useRoute()
const authStore = useAuthStore()

const formRef = ref(null)
const loading = ref(false)
const sessionExpired = computed(() => Boolean(route.query.expired))

onMounted(() => {
  if (sessionExpired.value) {
    ElMessage.warning('登录已超时，请重新登录')
  }
})

const form = reactive({ username: '', password: '' })

const rules = {
  username: [{ required: true, message: '请输入用户名', trigger: 'blur' }],
  password: [{ required: true, message: '请输入密码', trigger: 'blur' }]
}

async function handleLogin() {
  if (!formRef.value) return
  const valid = await formRef.value.validate().catch(() => false)
  if (!valid) return
  loading.value = true
  try {
    await authStore.login(form.username, form.password)
    ElMessage.success('登录成功，欢迎回来！')
    const target = typeof route.query.redirect === 'string' ? route.query.redirect : '/'
    try {
      await router.replace(target)
    } catch {
      window.location.assign(target)
    }
  } catch (err) {
    const msg = err?.response?.data?.message || err?.response?.data?.detail || '用户名或密码错误'
    ElMessage.error(msg)
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.login-container {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #0a0e27 0%, #1a1f4e 50%, #0d1b3e 100%);
  position: relative;
  overflow: hidden;
}

.login-bg {
  position: absolute;
  inset: 0;
  pointer-events: none;
}

.bg-circle {
  position: absolute;
  border-radius: 50%;
  background: radial-gradient(circle, rgba(64, 158, 255, 0.15), transparent 70%);
  animation: pulse 6s ease-in-out infinite;
}

.circle1 {
  width: 600px;
  height: 600px;
  top: -200px;
  right: -100px;
  animation-delay: 0s;
}

.circle2 {
  width: 400px;
  height: 400px;
  bottom: -100px;
  left: -100px;
  background: radial-gradient(circle, rgba(103, 194, 58, 0.1), transparent 70%);
  animation-delay: 2s;
}

.circle3 {
  width: 300px;
  height: 300px;
  top: 50%;
  left: 30%;
  background: radial-gradient(circle, rgba(230, 162, 60, 0.08), transparent 70%);
  animation-delay: 4s;
}

@keyframes pulse {
  0%, 100% { transform: scale(1); opacity: 0.8; }
  50% { transform: scale(1.1); opacity: 1; }
}

.login-card {
  width: 420px;
  background: rgba(255, 255, 255, 0.05);
  backdrop-filter: blur(20px);
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 20px;
  padding: 50px 40px 40px;
  box-shadow: 0 25px 50px rgba(0, 0, 0, 0.5);
  position: relative;
  z-index: 1;
}

.login-header {
  text-align: center;
  margin-bottom: 36px;
}

.logo {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 70px;
  height: 70px;
  background: linear-gradient(135deg, rgba(64, 158, 255, 0.3), rgba(64, 158, 255, 0.1));
  border-radius: 16px;
  margin-bottom: 16px;
  border: 1px solid rgba(64, 158, 255, 0.3);
}

.login-header h1 {
  color: #fff;
  font-size: 26px;
  font-weight: 700;
  letter-spacing: 2px;
  margin-bottom: 8px;
}

.login-header p {
  color: rgba(255, 255, 255, 0.5);
  font-size: 13px;
}

.idle-alert {
  margin-bottom: 16px;
}

.login-form :deep(.el-input__wrapper) {
  background: rgba(255, 255, 255, 0.08);
  border: 1px solid rgba(255, 255, 255, 0.15);
  box-shadow: none;
  border-radius: 10px;
  height: 48px;
}

.login-form :deep(.el-input__wrapper:hover),
.login-form :deep(.el-input__wrapper.is-focus) {
  border-color: #409EFF;
  background: rgba(64, 158, 255, 0.1);
}

.login-form :deep(.el-input__inner) {
  color: #fff;
  font-size: 14px;
}

.login-form :deep(.el-input__inner::placeholder) {
  color: rgba(255, 255, 255, 0.3);
}

.login-form :deep(.el-input__prefix-icon) {
  color: rgba(255, 255, 255, 0.4);
}

.login-btn {
  width: 100%;
  height: 48px;
  font-size: 16px;
  font-weight: 600;
  border-radius: 10px;
  background: linear-gradient(135deg, #409EFF, #007bff);
  border: none;
  letter-spacing: 2px;
  margin-top: 8px;
  transition: all 0.3s;
}

.login-btn:hover {
  transform: translateY(-2px);
  box-shadow: 0 8px 25px rgba(64, 158, 255, 0.5);
}

.login-footer {
  text-align: center;
  margin-top: 28px;
  color: rgba(255, 255, 255, 0.25);
  font-size: 12px;
}

:deep(.el-form-item) {
  margin-bottom: 20px;
}

:deep(.el-form-item__error) {
  color: #ff6b6b;
}
</style>
