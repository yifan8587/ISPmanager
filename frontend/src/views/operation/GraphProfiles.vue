<template>
  <div class="page">
    <div class="page-toolbar">
      <div class="pt-left">
        <span class="pt-title">图形 / 计费规则</span>
        <el-text type="info" size="small">定义时序图、95/99 峰值计费等可复用规则，供监控图形与流量截取引用。</el-text>
      </div>
      <el-button type="primary" :icon="Plus" @click="openEdit()">新增规则</el-button>
    </div>

    <el-table v-loading="loading" :data="list" border stripe>
      <el-table-column prop="code" label="编码" width="200" show-overflow-tooltip>
        <template #default="{ row }">
          <el-text type="primary">{{ row.code }}</el-text>
          <el-tag v-if="row.is_system" size="small" type="info" effect="plain" style="margin-left:4px;">系统</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="name" label="名称" min-width="160" />
      <el-table-column prop="profile_type_label" label="类型" width="130" />
      <el-table-column label="计费" width="200">
        <template #default="{ row }">
          <template v-if="row.profile_type === 'percentile_billing'">
            <el-tag size="small" type="warning">{{ row.percentile_n }} 分位</el-tag>
            <span style="font-size:12px;color:#606266;margin-left:4px;">{{ row.billing_mode_label }}</span>
          </template>
          <span v-else style="color:#C0C4CC;">-</span>
        </template>
      </el-table-column>
      <el-table-column prop="unit_display" label="单位" width="80" />
      <el-table-column label="对齐(秒)" prop="sample_align_sec" width="90" align="center" />
      <el-table-column label="状态" width="80">
        <template #default="{ row }">
          <el-tag :type="row.is_active ? 'success' : 'info'" size="small">{{ row.is_active ? '启用' : '停用' }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="150" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" size="small" @click="openEdit(row)">编辑</el-button>
          <el-button link type="danger" size="small" :disabled="row.is_system" @click="doDelete(row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="dialogVisible" :title="editRow ? '编辑规则' : '新增规则'" width="640px" destroy-on-close>
      <el-form :model="form" label-width="130px">
        <el-row :gutter="16">
          <el-col :span="12">
            <el-form-item label="编码" required>
              <el-input v-model="form.code" :disabled="!!editRow?.is_system" placeholder="如 traffic_p95_custom" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="名称" required>
              <el-input v-model="form.name" />
            </el-form-item>
          </el-col>
        </el-row>
        <el-form-item label="规则类型">
          <el-select v-model="form.profile_type" style="width:100%">
            <el-option label="时序流量图" value="timeseries" />
            <el-option label="95/99 峰值计费" value="percentile_billing" />
            <el-option label="入出叠加图" value="stacked_traffic" />
            <el-option label="单指标图" value="single_metric" />
          </el-select>
        </el-form-item>
        <template v-if="form.profile_type === 'percentile_billing'">
          <el-row :gutter="16">
            <el-col :span="12">
              <el-form-item label="分位数(N)">
                <el-input-number v-model="form.percentile_n" :min="50" :max="100" style="width:100%" />
              </el-form-item>
            </el-col>
            <el-col :span="12">
              <el-form-item label="计费模式">
                <el-select v-model="form.billing_mode" style="width:100%">
                  <el-option label="每采样 max(入,出)" value="max_in_out" />
                  <el-option label="仅入站" value="in_only" />
                  <el-option label="仅出站" value="out_only" />
                  <el-option label="入+出求和" value="sum_in_out" />
                </el-select>
              </el-form-item>
            </el-col>
          </el-row>
        </template>
        <el-row :gutter="16">
          <el-col :span="8">
            <el-form-item label="聚合方式">
              <el-select v-model="form.aggregation" style="width:100%">
                <el-option label="平均值" value="avg" />
                <el-option label="最大值" value="max" />
                <el-option label="最小值" value="min" />
                <el-option label="求和" value="sum" />
                <el-option label="峰值" value="peak" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="展示单位">
              <el-select v-model="form.unit_display" style="width:100%">
                <el-option label="bps" value="bps" />
                <el-option label="Kbps" value="Kbps" />
                <el-option label="Mbps" value="Mbps" />
                <el-option label="Gbps" value="Gbps" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="采样对齐(秒)">
              <el-input-number v-model="form.sample_align_sec" :min="60" :step="60" style="width:100%" />
            </el-form-item>
          </el-col>
        </el-row>
        <el-form-item label="入站匹配模式">
          <el-input v-model="form.in_patterns_text" placeholder="逗号分隔，如 net.if.in*, *ifHCInOctets*" />
        </el-form-item>
        <el-form-item label="出站匹配模式">
          <el-input v-model="form.out_patterns_text" placeholder="逗号分隔，如 net.if.out*, *ifHCOutOctets*" />
        </el-form-item>
        <el-form-item label="接口名正则">
          <el-input v-model="form.interface_name_pattern" placeholder="可选，如 ^eth\\d+$" />
        </el-form-item>
        <el-row :gutter="16">
          <el-col :span="8">
            <el-form-item label="计数器转速率">
              <el-switch v-model="form.convert_counter_to_rate" />
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="叠加显示">
              <el-switch v-model="form.stack_series" />
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="启用">
              <el-switch v-model="form.is_active" />
            </el-form-item>
          </el-col>
        </el-row>
        <el-form-item label="备注">
          <el-input v-model="form.remark" type="textarea" :rows="2" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="doSave">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus } from '@element-plus/icons-vue'
