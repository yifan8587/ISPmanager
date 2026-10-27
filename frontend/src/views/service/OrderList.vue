<template>
  <div class="page-container">
    <div class="page-header">
      <h2 class="page-title">服务工单管理</h2>
      <el-button v-if="canManage" type="primary" :icon="Plus" @click="openDialog()">新建工单</el-button>
    </div>

    <el-card shadow="never" class="search-card">
      <el-form :model="searchForm" inline>
        <el-form-item label="工单号">
          <el-input v-model="searchForm.search" placeholder="工单编号/客户名" clearable style="width:180px" @keyup.enter="loadData" />
        </el-form-item>
        <el-form-item label="客户">
          <el-select v-model="searchForm.customer" filterable clearable placeholder="全部客户" style="width:180px">
            <el-option v-for="c in customers" :key="c.id" :label="c.name" :value="c.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="状态">
          <el-select v-model="searchForm.status" clearable placeholder="全部" style="width:120px">
            <el-option v-for="s in statusOptions" :key="s.value" :label="s.label" :value="s.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="阶段">
          <el-select v-model="searchForm.current_stage" clearable placeholder="全部" style="width:120px">
            <el-option v-for="s in stageOptions" :key="s.value" :label="s.label" :value="s.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="类型">
          <el-select v-model="searchForm.order_type" clearable placeholder="全部" style="width:110px">
            <el-option v-for="t in orderTypeOptions" :key="t.value" :label="t.label" :value="t.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="时间">
          <el-date-picker v-model="dateRange" type="daterange" value-format="YYYY-MM-DD"
            start-placeholder="开始" end-placeholder="结束" style="width:240px" />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :icon="Search" @click="loadData">搜索</el-button>
          <el-button @click="resetSearch">重置</el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <el-card shadow="never" style="margin-top:12px;">
      <el-table :data="tableData" v-loading="loading" stripe border>
        <el-table-column prop="order_no" label="工单编号" width="180" />
        <el-table-column prop="customer_name" label="客户" width="130" />
        <el-table-column prop="product_name" label="服务产品" min-width="150">
          <template #default="{ row }">{{ row.product_name || '历史非目录服务' }}</template>
        </el-table-column>
        <el-table-column prop="order_type_label" label="类型" width="90" />
        <el-table-column prop="business_type_label" label="业务类型" width="130">
          <template #default="{ row }">{{ row.business_type_label || '-' }}</template>
        </el-table-column>
        <el-table-column label="需求/交付" min-width="180">
          <template #default="{ row }">
            <div class="request-summary">{{ requestSummary(row) }}</div>
            <div class="delivery-summary">
              <el-tag size="small" :type="deliveryTagType(row.delivery_summary?.status)">
                {{ deliveryLabel(row.delivery_summary?.status) }}
              </el-tag>
              <span>{{ row.delivery_summary?.active_count || 0 }}/{{ row.delivery_summary?.expected_quantity || row.requested_quantity || 1 }} 已激活</span>
              <el-tag
                v-if="row.manual_delivery?.open"
                size="small"
                type="warning"
              >清单 {{ row.manual_delivery.done_count }}/{{ row.manual_delivery.total_count }}</el-tag>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="期望POP" width="130">
          <template #default="{ row }">{{ row.preferred_pop_name || '-' }}</template>
        </el-table-column>
        <el-table-column label="来源" width="105">
          <template #default="{ row }">
            <el-tag size="small" effect="plain" :type="row.source === 'portal' ? 'success' : 'info'">
              {{ row.source_label || '-' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="status" label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="statusMap[row.status]?.type" size="small">{{ statusMap[row.status]?.label }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="生命周期" width="100">
          <template #default="{ row }">
            <el-tag size="small" type="info">{{ row.current_stage_label || stageMap[row.current_stage] || '-' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="处理人" width="100">
          <template #default="{ row }">{{ row.handler_name || '-' }}</template>
        </el-table-column>
        <el-table-column label="自动开通" width="130">
          <template #default="{ row }">
            <el-tooltip v-if="row.auto_provision_status && row.auto_provision_status !== 'none'"
              :content="row.auto_provision_message || ''" placement="top">
              <el-tag :type="apStatusMap[row.auto_provision_status]?.type" size="small">
                {{ apStatusMap[row.auto_provision_status]?.label }}
              </el-tag>
            </el-tooltip>
            <span v-else style="color:#C0C4CC">-</span>
            <div v-if="row.capacity_reservation" class="fx-lock">
              {{ reservationLabel(row.capacity_reservation) }}
            </div>
          </template>
        </el-table-column>
        <el-table-column label="支付" width="155">
          <template #default="{ row }">
            <template v-if="row.payment_billing">
              <el-tag
                size="small"
                :type="row.payment_billing.status === 'paid' ? 'success' : 'warning'"
              >
                {{ row.payment_billing.status_label }}
              </el-tag>
              <el-button link type="primary" size="small" @click="goBilling(row)">
                {{ row.payment_billing.bill_no }}
              </el-button>
            </template>
            <span v-else-if="Number(row.quoted_price) === 0 && row.status === 'approved'" class="muted">零金额</span>
            <span v-else class="muted">-</span>
          </template>
        </el-table-column>
        <el-table-column prop="billing_cycle_label" label="计费周期" width="100" />
        <el-table-column prop="quoted_price" label="报价" width="130" align="right">
          <template #default="{ row }">
            <div>{{ row.quoted_price ? `${row.fx_quote_currency || 'USD'} ${row.quoted_price}` : '-' }}</div>
            <div v-if="row.expected_delivery_at" class="fx-lock">预计 {{ formatDate(row.expected_delivery_at) }}</div>
            <div v-if="row.fx_locked_at" class="fx-lock">已锁汇率</div>
          </template>
        </el-table-column>
        <el-table-column prop="instance_count" label="实例数" width="80" align="center" />
        <el-table-column label="创建人" width="100">
          <template #default="{ row }">{{ row.created_by_name || '-' }}</template>
        </el-table-column>
        <el-table-column label="创建时间" width="160">
          <template #default="{ row }">{{ formatDate(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="340" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="openSnapshot(row)">资源看板</el-button>
            <el-button link type="info" size="small" @click="openLifecycle(row)">生命周期</el-button>
            <el-button v-if="canManage && row.status === 'draft'" link type="success" size="small" @click="handleSubmit(row)">提交</el-button>
            <el-button v-if="canManage && row.status === 'pending'" link type="warning" size="small" @click="handleApprove(row, true)">批准</el-button>
            <el-button v-if="canManage && row.status === 'pending'" link type="danger" size="small" @click="handleApprove(row, false)">驳回</el-button>
            <el-button
              v-if="canManage && canRetryProvision(row)"
              link type="warning" size="small" @click="retryProvision(row)"
            >重试开通</el-button>
            <el-button
              v-if="canManage && canDeliver(row)"
              link type="success" size="small" @click="openDelivery(row)"
            >资源交付</el-button>
            <el-button v-if="canManage && canEdit(row)" link type="primary" size="small" @click="openDialog(row)">编辑</el-button>
            <el-button v-if="canManage" link type="danger" size="small" @click="handleDelete(row)" :disabled="!canDelete(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-pagination v-model:current-page="page" v-model:page-size="pageSize" :total="total"
        layout="total, prev, pager, next" class="pagination" @change="loadData" />
    </el-card>

    <el-dialog v-model="dialogVisible" :title="editRow ? '编辑服务工单' : '新建服务工单'" width="920px" destroy-on-close>
      <el-skeleton v-if="dialogLoading" :rows="8" animated />
      <el-form v-else ref="formRef" :model="form" :rules="formRules" label-position="top">
        <el-alert
          v-if="editRow"
          :title="`工单 ${editRow.order_no} · ${editRow.source_label || '后台创建'} · 当前状态：${statusMap[editRow.status]?.label || editRow.status}`"
          type="info" :closable="false" show-icon class="form-alert"
        />
        <el-alert
          v-if="formLocked"
          title="该工单已进入审批/交付流程，客户、产品和规格不可再改；如需调整请走服务变更。"
          type="warning" :closable="false" show-icon class="form-alert"
        />
        <el-tabs v-model="formTab">
          <el-tab-pane label="基本信息" name="basic">
            <el-row :gutter="16">
              <el-col :span="12">
                <el-form-item label="客户" prop="customer">
                  <el-select v-model="form.customer" filterable placeholder="选择客户" style="width:100%" :disabled="formLocked">
                    <el-option v-for="c in customers" :key="c.id" :label="`${c.customer_no || ''} ${c.name}`" :value="c.id" />
                  </el-select>
                </el-form-item>
              </el-col>
              <el-col :span="12">
                <el-form-item label="当前处理人">
                  <el-select v-model="form.handler" filterable clearable placeholder="未指派" style="width:100%">
                    <el-option v-for="user in handlers" :key="user.id" :label="userLabel(user)" :value="user.id" />
                  </el-select>
                </el-form-item>
              </el-col>
              <el-col :span="8">
                <el-form-item label="工单类型" prop="order_type">
                  <el-select v-model="form.order_type" style="width:100%" :disabled="formLocked">
                    <el-option v-for="t in orderTypeOptions" :key="t.value" :label="t.label" :value="t.value" />
                  </el-select>
                </el-form-item>
              </el-col>
              <el-col :span="8">
                <el-form-item label="业务类型" prop="business_type">
                  <el-select v-model="form.business_type" style="width:100%" :disabled="formLocked">
                    <el-option v-for="t in businessTypeOptions" :key="t.value" :label="t.label" :value="t.value" />
                  </el-select>
                </el-form-item>
              </el-col>
              <el-col :span="8">
                <el-form-item label="需求数量">
                  <el-input-number v-model="form.requested_quantity" :min="1" :max="100" style="width:100%" :disabled="formLocked" />
                </el-form-item>
              </el-col>
              <el-col :span="12">
                <el-form-item label="服务产品" prop="product">
                  <el-select v-model="form.product" filterable clearable placeholder="新开服务请选择已发布产品" style="width:100%" :disabled="formLocked" @change="applyProduct">
                    <el-option v-for="p in products" :key="p.id" :label="`${p.product_no} - ${p.name}${p.pop_name ? ' · ' + p.pop_name : ''}`" :value="p.id" />
                  </el-select>
                </el-form-item>
              </el-col>
              <el-col :span="12">
                <el-form-item label="期望 POP 节点">
                  <el-select v-model="form.preferred_pop" filterable clearable placeholder="选择产品后自动关联节点" style="width:100%" :disabled="!!form.product">
                    <el-option v-for="p in pops" :key="p.id" :label="`${p.code} - ${p.name}${p.config_mode==='auto' ? '（自动）' : ''}`" :value="p.id" />
                  </el-select>
                </el-form-item>
              </el-col>
              <el-col :span="12">
                <el-form-item label="计费周期">
                  <el-select v-model="form.billing_cycle" style="width:100%">
                    <el-option v-for="b in billingCycleOptions" :key="b.value" :label="b.label" :value="b.value" />
                  </el-select>
                </el-form-item>
              </el-col>
              <el-col :span="12">
                <el-form-item label="报价（元）">
                  <el-input-number v-model="form.quoted_price" :min="0" :precision="2" style="width:100%" :disabled="formLocked" />
                </el-form-item>
              </el-col>
            </el-row>
          </el-tab-pane>

          <el-tab-pane label="资源规格" name="spec">
            <el-alert :title="specHint" type="info" :closable="false" show-icon class="form-alert" />
            <el-row :gutter="16">
              <template v-if="form.business_type === 'vps'">
                <el-col :span="8"><el-form-item label="vCPU"><el-input-number v-model="form.requested_vcpu" :min="1" style="width:100%" :disabled="formLocked" /></el-form-item></el-col>
                <el-col :span="8"><el-form-item label="内存（GB）"><el-input-number v-model="requestedRamGb" :min="1" :step="1" style="width:100%" :disabled="formLocked" /></el-form-item></el-col>
                <el-col :span="8"><el-form-item label="硬盘（GB）"><el-input-number v-model="form.requested_disk_gb" :min="1" style="width:100%" :disabled="formLocked" /></el-form-item></el-col>
                <el-col :span="8"><el-form-item label="带宽（Mbps）"><el-input-number v-model="form.requested_bandwidth_mbps" :min="0" style="width:100%" :disabled="formLocked" /></el-form-item></el-col>
                <el-col :span="8"><el-form-item label="IP 数量"><el-input-number v-model="form.requested_ip_count" :min="0" style="width:100%" :disabled="formLocked" /></el-form-item></el-col>
                <el-col :span="8">
                  <el-form-item label="期望操作系统">
                    <el-select
                      v-model="form.requested_os_template"
                      filterable
                      clearable
                      placeholder="选择系统模版"
                      style="width:100%"
                      :disabled="formLocked"
                      @change="onOsTemplateChange"
                    >
                      <el-option
                        v-for="item in osTemplateOptions"
                        :key="item.id"
                        :label="item.label || `${item.name} ${item.version}`"
                        :value="item.id"
                      />
                    </el-select>
                  </el-form-item>
                </el-col>
              </template>
              <template v-else-if="form.business_type === 'ip'">
                <el-col :span="12"><el-form-item label="IP 数量"><el-input-number v-model="form.requested_ip_count" :min="1" style="width:100%" /></el-form-item></el-col>
                <el-col :span="12"><el-form-item label="带宽需求（可选）"><el-input-number v-model="form.requested_bandwidth_mbps" :min="0" style="width:100%" /></el-form-item></el-col>
              </template>
              <template v-else-if="form.business_type === 'bandwidth'">
                <el-col :span="12"><el-form-item label="带宽（Mbps）"><el-input-number v-model="form.requested_bandwidth_mbps" :min="1" style="width:100%" /></el-form-item></el-col>
                <el-col :span="12"><el-form-item label="IP 数量（可选）"><el-input-number v-model="form.requested_ip_count" :min="0" style="width:100%" /></el-form-item></el-col>
              </template>
              <template v-else>
                <el-col :span="12"><el-form-item label="带宽（Mbps）"><el-input-number v-model="form.requested_bandwidth_mbps" :min="1" style="width:100%" /></el-form-item></el-col>
                <el-col :span="12"><el-form-item label="IP 数量"><el-input-number v-model="form.requested_ip_count" :min="0" style="width:100%" /></el-form-item></el-col>
              </template>
            </el-row>

            <template v-if="isEdge()">
              <el-divider content-position="left">边缘协议参数</el-divider>
              <el-row :gutter="16" v-if="form.business_type === 'edge_gre'">
                <el-col :span="12"><el-form-item label="本端 IP（GRE）"><el-input v-model="form.edge_local_ip" placeholder="GRE 隧道本端地址" /></el-form-item></el-col>
                <el-col :span="12"><el-form-item label="对端 IP（GRE）"><el-input v-model="form.edge_remote_ip" placeholder="GRE 隧道对端地址" /></el-form-item></el-col>
              </el-row>
              <el-row :gutter="16" v-else-if="form.business_type === 'edge_wg'">
                <el-col :span="12"><el-form-item label="本端接口 IP"><el-input v-model="form.edge_local_ip" placeholder="如 10.100.0.1/24" /></el-form-item></el-col>
                <el-col :span="12"><el-form-item label="WG 监听端口"><el-input-number v-model="form.edge_wg_port" :min="1" :max="65535" style="width:100%" /></el-form-item></el-col>
                <el-col :span="12"><el-form-item label="客户端公钥"><el-input v-model="form.edge_wg_client_pubkey" placeholder="WireGuard 对端公钥" /></el-form-item></el-col>
                <el-col :span="12"><el-form-item label="允许网段"><el-input v-model="form.edge_wg_allowed_ips" placeholder="如 10.0.0.0/8" /></el-form-item></el-col>
              </el-row>
              <el-row :gutter="16" v-else>
                <el-col :span="8"><el-form-item label="Socks5 端口"><el-input-number v-model="form.edge_socks5_port" :min="1" :max="65535" style="width:100%" /></el-form-item></el-col>
                <el-col :span="8"><el-form-item label="认证用户名"><el-input v-model="form.edge_socks5_user" /></el-form-item></el-col>
                <el-col :span="8"><el-form-item label="认证密码"><el-input v-model="form.edge_socks5_pass" show-password placeholder="编辑时留空保持原密码" /></el-form-item></el-col>
              </el-row>
            </template>
          </el-tab-pane>

          <el-tab-pane label="交付与说明" name="delivery">
            <template v-if="requestSnapshot">
              <el-alert title="客户原始请求快照（只读）" type="info" :closable="false" show-icon class="form-alert" />
              <el-descriptions :column="2" border size="small" class="request-snapshot">
                <el-descriptions-item label="提交来源">{{ requestSnapshot.source || '-' }}</el-descriptions-item>
                <el-descriptions-item label="产品">{{ requestSnapshot.product?.product_no }} · {{ requestSnapshot.product?.name }}</el-descriptions-item>
                <el-descriptions-item label="客户数量">{{ requestSnapshot.quantity || 1 }}</el-descriptions-item>
                <el-descriptions-item label="原始报价">{{ requestSnapshot.quote?.monthly_total || '-' }} {{ requestSnapshot.quote?.currency || '' }}</el-descriptions-item>
                <el-descriptions-item label="原始规格" :span="2">{{ requestSnapshotText }}</el-descriptions-item>
                <el-descriptions-item v-if="requestSnapshot.requirements" label="客户备注" :span="2">{{ requestSnapshot.requirements }}</el-descriptions-item>
              </el-descriptions>
              <el-divider />
            </template>
            <el-row :gutter="16">
              <el-col :span="12"><el-form-item label="服务开始"><el-date-picker v-model="form.service_start" type="date" value-format="YYYY-MM-DD" style="width:100%" /></el-form-item></el-col>
              <el-col :span="12"><el-form-item label="服务结束"><el-date-picker v-model="form.service_end" type="date" value-format="YYYY-MM-DD" style="width:100%" /></el-form-item></el-col>
            </el-row>
            <el-form-item label="客户需求说明"><el-input v-model="form.requirements" type="textarea" :rows="4" placeholder="记录客户用途、网络要求、交付约束等" :disabled="formLocked" /></el-form-item>
            <el-form-item label="内部处理备注"><el-input v-model="form.remark" type="textarea" :rows="3" placeholder="仅用于内部交接和处理说明" /></el-form-item>
          </el-tab-pane>
        </el-tabs>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="handleSave">确认</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="deliveryVisible" title="资源交付与工单同步" width="700px" destroy-on-close>
      <template v-if="deliveryOrder">
        <el-alert
          :title="`工单 ${deliveryOrder.order_no}：${deliveryRequestText(deliveryOrder)}`"
          type="info" :closable="false" show-icon class="form-alert"
        />
        <el-alert
          v-if="deliveryPopName"
          :title="`交付节点：${deliveryPopName}（已按产品关联 POP 过滤可交付资源）`"
          type="success" :closable="false" show-icon class="form-alert"
        />
        <el-alert
          v-if="isChangeDelivery"
          title="这是已付款的服务变更工单。交付会把新增 IP 挂到原实例并按变更规格开通，不会新建实例。"
          type="warning" :closable="false" show-icon class="form-alert"
        />
        <div v-if="manualDelivery?.exists" class="manual-delivery">
          <div class="manual-delivery-head">
            <el-tag size="small" type="warning">{{ manualDelivery.reason_label }}</el-tag>
            <span>{{ manualDelivery.done_count }}/{{ manualDelivery.total_count }} 步</span>
            <el-button
              v-if="manualDelivery.open"
              link type="primary" size="small"
              :loading="tokenLoading"
              @click="rotateDeliveryToken"
            >生成回传令牌</el-button>
          </div>
          <el-steps :active="manualDelivery.done_count" finish-status="success" align-center>
            <el-step
              v-for="item in manualDelivery.items || []"
              :key="item.key"
              :title="item.label"
              :status="item.done ? 'success' : 'wait'"
            />
          </el-steps>
          <el-text v-if="deliveryToken" type="warning" size="small">
            回传令牌仅显示一次：{{ deliveryToken }}
          </el-text>
        </div>
        <el-skeleton v-if="deliveryLoading" :rows="6" animated />
        <el-form v-else :model="deliveryForm" label-position="top">
          <el-form-item v-if="!isChangeDelivery && deliveryOrder.business_type === 'vps'" label="选择待交付 VPS">
            <el-select v-model="deliveryForm.vps_id" filterable placeholder="仅展示匹配当前客户和 POP 的未绑定 VPS" style="width:100%">
              <el-option v-for="item in deliveryOptions.vps" :key="item.id" :label="`${item.label} · ${item.pop_name || '-'} · ${item.bandwidth_mbps || 0}Mbps`" :value="item.id" />
            </el-select>
          </el-form-item>
          <el-form-item v-if="!isChangeDelivery && isDeliveryEdge" label="选择待交付边缘加速">
            <el-select v-model="deliveryForm.edge_id" filterable placeholder="仅展示匹配当前客户和 POP 的未绑定边缘资源" style="width:100%">
              <el-option v-for="item in deliveryOptions.edge" :key="item.id" :label="`${item.label} · ${item.protocol} · ${item.pop_name || '-'}`" :value="item.id" />
            </el-select>
          </el-form-item>
          <el-form-item v-if="!isChangeDelivery && needsBandwidthSelection" label="绑定带宽分配">
            <el-select v-model="deliveryForm.bandwidth_allocation_id" filterable clearable placeholder="请选择已建立的带宽分配" style="width:100%">
              <el-option v-for="item in filteredBandwidthOptions" :key="item.id" :label="`${item.allocated_mbps}Mbps · ${item.target_label}`" :value="item.id" />
            </el-select>
          </el-form-item>
          <el-form-item v-if="needsIpSelection" :label="isChangeDelivery ? '新增 IP（资源补齐后选择）' : (deliveryOrder.business_type === 'ip' ? '交付 IP（必选）' : '附加 IP（可选）')">
            <el-select v-model="deliveryForm.ip_ids" multiple filterable collapse-tags collapse-tags-tooltip placeholder="从当前 POP 的可用 IP 中选择" style="width:100%">
              <el-option v-for="item in deliveryOptions.ip_addresses" :key="item.id" :label="item.label" :value="item.id" />
            </el-select>
          </el-form-item>
          <el-row v-if="!isChangeDelivery" :gutter="16">
            <el-col :span="12"><el-form-item label="立即激活"><el-switch v-model="deliveryForm.activate" active-text="资源已交付并启用" inactive-text="仅预留，稍后激活" /></el-form-item></el-col>
            <el-col :span="12"><el-form-item label="测试服务"><el-switch v-model="deliveryForm.is_trial" active-text="测试交付" inactive-text="正式服务" /></el-form-item></el-col>
          </el-row>
          <el-form-item label="交付说明"><el-input v-model="deliveryForm.remark" type="textarea" :rows="3" placeholder="记录资源选择、交付结果或需要交接的事项" /></el-form-item>
        </el-form>
      </template>
      <template #footer>
        <el-button @click="deliveryVisible = false">取消</el-button>
        <el-button
          v-if="canManage && canQuickProvision"
          :loading="deliverySaving"
          @click="submitQuickProvision"
        >按产品节点快速建档</el-button>
        <el-button v-if="canManage" type="primary" :loading="deliverySaving" @click="submitDelivery">确认交付并同步工单</el-button>
      </template>
    </el-dialog>

    <el-drawer v-model="lifecycleVisible" title="服务全生命周期" size="560px" destroy-on-close>
      <div v-if="lifecycleOrder" style="margin-bottom:12px;color:#606266">
        工单 {{ lifecycleOrder.order_no }} · {{ lifecycleOrder.customer_name }}
      </div>
      <el-timeline v-loading="lifecycleLoading">
        <el-timeline-item
          v-for="item in lifecycleRecords"
          :key="item.id"
          :timestamp="formatDate(item.completed_at || item.submitted_at || item.created_at)"
          placement="top"
        >
          <div class="lc-title">
            <strong>{{ item.stage_label || item.title || item.stage }}</strong>
            <el-tag size="small" style="margin-left:8px">{{ item.status_label || item.status }}</el-tag>
          </div>
          <div class="lc-meta">
            提交人：{{ item.submitted_by_name || '-' }} /
            处理人：{{ item.handled_by_name || '-' }} /
            审批人：{{ item.approved_by_name || '-' }}
          </div>
          <div class="lc-detail" v-if="item.detail">{{ item.detail }}</div>
          <el-collapse v-if="item.resource_before || item.resource_after || item.fee_snapshot">
            <el-collapse-item title="资源/资费快照" name="snap">
              <pre class="snap-pre">{{ JSON.stringify({
                before: item.resource_before,
                after: item.resource_after,
                fee: item.fee_snapshot,
              }, null, 2) }}</pre>
            </el-collapse-item>
          </el-collapse>
        </el-timeline-item>
      </el-timeline>
      <el-empty v-if="!lifecycleLoading && !lifecycleRecords.length" description="暂无生命周期记录" />
    </el-drawer>

    <el-drawer v-model="snapshotVisible" title="服务单资源看板" size="620px" destroy-on-close>
      <template v-if="orderSnapshot">
        <el-descriptions :column="2" border size="small">
          <el-descriptions-item label="服务单">{{ orderSnapshot.order?.order_no }}</el-descriptions-item>
          <el-descriptions-item label="服务状态">{{ orderSnapshot.order?.status }}</el-descriptions-item>
          <el-descriptions-item label="客户">{{ orderSnapshot.order?.customer?.name }}</el-descriptions-item>
          <el-descriptions-item label="服务产品">{{ orderSnapshot.order?.product?.name || '-' }}</el-descriptions-item>
          <el-descriptions-item label="期望 POP" :span="2">{{ orderSnapshot.order?.preferred_pop?.name || '-' }}</el-descriptions-item>
        </el-descriptions>
        <el-divider>当前资源实例</el-divider>
        <el-card v-for="instance in orderSnapshot.instances || []" :key="instance.instance_id" shadow="never" class="snapshot-card">
          <template #header>{{ instance.instance_no }} · {{ instance.service_type }} · {{ instance.status }}</template>
          <el-descriptions :column="2" size="small">
            <el-descriptions-item label="POP">{{ instance.pop?.name || '-' }}</el-descriptions-item>
            <el-descriptions-item label="月费">{{ instance.fee?.monthly_fee || '-' }} {{ instance.fee?.currency || '' }}</el-descriptions-item>
            <el-descriptions-item label="vCPU">{{ instance.vps?.vcpu ?? '-' }}</el-descriptions-item>
            <el-descriptions-item label="内存">{{ ramText(instance.vps?.ram_mb) }}</el-descriptions-item>
            <el-descriptions-item label="磁盘">{{ instance.vps?.disk_gb ?? '-' }} GB</el-descriptions-item>
            <el-descriptions-item label="带宽">{{ instance.vps?.bandwidth_mbps ?? instance.edge?.bandwidth_mbps ?? '-' }} Mbps</el-descriptions-item>
            <el-descriptions-item label="IP" :span="2">{{ (instance.ip_addresses || []).map(ip => ip.address).join(', ') || '-' }}</el-descriptions-item>
          </el-descriptions>
        </el-card>
        <el-empty v-if="!snapshotLoading && !(orderSnapshot.instances || []).length" description="该服务单尚未交付资源" />
      </template>
    </el-drawer>
  </div>
</template>

<script setup>
import { computed, ref, reactive, onMounted } from 'vue'
import { gbToMb, mbToGb, ramText } from '@/utils/ram'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Search } from '@element-plus/icons-vue'
import {
  getOrders, getOrder, createOrder, updateOrder, deleteOrder, submitOrder,
  approveOrder, retryOrderProvision, getOrderLifecycle, getOrderSnapshot,
  getOrderDeliveryOptions, deliverOrderResources,
  getOrderManualDelivery, rotateOrderDeliveryToken, getOrderCatalog,
} from '@/api/service'
import { getCustomers } from '@/api/customer'
import { getPopPoints } from '@/api/resource'
import { getProducts, getProductOsTemplates } from '@/api/product'
import { getUsers } from '@/api/accounts'
import { usePermission } from '@/composables/usePermission'
import { useAuthStore } from '@/stores/auth'

const authStore = useAuthStore()
const isChannelAccount = computed(() =>
  authStore.user?.role === 'channel' || authStore.user?.is_channel_account
)
const { canManagePageAction, canManageModule, isSuperuser } = usePermission()
const canManage = computed(() => canManagePageAction('service', 'order') || canManageModule('service'))
const canQuickProvision = ref(false)
const deliveryPopName = ref('')

const loading = ref(false)
const router = useRouter()
const saving = ref(false)
const dialogVisible = ref(false)
const dialogLoading = ref(false)
const formRef = ref(null)
const editRow = ref(null)
const tableData = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const customers = ref([])
const products = ref([])
const osTemplateOptions = ref([])
const pops = ref([])
const handlers = ref([])
const formTab = ref('basic')
const deliveryVisible = ref(false)
const deliveryLoading = ref(false)
const deliverySaving = ref(false)
const deliveryOrder = ref(null)
const deliveryOptions = reactive({
  vps: [], edge: [], ip_addresses: [], bandwidth_allocations: [],
})
const deliveryForm = reactive({
  vps_id: null,
  edge_id: null,
  bandwidth_allocation_id: null,
  ip_ids: [],
  activate: true,
  is_trial: false,
  remark: '',
})
const manualDelivery = ref(null)
const deliveryToken = ref('')
const tokenLoading = ref(false)

const searchForm = reactive({ search: '', status: '', order_type: '', customer: null, current_stage: '' })
const dateRange = ref([])
const lifecycleVisible = ref(false)
const lifecycleLoading = ref(false)
const lifecycleOrder = ref(null)
const lifecycleRecords = ref([])
const snapshotVisible = ref(false)
const snapshotLoading = ref(false)
const orderSnapshot = ref(null)
const requestSnapshot = ref(null)
const stageOptions = [
  { value: 'order', label: '下单' }, { value: 'requirement', label: '需求确认' },
  { value: 'test', label: '测试' }, { value: 'usage', label: '正式使用' },
  { value: 'change', label: '变更' }, { value: 'termination', label: '终止' },
]
const stageMap = Object.fromEntries(stageOptions.map(s => [s.value, s.label]))

// 与后端 ServiceOrderWriteSerializer.fields 完全对齐
const form = reactive({
  order_type: 'new',
  business_type: 'vps',       // 业务类型：vps/edge_gre/edge_wg/edge_socks5/ip/bandwidth
  customer: null,
  contract: null,
  product: null,
  preferred_pop: null,        // 期望 POP 节点（自动开通判定该节点资源）
  handler: null,
  billing_cycle: 'monthly',  // 后端：billing_cycle
  requested_vcpu: null,
  requested_ram_mb: null,
  requested_disk_gb: null,
  requested_os: '',
  requested_os_template: null,
  requested_bandwidth_mbps: null,
  requested_ip_count: null,
  requested_quantity: 1,
  edge_local_ip: null,
  edge_remote_ip: null,
  edge_wg_port: 51820,
  edge_wg_keepalive: 25,
  edge_wg_allowed_ips: '',
  edge_wg_client_pubkey: '',
  edge_socks5_port: 1080,
  edge_socks5_user: '',
  edge_socks5_pass: '',
  requirements: '',
  service_start: null,
  service_end: null,
  quoted_price: null,
  reject_reason: '',
  remark: ''
})

const businessTypeOptions = [
  { value: 'vps', label: 'VPS 主机' },
  { value: 'edge_gre', label: '边缘加速 · GRE' },
  { value: 'edge_wg', label: '边缘加速 · WireGuard' },
  { value: 'edge_socks5', label: '边缘加速 · Socks5' },
  { value: 'ip', label: 'IP 资源' },
  { value: 'bandwidth', label: '带宽资源' }
]
const apStatusMap = {
  payment_pending: { label: '等待付款', type: 'warning' },
  provisioning: { label: '自动开通中', type: 'primary' },
  paid_pending: { label: '已付款待人工', type: 'warning' },
  success: { label: '自动开通成功', type: 'success' },
  insufficient: { label: '资源不足待人工', type: 'warning' },
  failed: { label: '自动开通失败', type: 'danger' },
  none: { label: '', type: 'info' }
}
const reservationLabel = (item) => {
  if (!item) return ''
  if (item.status === 'held') return item.expires_at ? '已预占容量' : '已预占（已付款）'
  if (item.status === 'consumed') return '容量已消耗'
  if (item.status === 'released') return '预占已释放'
  return item.status_label || ''
}

const statusOptions = [
  { value: 'draft', label: '草稿' }, { value: 'pending', label: '待审批' },
  { value: 'approved', label: '已审批' }, { value: 'provisioning', label: '开通中' },
  { value: 'active', label: '服务中' }, { value: 'suspended', label: '已暂停' },
  { value: 'terminated', label: '已终止' }, { value: 'rejected', label: '已驳回' }
]
const statusMap = {
  draft: { label: '草稿', type: 'info' }, pending: { label: '待审批', type: 'warning' },
  approved: { label: '已审批', type: 'primary' }, provisioning: { label: '开通中', type: 'primary' },
  active: { label: '服务中', type: 'success' }, suspend_pending: { label: '待暂停', type: 'warning' },
  suspended: { label: '已暂停', type: 'warning' }, terminate_pending: { label: '待终止', type: 'danger' },
  terminated: { label: '已终止', type: 'info' }, rejected: { label: '已驳回', type: 'danger' }
}
const orderTypeOptions = [
  { value: 'new', label: '新购' }, { value: 'renew', label: '续费' },
  { value: 'upgrade', label: '升级' }, { value: 'downgrade', label: '降级' },
  { value: 'terminate', label: '终止' }, { value: 'change', label: '变更' }
]
const billingCycleOptions = [
  { value: 'monthly', label: '按月' }, { value: 'quarterly', label: '按季' },
  { value: 'semi_annual', label: '半年' }, { value: 'annual', label: '按年' },
  { value: 'one_time', label: '一次性' }, { value: 'on_demand', label: '按需' }
]

const formRules = {
  customer: [{ required: true, message: '请选择客户' }],
  order_type: [{ required: true, message: '请选择工单类型' }],
  product: [{
    validator: (_rule, value, callback) => {
      if (form.order_type === 'new' && !value) callback(new Error('新开服务请选择已上线的服务产品'))
      else callback()
    },
    trigger: 'change'
  }]
}

const specHint = computed(() => {
  const hints = {
    vps: '填写每台 VPS 的需求规格；需求数量大于 1 时会按同一规格逐台交付。',
    edge_gre: 'GRE 需填写本端与对端 IP；自动下发前会校验节点和隧道配置。',
    edge_wg: 'WireGuard 需填写接口 IP、监听端口及对端公钥。',
    edge_socks5: 'Socks5 的认证信息仅用于节点侧配置，请妥善保管。',
    ip: '选择客户所需的独立 IP 数量，交付时从期望 POP 的可用地址中选择。',
    bandwidth: '带宽需要绑定到已建立的 VPS 或边缘加速带宽分配记录。',
  }
  return hints[form.business_type] || '按业务类型填写真实需求，交付完成后会自动回写工单状态。'
})
const isChangeDelivery = computed(() => ['upgrade', 'downgrade', 'change'].includes(deliveryOrder.value?.order_type))
const isDeliveryEdge = computed(() => ['edge_gre', 'edge_wg', 'edge_socks5'].includes(deliveryOrder.value?.business_type))
const needsIpSelection = computed(() => ['vps', 'edge_gre', 'edge_wg', 'edge_socks5', 'ip'].includes(deliveryOrder.value?.business_type) || isChangeDelivery.value)
const needsBandwidthSelection = computed(() => ['vps', 'edge_gre', 'edge_wg', 'edge_socks5', 'bandwidth'].includes(deliveryOrder.value?.business_type))
const filteredBandwidthOptions = computed(() => {
  if (!deliveryOrder.value) return []
  return deliveryOptions.bandwidth_allocations.filter((item) => {
    if (deliveryOrder.value.business_type === 'vps' && deliveryForm.vps_id) return item.vps_id === deliveryForm.vps_id
    if (isDeliveryEdge.value && deliveryForm.edge_id) return item.edge_id === deliveryForm.edge_id
    return true
  })
})
const requestedRamGb = computed({
  get: () => (form.requested_ram_mb ? mbToGb(form.requested_ram_mb) : undefined),
  set: (gb) => { form.requested_ram_mb = gbToMb(gb) },
})
const requestSnapshotText = computed(() => {
  const spec = requestSnapshot.value?.requested_spec || {}
  const parts = []
  if (spec.cpu_cores) parts.push(`${spec.cpu_cores} vCPU`)
  if (spec.ram_mb) parts.push(`${ramText(spec.ram_mb)} 内存`)
  if (spec.disk_gb) parts.push(`${spec.disk_gb} GB 硬盘`)
  if (spec.bandwidth_mbps) parts.push(`${spec.bandwidth_mbps} Mbps 带宽`)
  if (spec.ip_count) parts.push(`${spec.ip_count} 个 IP`)
  if (spec.nic_count) parts.push(`${spec.nic_count} 张网卡`)
  return parts.join(' / ') || '-'
})

function formatDate(dt) {
  if (!dt) return '-'
  return new Date(dt).toLocaleString('zh-CN', { hour12: false }).replace(/\//g, '-')
}

function userLabel(user) {
  const name = [user.first_name, user.last_name].filter(Boolean).join(' ') || user.username
  return `${name} (${user.username})`
}

function requestSummary(row) {
  const parts = []
  const isEdge = String(row.business_type || '').startsWith('edge')
  if (!isEdge) {
    if (row.requested_vcpu) parts.push(`${row.requested_vcpu} 核`)
    if (row.requested_ram_mb) parts.push(ramText(row.requested_ram_mb))
    if (row.requested_disk_gb) parts.push(`${row.requested_disk_gb} GB`)
  }
  if (row.requested_bandwidth_mbps) parts.push(`${row.requested_bandwidth_mbps} Mbps`)
  if (row.requested_ip_count) parts.push(`${row.requested_ip_count} IP`)
  return `${parts.join(' / ') || row.business_type_label || '服务资源'} ×${row.requested_quantity || 1}`
}

function deliveryLabel(value) {
  return {
    not_started: '待交付',
    partial: '部分交付',
    delivered: '已交付',
    terminated: '已终止',
    pending: '处理中',
  }[value] || '待交付'
}

function deliveryTagType(value) {
  return {
    not_started: 'info',
    partial: 'warning',
    delivered: 'success',
    terminated: 'info',
    pending: 'primary',
  }[value] || 'info'
}

function deliveryRequestText(row) {
  return `${row.business_type_label || row.business_type}，需求 ${requestSummary(row)}，已激活 ${row.delivery_summary?.active_count || 0}/${row.delivery_summary?.expected_quantity || row.requested_quantity || 1}`
}

function isChangeOrder(row) {
  return ['upgrade', 'downgrade', 'change'].includes(row.order_type)
}

function canDeliver(row) {
  if (!['approved', 'provisioning', 'active'].includes(row.status)) return false
  if (row.payment_billing && row.payment_billing.status !== 'paid') return false
  if (isChangeOrder(row)) {
    return ['failed', 'insufficient', 'paid_pending'].includes(row.auto_provision_status)
      || (row.related_instance && row.status === 'approved' && row.auto_provision_status !== 'success')
  }
  return (row.delivery_summary?.allocated_count || 0) < (row.delivery_summary?.expected_quantity || row.requested_quantity || 1)
}

function canEdit(row) {
  return ['draft', 'pending', 'approved', 'rejected'].includes(row.status)
}

function canDelete(row) {
  if ((row.instance_count || 0) > 0) return false
  if (['active', 'suspended'].includes(row.status)) return false
  if (row.status === 'provisioning' && !isSuperuser.value) return false
  return true
}

function isDeliveryLocked(row) {
  if (!row) return false
  if (['provisioning', 'active', 'suspended', 'terminated'].includes(row.status)) return true
  if (row.status === 'approved' && (row.payment_billing || ['payment_pending', 'provisioning', 'success'].includes(row.auto_provision_status))) {
    return true
  }
  return false
}

const formLocked = computed(() => isDeliveryLocked(editRow.value))

function canRetryProvision(row) {
  if (row.manual_delivery?.reason === 'stock' || row.auto_provision_status === 'insufficient') return false
  if (!['approved', 'provisioning'].includes(row.status)) return false
  if (!['failed', 'paid_pending'].includes(row.auto_provision_status)) return false
  if (row.payment_billing) return row.payment_billing.status === 'paid'
  return Number(row.quoted_price || 0) === 0
}

function formatApiError(error, fallback = '操作失败') {
  const data = error?.response?.data
  if (!data) return error?.message || fallback
  if (typeof data.message === 'string' && data.message && data.message !== '请求处理失败') {
    return data.message
  }
  if (typeof data.detail === 'string') return data.detail
  if (Array.isArray(data.detail)) return data.detail.join('；')
  const parts = []
  Object.entries(data).forEach(([key, value]) => {
    if (key === 'detail' || key === 'message' || key === 'code') return
    if (Array.isArray(value)) parts.push(`${key}: ${value.join('、')}`)
    else if (typeof value === 'string') parts.push(`${key}: ${value}`)
  })
  return parts.length ? parts.join('；') : (error?.message || fallback)
}

function applyProduct(productId) {
  const product = products.value.find(item => item.id === productId)
  if (!product) return
  form.preferred_pop = product.pop_point || null
  form.requested_vcpu = product.cpu_cores ?? null
  form.requested_ram_mb = product.ram_mb ?? null
  form.requested_disk_gb = product.disk_gb ?? null
  form.requested_bandwidth_mbps = product.bandwidth_mbps ?? null
  form.requested_ip_count = product.ip_count ?? null
  form.quoted_price = product.monthly_price != null ? Number(product.monthly_price) : null
  if (product.category === 'vps') {
    form.business_type = 'vps'
  } else {
    const types = (Array.isArray(product.accel_types) ? product.accel_types : String(product.accel_type || '').split(/[,;/]/))
      .map((item) => String(item || '').trim().toLowerCase())
      .filter((item) => item === 'gre' || item === 'wireguard' || item === 'wg')
    form.business_type = (types.length === 1 && (types[0] === 'wireguard' || types[0] === 'wg'))
      ? 'edge_wg'
      : 'edge_gre'
  }
}

async function openDelivery(row) {
  deliveryOrder.value = row
  Object.assign(deliveryForm, {
    vps_id: null, edge_id: null, bandwidth_allocation_id: null,
    ip_ids: [], activate: true, is_trial: false, remark: '',
  })
  Object.assign(deliveryOptions, { vps: [], edge: [], ip_addresses: [], bandwidth_allocations: [] })
  manualDelivery.value = row.manual_delivery || null
  deliveryToken.value = ''
  canQuickProvision.value = false
  deliveryPopName.value = row.preferred_pop_name || ''
  deliveryVisible.value = true
  deliveryLoading.value = true
  try {
    const [result, checklist] = await Promise.all([
      getOrderDeliveryOptions(row.id),
      getOrderManualDelivery(row.id).catch(() => null),
    ])
    Object.assign(deliveryOptions, {
      vps: result.vps || [],
      edge: result.edge || [],
      ip_addresses: result.ip_addresses || [],
      bandwidth_allocations: result.bandwidth_allocations || [],
    })
    canQuickProvision.value = Boolean(result.can_quick_provision)
    deliveryPopName.value = result.preferred_pop_name || result.product_pop_name || row.preferred_pop_name || ''
    if (checklist?.exists) manualDelivery.value = checklist
    if (row.business_type === 'ip' && result.requested_ip_count) {
      deliveryForm.ip_ids = (result.ip_addresses || []).slice(0, result.requested_ip_count).map(item => item.id)
    }
  } catch (error) {
    ElMessage.error(formatApiError(error, '无法加载可交付资源'))
    deliveryVisible.value = false
  } finally {
    deliveryLoading.value = false
  }
}

async function rotateDeliveryToken() {
  if (!deliveryOrder.value) return
  try {
    await ElMessageBox.confirm(
      '新令牌生成后旧令牌立即失效，原始值只显示一次。',
      '生成回传令牌',
      { type: 'warning' },
    )
  } catch { return }
  tokenLoading.value = true
  try {
    const res = await rotateOrderDeliveryToken(deliveryOrder.value.id)
    deliveryToken.value = res?.token || ''
    ElMessage.success('已生成回传令牌，请复制后交给节点或运维')
  } catch (error) {
    ElMessage.error(error?.response?.data?.detail || '生成令牌失败')
  } finally {
    tokenLoading.value = false
  }
}

async function submitDelivery() {
  if (!deliveryOrder.value) return
  if (!isChangeDelivery.value && deliveryOrder.value.business_type === 'vps' && !deliveryForm.vps_id) {
    return ElMessage.warning('请选择待交付的 VPS')
  }
  if (!isChangeDelivery.value && isDeliveryEdge.value && !deliveryForm.edge_id) {
    return ElMessage.warning('请选择待交付的边缘加速资源')
  }
  if (deliveryOrder.value.business_type === 'bandwidth' && !deliveryForm.bandwidth_allocation_id) {
    return ElMessage.warning('请选择带宽分配')
  }
  if (deliveryOrder.value.business_type === 'ip' && !deliveryForm.ip_ids.length) {
    return ElMessage.warning('请选择需要交付的 IP 地址')
  }
  try {
    await ElMessageBox.confirm(
      `确认交付工单 ${deliveryOrder.value.order_no}？将同步工单状态并绑定所选资源。`,
      '提交交付',
      { type: 'warning', confirmButtonText: '确认交付' },
    )
  } catch {
    return
  }
  deliverySaving.value = true
  try {
    const result = await deliverOrderResources(deliveryOrder.value.id, {
      ...deliveryForm,
      vps_id: deliveryForm.vps_id || undefined,
      edge_id: deliveryForm.edge_id || undefined,
      bandwidth_allocation_id: deliveryForm.bandwidth_allocation_id || undefined,
    })
    ElMessage.success(result?.detail || '资源已交付并同步工单')
    deliveryVisible.value = false
    await loadData()
  } catch (error) {
    ElMessage.error(formatApiError(error, '资源交付失败'))
  } finally {
    deliverySaving.value = false
  }
}

async function submitQuickProvision() {
  if (!deliveryOrder.value) return
  try {
    await ElMessageBox.confirm(
      `将按产品节点「${deliveryPopName.value || '产品 POP'}」创建基础资源并绑定工单 ${deliveryOrder.value.order_no}。边缘资源默认先建档、暂不激活。`,
      '按产品节点快速建档',
      { type: 'warning', confirmButtonText: '确认建档' },
    )
  } catch {
    return
  }
  deliverySaving.value = true
  try {
    const result = await deliverOrderResources(deliveryOrder.value.id, {
      quick_bootstrap: true,
      activate: false,
      remark: deliveryForm.remark || '按产品节点快速建档开通',
    })
    ElMessage.success(result?.detail || '已按产品节点完成基础资源建档')
    deliveryVisible.value = false
    await loadData()
  } catch (error) {
    ElMessage.error(formatApiError(error, '快速建档失败'))
  } finally {
    deliverySaving.value = false
  }
}

async function loadData() {
  loading.value = true
  try {
    const params = { page: page.value, page_size: pageSize.value }
    if (searchForm.search) params.search = searchForm.search
    if (searchForm.status) params.status = searchForm.status
    if (searchForm.order_type) params.order_type = searchForm.order_type
    if (searchForm.customer) params.customer = searchForm.customer
    if (searchForm.current_stage) params.current_stage = searchForm.current_stage
    if (dateRange.value?.length === 2) {
      params.start = dateRange.value[0]
      params.end = dateRange.value[1]
    }
    const res = await getOrders(params)
    tableData.value = res?.results || []
    total.value = res?.count || 0
  } finally { loading.value = false }
}

function resetSearch() {
  Object.assign(searchForm, { search: '', status: '', order_type: '', customer: null, current_stage: '' })
  dateRange.value = []
  loadData()
}

async function openLifecycle(row) {
  lifecycleOrder.value = row
  lifecycleVisible.value = true
  lifecycleLoading.value = true
  try {
    lifecycleRecords.value = await getOrderLifecycle(row.id) || []
  } catch {
    lifecycleRecords.value = []
  } finally {
    lifecycleLoading.value = false
  }
}

async function openSnapshot(row) {
  snapshotVisible.value = true
  snapshotLoading.value = true
  orderSnapshot.value = null
  try {
    orderSnapshot.value = await getOrderSnapshot(row.id)
  } finally {
    snapshotLoading.value = false
  }
}

async function handleApprove(row, approved) {
  let reject_reason = ''
  if (!approved) {
    const res = await ElMessageBox.prompt('请填写驳回原因', '驳回工单', { inputPlaceholder: '原因' }).catch(() => null)
    if (!res) return
    reject_reason = res.value
  } else {
    const price = Number(row.quoted_price || 0)
    const nextStep = price === 0 ? '审批后将直接进入开通。' : '审批后将生成专属预付账单，付款成功后自动开通。'
    await ElMessageBox.confirm(`确定批准工单 "${row.order_no}"？${nextStep}`, '审批', { type: 'info' })
  }
  try {
    const result = await approveOrder(row.id, { approved, reject_reason })
    if (!approved) {
      ElMessage.success('已驳回')
    } else if (result?.payment_billing) {
      ElMessage.success(`已批准，等待支付账单 ${result.payment_billing.bill_no}`)
    } else {
      ElMessage.success('已批准，已进入开通流程')
    }
    loadData()
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || e.message || '操作失败')
  }
}

function goBilling(row) {
  router.push({ name: 'BillingList', query: { service_order: row.id } })
}

async function retryProvision(row) {
  await ElMessageBox.confirm(`确认重试工单 "${row.order_no}" 的自动开通？`, '重试开通', { type: 'warning' })
  try {
    const result = await retryOrderProvision(row.id)
    const message = result?.auto_provision_message || '已提交重试'
    if (result?.auto_provision_status === 'success') ElMessage.success(message)
    else ElMessage.warning(message)
    await loadData()
  } catch (error) {
    ElMessage.error(error?.response?.data?.detail || '重试开通失败')
  }
}

async function openDialog(row = null) {
  editRow.value = row
  formTab.value = 'basic'
  dialogVisible.value = true
  dialogLoading.value = !!row
  if (row) {
    let detail = row
    try {
      detail = await getOrder(row.id)
    } catch (error) {
      ElMessage.error(error?.response?.data?.detail || '无法读取工单详情')
      dialogVisible.value = false
      return
    } finally {
      dialogLoading.value = false
    }
    Object.assign(form, {
      order_type: detail.order_type,
      business_type: detail.business_type || 'vps',
      customer: detail.customer,
      contract: detail.contract || null,
      product: detail.product || null,
      preferred_pop: detail.preferred_pop || null,
      handler: detail.handler || null,
      billing_cycle: detail.billing_cycle || 'monthly',
      requested_vcpu: detail.requested_vcpu || null,
      requested_ram_mb: detail.requested_ram_mb || null,
      requested_disk_gb: detail.requested_disk_gb || null,
      requested_os: detail.requested_os || '',
      requested_os_template: detail.requested_os_template || null,
      requested_bandwidth_mbps: detail.requested_bandwidth_mbps || null,
      requested_ip_count: detail.requested_ip_count || null,
      requested_quantity: detail.requested_quantity || 1,
      edge_local_ip: detail.edge_local_ip || null,
      edge_remote_ip: detail.edge_remote_ip || null,
      edge_wg_port: detail.edge_wg_port || 51820,
      edge_wg_keepalive: detail.edge_wg_keepalive || 25,
      edge_wg_allowed_ips: detail.edge_wg_allowed_ips || '',
      edge_wg_client_pubkey: detail.edge_wg_client_pubkey || '',
      edge_socks5_port: detail.edge_socks5_port || 1080,
      edge_socks5_user: detail.edge_socks5_user || '',
      edge_socks5_pass: '',
      requirements: detail.requirements || '',
      service_start: detail.service_start || null,
      service_end: detail.service_end || null,
      quoted_price: detail.quoted_price ? parseFloat(detail.quoted_price) : null,
      reject_reason: '',
      remark: detail.remark || ''
    })
    requestSnapshot.value = detail.request_snapshot || null
  } else {
    dialogLoading.value = false
    Object.assign(form, {
      order_type: 'new', business_type: 'vps', customer: null, contract: null, handler: null,
      product: null, preferred_pop: null, billing_cycle: 'monthly', requested_vcpu: null,
      requested_ram_mb: null, requested_disk_gb: null, requested_os: '', requested_os_template: null, requested_bandwidth_mbps: null,
      requested_ip_count: null, requested_quantity: 1,
      edge_local_ip: null, edge_remote_ip: null, edge_wg_port: 51820, edge_wg_keepalive: 25,
      edge_wg_allowed_ips: '', edge_wg_client_pubkey: '', edge_socks5_port: 1080,
      edge_socks5_user: '', edge_socks5_pass: '',
      requirements: '', service_start: null, service_end: null,
      quoted_price: null, reject_reason: '', remark: ''
    })
    requestSnapshot.value = null
  }
}

const isEdge = () => ['edge_gre', 'edge_wg', 'edge_socks5'].includes(form.business_type)

async function handleSubmit(row) {
  await ElMessageBox.confirm(
    `确定提交工单 "${row.order_no}"？提交后进入待审批；审批通过后，零金额工单会尝试自动开通，有报价则先生成专属预付账单。`,
    '提交工单', { type: 'info' }
  )
  try {
    await submitOrder(row.id)
    await loadData()
    ElMessage.success('工单已提交，待审批')
  } catch (e) {
    ElMessage.error('提交失败：' + formatApiError(e, '提交失败'))
  }
}

async function handleSave() {
  const valid = await formRef.value.validate().catch(() => false)
  if (!valid) return
  saving.value = true
  try {
    let payload = Object.fromEntries(
      Object.entries(form).filter(([, v]) => v !== null && v !== '' && v !== undefined)
    )
    if (formLocked.value) {
      const allowed = ['handler', 'remark', 'service_start', 'service_end', 'preferred_pop']
      payload = Object.fromEntries(Object.entries(payload).filter(([key]) => allowed.includes(key)))
    }
    if (editRow.value) {
      await updateOrder(editRow.value.id, payload)
      ElMessage.success('已更新')
    } else {
      await createOrder(payload)
      ElMessage.success('工单创建成功')
    }
    dialogVisible.value = false
    loadData()
  } catch (e) {
    ElMessage.error(formatApiError(e, '保存失败'))
  } finally { saving.value = false }
}

async function handleDelete(row) {
  try {
    await ElMessageBox.confirm(`确定删除工单 "${row.order_no}"？`, '警告', { type: 'warning' })
  } catch {
    return
  }
  try {
    await deleteOrder(row.id)
    ElMessage.success('已删除')
    loadData()
  } catch (error) {
    if (error?.response?.status === 404) {
      ElMessage.success('工单已不存在，列表已刷新')
      loadData()
      return
    }
    ElMessage.error(formatApiError(error, '删除失败'))
  }
}

function onOsTemplateChange(id) {
  const item = osTemplateOptions.value.find((row) => row.id === id)
  form.requested_os = item ? (item.label || `${item.name} ${item.version}`.trim()) : ''
}

onMounted(async () => {
  loadData()
  if (isChannelAccount.value) {
    const [cRes, catalogRes] = await Promise.allSettled([
      getCustomers({ page_size: 200 }),
      getOrderCatalog(),
    ])
    customers.value = cRes.status === 'fulfilled' ? cRes.value?.results || [] : []
    const catalog = catalogRes.status === 'fulfilled' ? catalogRes.value || {} : {}
    products.value = catalog.products || []
    pops.value = catalog.pops || []
    osTemplateOptions.value = catalog.os_templates || []
    return
  }
  const [cRes, productRes, popRes, usersRes, osRes] = await Promise.allSettled([
    getCustomers({ page_size: 200 }),
    getProducts({ page_size: 200, status: 'online' }),
    getPopPoints({ page_size: 500 }),
    getUsers({ page_size: 500, is_active: true }),
    getProductOsTemplates(),
  ])
  customers.value = cRes.status === 'fulfilled' ? cRes.value?.results || [] : []
  products.value = productRes.status === 'fulfilled' ? productRes.value?.results || [] : []
  pops.value = popRes.status === 'fulfilled' ? popRes.value?.results || [] : []
  handlers.value = usersRes?.status === 'fulfilled' ? usersRes.value?.results || [] : []
  osTemplateOptions.value = osRes.status === 'fulfilled' ? (osRes.value?.results || osRes.value || []) : []
})
</script>

<style scoped>
.page-container { min-height: 100%; }
.page-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 16px; }
.page-title { font-size: 18px; font-weight: 700; color: #1a1a2e; margin: 0; }
.search-card :deep(.el-card__body) { padding: 16px 16px 0; }
.pagination { margin-top: 16px; justify-content: flex-end; }
.lc-title { margin-bottom: 4px; }
.lc-meta { color: #909399; font-size: 12px; margin-bottom: 4px; }
.lc-detail { color: #606266; font-size: 13px; margin-bottom: 6px; }
.snap-pre { font-size: 12px; white-space: pre-wrap; word-break: break-all; max-height: 240px; overflow: auto; margin: 0; }
.snapshot-card { margin-bottom: 12px; }
.muted { color: #909399; }
.fx-lock { margin-top: 2px; font-size: 11px; color: #909399; }
.request-summary { line-height: 1.45; color: #303133; }
.delivery-summary { display: flex; align-items: center; gap: 6px; margin-top: 4px; color: #909399; font-size: 12px; flex-wrap: wrap; }
.manual-delivery { margin: 0 0 16px; padding: 12px; border: 1px solid #ebeef5; border-radius: 6px; }
.manual-delivery-head { display: flex; align-items: center; gap: 8px; margin-bottom: 10px; color: #606266; font-size: 13px; }
.form-alert { margin-bottom: 16px; }
.request-snapshot { margin-bottom: 12px; }
:deep(.el-tabs__content) { min-height: 330px; }
</style>
