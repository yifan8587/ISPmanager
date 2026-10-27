<template>
  <div class="excel-io">
    <el-button :icon="Download" :loading="exporting" @click="doExport">导出 Excel</el-button>
    <el-button :icon="Document" :loading="templating" @click="doTemplate">下载模板</el-button>
    <el-button :icon="Upload" :loading="importing" @click="pickFile">导入 Excel</el-button>
    <span v-if="hint" class="excel-hint">{{ hint }}</span>
    <input ref="fileRef" type="file" accept=".xlsx" hidden @change="onFile" />
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Download, Document, Upload } from '@element-plus/icons-vue'
import { downloadExcel, uploadExcel } from '@/utils/excelTransfer'

const props = defineProps({
  exportUrl: { type: String, required: true },
  templateUrl: { type: String, required: true },
  importUrl: { type: String, required: true },
  params: { type: Object, default: () => ({}) },
  filename: { type: String, default: 'export.xlsx' },
  hint: { type: String, default: '' },
})

const emit = defineEmits(['imported'])

const fileRef = ref(null)
const exporting = ref(false)
const templating = ref(false)
const importing = ref(false)

async function doExport() {
  exporting.value = true
  try {
    await downloadExcel(props.exportUrl, props.params, props.filename)
    ElMessage.success('已导出当前筛选结果')
  } catch (err) {
    ElMessage.error(err?.response?.data?.detail || err.message || '导出失败')
  } finally {
    exporting.value = false
  }
}

async function doTemplate() {
  templating.value = true
  try {
    await downloadExcel(props.templateUrl, {}, props.filename.replace('.xlsx', '-模板.xlsx'))
    ElMessage.success('已下载空白模板，请按表头填写后导入')
  } catch (err) {
    ElMessage.error(err?.response?.data?.detail || err.message || '下载模板失败')
  } finally {
    templating.value = false
  }
}

function pickFile() {
  if (fileRef.value) fileRef.value.value = ''
  fileRef.value?.click()
}

function summarize(result) {
  const parts = [
    `新增 ${result.created || 0}`,
    `更新 ${result.updated || 0}`,
    `跳过 ${result.skipped || 0}`,
  ]
  if (result.generated_ips) parts.push(`解析 IP ${result.generated_ips}`)
  if (result.pricing_created || result.pricing_updated) {
    parts.push(`定价新增 ${result.pricing_created || 0}`)
    parts.push(`定价更新 ${result.pricing_updated || 0}`)
  }
  if (result.rules_created || result.rules_updated) {
    parts.push(`阶梯新增 ${result.rules_created || 0}`)
    parts.push(`阶梯更新 ${result.rules_updated || 0}`)
  }
  return parts.join('，')
}

async function onFile(event) {
  const file = event.target.files?.[0]
  if (!file) return
  if (!String(file.name || '').toLowerCase().endsWith('.xlsx')) {
    ElMessage.error('仅支持 .xlsx 文件，请先下载模板填写')
    return
  }
  importing.value = true
  try {
    const result = await uploadExcel(props.importUrl, file)
    const errors = result?.errors || []
    if (errors.length) {
      await ElMessageBox.alert(
        `${summarize(result)}\n\n${errors.slice(0, 12).join('\n')}${errors.length > 12 ? `\n…共 ${errors.length} 条错误` : ''}`,
        '导入完成（含错误）',
        { type: 'warning', confirmButtonText: '知道了' },
      )
    } else {
      ElMessage.success(`导入完成：${summarize(result)}`)
    }
    emit('imported', result)
  } catch (err) {
    const data = err?.response?.data
    const detail = data?.file || data?.detail || data?.message || err.message || '导入失败'
    ElMessage.error(Array.isArray(detail) ? detail.join('；') : String(detail))
  } finally {
    importing.value = false
  }
}
</script>

<style scoped>
.excel-io {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.excel-hint {
  color: #909399;
  font-size: 12px;
  line-height: 1.4;
  max-width: 360px;
}
</style>
