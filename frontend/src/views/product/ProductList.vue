<template>
  <div class="page-container">
    <div class="page-header">
      <h2 class="page-title">产品列表</h2>
      <div class="page-header-actions">
        <ExcelImportExport
          export-url="/api/product/products/export/"
          template-url="/api/product/products/import-template/"
          import-url="/api/product/products/import/"
          filename="产品列表.xlsx"
          hint="表格中的产品编号会原样保存；系统里已有该编号则覆盖更新，不会另建一条。"
          :params="excelParams"
          @imported="loadData"
        />
        <el-button type="primary" :icon="Plus" @click="openDialog()">新建产品</el-button>
      </div>
    </div>

    <el-card shadow="never" class="search-card">
      <el-form :model="searchForm" inline>
        <el-form-item label="类别">
          <el-radio-group v-model="searchForm.category" @change="loadData">
            <el-radio-button label="">全部</el-radio-button>
            <el-radio-button label="vps">VPS</el-radio-button>
            <el-radio-button label="edge">边缘加速</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="状态">
          <el-select v-model="searchForm.status" clearable placeholder="全部" style="width:110px" @change="loadData">
            <el-option v-for="s in statusOptions" :key="s.value" :label="s.label" :value="s.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="区域">
          <el-select v-model="searchForm.region" clearable placeholder="全部" style="width:130px" @change="onRegionChange">
            <el-option v-for="r in regionOptions" :key="r.id" :label="r.name" :value="r.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="国家">
          <el-select v-model="searchForm.country" clearable filterable placeholder="全部" style="width:140px" @change="onCountryChange">
            <el-option v-for="c in filteredCountries" :key="c.id" :label="c.name" :value="c.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="城市">
          <el-select v-model="searchForm.city" clearable filterable placeholder="全部" style="width:140px" @change="onCityChange">
            <el-option v-for="c in filteredCities" :key="c.id" :label="c.name" :value="c.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="可用区">
          <el-select v-model="searchForm.availability_zone" clearable filterable placeholder="全部" style="width:150px" @change="loadData">
            <el-option v-for="z in filteredZones" :key="z.id" :label="z.name" :value="z.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="网络类型">
          <el-select v-model="searchForm.network_type" clearable placeholder="全部" style="width:110px" @change="loadData">
            <el-option v-for="t in networkTypeOptions" :key="t.value" :label="t.label" :value="t.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="IP属性">
          <el-select v-model="searchForm.ip_attribute" clearable placeholder="全部" style="width:110px" @change="loadData">
            <el-option v-for="t in ipAttributeOptions" :key="t.value" :label="t.label" :value="t.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="ASN/企业性质">
          <el-select v-model="searchForm.asn_nature" clearable placeholder="全部" style="width:120px" @change="loadData">
            <el-option v-for="t in asnNatureOptions" :key="t.value" :label="t.label" :value="t.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="热卖">
          <el-select v-model="searchForm.hot_sale" clearable placeholder="全部" style="width:110px" @change="loadData">
            <el-option label="热卖中" :value="true" />
            <el-option label="未热卖" :value="false" />
          </el-select>
        </el-form-item>
        <el-form-item label="POP">
          <el-select v-model="searchForm.pop_point" clearable filterable placeholder="全部" style="width:170px" @change="loadData">
            <el-option
              v-for="p in popOptions"
              :key="p.id"
              :label="p.ip_delivery_mode === 'nat' ? `${p.name}（NAT 交付）` : p.name"
              :value="p.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="关键词">
          <el-input v-model="searchForm.search" placeholder="名称/编号" clearable style="width:160px" @keyup.enter="loadData" />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :icon="Search" @click="loadData">搜索</el-button>
          <el-button @click="resetSearch">重置</el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <el-card shadow="never" style="margin-top:12px;">
      <el-table :data="tableData" v-loading="loading" stripe border>
        <el-table-column prop="product_no" label="产品编号" width="160" />
        <el-table-column prop="name" label="产品名称" min-width="140" />
        <el-table-column label="类别" width="90">
          <template #default="{ row }">
            <el-tag size="small" :type="row.category === 'vps' ? 'primary' : 'success'">{{ row.category_label }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="POP点" width="170">
          <template #default="{ row }">
            <div>{{ row.pop_name }} <span class="muted">{{ row.pop_code }}</span></div>
            <div class="muted" v-if="row.region_name || row.country_name || row.city_name">
              {{ [row.region_name, row.country_name, row.city_name, row.zone_name].filter(Boolean).join(' / ') }}
            </div>
            <el-tag v-if="row.ip_delivery_mode === 'nat'" size="small" type="warning" style="margin-top:4px">
              {{ row.ip_delivery_mode_label || 'NAT 交付' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="网络/ASN" width="150">
          <template #default="{ row }">
            <div style="display:flex;flex-direction:column;gap:2px;align-items:flex-start;">
              <el-tag
                v-for="t in productNetworkTypes(row)"
                :key="'nt-'+t.value"
                size="small"
                :type="t.value === 'home_broadband' ? 'warning' : 'primary'"
              >
                {{ t.label }}
              </el-tag>
              <el-tag v-if="!productNetworkTypes(row).length" size="small">-</el-tag>
              <el-tag v-for="t in productAsnNatures(row)" :key="'asn-'+t.value" size="small" effect="plain" type="info">
                {{ t.label }}
              </el-tag>
              <el-tag v-if="!productAsnNatures(row).length" size="small" effect="plain" type="info">-</el-tag>
              <el-tag v-for="a in (row.ip_attributes || [])" :key="a" size="small" effect="plain">
                {{ ipAttrLabel(a) }}
              </el-tag>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="模版" width="130">
          <template #default="{ row }">{{ row.template_name || '-' }}</template>
        </el-table-column>
        <el-table-column label="规格" min-width="190">
          <template #default="{ row }">
            <span v-if="row.category === 'vps'">{{ row.cpu_cores }}核/{{ ramText(row.ram_mb) }}/{{ row.disk_gb }}G/{{ row.bandwidth_mbps }}M/{{ row.ip_count }}IP</span>
            <span v-else>{{ row.accel_type_label || accelLabel(row.accel_type) }}/{{ row.bandwidth_mbps }}M/{{ row.ip_count }}IP</span>
          </template>
        </el-table-column>
        <el-table-column label="计费模式" width="100">
          <template #default="{ row }">{{ row.billing_mode_label || '包月' }}</template>
        </el-table-column>
        <el-table-column label="月租/信誉值" width="130" align="right">
          <template #default="{ row }">
            <div>${{ row.monthly_price }} <span class="muted">USD</span></div>
            <div class="muted">
              {{ row.reputation_price }} 信誉
              <el-tag v-if="row.allow_reputation_pay" size="small" type="success" effect="plain" style="margin-left:4px">可支付</el-tag>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="90">
          <template #default="{ row }">
            <el-tag size="small" :type="statusColor[row.status]">{{ row.status_label }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="热卖" width="110">
          <template #default="{ row }">
            <el-tag v-if="row.hot_sale || row.is_hot_sale" size="small" type="danger">🔥 热卖</el-tag>
            <span v-else class="muted">未设置</span>
            <div v-if="(row.sold_service_count || 0) >= 2" class="muted">在服 {{ row.sold_service_count }}</div>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="280" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" :loading="refreshingId === row.id" @click="refreshNetwork(row)">刷新</el-button>
            <el-button link type="primary" size="small" @click="openDrawer(row)">详情</el-button>
            <el-button link type="primary" size="small" @click="openDialog(row)">编辑</el-button>
            <el-button v-if="row.status !== 'online'" link type="success" size="small" @click="doPublish(row, 'online')">上线</el-button>
            <el-button v-else link type="warning" size="small" @click="doPublish(row, 'offline')">下线</el-button>
            <el-button link type="danger" size="small" @click="handleDelete(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-pagination v-model:current-page="page" v-model:page-size="pageSize" :total="total"
        layout="total, prev, pager, next" class="pagination" @change="loadData" />
    </el-card>

    <!-- 新建/编辑产品 -->
    <el-dialog v-model="dialogVisible" :title="editRow ? '编辑产品' : '新建产品'" width="860px" destroy-on-close>
      <el-form ref="formRef" :model="form" :rules="formRules" label-width="100px">
        <el-row :gutter="16">
          <el-col :span="12"><el-form-item label="产品名称" prop="name"><el-input v-model="form.name" /></el-form-item></el-col>
          <el-col :span="12">
            <el-form-item label="类别" prop="category">
              <el-radio-group v-model="form.category" @change="onCategoryChange">
                <el-radio-button label="vps">VPS</el-radio-button>
                <el-radio-button label="edge">边缘加速</el-radio-button>
              </el-radio-group>
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="产品模版">
              <el-select v-model="form.template" clearable filterable placeholder="选择后自动填充规格与定价" style="width:100%" @change="onTemplateChange">
                <el-option v-for="t in filteredTemplateOptions" :key="t.id" :label="t.name" :value="t.id" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="POP点" prop="pop_point">
              <el-select v-model="form.pop_point" filterable placeholder="选择POP点" style="width:100%" @change="onPopChange">
                <el-option
                  v-for="p in popOptions"
                  :key="p.id"
                  :label="popOptionLabel(p)"
                  :value="p.id"
                />
              </el-select>
              <el-alert
                v-if="selectedFormPop?.ip_delivery_mode === 'nat'"
                type="warning"
                :closable="false"
                show-icon
                style="margin-top:8px"
                title="该 POP 仅支持 NAT 交付"
                description="业务 IP 不能公网直连，门户选购与调用 IP 时会提示客户。"
              />
            </el-form-item>
          </el-col>
          <el-col v-if="!isSupplierAccount" :span="12">
            <el-form-item label="供应商">
              <el-select v-model="form.supplier" clearable filterable placeholder="可选" style="width:100%">
                <el-option v-for="s in supplierOptions" :key="s.id" :label="s.name" :value="s.id" />
              </el-select>
            </el-form-item>
          </el-col>
        </el-row>

        <el-divider content-position="left">规格{{ form.template && !editRow ? '（来自模版，可调整）' : '（可在模版基础上定制）' }}</el-divider>
        <el-row v-if="form.category === 'vps'" :gutter="16">
          <el-col :span="8"><el-form-item label="CPU核心"><el-input-number v-model="form.cpu_cores" :min="1" controls-position="right" style="width:100%" /></el-form-item></el-col>
          <el-col :span="8"><el-form-item label="内存(GB)"><el-input-number v-model="ramGb" :min="1" :step="1" controls-position="right" style="width:100%" /></el-form-item></el-col>
          <el-col :span="8"><el-form-item label="硬盘(GB)"><el-input-number v-model="form.disk_gb" :min="1" controls-position="right" style="width:100%" /></el-form-item></el-col>
          <el-col :span="8"><el-form-item label="网卡数"><el-input-number v-model="form.nic_count" :min="1" controls-position="right" style="width:100%" /></el-form-item></el-col>
          <el-col :span="8"><el-form-item label="带宽(Mbps)"><el-input-number v-model="form.bandwidth_mbps" :min="0" controls-position="right" style="width:100%" /></el-form-item></el-col>
          <el-col :span="8"><el-form-item label="IP数量"><el-input-number v-model="form.ip_count" :min="0" controls-position="right" style="width:100%" /></el-form-item></el-col>
          <el-col :span="24">
            <el-form-item label="系统版本">
              <el-select
                v-model="form.os_templates"
                multiple
                filterable
                clearable
                collapse-tags
                collapse-tags-tooltip
                placeholder="不选则门户展示全部已启用系统模版"
                style="width:100%"
              >
                <el-option
                  v-for="item in osTemplateOptions"
                  :key="item.id"
                  :label="osOptionLabel(item)"
                  :value="item.id"
                />
              </el-select>
            </el-form-item>
          </el-col>
        </el-row>
        <el-row v-else :gutter="16">
          <el-col :span="8">
            <el-form-item label="加速类型" prop="accel_types">
              <el-select
                v-model="form.accel_types"
                multiple
                collapse-tags
                collapse-tags-tooltip
                placeholder="可多选 GRE / WireGuard"
                style="width:100%"
              >
                <el-option v-for="a in accelOptions" :key="a.value" :label="a.label" :value="a.value" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="8"><el-form-item label="带宽(Mbps)"><el-input-number v-model="form.bandwidth_mbps" :min="0" controls-position="right" style="width:100%" /></el-form-item></el-col>
          <el-col :span="8"><el-form-item label="IP数量"><el-input-number v-model="form.ip_count" :min="0" controls-position="right" style="width:100%" /></el-form-item></el-col>
        </el-row>

        <el-divider content-position="left">基础定价（美元 USD）</el-divider>
        <el-row :gutter="16">
          <el-col :span="8"><el-form-item label="月租价($)"><el-input-number v-model="form.monthly_price" :min="0" :precision="2" controls-position="right" style="width:100%" /></el-form-item></el-col>
          <el-col :span="8"><el-form-item label="信誉值"><el-input-number v-model="form.reputation_price" :min="0" :precision="2" controls-position="right" style="width:100%" /></el-form-item></el-col>
          <el-col :span="8"><el-form-item label="安装费($)"><el-input-number v-model="form.setup_price" :min="0" :precision="2" controls-position="right" style="width:100%" /></el-form-item></el-col>
        </el-row>
        <el-row :gutter="16">
          <el-col :span="8">
            <el-form-item label="允许信誉值支付">
              <el-switch v-model="form.allow_reputation_pay" active-text="允许" inactive-text="关闭" />
            </el-form-item>
          </el-col>
        </el-row>
        <el-row :gutter="16">
          <el-col :span="8">
            <el-form-item label="计费模式">
              <el-select v-model="form.billing_mode" style="width:100%">
                <el-option v-for="m in billingModeOptions" :key="m.value" :label="m.label" :value="m.value" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col v-if="form.billing_mode === 'transfer_quota'" :span="8">
            <el-form-item label="流量包(GB/月)">
              <el-input-number v-model="form.transfer_quota_gb" :min="1" controls-position="right" style="width:100%" />
            </el-form-item>
          </el-col>
          <el-col v-if="form.billing_mode !== 'flat'" :span="8">
            <el-form-item :label="form.billing_mode === 'transfer_quota' ? '超额单价(每GB)' : '超额单价(每Mbps)'">
              <el-input-number v-model="form.overage_unit_price" :min="0" :precision="4" controls-position="right" style="width:100%" />
            </el-form-item>
          </el-col>
        </el-row>
        <el-divider content-position="left">门户展示</el-divider>
        <el-row :gutter="16">
          <el-col :span="8">
            <el-form-item label="是否热卖">
              <el-switch v-model="form.is_hot_sale" active-text="热卖" inactive-text="不设置" />
            </el-form-item>
          </el-col>
          <el-col :span="16">
            <el-alert
              v-if="!form.is_hot_sale && editRow && (editRow.sold_service_count || 0) >= 2"
              type="warning"
              :closable="false"
              show-icon
              :title="`该产品已有 ${editRow.sold_service_count} 个在服服务，门户将自动提示为热卖。`"
            />
          </el-col>
          <el-col :span="8">
            <el-form-item label="显示示例IP">
              <el-switch v-model="form.sample_ip_visible" />
            </el-form-item>
          </el-col>
          <el-col :span="16">
            <el-form-item label="示例IP" :required="form.sample_ip_visible">
              <el-input v-model="form.sample_ip" placeholder="如 203.0.113.10，可逗号分隔多个" :disabled="!form.sample_ip_visible" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="预估开通(小时)">
              <el-input-number v-model="form.estimated_provision_hours" :min="1" :max="8760" controls-position="right" style="width:100%" placeholder="库存不足上线时必填" />
            </el-form-item>
          </el-col>
        </el-row>
        <el-form-item label="产品描述"><el-input v-model="form.description" type="textarea" :rows="2" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="handleSave">确认</el-button>
      </template>
    </el-dialog>

    <!-- 产品详情抽屉 -->
    <el-drawer v-model="drawerVisible" :title="current?.name" size="60%" destroy-on-close>
      <el-tabs v-if="current" v-model="activeTab">
        <el-tab-pane label="概览" name="overview">
          <el-descriptions :column="2" border>
            <el-descriptions-item label="产品编号">{{ current.product_no }}</el-descriptions-item>
            <el-descriptions-item label="状态"><el-tag size="small" :type="statusColor[current.status]">{{ current.status_label }}</el-tag></el-descriptions-item>
            <el-descriptions-item label="POP点">
              {{ current.pop_name }} ({{ current.pop_code }})
              <el-tag v-if="current.ip_delivery_mode === 'nat'" size="small" type="warning" style="margin-left:6px">
                {{ current.ip_delivery_mode_label || 'NAT 交付' }}
              </el-tag>
            </el-descriptions-item>
            <el-descriptions-item label="区域/国家/城市">
              {{ [current.region_name, current.country_name, current.city_name, current.zone_name].filter(Boolean).join(' / ') || '-' }}
            </el-descriptions-item>
            <el-descriptions-item label="网络类型">
              <el-tag
                v-for="t in productNetworkTypes(current)"
                :key="t.value"
                size="small"
                :type="t.value === 'home_broadband' ? 'warning' : 'primary'"
                style="margin-right:4px"
              >
                {{ t.label }}
              </el-tag>
              <span v-if="!productNetworkTypes(current).length">-</span>
            </el-descriptions-item>
            <el-descriptions-item label="ASN/企业性质">
              <el-tag v-for="t in productAsnNatures(current)" :key="t.value" size="small" type="info" style="margin-right:4px">
                {{ t.label }}
              </el-tag>
              <span v-if="!productAsnNatures(current).length">-</span>
            </el-descriptions-item>
            <el-descriptions-item label="IP属性">
              <template v-if="(current.ip_attributes || []).length">
                <el-tag v-for="a in current.ip_attributes" :key="a" size="small" effect="plain" style="margin-right:4px">
                  {{ ipAttrLabel(a) }}
                </el-tag>
              </template>
              <span v-else>-</span>
            </el-descriptions-item>
            <el-descriptions-item label="供应商">{{ current.supplier_name || '-' }}</el-descriptions-item>
            <el-descriptions-item label="热卖">
              <el-tag v-if="current.hot_sale || current.is_hot_sale" size="small" type="danger">🔥 热卖</el-tag>
              <span v-else class="muted">未设置</span>
              <span v-if="current.sold_service_count" class="muted" style="margin-left:8px">在服 {{ current.sold_service_count }}</span>
            </el-descriptions-item>
            <el-descriptions-item label="模版">{{ current.template_name || '-' }}</el-descriptions-item>
            <el-descriptions-item label="当前规格">
              <span v-if="current.category === 'vps'">{{ current.cpu_cores }}核/{{ ramText(current.ram_mb) }}/{{ current.disk_gb }}G/{{ current.bandwidth_mbps }}M/{{ current.ip_count }}IP</span>
              <span v-else>{{ current.accel_type_label || accelLabel(current.accel_type) }}/{{ current.bandwidth_mbps }}M/{{ current.ip_count }}IP</span>
            </el-descriptions-item>
            <el-descriptions-item label="计费模式">{{ current.billing_mode_label || '包月' }}</el-descriptions-item>
            <el-descriptions-item v-if="current.billing_mode === 'transfer_quota'" label="流量包">
              {{ current.transfer_quota_gb || '-' }} GB/月
            </el-descriptions-item>
            <el-descriptions-item v-if="current.billing_mode && current.billing_mode !== 'flat'" label="超额单价">
              {{ current.overage_unit_price || '0' }}
              {{ current.billing_mode === 'transfer_quota' ? '/GB' : '/Mbps' }}
            </el-descriptions-item>
            <el-descriptions-item label="月租价">${{ current.monthly_price }} USD</el-descriptions-item>
            <el-descriptions-item label="示例 IP">
              <span v-if="current.sample_ip_visible && current.sample_ip">{{ current.sample_ip }}</span>
              <span v-else class="muted">{{ current.sample_ip ? `${current.sample_ip}（不展示）` : '未设置' }}</span>
            </el-descriptions-item>
            <el-descriptions-item label="预估开通">
              {{ current.estimated_provision_hours ? `${current.estimated_provision_hours} 小时` : '-' }}
              <el-tag v-if="current.allow_backorder" size="small" type="warning" style="margin-left:6px">缺货可售</el-tag>
            </el-descriptions-item>
            <el-descriptions-item label="信誉值">
              {{ current.reputation_price }}
              <el-tag v-if="current.allow_reputation_pay" size="small" type="success" effect="plain" style="margin-left:6px">允许信誉支付</el-tag>
              <el-tag v-else size="small" type="info" effect="plain" style="margin-left:6px">仅其他支付</el-tag>
            </el-descriptions-item>
          </el-descriptions>

          <el-alert v-if="current.template_pricing" type="info" :closable="false" style="margin-top:12px">
            <template #title>
              模版定价（{{ current.template_name }} @ {{ current.pop_name }}）：月租 ${{ current.template_pricing.monthly_price }}，
              信誉值 {{ current.template_pricing.reputation_price }}，安装费 ${{ current.template_pricing.setup_price }}
            </template>
          </el-alert>

          <div class="cap-row">
            <el-card shadow="never" class="cap-card">
              <div class="cap-title">资源容量校验</div>
              <el-tag v-if="capacity" :type="capacity.ok ? 'success' : 'danger'">{{ capacity.ok ? '资源充足' : '资源不足' }}</el-tag>
              <ul v-if="capacity && capacity.shortages.length" class="shortage">
                <li v-for="(s, i) in capacity.shortages" :key="i">{{ s }}</li>
              </ul>
            </el-card>
            <el-card shadow="never" class="cap-card">
              <div class="cap-title">库存估算（该POP可开通数）</div>
              <div class="stock-num">{{ stock?.stock === null || stock?.stock === undefined ? '不受限/未知' : stock?.stock }}</div>
              <div v-if="stock?.limiting_label" class="muted">瓶颈资源：{{ stock.limiting_label }}</div>
            </el-card>
          </div>

          <el-card v-if="capacity?.detail" shadow="never" style="margin-top:12px">
            <div class="cap-title">资源明细（有效容量含 CPU 超分 / 系统预留）</div>
            <el-table :data="capacityRows(capacity.detail)" size="small" border>
              <el-table-column prop="label" label="资源" width="120" />
              <el-table-column label="本产品需求" width="110" align="right"><template #default="{ row }">{{ row.need || '-' }}</template></el-table-column>
              <el-table-column label="已用 / 总量" min-width="150" align="right">
                <template #default="{ row }">{{ row.total === null ? '未知' : `${row.used ?? '-'} / ${row.total}` }}</template>
              </el-table-column>
              <el-table-column label="剩余" width="100" align="right"><template #default="{ row }">{{ row.available === null ? '-' : row.available }}</template></el-table-column>
              <el-table-column label="使用率" width="140">
                <template #default="{ row }">
                  <el-progress v-if="row.usage_pct !== null" :percentage="row.usage_pct" :stroke-width="10" :color="usageColor(row.usage_pct)" />
                  <span v-else class="muted">-</span>
                </template>
              </el-table-column>
            </el-table>
          </el-card>
        </el-tab-pane>

        <el-tab-pane label="阶梯定价（超出模版）" name="rules">
          <div class="rules-head">
            <span class="muted">阶梯以模版规格为基准，超出部分按区间逐档计价</span>
            <el-button size="small" type="primary" :icon="Plus" @click="openRule()">新增规则</el-button>
          </div>
          <el-table :data="current.upgrade_rules || []" size="small" border>
            <el-table-column label="资源" width="110"><template #default="{ row }">{{ row.resource_kind_label }}</template></el-table-column>
            <el-table-column label="阶梯区间" width="130">
              <template #default="{ row }">{{ row.min_qty }} ~ {{ row.max_qty ?? '∞' }}</template>
            </el-table-column>
            <el-table-column label="每单位月租($)" align="right"><template #default="{ row }">${{ row.unit_price }}</template></el-table-column>
            <el-table-column label="每单位信誉" align="right"><template #default="{ row }">{{ row.unit_reputation }}</template></el-table-column>
            <el-table-column label="操作" width="120">
              <template #default="{ row }">
                <el-button link type="primary" size="small" @click="openRule(row)">编辑</el-button>
                <el-button link type="danger" size="small" @click="removeRule(row)">删除</el-button>
              </template>
            </el-table-column>
            <template #empty>暂无阶梯规则</template>
          </el-table>
        </el-tab-pane>

        <el-tab-pane label="报价测算" name="quote">
          <el-form :model="quoteForm" label-width="92px" inline class="quote-form">
            <el-form-item label="CPU核心"><el-input-number v-model="quoteForm.cpu_cores" :min="0" controls-position="right" style="width:130px" /></el-form-item>
            <el-form-item label="内存(GB)"><el-input-number v-model="quoteRamGb" :min="0" :step="1" controls-position="right" style="width:130px" /></el-form-item>
            <el-form-item label="硬盘(GB)"><el-input-number v-model="quoteForm.disk_gb" :min="0" controls-position="right" style="width:130px" /></el-form-item>
            <el-form-item label="带宽(Mbps)"><el-input-number v-model="quoteForm.bandwidth_mbps" :min="0" controls-position="right" style="width:130px" /></el-form-item>
            <el-form-item label="IP数量"><el-input-number v-model="quoteForm.ip_count" :min="0" controls-position="right" style="width:130px" /></el-form-item>
            <el-form-item><el-button type="primary" :loading="quoting" @click="doQuote">测算</el-button></el-form-item>
          </el-form>
          <el-result v-if="quoteResult" :icon="quoteResult.capacity.ok ? 'success' : 'warning'"
            :title="`月租 $${quoteResult.price.monthly_price} / 信誉值 ${quoteResult.price.reputation_price}`"
            :sub-title="quoteResult.capacity.ok ? '资源充足' : ('资源不足：' + quoteResult.capacity.shortages.join('；'))">
          </el-result>

          <el-row v-if="quoteResult" :gutter="12" style="margin-top:4px">
            <el-col :span="12">
              <el-card shadow="never" class="cap-card">
                <div class="cap-title">该配置库存估算（POP 可开通数）</div>
                <div class="stock-num">
                  {{ quoteResult.stock?.stock === null || quoteResult.stock?.stock === undefined ? '不受限/未知' : quoteResult.stock.stock }}
                </div>
                <div v-if="quoteResult.stock?.limiting_label" class="muted">瓶颈资源：{{ quoteResult.stock.limiting_label }}</div>
              </el-card>
            </el-col>
            <el-col :span="12">
              <el-card shadow="never" class="cap-card">
                <div class="cap-title">资源充足性</div>
                <el-tag :type="quoteResult.capacity.ok ? 'success' : 'danger'">{{ quoteResult.capacity.ok ? '资源充足' : '资源不足' }}</el-tag>
                <ul v-if="quoteResult.capacity.shortages.length" class="shortage">
                  <li v-for="(s, i) in quoteResult.capacity.shortages" :key="i">{{ s }}</li>
                </ul>
              </el-card>
            </el-col>
          </el-row>

          <el-card v-if="quoteResult?.stock?.by_resource" shadow="never" style="margin-top:12px">
            <div class="cap-title">各资源可开通数（取最小值为库存）</div>
            <el-table :data="stockRows(quoteResult.stock.by_resource)" size="small" border>
              <el-table-column prop="label" label="资源" width="120" />
              <el-table-column label="单台需求" width="110" align="right"><template #default="{ row }">{{ row.need }}</template></el-table-column>
              <el-table-column label="剩余可用" width="120" align="right"><template #default="{ row }">{{ row.available }}</template></el-table-column>
              <el-table-column label="可开通数" align="right">
                <template #default="{ row }">
                  <el-tag size="small" :type="row.bottleneck ? 'danger' : 'info'">{{ row.count }}</el-tag>
                </template>
              </el-table-column>
            </el-table>
          </el-card>

          <el-table v-if="quoteResult && quoteResult.price.breakdown.length" :data="quoteResult.price.breakdown" size="small" border style="margin-top:12px">
            <el-table-column prop="resource_kind" label="升级资源" />
            <el-table-column prop="upgrade_qty" label="升级数量" />
            <el-table-column prop="monthly_delta" label="月租增量" />
            <el-table-column prop="reputation_delta" label="信誉增量" />
          </el-table>
        </el-tab-pane>

        <el-tab-pane label="发布日志" name="logs">
          <el-timeline>
            <el-timeline-item v-for="log in publishLogs" :key="log.id" :timestamp="log.created_at" placement="top">
              [{{ log.action_label }}] {{ log.from_status }} → {{ log.to_status }} {{ log.message }} <span class="muted">{{ log.operator_name }}</span>
            </el-timeline-item>
          </el-timeline>
        </el-tab-pane>
      </el-tabs>
    </el-drawer>

    <!-- 阶梯规则编辑 -->
    <el-dialog v-model="ruleVisible" :title="ruleEdit ? '编辑规则' : '新增规则'" width="500px" destroy-on-close>
      <el-form ref="ruleRef" :model="ruleForm" :rules="ruleRules" label-width="110px">
        <el-form-item label="资源类型" prop="resource_kind">
          <el-select v-model="ruleForm.resource_kind" style="width:100%">
            <el-option v-for="r in resourceKinds" :key="r.value" :label="r.label" :value="r.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="区间起(含)" prop="min_qty"><el-input-number v-model="ruleForm.min_qty" :min="0" controls-position="right" style="width:100%" /></el-form-item>
        <el-form-item label="区间止(含)"><el-input-number v-model="ruleForm.max_qty" :min="0" controls-position="right" style="width:100%" placeholder="留空=不封顶" /></el-form-item>
        <el-form-item label="每单位月租"><el-input-number v-model="ruleForm.unit_price" :min="0" :precision="2" controls-position="right" style="width:100%" /></el-form-item>
        <el-form-item label="每单位信誉"><el-input-number v-model="ruleForm.unit_reputation" :min="0" :precision="2" controls-position="right" style="width:100%" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="ruleVisible = false">取消</el-button>
        <el-button type="primary" :loading="ruleSaving" @click="saveRule">确认</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { gbToMb, mbToGb, ramAmount, ramText } from '@/utils/ram'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Search } from '@element-plus/icons-vue'
import {
  getProducts, getProduct, createProduct, updateProduct, deleteProduct,
  publishProduct, refreshProductNetwork, quoteProduct, getProductPublishLogs, getTemplates, getProductOsTemplates,
  createUpgradeRule, updateUpgradeRule, deleteUpgradeRule,
} from '@/api/product'
import { getPopPoints, getRegions, getCountries, getCities, getAvailabilityZones, getNetworkTypes } from '@/api/resource'
import ExcelImportExport from '@/components/ExcelImportExport.vue'
import { getSuppliersBrief } from '@/api/supplier'
import { useAuthStore } from '@/stores/auth'

const route = useRoute()
const router = useRouter()
const authStore = useAuthStore()
const isSupplierAccount = computed(() => authStore.user?.role === 'supplier' || authStore.user?.is_supplier_account)
const loading = ref(false)
const refreshingId = ref(null)
const saving = ref(false)
const dialogVisible = ref(false)
const formRef = ref(null)
const editRow = ref(null)
const tableData = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const popOptions = ref([])
const supplierOptions = ref([])
const templateOptions = ref([])
const regionOptions = ref([])
const countryOptions = ref([])
const cityOptions = ref([])
const zoneOptions = ref([])
const osTemplateOptions = ref([])
function osOptionLabel(item) {
  const base = item.label || `${item.name || ''} ${item.version || ''}`.trim()
  if (item.is_stale) return `${base}（仍是备份，请先同步 Cloud）`
  const user = item.default_user ? ` · ${item.default_user}` : ''
  return `${base}${user}`
}

const networkTypeOptions = ref([
  { value: 'home_broadband', label: '家宽' }, { value: 'idc', label: 'IDC' },
])
const ipAttributeOptions = [
  { value: 'native', label: '原生' }, { value: 'broadcast', label: '广播' },
]
const asnNatureOptions = [
  { value: 'isp', label: '单ISP' }, { value: 'dual_isp', label: '双ISP' }, { value: 'non_isp', label: '非ISP' },
]
function productNetworkTypes(row) {
  if (Array.isArray(row?.network_types) && row.network_types.length) return row.network_types
  if (row?.network_type) {
    return [{ value: row.network_type, label: row.network_type_label || row.network_type }]
  }
  return []
}
function productAsnNatures(row) {
  if (Array.isArray(row?.asn_natures) && row.asn_natures.length) return row.asn_natures
  if (row?.asn_nature) {
    return [{ value: row.asn_nature, label: row.asn_nature_label || row.asn_nature }]
  }
  return []
}
const billingModeOptions = [
  { value: 'flat', label: '包月' },
  { value: '95th', label: '95 计费' },
  { value: 'avg', label: '平均带宽' },
  { value: 'transfer_quota', label: '流量包' },
]
function ipAttrLabel(v) { return ({ native: '原生', broadcast: '广播' })[v] || v }

const statusOptions = [
  { value: 'draft', label: '草稿' }, { value: 'online', label: '已上线' },
  { value: 'offline', label: '已下线' }, { value: 'archived', label: '已归档' },
]
const statusColor = { draft: 'info', online: 'success', offline: 'warning', archived: '' }
const accelOptions = [
  { value: 'gre', label: 'GRE' }, { value: 'wireguard', label: 'WireGuard' },
]
const resourceKinds = [
  { value: 'cpu', label: 'CPU 核' }, { value: 'ram', label: '内存(GB)' }, { value: 'disk', label: '硬盘(GB)' },
  { value: 'bandwidth', label: '带宽(Mbps)' }, { value: 'ip', label: 'IP 数量' }, { value: 'nic', label: '网卡数量' },
]

const searchForm = reactive({
  category: '', status: '', pop_point: '', search: '',
  region: '', country: '', city: '', availability_zone: '',
  network_type: '', ip_attribute: '', asn_nature: '', hot_sale: '',
})
const excelParams = computed(() => {
  const params = {}
  if (searchForm.category) params.category = searchForm.category
  if (searchForm.status) params.status = searchForm.status
  if (searchForm.pop_point) params.pop_point = searchForm.pop_point
  if (searchForm.search) params.search = searchForm.search
  if (searchForm.region) params.region = searchForm.region
  if (searchForm.country) params.country = searchForm.country
  if (searchForm.city) params.city = searchForm.city
  if (searchForm.availability_zone) params.availability_zone = searchForm.availability_zone
  if (searchForm.network_type) params.network_type = searchForm.network_type
  if (searchForm.ip_attribute) params.ip_attribute = searchForm.ip_attribute
  if (searchForm.asn_nature) params.asn_nature = searchForm.asn_nature
  if (searchForm.hot_sale !== '' && searchForm.hot_sale !== null && searchForm.hot_sale !== undefined) {
    params.hot_sale = searchForm.hot_sale
  }
  return params
})

const filteredCountries = computed(() =>
  searchForm.region
    ? countryOptions.value.filter((c) => c.region === searchForm.region)
    : countryOptions.value)
const filteredCities = computed(() =>
  searchForm.country
    ? cityOptions.value.filter((c) => c.country === searchForm.country)
    : cityOptions.value)
const filteredZones = computed(() => {
  if (searchForm.city) {
    return zoneOptions.value.filter((z) => String(z.city) === String(searchForm.city))
  }
  if (searchForm.country) {
    const cityIds = new Set(
      cityOptions.value.filter((c) => c.country === searchForm.country).map((c) => String(c.id)),
    )
    return zoneOptions.value.filter((z) => cityIds.has(String(z.city)))
  }
  return zoneOptions.value
})

function onRegionChange() {
  searchForm.country = ''
  searchForm.city = ''
  searchForm.availability_zone = ''
  loadData()
}
function onCountryChange() {
  searchForm.city = ''
  searchForm.availability_zone = ''
  loadData()
}
function onCityChange() {
  searchForm.availability_zone = ''
  loadData()
}

const defaultForm = () => ({
  name: '', category: 'vps', pop_point: null, template: null, supplier: null,
  cpu_cores: 1, ram_mb: 1024, disk_gb: 40, nic_count: 1, bandwidth_mbps: 5, ip_count: 1,
  accel_types: ['gre', 'wireguard'], monthly_price: 0, reputation_price: 0, setup_price: 0, currency: 'USD',
  description: '', billing_mode: 'flat', transfer_quota_gb: null, overage_unit_price: 0,
  sample_ip: '', sample_ip_visible: false, estimated_provision_hours: null,
  allow_reputation_pay: false, is_hot_sale: false,
  os_templates: [],
})
const form = reactive(defaultForm())
const selectedFormPop = computed(() => popOptions.value.find((p) => p.id === form.pop_point) || null)
function popOptionLabel(p) {
  const loc = [p.name, p.zone_name || p.availability_zone_name, p.city_name].filter(Boolean).join(' / ')
  return p.ip_delivery_mode === 'nat' ? `${loc}（NAT 交付）` : loc
}
const formRules = {
  name: [{ required: true, message: '请输入产品名称' }],
  pop_point: [{ required: true, message: '请选择POP点' }],
  accel_types: [{
    validator: (_rule, value, callback) => {
      if (form.category === 'edge' && !(value || []).length) {
        callback(new Error('请至少选择一种加速类型'))
      } else {
        callback()
      }
    },
    trigger: 'change',
  }],
}

const ramGb = computed({
  get: () => mbToGb(form.ram_mb),
  set: (gb) => { form.ram_mb = gbToMb(gb) },
})
function parseAccelTypes(v) {
  const items = Array.isArray(v) ? v : String(v || '').split(/[,;/]/)
  const out = []
  for (const item of items) {
    let value = String(item || '').trim().toLowerCase()
    if (value === 'wg') value = 'wireguard'
    if ((value === 'gre' || value === 'wireguard') && !out.includes(value)) out.push(value)
  }
  return out
}
function accelLabel(v) {
  const map = { gre: 'GRE', wireguard: 'WireGuard' }
  const types = parseAccelTypes(v)
  return types.length ? types.map((t) => map[t]).join(' / ') : '-'
}
function usageColor(pct) { return pct >= 90 ? '#f56c6c' : pct >= 70 ? '#e6a23c' : '#67c23a' }

const RES_ORDER = ['cpu_cores', 'ram_mb', 'disk_gb', 'bandwidth_mbps', 'ip_count']
function capacityRows(detail) {
  return RES_ORDER.filter((k) => detail[k]).map((k) => ({
    key: k,
    ...detail[k],
    need: ramAmount(k, detail[k].need),
    total: ramAmount(k, detail[k].total),
    used: ramAmount(k, detail[k].used),
    available: ramAmount(k, detail[k].available),
  }))
}
function stockRows(byRes) {
  const min = Object.values(byRes).filter((v) => v).reduce((m, v) => Math.min(m, v.count), Infinity)
  return RES_ORDER.filter((k) => byRes[k]).map((k) => ({
    label: ({ cpu_cores: 'CPU 核', ram_mb: '内存(GB)', disk_gb: '硬盘(GB)', bandwidth_mbps: '带宽(Mbps)', ip_count: 'IP 数量' })[k],
    ...byRes[k],
    need: ramAmount(k, byRes[k].need),
    available: ramAmount(k, byRes[k].available),
    bottleneck: byRes[k].count === min,
  }))
}

const filteredTemplateOptions = computed(() =>
  templateOptions.value.filter((t) => !form.category || t.category === form.category))

async function loadOptions() {
  const [pops, sups, tpls, regions, countries, cities, zones, ntypes, ostpls] = await Promise.all([
    getPopPoints({ page_size: 200 }).catch(() => null),
    getSuppliersBrief().catch(() => null),
    getTemplates({ page_size: 500, is_active: true }).catch(() => null),
    getRegions({ page_size: 200 }).catch(() => null),
    getCountries({ page_size: 500 }).catch(() => null),
    getCities({ page_size: 1000 }).catch(() => null),
    getAvailabilityZones({ page_size: 2000 }).catch(() => null),
    getNetworkTypes({ page_size: 200, is_active: true }).catch(() => null),
    getProductOsTemplates().catch(() => null),
  ])
  popOptions.value = pops?.results || pops || []
  supplierOptions.value = sups || []
  templateOptions.value = tpls?.results || tpls || []
  regionOptions.value = regions?.results || regions || []
  countryOptions.value = countries?.results || countries || []
  cityOptions.value = cities?.results || cities || []
  zoneOptions.value = zones?.results || zones || []
  const ntList = ntypes?.results || ntypes || []
  if (ntList.length) {
    networkTypeOptions.value = ntList.map((t) => ({ value: t.code, label: t.name }))
  }
  osTemplateOptions.value = ostpls?.results || ostpls || []
}

async function loadData() {
  loading.value = true
  try {
    const params = { page: page.value, page_size: pageSize.value }
    if (searchForm.category) params.category = searchForm.category
    if (searchForm.status) params.status = searchForm.status
    if (searchForm.pop_point) params.pop_point = searchForm.pop_point
    if (searchForm.search) params.search = searchForm.search
    if (searchForm.region) params.region = searchForm.region
    if (searchForm.country) params.country = searchForm.country
    if (searchForm.city) params.city = searchForm.city
    if (searchForm.availability_zone) params.availability_zone = searchForm.availability_zone
    if (searchForm.network_type) params.network_type = searchForm.network_type
    if (searchForm.ip_attribute) params.ip_attribute = searchForm.ip_attribute
    if (searchForm.asn_nature) params.asn_nature = searchForm.asn_nature
    if (searchForm.hot_sale !== '' && searchForm.hot_sale !== null && searchForm.hot_sale !== undefined) {
      params.hot_sale = searchForm.hot_sale
    }
    const res = await getProducts(params)
    tableData.value = res?.results || []
    total.value = res?.count || 0
  } finally { loading.value = false }
}

async function refreshNetwork(row) {
  refreshingId.value = row.id
  try {
    const data = await refreshProductNetwork(row.id)
    const index = tableData.value.findIndex((item) => item.id === row.id)
    if (index >= 0 && data) tableData.value[index] = data
    const nets = (data?.network_types || []).map((item) => item.label).filter(Boolean)
    const asns = (data?.asn_natures || []).map((item) => item.label).filter(Boolean)
    const attrs = (data?.ip_attributes || []).map((item) => ipAttrLabel(item))
    const summary = [nets.join('、'), asns.join('、'), attrs.join('、')].filter(Boolean).join(' / ')
    ElMessage.success(summary ? `已按 POP 点属性刷新：${summary}` : '该 POP 点尚未设置网络、ASN 或 IP 属性')
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || e?.message || '刷新失败')
  } finally {
    refreshingId.value = null
  }
}

function resetSearch() {
  Object.assign(searchForm, {
    category: '', status: '', pop_point: '', search: '',
    region: '', country: '', city: '', availability_zone: '',
    network_type: '', ip_attribute: '', asn_nature: '', hot_sale: '',
  })
  loadData()
}

function onCategoryChange() {
  form.template = null
  if (form.category === 'edge') {
    form.os_templates = []
    if (!(form.accel_types || []).length) {
      form.accel_types = ['gre', 'wireguard']
    }
  }
}

function applyTemplateConfig(t) {
  form.category = t.category
  if (!editRow.value) {
    form.name = t.name
    form.description = t.description || ''
  }
  if (t.category === 'vps') {
    Object.assign(form, {
      cpu_cores: t.cpu_cores, ram_mb: t.ram_mb, disk_gb: t.disk_gb,
      nic_count: t.nic_count, bandwidth_mbps: t.bandwidth_mbps, ip_count: t.ip_count,
    })
  } else {
    Object.assign(form, {
      accel_types: parseAccelTypes(t.accel_types || t.accel_type),
      bandwidth_mbps: t.bandwidth_mbps,
      ip_count: t.ip_count,
    })
  }
}

function applyTemplatePricing() {
  if (!form.template || !form.pop_point) return
  const t = templateOptions.value.find((x) => x.id === form.template)
  const pr = (t?.pricings || []).find((p) => p.pop_point === form.pop_point)
  if (pr) {
    form.monthly_price = parseFloat(pr.monthly_price) || 0
    form.reputation_price = parseFloat(pr.reputation_price) || 0
    form.setup_price = parseFloat(pr.setup_price) || 0
  }
}

function onTemplateChange(id) {
  if (!id) return
  const t = templateOptions.value.find((x) => x.id === id)
  if (!t) return
  applyTemplateConfig(t)
  applyTemplatePricing()
}

function onPopChange() {
  applyTemplatePricing()
}

function openDialog(row = null) {
  editRow.value = row
  Object.assign(form, defaultForm())
  if (row) {
    Object.assign(form, {
      name: row.name, category: row.category, pop_point: row.pop_point, template: row.template, supplier: row.supplier,
      cpu_cores: row.cpu_cores || 1, ram_mb: row.ram_mb || 1024, disk_gb: row.disk_gb || 40,
      nic_count: row.nic_count || 1, bandwidth_mbps: row.bandwidth_mbps || 0, ip_count: row.ip_count || 0,
      accel_types: parseAccelTypes(row.accel_types || row.accel_type), monthly_price: parseFloat(row.monthly_price) || 0,
      reputation_price: parseFloat(row.reputation_price) || 0, setup_price: parseFloat(row.setup_price) || 0,
      currency: row.currency || 'USD',
      description: row.description || '',
      billing_mode: row.billing_mode || 'flat',
      transfer_quota_gb: row.transfer_quota_gb || null,
      overage_unit_price: parseFloat(row.overage_unit_price) || 0,
      sample_ip: row.sample_ip || '',
      sample_ip_visible: !!row.sample_ip_visible,
      estimated_provision_hours: row.estimated_provision_hours || null,
      allow_reputation_pay: !!row.allow_reputation_pay,
      is_hot_sale: !!row.is_hot_sale,
      os_templates: row.os_template_ids || (row.os_templates || []).map((item) => item.id).filter(Boolean),
    })
  }
  dialogVisible.value = true
}

async function handleSave() {
  const valid = await formRef.value.validate().catch(() => false)
  if (!valid) return
  if (form.sample_ip_visible && !String(form.sample_ip || '').trim()) {
    ElMessage.warning('勾选显示示例 IP 时请填写示例地址')
    return
  }
  saving.value = true
  try {
    if (editRow.value) {
      await updateProduct(editRow.value.id, form)
      ElMessage.success('已更新')
    } else {
      const created = await createProduct(form)
      if (form.template) {
        try {
          await publishOnline(created.id, created)
          ElMessage.success('产品已按模版创建并上线')
        } catch (e) {
          ElMessage.warning(e?.response?.data?.detail || e?.message || '产品已创建，但自动上线失败')
        }
      } else {
        ElMessage.success('产品创建成功')
      }
    }
    dialogVisible.value = false
    loadData()
  } finally { saving.value = false }
}

async function handleDelete(row) {
  await ElMessageBox.confirm(`确定删除产品 "${row.name}"？`, '警告', { type: 'warning' })
  await deleteProduct(row.id)
  ElMessage.success('已删除')
  loadData()
}

async function publishOnline(id, row = {}) {
  const extra = {}
  if (row.estimated_provision_hours) extra.estimated_provision_hours = row.estimated_provision_hours
  try {
    await publishProduct(id, { action: 'online', ...extra })
    return
  } catch (e) {
    const data = e?.response?.data || {}
    if (!data.requires_estimated_provision) throw e
    const { value } = await ElMessageBox.prompt(
      '当前 POP 库存不足。填写预估开通时间（小时）后仍可上线，客户下单时仅提示库存紧张、交付时间可能略有延长。',
      '库存不足，填写预估开通时间',
      {
        inputPattern: /^[1-9]\d*$/,
        inputErrorMessage: '请输入大于 0 的小时数',
        inputPlaceholder: row.estimated_provision_hours ? String(row.estimated_provision_hours) : '例如 24',
        confirmButtonText: '确认上线',
        cancelButtonText: '取消',
      },
    )
    await publishProduct(id, {
      action: 'online',
      estimated_provision_hours: Number(value),
      allow_backorder: true,
    })
  }
}

async function doPublish(row, action) {
  try {
    if (action === 'online') await publishOnline(row.id, row)
    else await publishProduct(row.id, { action })
    ElMessage.success(action === 'online' ? '已上线' : '已下线')
    loadData()
  } catch (e) {
    if (e === 'cancel' || e === 'close') return
    ElMessage.error(e?.response?.data?.detail || e?.message || '操作失败')
  }
}

// ── 详情抽屉 ──────────────────────────────────────────────
const drawerVisible = ref(false)
const activeTab = ref('overview')
const current = ref(null)
const capacity = ref(null)
const stock = ref(null)
const publishLogs = ref([])

function openDrawer(row) {
  router.push(`/product/products/${row.id}`)
}

// ── 报价 ──────────────────────────────────────────────────
const quoting = ref(false)
const quoteResult = ref(null)
const quoteForm = reactive({ cpu_cores: 1, ram_mb: 1024, disk_gb: 40, bandwidth_mbps: 5, ip_count: 1 })
const quoteRamGb = computed({
  get: () => mbToGb(quoteForm.ram_mb),
  set: (gb) => { quoteForm.ram_mb = gbToMb(gb) },
})

async function doQuote() {
  quoting.value = true
  try {
    quoteResult.value = await quoteProduct(current.value.id, quoteForm)
  } finally { quoting.value = false }
}

// ── 阶梯规则 ──────────────────────────────────────────────
const ruleVisible = ref(false)
const ruleSaving = ref(false)
const ruleRef = ref(null)
const ruleEdit = ref(null)
const ruleForm = reactive({ resource_kind: 'cpu', min_qty: 1, max_qty: null, unit_price: 0, unit_reputation: 0 })
const ruleRules = { resource_kind: [{ required: true, message: '请选择资源类型' }] }

function openRule(row = null) {
  ruleEdit.value = row
  if (row) {
    Object.assign(ruleForm, {
      resource_kind: row.resource_kind, min_qty: row.min_qty, max_qty: row.max_qty,
      unit_price: parseFloat(row.unit_price) || 0, unit_reputation: parseFloat(row.unit_reputation) || 0,
    })
  } else {
    Object.assign(ruleForm, { resource_kind: 'cpu', min_qty: 1, max_qty: null, unit_price: 0, unit_reputation: 0 })
  }
  ruleVisible.value = true
}

async function saveRule() {
  const valid = await ruleRef.value.validate().catch(() => false)
  if (!valid) return
  ruleSaving.value = true
  try {
    const payload = { ...ruleForm, product: current.value.id }
    if (ruleEdit.value) { await updateUpgradeRule(ruleEdit.value.id, payload); ElMessage.success('已更新') }
    else { await createUpgradeRule(payload); ElMessage.success('已新增') }
    ruleVisible.value = false
    current.value = await getProduct(current.value.id)
  } finally { ruleSaving.value = false }
}

async function removeRule(row) {
  await ElMessageBox.confirm('确定删除该阶梯规则？', '警告', { type: 'warning' })
  await deleteUpgradeRule(row.id)
  ElMessage.success('已删除')
  current.value = await getProduct(current.value.id)
}

onMounted(async () => {
  await loadOptions()
  await loadData()
  // 从详情页「编辑」返回时自动打开编辑弹窗
  const editId = route.query.edit
  if (editId != null) {
    try {
      const row = await getProduct(editId)
      openDialog(row)
    } catch { /* ignore */ }
    router.replace({ path: '/product/products' })
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
.muted { color: #909399; font-size: 12px; }
.cap-row { display: flex; gap: 12px; margin-top: 12px; }
.cap-card { flex: 1; }
.cap-title { font-weight: 600; color: #606266; margin-bottom: 8px; }
.shortage { color: #f56c6c; margin: 8px 0 0; padding-left: 18px; }
.stock-num { font-size: 28px; font-weight: 700; color: #409EFF; }
.rules-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px; }
.quote-form { display: flex; flex-wrap: wrap; }
.quote-form :deep(.el-form-item) { margin-right: 16px; }
</style>