import {
  getGraphProfiles, createGraphProfile, updateGraphProfile, deleteGraphProfile,
} from '@/api/operation'

const loading = ref(false)
const list = ref([])
const dialogVisible = ref(false)
const saving = ref(false)
const editRow = ref(null)

const defaultForm = () => ({
  code: '', name: '', profile_type: 'percentile_billing',
  aggregation: 'avg', percentile_n: 95, billing_mode: 'max_in_out', unit_display: 'Mbps',
  sample_align_sec: 300, in_patterns_text: '', out_patterns_text: '', interface_name_pattern: '',
  stack_series: false, convert_counter_to_rate: true, is_active: true, remark: '',
})
const form = reactive(defaultForm())

async function load() {
  loading.value = true
  try {
    const res = await getGraphProfiles()
    list.value = res?.results || res || []
  } catch (e) {
    ElMessage.error('加载失败：' + (e?.response?.data?.detail || e.message))
  } finally { loading.value = false }
}

function openEdit(row = null) {
  editRow.value = row
  Object.assign(form, defaultForm())
  if (row) {
    Object.assign(form, {
      code: row.code, name: row.name, profile_type: row.profile_type,
      aggregation: row.aggregation, percentile_n: row.percentile_n,
      billing_mode: row.billing_mode, unit_display: row.unit_display,
      sample_align_sec: row.sample_align_sec,
      in_patterns_text: (row.item_key_in_patterns || []).join(', '),
      out_patterns_text: (row.item_key_out_patterns || []).join(', '),
      interface_name_pattern: row.interface_name_pattern || '',
      stack_series: row.stack_series, convert_counter_to_rate: row.convert_counter_to_rate,
      is_active: row.is_active, remark: row.remark || '',
    })
  }
  dialogVisible.value = true
}

function splitPatterns(text) {
  return (text || '').split(/[,，\s]+/).map(s => s.trim()).filter(Boolean)
}

async function doSave() {
  if (!form.code || !form.name) { ElMessage.warning('请填写编码和名称'); return }
  saving.value = true
  try {
    const payload = {
      code: form.code, name: form.name, profile_type: form.profile_type,
      aggregation: form.aggregation, percentile_n: form.percentile_n,
      billing_mode: form.billing_mode, unit_display: form.unit_display,
      sample_align_sec: form.sample_align_sec,
      item_key_in_patterns: splitPatterns(form.in_patterns_text),
      item_key_out_patterns: splitPatterns(form.out_patterns_text),
      interface_name_pattern: form.interface_name_pattern,
      stack_series: form.stack_series, convert_counter_to_rate: form.convert_counter_to_rate,
      is_active: form.is_active, remark: form.remark,
    }
    if (editRow.value) await updateGraphProfile(editRow.value.id, payload)
    else await createGraphProfile(payload)
    ElMessage.success(editRow.value ? '已更新' : '已创建')
    dialogVisible.value = false
    load()
  } catch (e) {
    ElMessage.error('保存失败：' + (e?.response?.data?.detail || JSON.stringify(e?.response?.data) || e.message))
  } finally { saving.value = false }
}

async function doDelete(row) {
  await ElMessageBox.confirm(`确定删除规则「${row.name}」？`, '警告', { type: 'warning' })
  await deleteGraphProfile(row.id)
  ElMessage.success('已删除')
  load()
}

onMounted(load)
</script>

<style scoped>
.page { background: #fff; border-radius: 8px; padding: 16px 20px; }
.page-toolbar { display: flex; align-items: center; margin-bottom: 14px; }
.pt-left { display: flex; flex-direction: column; gap: 2px; }
.pt-title { font-size: 16px; font-weight: 700; color: #1a1a2e; }
.page-toolbar > .el-button { margin-left: auto; }
</style>
