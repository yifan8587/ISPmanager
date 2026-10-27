<template>
  <div class="page-container">
    <div class="page-header">
      <h2 class="page-title">
        账单管理
        <el-tag v-if="settlementCurrency" size="small" type="info" effect="plain">默认结算：{{ settlementCurrency }}</el-tag>
      </h2>
      <div>
        <el-button type="success" :icon="Document" @click="openGenDialog()">按月生成账单</el-button>
        <el-button type="primary" :icon="Plus" @click="openDialog()">新建账单</el-button>
      </div>
    </div>
    <el-alert
      v-if="searchForm.service_order"
      title="当前仅显示所选服务工单的专属预付账单"
      type="info"
      show-icon
      closable
      @close="clearOrderFilter"
    />

    <el-card shadow="never" class="search-card">
      <el-form :model="searchForm" inline>
        <el-form-item label="状态">
          <el-select v-model="searchForm.status" clearable placeholder="全部" style="width:110px">
            <el-option v-for="s in statusOptions" :key="s.value" :label="s.label" :value="s.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="账期">
          <el-input v-model="searchForm.bill_month" placeholder="如 2024-01" clearable style="width:130px" />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :icon="Search" @click="loadData">搜索</el-button>
          <el-button @click="resetSearch">重置</el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <el-card shadow="never" style="margin-top:12px;">
      <el-table :data="tableData" v-loading="loading" stripe border>
        <el-table-column type="expand">
          <template #default="{ row }">
            <div class="bill-items">
              <el-table v-if="row.items && row.items.length" :data="row.items" size="small" border>
                <el-table-column prop="instance_no" label="实例" width="150" />
                <el-table-column prop="description" label="计费项" min-width="180" show-overflow-tooltip />
                <el-table-column label="关联产品" width="160" show-overflow-tooltip>
                  <template #default="{ row: it }">
                    <span v-if="it.product_name">{{ it.product_name }}</span>
                    <span v-else style="color:#c0c4cc">—</span>
                  </template>
                </el-table-column>
                <el-table-column label="计费周期" width="190">
                  <template #default="{ row: it }">{{ it.period_start }} ~ {{ it.period_end }}</template>
                </el-table-column>
                <el-table-column label="天数" width="90" align="center">
                  <template #default="{ row: it }">{{ it.days_used }}/{{ it.days_in_month }}</template>
                </el-table-column>
                <el-table-column label="计费方式" width="90" align="center">
                  <template #default="{ row: it }">
                    <el-tag v-if="it.is_trial" size="small" type="info">测试</el-tag>
                    <el-tag v-else-if="it.is_prorated" size="small" type="warning">按天</el-tag>
                    <el-tag v-else size="small" type="success">整月</el-tag>
                  </template>
                </el-table-column>
                <el-table-column label="基础/超出" width="140" align="right">
                  <template #default="{ row: it }">
                    <div>{{ curSym(it.source_currency) }}{{ Number(it.base_monthly_fee || 0).toFixed(2) }}</div>
                    <div v-if="Number(it.overage_monthly_fee) > 0" class="overage-text">
                      +{{ curSym(it.source_currency) }}{{ Number(it.overage_monthly_fee).toFixed(2) }} 超出
                    </div>
                  </template>
                </el-table-column>
                <el-table-column label="原价" width="120" align="right">
                  <template #default="{ row: it }">{{ curSym(it.source_currency) }}{{ Number(it.amount_original).toFixed(2) }}</template>
                </el-table-column>
                <el-table-column label="结算金额" width="120" align="right">
                  <template #default="{ row: it }">
                    <span :class="{ 'text-muted': it.is_trial }">{{ curSym(it.currency) }}{{ Number(it.amount).toFixed(2) }}</span>
                  </template>
                </el-table-column>
                <el-table-column label="操作" width="80" align="center">
                  <template #default="{ row: it }">
                    <el-button link type="primary" size="small" @click="openItemDetail(it)">详情</el-button>
                  </template>
                </el-table-column>
              </el-table>
              <el-empty v-else description="该账单无明细（手工账单）" :image-size="48" />
            </div>
          </template>
        </el-table-column>
        <el-table-column prop="bill_no" label="账单编号" width="160" />
        <el-table-column prop="customer_name" label="客户" width="130" />
        <el-table-column prop="bill_month" label="账期" width="100" />
        <el-table-column label="来源" width="80">
          <template #default="{ row }">
            <el-tag v-if="row.billing_kind === 'traffic_overage'" size="small" type="warning">流量超额</el-tag>
            <el-tag v-else size="small" :type="row.auto_generated ? 'success' : 'info'">{{ row.auto_generated ? '系统' : '手工' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="gross_amount" label="优惠前金额" width="120" align="right">
          <template #default="{ row }">{{ curSym(row.currency) }}{{ Number(row.gross_amount || row.amount || 0).toFixed(2) }}</template>
        </el-table-column>
        <el-table-column label="推广优惠" width="150" align="right">
          <template #default="{ row }">
            <template v-if="Number(row.promotion_discount_amount || 0) > 0">
              <div class="promotion-discount">-{{ curSym(row.currency) }}{{ Number(row.promotion_discount_amount).toFixed(2) }}</div>
              <div class="promotion-code">{{ row.promotion_code }}</div>
            </template>
            <span v-else class="text-muted">-</span>
          </template>
        </el-table-column>
        <el-table-column prop="amount" label="优惠后应付" width="120" align="right">
          <template #default="{ row }">{{ curSym(row.currency) }}{{ Number(row.amount || 0).toFixed(2) }}</template>
        </el-table-column>
        <el-table-column prop="paid_amount" label="已付金额" width="120" align="right">
          <template #default="{ row }">
            <span :class="row.paid_amount >= row.amount ? 'text-success' : ''">{{ curSym(row.currency) }}{{ Number(row.paid_amount || 0).toFixed(2) }}</span>
            <div v-if="Number(row.refunded_amount || 0) > 0" class="refunded-text">
              已退 {{ curSym(row.currency) }}{{ Number(row.refunded_amount).toFixed(2) }}
            </div>
          </template>
        </el-table-column>
        <el-table-column prop="status" label="状态" width="90">
          <template #default="{ row }">
            <el-tag :type="statusMap[row.status]?.type" size="small">{{ statusMap[row.status]?.label }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="业务开通" min-width="145">
          <template #default="{ row }">
            <el-tooltip
              v-if="row.service_order_info"
              :content="row.service_order_info.auto_provision_message || ''"
              placement="top"
            >
              <el-tag
                size="small"
                :type="provisionStatusMap[row.service_order_info.auto_provision_status]?.type || 'info'"
              >
                {{ row.service_order_info.auto_provision_status_label }}
              </el-tag>
            </el-tooltip>
            <span v-else class="text-muted">-</span>
          </template>
        </el-table-column>
        <el-table-column prop="due_date" label="到期日" width="110" />
        <el-table-column prop="payment_method" label="支付方式" width="110">
          <template #default="{ row }">{{ paymentMap[row.payment_method] || row.payment_method || '-' }}</template>
        </el-table-column>
        <el-table-column label="操作" width="360" fixed="right">
          <template #default="{ row }">
            <el-button
              v-if="row.status === 'unpaid' || row.status === 'overdue'"
              link
              type="warning"
              size="small"
              @click="openOnlinePayDialog(row)"
            >在线支付</el-button>
            <el-button link type="success" size="small" v-if="row.status === 'unpaid' || row.status === 'overdue'" @click="openPayDialog(row)">登记收款</el-button>
            <el-button
              v-if="row.service_order_info?.can_request_refund"
              link
              type="warning"
              size="small"
              @click="handleRequestRefund(row)"
            >退款</el-button>
            <el-button
              v-if="row.status === 'refunding'"
              link
              type="warning"
              size="small"
              @click="handleCompleteRefund(row)"
            >确认退款</el-button>
            <el-button link type="primary" size="small" @click="openDialog(row)">编辑</el-button>
            <el-button link type="danger" size="small" @click="handleDelete(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-pagination v-model:current-page="page" v-model:page-size="pageSize" :total="total"
        layout="total, prev, pager, next" class="pagination" @change="loadData" />
    </el-card>

    <!-- 账单对话框 -->
    <el-dialog v-model="dialogVisible" :title="editRow ? '编辑账单' : '新建账单'" width="500px" destroy-on-close>
      <el-form ref="formRef" :model="form" :rules="formRules" label-width="90px">
        <el-form-item label="关联客户" prop="customer">
          <el-select v-model="form.customer" filterable placeholder="选择客户" style="width:100%">
            <el-option v-for="c in customers" :key="c.id" :label="c.name" :value="c.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="账期" prop="bill_month">
          <el-date-picker v-model="form.bill_month" type="month" value-format="YYYY-MM" style="width:100%" />
        </el-form-item>
        <el-form-item label="优惠前金额" prop="amount">
          <el-input-number v-model="form.amount" :min="0" :precision="2" controls-position="right" style="width:100%" />
        </el-form-item>
        <el-alert
          title="若客户已通过推广码绑定渠道，保存时将按该客户注册时固化的优惠比例自动计算应付金额。"
          type="info"
          :closable="false"
          show-icon
        />
        <el-form-item label="币种" prop="currency">
          <el-select v-model="form.currency" style="width:100%">
            <el-option v-for="c in currencyChoices" :key="c.value" :label="c.label" :value="c.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="到期日" prop="due_date">
          <el-date-picker v-model="form.due_date" type="date" value-format="YYYY-MM-DD" style="width:100%" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="handleSave">确认</el-button>
      </template>
    </el-dialog>

    <!-- 按月生成账单对话框 -->
    <el-dialog v-model="genDialogVisible" title="按月生成账单" width="1120px" destroy-on-close>
      <el-form :model="genForm" inline>
        <el-form-item label="客户">
          <el-select v-model="genForm.customer" filterable placeholder="选择客户" style="width:220px">
            <el-option v-for="c in customers" :key="c.id" :label="c.name" :value="c.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="账期">
          <el-date-picker v-model="genForm.bill_month" type="month" value-format="YYYY-MM" placeholder="选择账期月份" style="width:150px" />
        </el-form-item>
        <el-form-item label="结算币种">
          <el-select v-model="genForm.currency" style="width:160px">
            <el-option v-for="c in currencyChoices" :key="c.value" :label="c.label" :value="c.value" />
          </el-select>
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :loading="previewing" @click="doPreview">计算预览</el-button>
        </el-form-item>
      </el-form>

      <template v-if="previewData">
        <el-descriptions :column="4" border size="small" style="margin-bottom:12px">
          <el-descriptions-item label="账期">{{ previewData.bill_month }}</el-descriptions-item>
          <el-descriptions-item label="计费项数">{{ previewData.item_count }}</el-descriptions-item>
          <el-descriptions-item label="优惠前金额">
            <span>{{ previewData.currency_symbol }}{{ Number(previewData.gross_total || previewData.billable_total).toFixed(2) }}</span>
          </el-descriptions-item>
          <el-descriptions-item label="推广优惠">
            <span :class="{ 'promotion-discount': Number(previewData.promotion_discount_amount || 0) > 0 }">
              -{{ previewData.currency_symbol }}{{ Number(previewData.promotion_discount_amount || 0).toFixed(2) }}
              <small v-if="previewData.promotion_code">（{{ previewData.promotion_code }}）</small>
            </span>
          </el-descriptions-item>
          <el-descriptions-item label="优惠后应付">
            <span class="amount-strong">{{ previewData.currency_symbol }}{{ Number(previewData.billable_total).toFixed(2) }}</span>
          </el-descriptions-item>
          <el-descriptions-item label="测试(未计费)">
            {{ previewData.currency_symbol }}{{ Number(previewData.trial_total).toFixed(2) }}（{{ previewData.trial_count }} 项）
          </el-descriptions-item>
        </el-descriptions>

        <el-table :data="previewData.items" size="small" border max-height="360">
          <el-table-column prop="instance_no" label="实例" width="150" />
          <el-table-column prop="description" label="计费项" min-width="160" show-overflow-tooltip />
          <el-table-column label="关联产品" width="150" show-overflow-tooltip>
            <template #default="{ row }">
              <span v-if="row.product_name">{{ row.product_name }}</span>
              <span v-else style="color:#c0c4cc">—</span>
            </template>
          </el-table-column>
          <el-table-column label="计费周期" width="180">
            <template #default="{ row }">{{ row.period_start }} ~ {{ row.period_end }}</template>
          </el-table-column>
          <el-table-column label="天数" width="90" align="center">
            <template #default="{ row }">{{ row.days_used }}/{{ row.days_in_month }}</template>
          </el-table-column>
          <el-table-column label="方式" width="80" align="center">
            <template #default="{ row }">
              <el-tag v-if="row.is_trial" size="small" type="info">测试</el-tag>
              <el-tag v-else-if="row.is_prorated" size="small" type="warning">按天</el-tag>
              <el-tag v-else size="small" type="success">整月</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="基础/超出" width="140" align="right">
            <template #default="{ row }">
              <div>{{ curSym(row.source_currency) }}{{ Number(row.base_monthly_fee || row.unit_monthly_fee).toFixed(2) }}</div>
              <div v-if="Number(row.overage_monthly_fee) > 0" class="overage-text">
                +{{ curSym(row.source_currency) }}{{ Number(row.overage_monthly_fee).toFixed(2) }} 超出
              </div>
            </template>
          </el-table-column>
          <el-table-column label="整月单价" width="120" align="right">
            <template #default="{ row }">{{ curSym(row.source_currency) }}{{ Number(row.unit_monthly_fee).toFixed(2) }}</template>
          </el-table-column>
          <el-table-column label="结算金额" width="110" align="right">
            <template #default="{ row }">
              <span :class="{ 'text-muted': row.is_trial }">{{ curSym(row.currency) }}{{ Number(row.amount).toFixed(2) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="130" fixed="right">
            <template #default="{ row }">
              <el-button link type="primary" size="small" @click="openItemDetail(row)">详情</el-button>
              <el-button v-if="row.is_trial" link type="success" size="small" @click="convertTrial(row)">转入计费</el-button>
            </template>
          </el-table-column>
        </el-table>
        <el-empty v-if="!previewData.items.length" description="该客户当月无可计费服务" :image-size="60" />
      </template>

      <template #footer>
        <el-button @click="genDialogVisible = false">关闭</el-button>
        <el-button type="primary" :loading="generating" :disabled="!previewData" @click="doGenerate">生成账单</el-button>
      </template>
    </el-dialog>

    <!-- 登记收款对话框 -->
    <el-dialog v-model="payDialogVisible" title="登记收款" width="400px" destroy-on-close>
      <el-form ref="payFormRef" :model="payForm" :rules="payRules" label-width="90px">
        <el-form-item label="收款金额" prop="amount">
          <el-input-number v-model="payForm.amount" :min="0.01" :precision="2" style="width:100%" />
        </el-form-item>
        <el-form-item label="支付方式" prop="payment_method">
          <el-select v-model="payForm.payment_method" style="width:100%">
            <el-option v-for="p in paymentOptions" :key="p.value" :label="p.label" :value="p.value" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="payDialogVisible = false">取消</el-button>
        <el-button type="success" :loading="saving" @click="handlePay">确认收款</el-button>
      </template>
    </el-dialog>

    <!-- 在线支付 -->
    <el-dialog v-model="onlinePayVisible" title="在线支付" width="520px" destroy-on-close>
      <template v-if="onlinePayRow">
        <el-descriptions :column="2" border size="small" class="online-pay-summary">
          <el-descriptions-item label="账单">{{ onlinePayRow.bill_no }}</el-descriptions-item>
          <el-descriptions-item label="待付金额">
            {{ curSym(onlinePayRow.currency) }}{{ Number(onlineOutstanding).toFixed(2) }}
          </el-descriptions-item>
        </el-descriptions>
        <el-form label-width="100px" class="online-pay-form" v-loading="onlineMethodsLoading">
          <el-form-item label="支付金额">
            <el-input-number
              v-model="onlinePayAmount"
              :min="0.01"
              :max="onlineOutstanding"
              :precision="2"
              style="width:100%"
            />
          </el-form-item>
          <el-form-item label="支付渠道">
            <el-radio-group v-model="selectedOnlineChannel" class="channel-list">
              <el-radio
                v-for="channel in onlineMethods"
                :key="channel.id"
                :value="channel.id"
                border
                class="channel-option"
              >
                <span>{{ channel.name }}</span>
                <small>
                  {{ channel.provider_label }}{{ channel.sandbox ? '（沙箱）' : '' }}
                  · 收款 {{ channel.payment_currency }}
                </small>
              </el-radio>
            </el-radio-group>
            <el-empty v-if="!onlineMethodsLoading && !onlineMethods.length" description="当前账单没有可用的在线支付渠道" :image-size="56" />
          </el-form-item>
          <el-alert
            v-if="selectedPaymentChannel && selectedPaymentChannel.payment_currency !== onlinePayRow.currency"
            type="info"
            :closable="false"
            show-icon
            :title="`预计支付 ${curSym(selectedPaymentChannel.payment_currency)}${convertedOnlineAmount}`"
            :description="`汇率：1 ${onlinePayRow.currency} = ${selectedPaymentChannel.exchange_rate} ${selectedPaymentChannel.payment_currency}；实际下单金额以后端换算结果为准。`"
          />
        </el-form>
      </template>
      <template #footer>
        <el-button @click="onlinePayVisible = false">取消</el-button>
        <el-button
          type="primary"
          :disabled="!selectedOnlineChannel || !onlineMethods.length"
          :loading="onlinePayStarting"
          @click="startOnlinePayment"
        >前往支付</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="qrPayVisible" title="微信扫码支付" width="420px" @closed="stopPaymentPolling">
      <div class="qr-pay-content">
        <div class="qr-amount">
          请使用微信扫码支付 {{ curSym(activeOnlinePayment?.currency) }}{{ Number(activeOnlinePayment?.amount || 0).toFixed(2) }}
        </div>
        <el-image v-if="qrCodeImage" :src="qrCodeImage" fit="contain" class="qr-image" />
        <el-skeleton v-else :rows="6" animated />
        <div class="qr-status">正在等待支付结果…</div>
        <el-input :model-value="qrCodeText" readonly type="textarea" :rows="2" />
      </div>
      <template #footer>
        <el-button @click="qrPayVisible = false">关闭</el-button>
        <el-button type="primary" :loading="paymentRefreshing" @click="forceRefreshActivePayment">刷新状态</el-button>
      </template>
    </el-dialog>

    <!-- 计费明细详情对话框 -->
    <el-dialog v-model="detailVisible" title="计费明细" width="720px" destroy-on-close>
      <template v-if="detailRow">
        <el-descriptions :column="2" border size="small">
          <el-descriptions-item label="计费项">{{ detailRow.description }}</el-descriptions-item>
          <el-descriptions-item label="关联产品">{{ detailRow.product_name || '—' }}</el-descriptions-item>
          <el-descriptions-item label="计费依据">{{ detailRow.detail?.basis_label || '—' }}</el-descriptions-item>
          <el-descriptions-item label="计费周期">{{ detailRow.period_start }} ~ {{ detailRow.period_end }}</el-descriptions-item>
          <el-descriptions-item label="计费方式">
            <el-tag v-if="detailRow.is_trial" size="small" type="info">测试（未计费）</el-tag>
            <el-tag v-else-if="detailRow.is_prorated" size="small" type="warning">按天比例</el-tag>
            <el-tag v-else size="small" type="success">整月</el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="使用天数">{{ detailRow.days_used }} / {{ detailRow.days_in_month }} 天</el-descriptions-item>
        </el-descriptions>

        <!-- 规格对比：实际 vs 模版基准 -->
        <template v-if="detailRow.detail?.spec_dims?.length">
          <el-divider content-position="left">规格对比（实际 vs 模版基准）</el-divider>
          <el-table :data="detailRow.detail.spec_dims" size="small" border>
            <el-table-column prop="label" label="资源项" width="120" />
            <el-table-column label="模版基准" align="right">
              <template #default="{ row }">{{ row.base }} {{ row.unit }}</template>
            </el-table-column>
            <el-table-column label="实际分配" align="right">
              <template #default="{ row }">{{ row.actual }} {{ row.unit }}</template>
            </el-table-column>
            <el-table-column label="超出" align="right">
              <template #default="{ row }">
                <span v-if="row.over > 0" class="overage-text">+{{ row.over }} {{ row.unit }}</span>
                <span v-else style="color:#67c23a">0</span>
              </template>
            </el-table-column>
          </el-table>
        </template>

        <!-- 超出阶梯计价逐项 -->
        <template v-if="detailRow.detail?.overage_items?.length">
          <el-divider content-position="left">超出部分阶梯计价</el-divider>
          <el-table :data="detailRow.detail.overage_items" size="small" border>
            <el-table-column prop="label" label="资源项" width="120" />
            <el-table-column prop="upgrade_qty" label="超出单位数" align="right" />
            <el-table-column label="月租增量" align="right">
              <template #default="{ row }">+{{ curSym(detailRow.source_currency) }}{{ Number(row.monthly_delta).toFixed(2) }}</template>
            </el-table-column>
          </el-table>
        </template>

        <!-- 计费公式汇总 -->
        <el-divider content-position="left">计费汇总</el-divider>
        <div class="calc-box">
          <div class="calc-row">
            <span>产品基础月租</span>
            <span>{{ curSym(detailRow.source_currency) }}{{ Number(detailRow.base_monthly_fee).toFixed(2) }}</span>
          </div>
          <div class="calc-row">
            <span>超出部分月租</span>
            <span :class="{ 'overage-text': Number(detailRow.overage_monthly_fee) > 0 }">
              +{{ curSym(detailRow.source_currency) }}{{ Number(detailRow.overage_monthly_fee).toFixed(2) }}
            </span>
          </div>
          <div class="calc-row calc-sub">
            <span>整月单价（基础 + 超出）</span>
            <span>{{ curSym(detailRow.source_currency) }}{{ Number(detailRow.unit_monthly_fee).toFixed(2) }}</span>
          </div>
          <div class="calc-row" v-if="detailRow.is_prorated">
            <span>× 按天比例（{{ detailRow.days_used }}/{{ detailRow.days_in_month }}）</span>
            <span>× {{ detailRow.detail?.ratio || (detailRow.days_used / detailRow.days_in_month).toFixed(4) }}</span>
          </div>
          <div class="calc-row calc-sub">
            <span>原始金额</span>
            <span>{{ curSym(detailRow.source_currency) }}{{ Number(detailRow.amount_original).toFixed(2) }}</span>
          </div>
          <div class="calc-row" v-if="detailRow.source_currency !== detailRow.currency">
            <span>× 折算汇率（{{ detailRow.source_currency }} → {{ detailRow.currency }}）</span>
            <span>× {{ detailRow.exchange_rate }}</span>
          </div>
          <div class="calc-row calc-total">
            <span>结算金额{{ detailRow.is_trial ? '（测试，不计入账单）' : '' }}</span>
            <span>{{ curSym(detailRow.currency) }}{{ Number(detailRow.amount).toFixed(2) }}</span>
          </div>
        </div>
      </template>
      <template #footer>
        <el-button @click="detailVisible = false">关闭</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Search, Document } from '@element-plus/icons-vue'
import QRCode from 'qrcode'
import {
  getBillings, createBilling, updateBilling, payBilling, deleteBilling, previewBilling, generateBilling,
  getBillingOnlinePaymentMethods, getPaymentTransaction, startBillingOnlinePayment,
  reconcilePaymentTransaction, requestBillingRefund, completeBillingRefund,
} from '@/api/customer'
import { getCustomers } from '@/api/customer'
import { getCurrencies } from '@/api/system'
import { convertInstanceToBilling } from '@/api/service'

const router = useRouter()
const route = useRoute()
const loading = ref(false)
const saving = ref(false)
const dialogVisible = ref(false)
const payDialogVisible = ref(false)
const formRef = ref(null)
const payFormRef = ref(null)
const editRow = ref(null)
const payRow = ref(null)
const tableData = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const customers = ref([])

const searchForm = reactive({
  status: '',
  bill_month: '',
  service_order: route.query.service_order || '',
})
const form = reactive({ customer: null, bill_month: '', amount: 0, currency: 'CNY', due_date: '' })
const payForm = reactive({ amount: 0, payment_method: 'bank_transfer' })

// 在线支付
const onlinePayVisible = ref(false)
const onlineMethodsLoading = ref(false)
const onlinePayStarting = ref(false)
const onlinePayRow = ref(null)
const onlineMethods = ref([])
const selectedOnlineChannel = ref(null)
const onlinePayAmount = ref(0)
const activeOnlinePayment = ref(null)
const qrPayVisible = ref(false)
const paymentRefreshing = ref(false)
const qrCodeImage = ref('')
const qrCodeText = ref('')
let paymentPollingTimer = null
const onlineOutstanding = computed(() => {
  if (!onlinePayRow.value) return 0
  return Math.max(0, Number(onlinePayRow.value.amount || 0) - Number(onlinePayRow.value.paid_amount || 0))
})
const selectedPaymentChannel = computed(
  () => onlineMethods.value.find(channel => channel.id === selectedOnlineChannel.value) || null,
)
const convertedOnlineAmount = computed(() => {
  const rate = Number(selectedPaymentChannel.value?.exchange_rate || 1)
  const digits = selectedPaymentChannel.value?.payment_currency === 'JPY' ? 0 : 2
  return (Number(onlinePayAmount.value || 0) * rate).toFixed(digits)
})

// 按月生成
const genDialogVisible = ref(false)
const previewing = ref(false)
const generating = ref(false)
const previewData = ref(null)
const genForm = reactive({ customer: null, bill_month: '', currency: 'CNY' })
const currencyChoices = ref([{ value: 'CNY', label: '人民币 (CNY)' }])
const currencySymbols = ref({ CNY: '¥', USD: '$', CREDIT: '积分' })
const settlementCurrency = ref('')

function curSym(c) { return currencySymbols.value[c] || c || '¥' }

// 计费明细详情
const detailVisible = ref(false)
const detailRow = ref(null)
function openItemDetail(row) {
  router.push({ path: '/customer/billings/item-detail', state: { billingItem: JSON.parse(JSON.stringify(row)) } })
}

const statusOptions = [
  { value: 'unpaid', label: '未付' }, { value: 'paid', label: '已付' },
  { value: 'overdue', label: '逾期' }, { value: 'cancelled', label: '已取消' },
  { value: 'refunding', label: '退款中' }, { value: 'refunded', label: '已退款' },
]
const statusMap = {
  unpaid: { label: '未付', type: 'warning' }, paid: { label: '已付', type: 'success' },
  overdue: { label: '逾期', type: 'danger' }, cancelled: { label: '已取消', type: 'info' },
  refunding: { label: '退款中', type: 'warning' }, refunded: { label: '已退款', type: 'info' },
}
const provisionStatusMap = {
  payment_pending: { type: 'warning' },
  provisioning: { type: 'primary' },
  paid_pending: { type: 'warning' },
  success: { type: 'success' },
  insufficient: { type: 'warning' },
  failed: { type: 'danger' },
  none: { type: 'info' },
}
const paymentOptions = [
  { value: 'bank_transfer', label: '银行转账' }, { value: 'alipay', label: '支付宝' },
  { value: 'wechat', label: '微信支付' }, { value: 'paypal', label: 'PayPal' },
  { value: 'stripe', label: 'Stripe' }, { value: 'airwallex', label: 'Airwallex' },
  { value: 'unionpay', label: '银联在线支付' }, { value: 'cash', label: '现金' },
  { value: 'credit_payment', label: '信用支付' }, { value: 'other', label: '其他' }
]
const paymentMap = {
  bank_transfer: '银行转账', alipay: '支付宝', wechat: '微信支付', paypal: 'PayPal',
  stripe: 'Stripe', airwallex: 'Airwallex', unionpay: '银联在线支付',
  cash: '现金', credit_payment: '信用支付', other: '其他',
}

const formRules = {
  customer: [{ required: true, message: '请选择客户' }],
  bill_month: [{ required: true, message: '请选择账期' }],
  amount: [{ required: true, message: '请输入金额' }],
  due_date: [{ required: true, message: '请选择到期日' }]
}
const payRules = {
  amount: [{ required: true, message: '请输入收款金额' }],
  payment_method: [{ required: true, message: '请选择支付方式' }]
}

async function loadData() {
  loading.value = true
  try {
    const res = await getBillings({ ...searchForm, page: page.value, page_size: pageSize.value })
    tableData.value = res?.results || []
    total.value = res?.count || 0
  } finally { loading.value = false }
}

function clearOrderFilter() {
  searchForm.service_order = ''
  router.replace({ query: { ...route.query, service_order: undefined } })
  loadData()
}

function resetSearch() {
  Object.assign(searchForm, { status: '', bill_month: '', service_order: '' })
  router.replace({ query: { ...route.query, service_order: undefined } })
  loadData()
}

function openDialog(row = null) {
  editRow.value = row
  if (row) Object.assign(form, { customer: row.customer, bill_month: row.bill_month, amount: row.amount, currency: row.currency || 'CNY', due_date: row.due_date })
  else Object.assign(form, { customer: null, bill_month: '', amount: 0, currency: genForm.currency || 'CNY', due_date: '' })
  dialogVisible.value = true
}

function openPayDialog(row) {
  payRow.value = row
  payForm.amount = row.amount - (row.paid_amount || 0)
  payForm.payment_method = 'bank_transfer'
  payDialogVisible.value = true
}

async function openOnlinePayDialog(row) {
  onlinePayRow.value = row
  onlinePayAmount.value = Math.max(0, Number(row.amount || 0) - Number(row.paid_amount || 0))
  selectedOnlineChannel.value = null
  onlineMethods.value = []
  onlinePayVisible.value = true
  onlineMethodsLoading.value = true
  try {
    const res = await getBillingOnlinePaymentMethods(row.id)
    onlineMethods.value = Array.isArray(res) ? res : (res?.results || [])
    selectedOnlineChannel.value = onlineMethods.value[0]?.id || null
  } catch (error) {
    ElMessage.error(error?.response?.data?.detail || '加载在线支付渠道失败')
  } finally {
    onlineMethodsLoading.value = false
  }
}

function submitGatewayForm(checkout) {
  const form = document.createElement('form')
  form.method = checkout.form_method || 'POST'
  form.action = checkout.form_action
  form.style.display = 'none'
  Object.entries(checkout.form_fields || {}).forEach(([name, value]) => {
    const input = document.createElement('input')
    input.type = 'hidden'
    input.name = name
    input.value = String(value)
    form.appendChild(input)
  })
  document.body.appendChild(form)
  form.submit()
}

function loadScript(src) {
  return new Promise((resolve, reject) => {
    const existing = document.querySelector(`script[src="${src}"]`)
    if (existing) {
      if (existing.dataset.loaded === 'true') resolve()
      else {
        existing.addEventListener('load', resolve, { once: true })
        existing.addEventListener('error', reject, { once: true })
      }
      return
    }
    const script = document.createElement('script')
    script.src = src
    script.async = true
    script.addEventListener('load', () => {
      script.dataset.loaded = 'true'
      resolve()
    }, { once: true })
    script.addEventListener('error', reject, { once: true })
    document.head.appendChild(script)
  })
}

async function launchAirwallex(checkout) {
  const payload = checkout.sdk_payload || {}
  try {
    await loadScript('https://checkout.airwallex.com/assets/elements.bundle.min.js')
    const sdk = window.AirwallexComponentsSDK
    if (!sdk?.init) throw new Error('Airwallex SDK 未正确加载')
    const { payments } = await sdk.init({
      env: payload.env,
      enabledElements: ['payments'],
    })
    if (!payments?.redirectToCheckout) throw new Error('Airwallex 收银台不可用')
    await payments.redirectToCheckout({
      env: payload.env,
      mode: 'payment',
      currency: payload.currency,
      country_code: payload.country_code,
      intent_id: payload.intent_id,
      client_secret: payload.client_secret,
      successUrl: payload.success_url,
    })
  } catch (error) {
    ElMessage.error(error?.message || '无法打开 Airwallex 收银台')
  }
}

async function startOnlinePayment() {
  if (!onlinePayRow.value || !selectedOnlineChannel.value) return
  if (onlinePayAmount.value <= 0 || onlinePayAmount.value > onlineOutstanding.value) {
    ElMessage.warning('请输入不超过待付金额的支付金额')
    return
  }
  onlinePayStarting.value = true
  try {
    const res = await startBillingOnlinePayment(onlinePayRow.value.id, {
      channel_id: selectedOnlineChannel.value,
      amount: onlinePayAmount.value,
    })
    const checkout = res?.checkout || {}
    activeOnlinePayment.value = res?.transaction || null
    onlinePayVisible.value = false

    if (checkout.mode === 'redirect' && checkout.url) {
      window.location.assign(checkout.url)
      return
    }
    if (checkout.mode === 'form' && checkout.form_action) {
      submitGatewayForm(checkout)
      return
    }
    if (checkout.mode === 'qr' && checkout.qr_code_text) {
      qrCodeText.value = checkout.qr_code_text
      qrCodeImage.value = await QRCode.toDataURL(checkout.qr_code_text, {
        width: 240,
        margin: 1,
        errorCorrectionLevel: 'M',
      })
      qrPayVisible.value = true
      startPaymentPolling()
      return
    }
    if (checkout.mode === 'sdk' && checkout.sdk === 'airwallex') {
      await launchAirwallex(checkout)
      return
    }
    throw new Error('支付渠道未返回有效的支付指令')
  } catch (error) {
    ElMessage.error(error?.response?.data?.detail || error?.message || '创建在线支付订单失败')
  } finally {
    onlinePayStarting.value = false
  }
}

function stopPaymentPolling() {
  if (paymentPollingTimer) {
    window.clearInterval(paymentPollingTimer)
    paymentPollingTimer = null
  }
}

function startPaymentPolling() {
  stopPaymentPolling()
  paymentPollingTimer = window.setInterval(() => refreshActivePayment(false), 3000)
}

async function forceRefreshActivePayment() {
  paymentRefreshing.value = true
  try {
    await refreshActivePayment(true)
  } finally {
    paymentRefreshing.value = false
  }
}

async function refreshActivePayment(force = false) {
  if (!activeOnlinePayment.value?.transaction_no) return
  try {
    const payment = force === true
      ? await reconcilePaymentTransaction(activeOnlinePayment.value.transaction_no)
      : await getPaymentTransaction(activeOnlinePayment.value.transaction_no)
    activeOnlinePayment.value = payment
    if (payment.status === 'succeeded') {
      stopPaymentPolling()
      qrPayVisible.value = false
      ElMessage.success('支付成功，账单状态已更新')
      await loadData()
    } else if (['failed', 'cancelled', 'expired'].includes(payment.status)) {
      stopPaymentPolling()
      qrPayVisible.value = false
      ElMessage.warning(payment.failure_reason || '支付未完成，请重新发起支付')
      await loadData()
    }
  } catch {
    // 二维码轮询中短暂网络失败不打断用户支付。
  }
}

async function restoreReturnedPayment() {
  const transactionNo = route.query.payment_transaction
  if (!transactionNo) return
  if (route.query.payment_error) ElMessage.warning(String(route.query.payment_error))
  activeOnlinePayment.value = { transaction_no: String(transactionNo) }
  await refreshActivePayment()
}

async function handleSave() {
  const valid = await formRef.value.validate().catch(() => false)
  if (!valid) return
  saving.value = true
  try {
    if (editRow.value) { await updateBilling(editRow.value.id, form); ElMessage.success('已更新') }
    else { await createBilling(form); ElMessage.success('创建成功') }
    dialogVisible.value = false
    loadData()
  } finally { saving.value = false }
}

async function handlePay() {
  const valid = await payFormRef.value.validate().catch(() => false)
  if (!valid) return
  saving.value = true
  try {
    await payBilling(payRow.value.id, payForm)
    ElMessage.success('收款登记成功')
    payDialogVisible.value = false
    loadData()
  } finally { saving.value = false }
}

function openGenDialog() {
  previewData.value = null
  const now = new Date()
  const ym = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`
  Object.assign(genForm, { customer: null, bill_month: ym, currency: genForm.currency || 'CNY' })
  genDialogVisible.value = true
}

async function doPreview() {
  if (!genForm.customer || !genForm.bill_month) {
    ElMessage.warning('请选择客户与账期')
    return
  }
  previewing.value = true
  try {
    previewData.value = await previewBilling({
      customer: genForm.customer, bill_month: genForm.bill_month, currency: genForm.currency,
    })
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '预览失败')
  } finally {
    previewing.value = false
  }
}

async function convertTrial(row) {
  try {
    await ElMessageBox.confirm(`确认将测试服务「${row.instance_no}」转入计费周期？`, '转入计费', { type: 'warning' })
  } catch { return }
  try {
    await convertInstanceToBilling(row.service_instance, {})
    ElMessage.success('已转入计费周期')
    await doPreview()
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '操作失败')
  }
}

async function doGenerate() {
  generating.value = true
  try {
    const res = await generateBilling({
      customer: genForm.customer, bill_month: genForm.bill_month, currency: genForm.currency,
    })
    if (res.updated === false) ElMessage.warning('该账期账单已支付，未覆盖')
    else ElMessage.success('账单已生成/更新')
    genDialogVisible.value = false
    loadData()
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '生成失败')
  } finally {
    generating.value = false
  }
}

async function handleRequestRefund(row) {
  try {
    await ElMessageBox.confirm(
      `确认对账单「${row.bill_no}」发起退款？Stripe / PayPal 将原路退回，其他渠道会标为待人工退。已付金额不会回滚。`,
      '发起退款',
      { type: 'warning', confirmButtonText: '发起退款', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  loading.value = true
  try {
    const res = await requestBillingRefund(row.id)
    const refund = res?.refund || {}
    ElMessage.success(refund.manual ? '已标记待人工退款' : '退款已发起')
    await loadData()
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '退款失败')
  } finally {
    loading.value = false
  }
}

async function handleCompleteRefund(row) {
  try {
    await ElMessageBox.confirm(
      `确认账单「${row.bill_no}」的人工退款已完成？`,
      '确认退款',
      { type: 'warning', confirmButtonText: '确认', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  loading.value = true
  try {
    await completeBillingRefund(row.id)
    ElMessage.success('已确认退款')
    await loadData()
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '确认退款失败')
  } finally {
    loading.value = false
  }
}

async function handleDelete(row) {
  try {
    await ElMessageBox.confirm(
      `确定删除账单「${row.bill_no}」吗？删除后不可恢复。`,
      '删除账单',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' }
    )
  } catch {
    return
  }
  loading.value = true
  try {
    await deleteBilling(row.id)
    ElMessage.success('已删除')
    loadData()
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  loadData()
  const res = await getCustomers({ page_size: 200 })
  customers.value = res?.results || []
  try {
    const cur = await getCurrencies()
    currencyChoices.value = (cur.choices || []).map(c => ({ value: c.value, label: `${c.label} (${c.value})` }))
    const sym = {}
    ;(cur.choices || []).forEach(c => { sym[c.value] = c.symbol })
    if (Object.keys(sym).length) currencySymbols.value = sym
    genForm.currency = cur.default_currency || cur.settlement_currency || 'CNY'
    form.currency = genForm.currency
    settlementCurrency.value = genForm.currency
  } catch { /* 使用默认币种 */ }
  await restoreReturnedPayment()
})

onBeforeUnmount(stopPaymentPolling)
</script>

<style scoped>
.page-container { min-height: 100%; }
.page-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 16px; }
.page-title { font-size: 18px; font-weight: 700; color: #1a1a2e; margin: 0; display: flex; align-items: center; gap: 8px; }
.search-card :deep(.el-card__body) { padding: 16px 16px 0; }
.pagination { margin-top: 16px; justify-content: flex-end; }
.text-success { color: #67C23A; }
.text-muted { color: #909399; text-decoration: line-through; }
.promotion-discount { color: #67c23a; font-weight: 600; }
.promotion-code { margin-top: 2px; color: #909399; font-size: 11px; }
.overage-text { font-size: 11px; color: #e6a23c; }
.refunded-text { margin-top: 2px; font-size: 11px; color: #e6a23c; }
.calc-box { background: #f7f8fa; border-radius: 6px; padding: 12px 16px; }
.calc-row { display: flex; justify-content: space-between; align-items: center; padding: 5px 0; font-size: 13px; color: #606266; }
.calc-row.calc-sub { border-top: 1px dashed #dcdfe6; margin-top: 2px; padding-top: 8px; color: #303133; font-weight: 600; }
.calc-row.calc-total { border-top: 2px solid #409eff; margin-top: 6px; padding-top: 10px; font-size: 16px; font-weight: 700; color: #409eff; }
.bill-items { padding: 8px 16px; }
.amount-strong { color: #e6a23c; font-weight: 700; }
.online-pay-summary { margin-bottom: 16px; }
.online-pay-form { padding: 4px 8px 0 0; }
.channel-list { display: flex; flex-direction: column; gap: 8px; width: 100%; }
.channel-option { display: flex; align-items: center; margin: 0; min-height: 40px; }
.channel-option small { margin-left: 8px; color: #909399; }
.qr-pay-content { display: flex; align-items: center; flex-direction: column; gap: 12px; text-align: center; }
.qr-amount { font-size: 15px; color: #303133; font-weight: 600; }
.qr-image { width: 240px; height: 240px; }
.qr-status { color: #909399; font-size: 13px; }
</style>
