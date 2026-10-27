<template>
  <div class="page-container">
    <div class="page-header">
      <h2 class="page-title">POP节点管理</h2>
      <div class="page-header-actions">
        <ExcelImportExport
          export-url="/api/resource/pop-points/export/"
          template-url="/api/resource/pop-points/import-template/"
          import-url="/api/resource/pop-points/import/"
          filename="POP节点.xlsx"
          hint="表格中的节点编号会原样保存；系统里已有该编号则覆盖更新，不会另建一条。"
          :params="excelParams"
          @imported="loadData"
        />
        <el-button type="primary" :icon="Plus" @click="openDialog()">新增节点</el-button>
      </div>
    </div>

    <el-card shadow="never" class="search-card">
      <el-form :model="searchForm" inline>
        <el-form-item label="关键词">
          <el-input v-model="searchForm.search" placeholder="节点名称/编号" clearable style="width:180px" @keyup.enter="loadData" />
        </el-form-item>
        <el-form-item label="状态">
          <el-select v-model="searchForm.status" clearable placeholder="全部" style="width:110px">
            <el-option v-for="s in statusOptions" :key="s.value" :label="s.label" :value="s.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="资源位置">
          <ResourceLocationFilter
            :location="location"
            :regions="regions"
            :zones="filteredZones"
            :pop-points="filteredPopPoints"
            :pop-label="popLabel"
            :loading="loadingLocation"
            @update="applyLocation"
            @change="reloadForLocation"
          />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :icon="Search" @click="loadData">搜索</el-button>
          <el-button @click="resetSearch">重置</el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <el-card shadow="never" style="margin-top:12px;">
      <el-table :data="groupedTableData" v-loading="loading" stripe border :span-method="locationSpanMethod">
        <!-- 后端字段：code（节点编号） -->
        <el-table-column label="节点编号" width="160">
          <template #default="{ row }">
            <ResourceTableGroupLabel v-if="isLocationGroup(row)" :row="row" />
            <span v-else>{{ row.code }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="name" label="节点名称" min-width="150" />
        <el-table-column label="类型" width="80">
          <template #default="{ row }">
            <el-tag size="small" :type="popTypeMap[row.pop_type]?.type || 'info'">
              {{ popTypeMap[row.pop_type]?.label || row.pop_type || 'POP' }}
            </el-tag>
          </template>
        </el-table-column>
        <!-- 后端字段：city_info.name（嵌套对象） -->
        <el-table-column label="城市" width="120">
          <template #default="{ row }">
            {{ row.city_info?.name || '-' }}
            <span v-if="row.city_info?.country_name" style="color:#909399;font-size:11px;">
              / {{ row.city_info.country_name }}
            </span>
            <div v-if="row.zone_name || row.availability_zone_name" class="form-hint">
              {{ row.zone_name || row.availability_zone_name }}
            </div>
          </template>
        </el-table-column>
        <el-table-column label="区域" width="120">
          <template #default="{ row }">{{ row.region_name || row.city_info?.region_name || '-' }}</template>
        </el-table-column>
        <el-table-column label="网络/ASN" width="130">
          <template #default="{ row }">
            <div style="display:flex;flex-direction:column;gap:2px;">
              <el-tag size="small" :type="row.network_type === 'home_broadband' ? 'warning' : 'primary'">
                {{ row.network_type_label || networkTypeMap[row.network_type] || '-' }}
              </el-tag>
              <el-tag size="small" effect="plain" type="info">
                {{ row.asn_nature_label || asnNatureMap[row.asn_nature] || '-' }}
              </el-tag>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="IP属性" width="90">
          <template #default="{ row }">
            <el-tag size="small" effect="plain">
              {{ row.ip_attribute_label || ipAttributeMap[row.ip_attribute] || '原生' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="status" label="状态" width="90">
          <template #default="{ row }">
            <el-tag :type="statusMap[row.status]?.type" size="small">{{ statusMap[row.status]?.label }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="部署" width="100">
          <template #default="{ row }">
            <el-tag :type="deployMap[row.deploy_status]?.type || 'info'" size="small">
              {{ row.deploy_status_label || deployMap[row.deploy_status]?.label || '未部署' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="总带宽(Mbps)" width="120" align="right">
          <template #default="{row}">
            <el-button link type="primary" size="small" @click="openBwDialog(row)">
              {{ row.total_bandwidth_mbps }}
            </el-button>
          </template>
        </el-table-column>
        <el-table-column label="带宽分配" width="240">
          <template #default="{ row }">
            <div style="display:flex;flex-direction:column;gap:3px;">
              <div style="display:flex;align-items:center;gap:6px;">
                <el-progress
                  :percentage="row.bw_usage_pct || 0"
                  :stroke-width="8"
                  :color="getBwColor(row.bw_usage_pct)"
                  style="flex:1;"
                />
                <span style="font-size:11px;color:#606266;white-space:nowrap;">
                  {{ row.used_bandwidth_mbps }}/{{ row.total_bandwidth_mbps }}M
                </span>
              </div>
              <div style="display:flex;gap:4px;flex-wrap:wrap;">
                <el-tag size="small" type="primary" v-if="row.vps_bandwidth_mbps">
                  VPS {{ row.vps_bandwidth_mbps }}M
                </el-tag>
                <el-tag size="small" type="success" v-if="row.edge_bandwidth_mbps">
                  Edge {{ row.edge_bandwidth_mbps }}M
                </el-tag>
                <el-tag size="small" type="info" v-if="row.available_bandwidth_mbps">
                  余 {{ row.available_bandwidth_mbps }}M
                </el-tag>
              </div>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="硬件配置" width="180">
          <template #default="{ row }">
            <div v-if="row.cpu_cores || row.ram_gb || row.disk_gb" style="font-size:12px;color:#606266;line-height:1.6;">
              <el-tag size="small" effect="plain">{{ row.cpu_cores || '-' }} 核</el-tag>
              <el-tag size="small" effect="plain" type="success" style="margin-left:4px">{{ row.ram_gb || '-' }} G</el-tag>
              <el-tag size="small" effect="plain" type="warning" style="margin-left:4px">{{ row.disk_gb || '-' }} G盘</el-tag>
            </div>
            <span v-else style="color:#C0C4CC;font-size:12px;">未填写</span>
          </template>
        </el-table-column>
        <!-- 后端字段：mgmt_ip（非 management_ip） -->
        <el-table-column prop="mgmt_ip" label="管理IP" width="140">
          <template #default="{ row }">{{ row.mgmt_ip || '-' }}</template>
        </el-table-column>
        <el-table-column label="运营商" width="160">
          <!-- 后端字段：carriers_info（数组对象） -->
          <template #default="{ row }">
            <el-tag
              v-for="c in (row.carriers_info || [])"
              :key="c.id"
              size="small"
              style="margin:1px;"
            >{{ c.code || c.name }}</el-tag>
            <span v-if="!row.carriers_info?.length" style="color:#C0C4CC">-</span>
          </template>
        </el-table-column>
        <el-table-column label="供应商" width="130">
          <template #default="{ row }">
            <span v-if="row.supplier_name">{{ row.supplier_name }}</span>
            <span v-else style="color:#C0C4CC">-</span>
          </template>
        </el-table-column>
        <el-table-column label="配置模式" width="100">
          <template #default="{ row }">
            <el-tag :type="configModeMap[row.config_mode]?.type || 'info'" size="small">
              {{ row.config_mode_label || configModeMap[row.config_mode]?.label || '手动配置' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="IP交付" width="100">
          <template #default="{ row }">
            <el-tag :type="row.ip_delivery_mode === 'nat' ? 'warning' : 'success'" size="small">
              {{ row.ip_delivery_mode_label || (row.ip_delivery_mode === 'nat' ? 'NAT 交付' : '公网直连') }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="VPS/边缘" width="100" align="center">
          <template #default="{ row }">
            <el-tag size="small" type="info">{{ row.vps_count || 0 }}V/{{ row.edge_count || 0 }}E</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="250" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="openDetail(row)">
              <el-icon><Document/></el-icon> 详情
            </el-button>
            <el-button link type="success" size="small" @click="openDeploy(row)">
              <el-icon><Monitor/></el-icon> 部署
            </el-button>
            <el-button link type="primary" size="small" @click="openDialog(row)">编辑</el-button>
            <el-button link type="danger" size="small" @click="handleDelete(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-pagination v-model:current-page="page" v-model:page-size="pageSize" :total="total"
        layout="total, prev, pager, next" class="pagination" @change="loadData" />
    </el-card>

    <el-dialog v-model="dialogVisible" :title="editRow ? '编辑POP节点' : '新增POP节点'" width="760px" destroy-on-close>
      <el-form ref="formRef" v-loading="dialogLoading" :model="form" :rules="formRules" label-width="110px">
        <el-divider content-position="left">基础信息</el-divider>
        <el-row :gutter="16">
          <el-col :span="12">
            <el-form-item label="节点名称" prop="name">
              <el-input v-model="form.name" placeholder="如 Tokyo-POP-01" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="节点类型" prop="pop_type">
              <el-select v-model="form.pop_type" style="width:100%">
                <el-option v-for="t in popTypeOptions" :key="t.value" :label="t.label" :value="t.value" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="状态">
              <el-select v-model="form.status" style="width:100%">
                <el-option v-for="s in statusOptions" :key="s.value" :label="s.label" :value="s.value" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="所在城市" prop="city">
              <el-select v-model="form.city" filterable placeholder="搜索选择城市" style="width:100%" @change="onFormCityChange">
                <el-option
                  v-for="c in cities"
                  :key="c.id"
                  :label="[c.region_name, c.country_name, c.name].filter(Boolean).join(' / ')"
                  :value="c.id"
                />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="可用区">
              <el-select v-model="form.availability_zone" clearable filterable placeholder="可选，默认为空" style="width:100%">
                <el-option v-for="z in formZones" :key="z.id" :label="z.code ? `${z.name} (${z.code})` : z.name" :value="z.id" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="网络类型">
              <el-select v-model="form.network_type" style="width:100%">
                <el-option v-for="t in networkTypeOptions" :key="t.value" :label="t.label" :value="t.value" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="ASN/企业性质">
              <el-select v-model="form.asn_nature" style="width:100%">
                <el-option v-for="t in asnNatureOptions" :key="t.value" :label="t.label" :value="t.value" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="IP属性">
              <el-select v-model="form.ip_attribute" style="width:100%">
                <el-option v-for="t in ipAttributeOptions" :key="t.value" :label="t.label" :value="t.value" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="24">
            <el-form-item label="运营商">
              <el-select v-model="form.carriers" multiple filterable placeholder="选择关联运营商" style="width:100%">
                <el-option v-for="c in carriers" :key="c.id" :label="`${c.code || ''} ${c.name}`" :value="c.id" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="12" v-if="!isSupplierAccount">
            <el-form-item label="供应商">
              <el-select v-model="form.supplier" clearable filterable placeholder="选择关联供应商（便于后续对接）" style="width:100%">
                <el-option v-for="s in suppliers" :key="s.id" :label="`${s.supplier_no || ''} ${s.name}`" :value="s.id" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="供应商侧POP标识">
              <el-input v-model="form.supplier_external_id" placeholder="供应商系统中对应的 POP 标识（可选）" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="配置模式">
              <el-select v-model="form.config_mode" style="width:100%">
                <el-option v-for="m in configModeOptions" :key="m.value" :label="m.label" :value="m.value" />
              </el-select>
              <div class="form-hint">自动：工单提交后资源足够则自动下发开通；手动：需人工处理</div>
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="IP交付方式">
              <el-select v-model="form.ip_delivery_mode" style="width:100%">
                <el-option label="公网直连" value="public" />
                <el-option label="NAT 交付" value="nat" />
              </el-select>
              <div class="form-hint">NAT 交付：业务 IP 仅经节点 NAT 访问，产品与门户会提示客户</div>
            </el-form-item>
          </el-col>
        </el-row>

        <el-divider content-position="left">管理信息</el-divider>
        <div v-if="editRow" class="form-hint section-keep-hint">已读取当前配置。这里没改的内容会原样保留，密码留空表示不修改。</div>
        <el-row :gutter="16">
          <!-- 后端字段：mgmt_ip（非 management_ip） -->
          <el-col :span="12">
            <el-form-item label="管理IP">
              <el-input v-model="form.mgmt_ip" placeholder="如 10.0.0.1" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="SSH端口">
              <el-input-number v-model="form.ssh_port" :min="1" :max="65535" :controls="false" style="width:100%" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="管理用户名">
              <el-input v-model="form.mgmt_username" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="管理密码">
              <el-input
                v-model="form.mgmt_password"
                type="password"
                show-password
                :placeholder="editRow ? (secretFlags.mgmt_password ? '已保存，留空则不修改' : '未设置') : '管理密码'"
              />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="管理URL">
              <el-input v-model="form.mgmt_url" placeholder="http://..." />
            </el-form-item>
          </el-col>
        </el-row>

        <el-divider content-position="left">节点 API（部署后数据同步用）</el-divider>
        <div v-if="editRow" class="form-hint section-keep-hint">已读取当前 API 配置。只改动需要变更的项，密码留空则继续使用已保存的密码。</div>
        <el-row :gutter="16">
          <el-col :span="24">
            <el-form-item label="API 基址">
              <el-input
                v-model="form.api_base_url"
                placeholder="http://1.2.3.4 或 https://pe.example.com"
                clearable
              />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="API 用户名">
              <el-input
                v-model="form.api_username"
                placeholder="如 admin（默认可与 SSH 用户名相同）"
                clearable
              />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="API 密码">
              <el-input
                v-model="form.api_password"
                type="password"
                show-password
                :placeholder="editRow ? (secretFlags.api_password ? '已保存，留空则不修改' : '未设置') : '节点 pemanager 登录密码'"
                clearable
              />
            </el-form-item>
          </el-col>
          <el-col :span="24">
            <el-form-item label="校验 HTTPS 证书">
              <el-switch v-model="form.api_verify_ssl" />
              <div class="form-hint">
                节点面板按 IP 访问时多为自签证书，请保持关闭。关闭后仍走 HTTPS，只是不校验证书。已部署正式域名证书时可打开。
              </div>
            </el-form-item>
          </el-col>
        </el-row>

        <el-divider content-position="left">网络带宽</el-divider>
        <el-row :gutter="16">
          <el-col :span="12">
            <el-form-item label="总带宽(Mbps)">
              <el-input-number v-model="form.total_bandwidth_mbps" :min="0" style="width:100%" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="已用带宽">
              <el-input v-model="usedBandwidthDisplay" disabled>
                <template #append>Mbps（自动汇总）</template>
              </el-input>
            </el-form-item>
          </el-col>
        </el-row>

        <el-divider content-position="left">硬件信息（用于产品规划/部署测算可开通数量）</el-divider>
        <el-alert type="info" :closable="false" show-icon style="margin-bottom:12px"
          title="填写 CPU 核心数 / 内存 / 硬盘总量后，系统会在产品规划与部署时自动测算该 POP 还能开通多少产品。" />
        <el-row :gutter="16">
          <el-col :span="8">
            <el-form-item label="CPU核心数">
              <el-input-number v-model="form.cpu_cores" :min="0" controls-position="right" style="width:100%" />
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="内存(GB)">
              <el-input-number v-model="form.ram_gb" :min="0" controls-position="right" style="width:100%" />
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="硬盘(GB)">
              <el-input-number v-model="form.disk_gb" :min="0" :step="10" controls-position="right" style="width:100%" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="CPU型号">
              <el-input v-model="form.cpu_model" placeholder="可选，如 Xeon E5-2680v4" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="硬盘描述">
              <el-input v-model="form.disk_info" placeholder="可选，如 2×480GB SSD" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="操作系统">
              <el-select v-model="form.os_type" clearable style="width:100%">
                <el-option v-for="o in osOptions" :key="o.value" :label="o.label" :value="o.value" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="系统版本">
              <el-input v-model="form.os_version" />
            </el-form-item>
          </el-col>
        </el-row>

        <el-form-item label="备注">
          <el-input v-model="form.remark" type="textarea" :rows="2" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" :disabled="dialogLoading" @click="handleSave">确认</el-button>
      </template>
    </el-dialog>

    <!-- 带宽设置弹窗 -->
    <el-dialog v-model="bwDialogVisible" title="设置POP节点总带宽" width="500px" destroy-on-close>
      <div v-if="bwPop" style="margin-bottom:16px;">
        <el-descriptions :column="2" border size="small">
          <el-descriptions-item label="节点">
            <el-tag type="primary" size="small">{{ bwPop.code }}</el-tag> {{ bwPop.name }}
          </el-descriptions-item>
          <el-descriptions-item label="当前总带宽">{{ bwPop.total_bandwidth_mbps }} Mbps</el-descriptions-item>
          <el-descriptions-item label="VPS已分配">
            <el-tag type="primary" size="small">{{ bwPop.vps_bandwidth_mbps || 0 }} Mbps</el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="Edge已分配">
            <el-tag type="success" size="small">{{ bwPop.edge_bandwidth_mbps || 0 }} Mbps</el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="已用合计" :span="2">
            <el-tag :type="(bwPop.bw_usage_pct||0)>=90?'danger':(bwPop.bw_usage_pct||0)>=70?'warning':'success'">
              {{ bwPop.used_bandwidth_mbps || 0 }} Mbps ({{ bwPop.bw_usage_pct || 0 }}%)
            </el-tag>
          </el-descriptions-item>
        </el-descriptions>
        <!-- 带宽进度条 -->
        <div style="margin-top:12px;">
          <el-progress
            :percentage="bwPop.bw_usage_pct || 0"
            :stroke-width="14"
            :color="getBwColor(bwPop.bw_usage_pct)"
            :format="p => p + '%'"
          />
        </div>
      </div>
      <el-form label-width="100px">
        <el-form-item label="新总带宽">
          <el-input-number
            v-model="bwForm.total_bandwidth_mbps"
            :min="bwPop?.used_bandwidth_mbps || 0"
            :step="100"
            style="width:200px"
          /> <span style="margin-left:8px;color:#909399;">Mbps</span>
        </el-form-item>
        <el-form-item>
          <el-text type="warning" size="small">
            最小值不能低于当前已分配带宽 {{ bwPop?.used_bandwidth_mbps || 0 }} Mbps
          </el-text>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="bwDialogVisible=false">取消</el-button>
        <el-button type="primary" :loading="bwSaving" @click="handleSetBandwidth">确认设置</el-button>
      </template>
    </el-dialog>

  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Search, Document, Monitor } from '@element-plus/icons-vue'
import { getPopPoints, createPopPoint, updatePopPoint, deletePopPoint, getCarriers, getAvailabilityZones, setPopPointBandwidth, getPopPointDetail } from '@/api/resource'
import { getSuppliers } from '@/api/supplier'
import { useRegionGroupedRows, useResourceLocation } from '@/composables/useResourceLocation'
import ResourceLocationFilter from './components/ResourceLocationFilter.vue'
import ResourceTableGroupLabel from './components/ResourceTableGroupLabel.vue'
import ExcelImportExport from '@/components/ExcelImportExport.vue'
import { useAuthStore } from '@/stores/auth'

const route = useRoute()
const router = useRouter()
const authStore = useAuthStore()
const isSupplierAccount = computed(() => authStore.user?.role === 'supplier' || authStore.user?.is_supplier_account)

const loading = ref(false)
const saving = ref(false)
const dialogVisible = ref(false)
const dialogLoading = ref(false)
let dialogSeq = 0
const secretFlags = reactive({ mgmt_password: false, api_password: false, mgmt_token: false })
const originalMgmt = ref(null)
const bwDialogVisible = ref(false)
const bwSaving = ref(false)
const bwPop = ref(null)
const bwForm = reactive({ total_bandwidth_mbps: 0 })
function openDetail(row) {
  router.push(`/resource/pop/${row.id}`)
}

function openDeploy(row) {
  router.push(`/resource/pop/${row.id}/deploy`)
}

function openBwDialog(row) {
  bwPop.value = row
  bwForm.total_bandwidth_mbps = row.total_bandwidth_mbps || 0
  bwDialogVisible.value = true
}

async function handleSetBandwidth() {
  bwSaving.value = true
  try {
    await setPopPointBandwidth(bwPop.value.id, { total_bandwidth_mbps: bwForm.total_bandwidth_mbps })
    ElMessage.success(`总带宽已设置为 ${bwForm.total_bandwidth_mbps} Mbps`)
    bwDialogVisible.value = false
    loadData()
  } catch (e) {
    const msg = e?.response?.data?.detail || e.message
    ElMessage.error('设置失败：' + msg)
  } finally {
    bwSaving.value = false
  }
}
const formRef = ref(null)
const editRow = ref(null)
const tableData = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const carriers = ref([])
const suppliers = ref([])
const zones = ref([])
const formZones = computed(() => {
  if (!form.city) return zones.value
  return zones.value.filter((item) => String(item.city) === String(form.city))
})
function onFormCityChange() {
  if (form.availability_zone && !formZones.value.some((item) => item.id === form.availability_zone)) {
    form.availability_zone = null
  }
}

const searchForm = reactive({ search: '', status: '' })
const {
  location, regions, cities, filteredZones, filteredPopPoints,
  loadingLocation, popLabel,
  resetLocation, locationParams, loadLocationOptions,
} = useResourceLocation()
const excelParams = computed(() => {
  const params = { ...locationParams() }
  if (searchForm.search) params.search = searchForm.search
  if (searchForm.status) params.status = searchForm.status
  return params
})
const {
  groupedRows: groupedTableData,
  isLocationGroup,
  locationSpanMethod,
} = useRegionGroupedRows(tableData, 19)

const configModeMap = { auto: { label: '自动配置', type: 'success' }, manual: { label: '手动配置', type: 'info' } }
const configModeOptions = [
  { value: 'manual', label: '手动配置' }, { value: 'auto', label: '自动配置' }
]

// 已用带宽只读展示（后端自动汇总，非写入字段）
const usedBandwidthDisplay = ref(0)

// 与后端 PopPointWriteSerializer.fields 完全对齐
const form = reactive({
  name: '',
  pop_type: 'pop',
  status: 'online',
  city: null,
  availability_zone: null,
  network_type: 'idc',
  asn_nature: 'isp',
  ip_attribute: 'native',
  carriers: [],          // M2M字段，传ID列表
  supplier: null,        // 供应商 FK
  supplier_external_id: '',
  config_mode: 'manual', // 自动配置 / 手动配置
  ip_delivery_mode: 'public',
  mgmt_ip: '',           // 后端：mgmt_ip（非 management_ip）
  ssh_port: 22,
  mgmt_username: '',
  mgmt_password: '',
  mgmt_token: '',
  mgmt_url: '',
  api_base_url: '',
  api_username: '',
  api_password: '',
  api_verify_ssl: false,
  snmp_community: '',
  snmp_version: 'v2c',
  cpu_model: '',
  cpu_cores: null,
  ram_gb: null,
  disk_gb: null,
  disk_info: '',
  os_type: '',
  os_version: '',
  total_bandwidth_mbps: 1000,
  remark: ''
  // 注意：没有 rack_location 字段（后端不存在此字段）
  // 已用带宽由后端从下挂 VPS + 边缘加速自动汇总，前端只读展示
})

const statusOptions = [
  { value: 'online', label: '在线' }, { value: 'offline', label: '离线' },
  { value: 'maintenance', label: '维护中' }, { value: 'decommissioned', label: '已下线' }
]
const statusMap = {
  online: { label: '在线', type: 'success' }, offline: { label: '离线', type: 'danger' },
  maintenance: { label: '维护中', type: 'warning' }, decommissioned: { label: '已下线', type: 'info' }
}
const popTypeOptions = [
  { value: 'pop', label: 'POP' }, { value: 'pe', label: 'PE' },
  { value: 'vdc', label: 'VDC' }, { value: 'other', label: '其他' }
]
const popTypeMap = {
  pop: { label: 'POP', type: 'primary' }, pe: { label: 'PE', type: 'warning' },
  vdc: { label: 'VDC', type: 'success' }, other: { label: '其他', type: 'info' }
}
const deployMap = {
  undeployed: { label: '未部署', type: 'info' }, deploying: { label: '部署中', type: 'warning' },
  deployed: { label: '已部署', type: 'success' }, failed: { label: '部署失败', type: 'danger' }
}
const networkTypeOptions = [
  { value: 'home_broadband', label: '家宽' }, { value: 'idc', label: 'IDC' }
]
const networkTypeMap = { home_broadband: '家宽', idc: 'IDC' }
const asnNatureOptions = [
  { value: 'isp', label: '单ISP' }, { value: 'dual_isp', label: '双ISP' }, { value: 'non_isp', label: '非ISP' }
]
const asnNatureMap = { isp: '单ISP', non_isp: '非ISP', dual_isp: '双ISP' }
const ipAttributeOptions = [
  { value: 'native', label: '原生' }, { value: 'broadcast', label: '广播' },
]
const ipAttributeMap = { native: '原生', broadcast: '广播' }
const osOptions = [
  { value: 'debian', label: 'Debian' }, { value: 'ubuntu', label: 'Ubuntu' },
  { value: 'centos', label: 'CentOS' }, { value: 'rhel', label: 'RHEL' },
  { value: 'windows', label: 'Windows Server' }, { value: 'other', label: '其他' }
]

const formRules = {
  name: [{ required: true, message: '请输入节点名称' }],
  city: [{ required: true, message: '请选择城市' }]
}

function getBwColor(pct) {
  if ((pct || 0) >= 90) return '#F56C6C'
  if ((pct || 0) >= 70) return '#E6A23C'
  return '#67C23A'
}

async function loadData() {
  loading.value = true
  try {
    const params = { page: page.value, page_size: pageSize.value, ...locationParams() }
    if (searchForm.search) params.search = searchForm.search
    if (searchForm.status) params.status = searchForm.status
    const res = await getPopPoints(params)
    tableData.value = res?.results || []
    total.value = res?.count || 0
  } finally { loading.value = false }
}

function applyLocation(next) {
  Object.assign(location, next)
}

function reloadForLocation() {
  page.value = 1
  loadData()
}

function resetSearch() {
  Object.assign(searchForm, { search: '', status: '' })
  resetLocation()
  loadData()
}

function resetSecretFlags() {
  secretFlags.mgmt_password = false
  secretFlags.api_password = false
  secretFlags.mgmt_token = false
  originalMgmt.value = null
}

function applyEditForm(row) {
  secretFlags.mgmt_password = !!(row.has_mgmt_password || row.mgmt_password)
  secretFlags.api_password = !!(row.has_api_password || row.api_password)
  secretFlags.mgmt_token = !!(row.has_mgmt_token || row.mgmt_token)
  originalMgmt.value = {
    mgmt_ip: row.mgmt_ip || '',
    ssh_port: row.ssh_port ?? 22,
    mgmt_username: row.mgmt_username || '',
    mgmt_url: row.mgmt_url || '',
    api_base_url: row.api_base_url || '',
    api_username: row.api_username || '',
    api_verify_ssl: row.api_verify_ssl === true,
  }
  Object.assign(form, {
    name: row.name, pop_type: row.pop_type || 'pop', status: row.status, city: row.city,
    availability_zone: row.availability_zone || null,
    network_type: row.network_type || 'idc',
    asn_nature: row.asn_nature || 'isp',
    ip_attribute: row.ip_attribute || 'native',
    carriers: (row.carriers || []),
    supplier: row.supplier || null,
    supplier_external_id: row.supplier_external_id || '',
    config_mode: row.config_mode || 'manual',
    ip_delivery_mode: row.ip_delivery_mode || 'public',
    mgmt_ip: row.mgmt_ip || '',
    ssh_port: row.ssh_port ?? 22,
    mgmt_username: row.mgmt_username || '',
    mgmt_password: '',
    mgmt_token: '',
    mgmt_url: row.mgmt_url || '',
    api_base_url: row.api_base_url || '',
    api_username: row.api_username || '',
    api_password: '',
    api_verify_ssl: row.api_verify_ssl === true,
    snmp_community: '',
    snmp_version: row.snmp_version || 'v2c',
    cpu_model: row.cpu_model || '',
    cpu_cores: row.cpu_cores || null,
    ram_gb: row.ram_gb || null,
    disk_gb: row.disk_gb || null,
    disk_info: row.disk_info || '',
    os_type: row.os_type || '',
    os_version: row.os_version || '',
    total_bandwidth_mbps: row.total_bandwidth_mbps || 1000,
    remark: row.remark || ''
  })
  usedBandwidthDisplay.value = row.used_bandwidth_mbps || 0
}

async function openDialog(row = null) {
  const seq = ++dialogSeq
  dialogVisible.value = true
  if (!row) {
    editRow.value = null
    resetSecretFlags()
    Object.assign(form, {
      name: '', pop_type: 'pop', status: 'online', city: location.city || null, availability_zone: null, network_type: 'idc', asn_nature: 'isp', ip_attribute: 'native',
      carriers: [], supplier: null, supplier_external_id: '', config_mode: 'manual',
      ip_delivery_mode: 'public',
      mgmt_ip: '', ssh_port: 22, mgmt_username: '', mgmt_password: '',
      mgmt_token: '', mgmt_url: '', api_base_url: '', api_username: '', api_password: '',
      api_verify_ssl: false,
      snmp_community: '', snmp_version: 'v2c',
      cpu_model: '', cpu_cores: null, ram_gb: null, disk_gb: null, disk_info: '',
      os_type: '', os_version: '', total_bandwidth_mbps: 1000, remark: ''
    })
    usedBandwidthDisplay.value = 0
    if (isSupplierAccount.value && authStore.user?.supplier_id) {
      form.supplier = authStore.user.supplier_id
    }
    dialogLoading.value = false
    return
  }
  editRow.value = row
  dialogLoading.value = true
  try {
    const detail = await getPopPointDetail(row.id)
    if (seq !== dialogSeq) return
    editRow.value = detail
    applyEditForm(detail)
  } catch {
    if (seq !== dialogSeq) return
    applyEditForm(row)
  } finally {
    if (seq === dialogSeq) dialogLoading.value = false
  }
}

async function handleSave() {
  if (dialogLoading.value) return
  const valid = await formRef.value.validate().catch(() => false)
  if (!valid) return
  saving.value = true
  try {
    const payload = { ...form }
    if (isSupplierAccount.value && authStore.user?.supplier_id) {
      payload.supplier = authStore.user.supplier_id
    }
    const mgmtPwd = payload.mgmt_password
    if (!payload.mgmt_password) delete payload.mgmt_password
    if (!payload.mgmt_token) delete payload.mgmt_token
    if (!editRow.value) {
      // 新建时未单独填写，API 凭据默认与 SSH 管理凭据一致
      if (!payload.api_username && payload.mgmt_username) payload.api_username = payload.mgmt_username
      if (!payload.api_password && mgmtPwd) payload.api_password = mgmtPwd
    } else {
      // 编辑不改 SNMP，避免把未展示的团体名清空
      delete payload.snmp_community
      delete payload.snmp_version
      if (originalMgmt.value) {
        for (const key of Object.keys(originalMgmt.value)) {
          if (payload[key] === originalMgmt.value[key]) delete payload[key]
        }
      }
    }
    if (!payload.api_password) delete payload.api_password
    if (editRow.value) {
      await updatePopPoint(editRow.value.id, payload)
      ElMessage.success('已更新')
    } else {
      await createPopPoint(payload)
      ElMessage.success('节点创建成功')
    }
    dialogVisible.value = false
    loadData()
  } finally { saving.value = false }
}

async function handleDelete(row) {
  await ElMessageBox.confirm(`确定删除节点 "${row.name}"？`, '警告', { type: 'warning' })
  await deletePopPoint(row.id)
  ElMessage.success('已删除')
  loadData()
}

onMounted(async () => {
  loadData()
  const [, carriersRes, suppliersRes, zonesRes] = await Promise.allSettled([
    loadLocationOptions(),
    getCarriers({ page_size: 200 }),
    isSupplierAccount.value ? Promise.resolve({ results: [] }) : getSuppliers({ page_size: 200 }),
    getAvailabilityZones({ page_size: 2000 }),
  ])
  carriers.value = carriersRes.status === 'fulfilled' ? carriersRes.value?.results || [] : []
  suppliers.value = suppliersRes.status === 'fulfilled' ? suppliersRes.value?.results || [] : []
  zones.value = zonesRes.status === 'fulfilled' ? zonesRes.value?.results || [] : []

  // 从详情页「编辑节点」返回时自动打开编辑弹窗
  const editId = route.query.edit
  if (editId != null) {
    try {
      const row = await getPopPointDetail(editId)
      openDialog(row)
    } catch { /* ignore */ }
    router.replace({ path: '/resource/pop' })
  }
})
</script>

<style scoped>
.page-container { min-height: 100%; }
.page-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 16px; }
.page-header-actions { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.page-title { font-size: 18px; font-weight: 700; color: #1a1a2e; margin: 0; }
.search-card :deep(.el-card__body) { padding: 16px 16px 0; }
.pagination { margin-top: 16px; justify-content: flex-end; }
.form-hint { font-size: 12px; color: #909399; line-height: 1.4; margin-top: 4px; }
.section-keep-hint { margin: -4px 0 12px; }
</style>
