<template>
  <el-upload
    class="archive-upload"
    drag
    :accept="ARCHIVE_ACCEPT"
    :disabled="disabled"
    :show-file-list="false"
    :http-request="handleRequest"
    :before-upload="beforeArchiveUpload"
    :name="'file'"
  >
    <el-icon class="el-icon--upload"><UploadFilled /></el-icon>
    <div class="el-upload__text">
      将 <em>.tar.gz</em> 拖到此处，或 <em>点击选择本地文件</em>
    </div>
    <template #tip>
      <div class="el-upload__tip">
        {{ tip || '支持 .tar.gz / .tgz / .tar。可拖入，或在对话框里把类型改成「所有文件」。' }}
        <el-button
          link
          type="primary"
          :disabled="disabled"
          @click.stop="openAllFiles"
        >从全部文件中选择</el-button>
      </div>
    </template>
  </el-upload>
  <input
    ref="allFilesInput"
    type="file"
    class="all-files-input"
    @change="onAllFilesPicked"
  >
</template>

<script setup>
import { ref } from 'vue'
import { UploadFilled } from '@element-plus/icons-vue'
import { ARCHIVE_ACCEPT, beforeArchiveUpload } from '@/utils/archiveUpload'

defineProps({
  disabled: { type: Boolean, default: false },
  tip: { type: String, default: '' },
})

const emit = defineEmits(['upload'])
const allFilesInput = ref(null)

function handleRequest(options) {
  emit('upload', options)
}

function openAllFiles() {
  allFilesInput.value?.click()
}

function onAllFilesPicked(event) {
  const file = event.target.files?.[0]
  event.target.value = ''
  if (!beforeArchiveUpload(file)) return
  emit('upload', { file })
}
</script>

<style scoped>
.archive-upload {
  width: min(520px, 100%);
}
.archive-upload :deep(.el-upload) {
  width: 100%;
}
.archive-upload :deep(.el-upload-dragger) {
  padding: 18px 16px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
}
.archive-upload :deep(.el-icon--upload) {
  margin-bottom: 6px;
  font-size: 28px;
  color: #409eff;
}
.all-files-input {
  display: none;
}
</style>
