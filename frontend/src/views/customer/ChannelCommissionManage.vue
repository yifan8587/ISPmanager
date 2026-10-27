<template>
  <div class="page-container">
    <div class="page-header">
      <div>
        <h2 class="page-title">渠道佣金与财务</h2>
        <p class="page-subtitle">统一管理佣金计提、渠道结算、资金流水和收款资料。</p>
      </div>
      <el-button v-if="canWriteFinance && activeTab === 'commissions'" type="primary" :disabled="!canCreateSettlement" @click="openSettlementDialog">
        发起结算（{{ selectedCommissions.length }}）
      </el-button>
      <el-button v-if="canWriteFinance && activeTab === 'ledgers'" type="primary" @click="openAdjustmentDialog">人工调整</el-button>
    </div>

    <el-row :gutter="12" class="summary-row" v-loading="summaryLoading">
      <el-col v-if="settlementSummary" :span="24">
        <el-card shadow="never" class="summary-card">
          <div class="currency-title">结算币种汇总（{{ settlementSummary.currency }}）</div>
          <div class="summary-values">
            <div v-if="settlementSummary.balance != null"><span>账户余额</span><strong>{{ money(settlementSummary.balance, settlementSummary.currency) }}</strong></div>
            <div v-if="settlementSummary.pending_amount != null"><span>待结算</span><strong>{{ money(settlementSummary.pending_amount, settlementSummary.currency) }}</strong></div>
            <div v-if="settlementSummary.settling_amount != null"><span>结算中</span><strong>{{ money(settlementSummary.settling_amount, settlementSummary.currency) }}</strong></div>
            <div v-if="settlementSummary.paid_amount != null"><span>已打款</span><strong class="success-text">{{ money(settlementSummary.paid_amount, settlementSummary.currency) }}</strong></div>
            <div v-if="settlementSummary.accrual != null"><span>计提</span><strong>{{ money(settlementSummary.accrual, settlementSummary.currency) }}</strong></div>
            <div v-if="settlementSummary.payout != null"><span>打款流水</span><strong>{{ money(settlementSummary.payout, settlementSummary.currency) }}</strong></div>
          </div>
        </el-card>
      </el-col>
      <el-col v-for="item in currencySummary" :key="item.currency" :xs="24" :xl="12">
        <el-card shadow="never" class="summary-card">
          <div class="currency-title">{{ item.currency }}</div>
          <div class="summary-values">
            <div><span>应付余额</span><strong>{{ money(item.payable_balance, item.currency) }}</strong></div>
            <div><span>待结算</span><strong>{{ money(item.pending_amount, item.currency) }}</strong></div>
            <div><span>结算中</span><strong>{{ money(item.settling_amount, item.currency) }}</strong></div>
            <div><span>已打款</span><strong class="success-text">{{ money(item.paid_amount, item.currency) }}</strong></div>
            <div><span>调整</span><strong :class="amountClass(item.adjustment_amount)">{{ signedMoney(item.adjustment_amount, item.currency) }}</strong></div>
          </div>
        </el-card>
      </el-col>
      <el-col v-if="!currencySummary.length" :span="24">
        <el-card shadow="never" class="summary-card empty-summary">暂无渠道财务数据</el-card>
      </el-col>
    </el-row>

    <el-tabs v-model="activeTab">
      <el-tab-pane label="佣金明细" name="commissions">
        <el-card shadow="never" class="search-card">
          <el-form inline>
            <el-form-item label="渠道"><channel-select v-model="commissionSearch.channel" :channels="channels" /></el-form-item>
            <el-form-item label="状态">
              <el-select v-model="commissionSearch.status" clearable placeholder="全部" style="width:130px">
                <el-option v-for="item in commissionStatuses" :key="item.value" :label="item.label" :value="item.value" />
              </el-select>
            </el-form-item>
            <el-form-item label="币种"><el-input v-model.trim="commissionSearch.currency" clearable placeholder="如 CNY" style="width:110px" /></el-form-item>
            <el-form-item>
              <el-button type="primary" @click="searchCommissions">搜索</el-button>
              <el-button @click="resetCommissionSearch">重置</el-button>
            </el-form-item>
          </el-form>
        </el-card>
        <el-card shadow="never" class="table-card">
          <el-table :data="commissions" v-loading="commissionLoading" border stripe @selection-change="selectedCommissions = $event">
            <el-table-column type="expand">
              <template #default="{ row }">
                <div class="expand-panel">
                  <el-descriptions :column="4" border size="small">
                    <el-descriptions-item label="推荐客户">{{ row.customer_name || '-' }}（{{ row.customer_no || '-' }}）</el-descriptions-item>
                    <el-descriptions-item label="账单优惠前">{{ money(firstValue(row, ['gross_amount', 'billing_gross_amount']), row.currency) }}</el-descriptions-item>
                    <el-descriptions-item label="优惠金额">{{ money(firstValue(row, ['promotion_discount_amount', 'discount_amount']), row.currency) }}</el-descriptions-item>
                    <el-descriptions-item label="优惠后">{{ money(firstValue(row, ['billing_amount', 'net_amount'], row.commission_base_amount), row.currency) }}</el-descriptions-item>
                    <el-descriptions-item label="支付摘要" :span="2">{{ paymentSummary(row) }}</el-descriptions-item>
                    <el-descriptions-item label="推广码">{{ row.promotion_code || '-' }}</el-descriptions-item>
                    <el-descriptions-item label="结算单">{{ row.settlement_no || '-' }}</el-descriptions-item>
                  </el-descriptions>
                  <div class="subsection-title">服务项目</div>
                  <el-table :data="commissionServices(row)" size="small" border>
                    <el-table-column prop="description" label="项目" min-width="180">
                      <template #default="{ row: item }">{{ item.description || item.product_name || item.name || '-' }}</template>
                    </el-table-column>
                    <el-table-column prop="instance_no" label="实例编号" min-width="130" />
                    <el-table-column label="周期" min-width="190">
                      <template #default="{ row: item }">{{ item.period_start || '-' }} ~ {{ item.period_end || '-' }}</template>
                    </el-table-column>
                    <el-table-column label="金额" width="140" align="right">
                      <template #default="{ row: item }">{{ money(item.amount, item.currency || row.currency) }}</template>
                    </el-table-column>
                  </el-table>
                  <el-empty v-if="!commissionServices(row).length" description="暂无服务项目快照" :image-size="48" />
                </div>
              </template>
            </el-table-column>
            <el-table-column v-if="canWriteFinance" type="selection" width="48" :selectable="isCommissionSelectable" />
            <el-table-column prop="bill_no" label="来源账单" width="160" />
            <el-table-column label="推荐客户" min-width="160"><template #default="{ row }">{{ row.customer_name }}<small class="muted"> {{ row.customer_no }}</small></template></el-table-column>
            <el-table-column label="渠道" min-width="160"><template #default="{ row }">{{ row.channel_name }}<small class="muted"> {{ row.channel_no }}</small></template></el-table-column>
            <el-table-column label="提成基数" width="130" align="right"><template #default="{ row }">{{ money(row.commission_base_amount, row.currency) }}</template></el-table-column>
            <el-table-column label="比例" width="85" align="right"><template #default="{ row }">{{ Number(row.commission_rate || 0) }}%</template></el-table-column>
            <el-table-column label="佣金" width="130" align="right"><template #default="{ row }"><strong class="commission-amount">{{ money(row.commission_amount, row.currency) }}</strong></template></el-table-column>
            <el-table-column label="状态" width="100"><template #default="{ row }"><el-tag size="small" :type="commissionStatusType[row.status]">{{ row.status_label || row.status }}</el-tag></template></el-table-column>
            <el-table-column label="产生时间" width="165"><template #default="{ row }">{{ dateTime(row.earned_at) }}</template></el-table-column>
            <el-table-column label="操作" width="75" fixed="right">
              <template #default="{ row }"><el-button v-if="canWriteFinance && row.status === 'pending'" link type="danger" size="small" @click="voidCommission(row)">作废</el-button><span v-else>-</span></template>
            </el-table-column>
          </el-table>
          <page-bar v-model="commissionPage" :total="commissionTotal" @change="loadCommissions" />
        </el-card>
      </el-tab-pane>

      <el-tab-pane label="结算单" name="settlements">
        <el-card shadow="never" class="search-card">
          <el-form inline>
            <el-form-item label="渠道"><channel-select v-model="settlementSearch.channel" :channels="channels" /></el-form-item>
            <el-form-item label="状态">
              <el-select v-model="settlementSearch.status" clearable placeholder="全部" style="width:130px">
                <el-option v-for="item in settlementStatuses" :key="item.value" :label="item.label" :value="item.value" />
              </el-select>
            </el-form-item>
            <el-form-item><el-button type="primary" @click="searchSettlements">搜索</el-button></el-form-item>
          </el-form>
        </el-card>
        <el-card shadow="never" class="table-card">
          <el-table :data="settlements" v-loading="settlementLoading" border stripe>
            <el-table-column prop="settlement_no" label="结算单号" width="190" />
            <el-table-column prop="channel_name" label="渠道" min-width="160" />
            <el-table-column label="结算周期" width="210"><template #default="{ row }">{{ row.period_start || '-' }} ~ {{ row.period_end || '-' }}</template></el-table-column>
            <el-table-column prop="commission_count" label="佣金笔数" width="100" align="center" />
            <el-table-column label="结算金额" width="145" align="right"><template #default="{ row }"><strong>{{ money(row.total_amount, row.currency) }}</strong></template></el-table-column>
            <el-table-column label="状态" width="105"><template #default="{ row }"><el-tag size="small" :type="settlementStatusType[row.status]">{{ row.status_label || row.status }}</el-tag></template></el-table-column>
            <el-table-column prop="payment_method_label" label="打款方式" width="120"><template #default="{ row }">{{ row.payment_method_label || row.payment_method || '-' }}</template></el-table-column>
            <el-table-column prop="payment_reference" label="打款凭证" min-width="140"><template #default="{ row }">{{ row.payment_reference || '-' }}</template></el-table-column>
            <el-table-column label="操作" width="240" fixed="right">
              <template #default="{ row }">
                <el-button link type="primary" size="small" @click="openSettlementDetail(row)">详情</el-button>
                <el-button v-if="canWriteFinance && row.status === 'draft'" link type="primary" size="small" @click="confirmSettlement(row)">确认</el-button>
                <el-button v-if="canWriteFinance && ['draft', 'confirmed'].includes(row.status)" link type="success" size="small" @click="openPaySettlement(row)">登记打款</el-button>
                <el-button v-if="canWriteFinance && !['paid', 'cancelled'].includes(row.status)" link type="danger" size="small" @click="cancelSettlement(row)">取消</el-button>
              </template>
            </el-table-column>
          </el-table>
          <page-bar v-model="settlementPage" :total="settlementTotal" @change="loadSettlements" />
        </el-card>
      </el-tab-pane>

      <el-tab-pane label="财务流水" name="ledgers">
        <el-card shadow="never" class="search-card">
          <el-form inline>
            <el-form-item label="渠道"><channel-select v-model="ledgerSearch.channel" :channels="channels" /></el-form-item>
            <el-form-item label="币种"><el-input v-model.trim="ledgerSearch.currency" clearable placeholder="如 CNY" style="width:105px" /></el-form-item>
            <el-form-item label="事件">
              <el-select v-model="ledgerSearch.event_type" clearable placeholder="全部事件" style="width:145px">
                <el-option v-for="item in ledgerEventTypes" :key="item.value" :label="item.label" :value="item.value" />
              </el-select>
            </el-form-item>
            <el-form-item label="日期"><el-date-picker v-model="ledgerSearch.date" type="date" value-format="YYYY-MM-DD" placeholder="选择日期" /></el-form-item>
            <el-form-item><el-button type="primary" @click="searchLedgers">搜索</el-button><el-button @click="resetLedgerSearch">重置</el-button></el-form-item>
          </el-form>
        </el-card>
        <el-card shadow="never" class="table-card">
          <el-table :data="ledgers" v-loading="ledgerLoading" border stripe>
            <el-table-column prop="created_at" label="发生时间" width="170"><template #default="{ row }">{{ dateTime(row.occurred_at || row.created_at) }}</template></el-table-column>
            <el-table-column prop="channel_name" label="渠道" min-width="160" />
            <el-table-column label="事件" width="130"><template #default="{ row }"><el-tag size="small" effect="plain">{{ eventTypeLabel(row.entry_type) }}</el-tag></template></el-table-column>
            <el-table-column label="变动金额" width="145" align="right"><template #default="{ row }"><strong :class="amountClass(row.delta)">{{ signedMoney(row.delta, row.currency) }}</strong></template></el-table-column>
            <el-table-column label="变动后余额" width="145" align="right"><template #default="{ row }">{{ money(row.balance_after, row.currency) }}</template></el-table-column>
            <el-table-column prop="reference_no" label="关联单据" min-width="145"><template #default="{ row }">{{ row.reference_no || row.settlement_no || row.bill_no || '-' }}</template></el-table-column>
            <el-table-column prop="reason" label="原因 / 摘要" min-width="190"><template #default="{ row }">{{ row.reason || row.description || '-' }}</template></el-table-column>
            <el-table-column prop="payment_reference" label="凭证" min-width="140"><template #default="{ row }">{{ row.payment_reference || row.evidence || '-' }}</template></el-table-column>
            <el-table-column label="操作人" width="120"><template #default="{ row }">{{ userName(row.operator_info || row.created_by_info) }}</template></el-table-column>
          </el-table>
          <page-bar v-model="ledgerPage" :total="ledgerTotal" @change="loadLedgers" />
        </el-card>
      </el-tab-pane>

      <el-tab-pane label="收款资料" name="payout">
        <el-card shadow="never" class="payout-card" v-loading="payoutLoading">
          <el-form label-width="110px">
            <el-form-item label="渠道"><channel-select v-model="payoutChannel" :channels="channels" @change="loadPayoutProfile" /></el-form-item>
          </el-form>
          <template v-if="payoutChannel">
            <el-divider />
            <el-descriptions v-if="payoutProfile && !payoutEditing" :column="2" border>
              <el-descriptions-item label="收款方式">{{ payoutProfile.payment_method_label || paymentMethodLabel(payoutProfile.payment_method) }}</el-descriptions-item>
              <el-descriptions-item label="账户名称">{{ payoutProfile.account_name || '-' }}</el-descriptions-item>
              <el-descriptions-item label="收款账号">{{ payoutProfile.masked_account_number || payoutProfile.account_number_masked || maskAccount(payoutProfile.account_number) }}</el-descriptions-item>
              <el-descriptions-item label="收款人">{{ payoutProfile.recipient_name || '-' }}</el-descriptions-item>
              <el-descriptions-item label="开户银行">{{ payoutProfile.bank_name || '-' }}</el-descriptions-item>
              <el-descriptions-item label="开户支行">{{ payoutProfile.bank_branch || '-' }}</el-descriptions-item>
              <el-descriptions-item label="税号">{{ payoutProfile.tax_id || '-' }}</el-descriptions-item>
              <el-descriptions-item label="状态"><el-tag :type="payoutProfile.is_active ? 'success' : 'info'">{{ payoutProfile.is_active ? '启用' : '停用' }}</el-tag></el-descriptions-item>
            </el-descriptions>
            <el-empty v-else-if="!payoutEditing" description="尚未维护收款资料" />
            <el-form v-else ref="payoutFormRef" :model="payoutForm" :rules="payoutRules" label-width="110px" class="edit-form">
              <el-row :gutter="16">
                <el-col :sm="12" :xs="24"><el-form-item label="收款方式" prop="payment_method"><el-select v-model="payoutForm.payment_method" style="width:100%"><el-option v-for="item in paymentMethods" :key="item.value" :label="item.label" :value="item.value" /></el-select></el-form-item></el-col>
                <el-col :sm="12" :xs="24"><el-form-item label="账户名称" prop="account_name"><el-input v-model.trim="payoutForm.account_name" /></el-form-item></el-col>
                <el-col :sm="12" :xs="24"><el-form-item label="收款账号" :prop="payoutProfile ? '' : 'account_number'"><el-input v-model.trim="payoutForm.account_number" show-password autocomplete="new-password" :placeholder="payoutProfile ? `留空保留 ${payoutMaskedAccount}` : '请输入收款账号'" /></el-form-item></el-col>
                <el-col :sm="12" :xs="24"><el-form-item label="收款人" prop="recipient_name"><el-input v-model.trim="payoutForm.recipient_name" /></el-form-item></el-col>
                <el-col :sm="12" :xs="24"><el-form-item label="开户银行"><el-input v-model.trim="payoutForm.bank_name" /></el-form-item></el-col>
                <el-col :sm="12" :xs="24"><el-form-item label="开户支行"><el-input v-model.trim="payoutForm.bank_branch" /></el-form-item></el-col>
                <el-col :sm="12" :xs="24"><el-form-item label="税号"><el-input v-model.trim="payoutForm.tax_id" /></el-form-item></el-col>
                <el-col :sm="12" :xs="24"><el-form-item label="状态"><el-switch v-model="payoutForm.is_active" active-text="启用" inactive-text="停用" /></el-form-item></el-col>
              </el-row>
            </el-form>
            <div v-if="canWriteFinance" class="form-actions">
              <el-button v-if="!payoutEditing" type="primary" @click="startEditPayout">编辑收款资料</el-button>
              <template v-else><el-button @click="payoutEditing = false">取消</el-button><el-button type="primary" :loading="payoutSaving" @click="savePayoutProfile">保存</el-button></template>
            </div>
          </template>
          <el-empty v-else description="请先选择渠道" />
        </el-card>
      </el-tab-pane>
    </el-tabs>

    <el-dialog v-model="settlementDialogVisible" title="发起渠道佣金结算" width="480px">
      <el-alert :title="settlementPreview" type="info" :closable="false" show-icon />
      <el-form label-width="90px" class="dialog-form"><el-form-item label="结算备注"><el-input v-model="settlementForm.remark" type="textarea" :rows="3" /></el-form-item></el-form>
      <template #footer><el-button @click="settlementDialogVisible = false">取消</el-button><el-button type="primary" :loading="settlementCreating" @click="createSettlement">确认发起</el-button></template>
    </el-dialog>

    <el-dialog v-model="payDialogVisible" title="登记渠道打款" width="460px">
      <el-form label-width="90px">
        <el-form-item label="结算单">{{ activeSettlement?.settlement_no }}</el-form-item>
        <el-form-item label="结算金额">{{ activeSettlement ? money(activeSettlement.total_amount, activeSettlement.currency) : '-' }}</el-form-item>
        <el-form-item label="打款方式" required><el-select v-model="payForm.payment_method" style="width:100%"><el-option v-for="item in paymentMethods" :key="item.value" :label="item.label" :value="item.value" /></el-select></el-form-item>
        <el-form-item label="打款凭证" required><el-input v-model.trim="payForm.payment_reference" placeholder="银行流水号、付款凭证号等" /></el-form-item>
      </el-form>
      <template #footer><el-button @click="payDialogVisible = false">取消</el-button><el-button type="success" :loading="settlementSaving" @click="markSettlementPaid">确认打款</el-button></template>
    </el-dialog>

    <el-dialog v-model="adjustmentDialogVisible" title="人工调整渠道余额" width="500px">
      <el-alert title="正数增加应付余额，负数减少应付余额；操作将记录在财务流水中。" type="warning" :closable="false" show-icon />
      <el-form ref="adjustmentFormRef" :model="adjustmentForm" :rules="adjustmentRules" label-width="90px" class="dialog-form">
        <el-form-item label="渠道" prop="channel"><channel-select v-model="adjustmentForm.channel" :channels="channels" /></el-form-item>
        <el-form-item label="币种" prop="currency"><el-input v-model.trim="adjustmentForm.currency" placeholder="CNY" /></el-form-item>
        <el-form-item label="调整金额" prop="delta"><el-input-number v-model="adjustmentForm.delta" :precision="2" :step="100" controls-position="right" style="width:100%" /></el-form-item>
        <el-form-item label="调整原因" prop="reason"><el-input v-model.trim="adjustmentForm.reason" type="textarea" :rows="3" /></el-form-item>
          <el-form-item label="关联单号" prop="reference"><el-input v-model.trim="adjustmentForm.reference" placeholder="审批单号、调整单号等" /></el-form-item>
          <el-form-item label="凭证依据" prop="evidence"><el-input v-model.trim="adjustmentForm.evidence" placeholder="转账凭证或其他审计依据" /></el-form-item>
      </el-form>
      <template #footer><el-button @click="adjustmentDialogVisible = false">取消</el-button><el-button type="primary" :loading="adjustmentSaving" @click="submitAdjustment">确认调整</el-button></template>
    </el-dialog>

    <el-drawer v-model="settlementDetailVisible" title="结算详情" size="82%">
      <div v-loading="settlementDetailLoading">
        <el-descriptions v-if="settlementDetail" :column="3" border>
          <el-descriptions-item label="结算单">{{ settlementDetail.settlement_no }}</el-descriptions-item>
          <el-descriptions-item label="渠道">{{ settlementDetail.channel_name }}</el-descriptions-item>
          <el-descriptions-item label="结算金额">{{ money(settlementDetail.total_amount, settlementDetail.currency) }}</el-descriptions-item>
          <el-descriptions-item label="收款方式">{{ paymentMethodLabel(settlementPayout.account_type === 'bank' ? 'bank_transfer' : settlementPayout.account_type) }}</el-descriptions-item>
          <el-descriptions-item label="收款户名">{{ settlementPayout.entity_name || '-' }}</el-descriptions-item>
          <el-descriptions-item label="收款账号">{{ settlementPayout.masked_account || '-' }}</el-descriptions-item>
          <el-descriptions-item label="打款方式">{{ settlementDetail.payment_method_label || paymentMethodLabel(settlementDetail.payment_method) }}</el-descriptions-item>
          <el-descriptions-item label="打款凭证">{{ settlementDetail.payment_reference || '-' }}</el-descriptions-item>
          <el-descriptions-item label="备注">{{ settlementDetail.remark || '-' }}</el-descriptions-item>
        </el-descriptions>
        <el-row :gutter="12" class="detail-grid">
          <el-col :md="12" :xs="24">
            <div class="subsection-title">客户小计</div>
            <el-table :data="settlementCustomerSubtotals" size="small" border>
              <el-table-column prop="customer_name" label="客户" min-width="140" /><el-table-column prop="commission_count" label="笔数" width="75" /><el-table-column label="佣金" width="130" align="right"><template #default="{ row }">{{ money(row.commission_amount || row.total_amount, settlementDetail?.currency) }}</template></el-table-column>
            </el-table>
          </el-col>
          <el-col :md="12" :xs="24">
            <div class="subsection-title">服务小计</div>
            <el-table :data="settlementServiceSubtotals" size="small" border>
              <el-table-column prop="service_name" label="服务" min-width="140"><template #default="{ row }">{{ row.service_name || row.service_type_label || row.service_type || '-' }}</template></el-table-column><el-table-column prop="item_count" label="项目数" width="75" /><el-table-column label="佣金" width="130" align="right"><template #default="{ row }">{{ money(row.commission_amount || row.total_amount, settlementDetail?.currency) }}</template></el-table-column>
            </el-table>
          </el-col>
        </el-row>
        <div class="subsection-title">操作时间线</div>
        <el-timeline v-if="settlementTimeline.length" class="settlement-timeline">
          <el-timeline-item v-for="(item, index) in settlementTimeline" :key="item.id || index" :timestamp="dateTime(item.at)" placement="top">
            <strong>{{ timelineEventLabel(item.event) }}</strong>
            <span class="timeline-detail"> · {{ userName(item.operator) }}</span>
          </el-timeline-item>
        </el-timeline>
        <el-empty v-else description="暂无操作时间线" :image-size="48" />
        <div class="subsection-title">佣金明细</div>
        <el-table :data="settlementCommissions" border stripe>
          <el-table-column prop="bill_no" label="来源账单" width="160" /><el-table-column prop="customer_name" label="推荐客户" min-width="160" /><el-table-column prop="promotion_code" label="推广码" width="110" />
          <el-table-column label="提成基数" width="130" align="right"><template #default="{ row }">{{ money(row.commission_base_amount, row.currency) }}</template></el-table-column>
          <el-table-column label="比例" width="90" align="right"><template #default="{ row }">{{ Number(row.commission_rate || 0) }}%</template></el-table-column>
          <el-table-column label="提成金额" width="130" align="right"><template #default="{ row }">{{ money(row.commission_amount, row.currency) }}</template></el-table-column>
          <el-table-column label="产生时间" width="170"><template #default="{ row }">{{ dateTime(row.earned_at) }}</template></el-table-column>
        </el-table>
      </div>
    </el-drawer>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import dayjs from 'dayjs'
import {
  adjustChannelFinanceLedger,
  cancelChannelSettlement,
  confirmChannelSettlement,
  createChannelSettlement,
  getChannelCommissionSummary,
  getChannelCommissions,
  getChannelFinanceLedgers,
  getChannelFinanceLedgerSummary,
  getChannelPayoutProfile,
  getChannelSettlementDetail,
  getChannelSettlementCommissions,
  getChannelSettlements,
  markChannelSettlementPaid,
  updateChannelPayoutProfile,
  voidChannelCommission,
  getCustomers,
} from '@/api/customer'
import ChannelSelect from './components/ChannelSelect.vue'
import PageBar from './components/PageBar.vue'
import { useAuthStore } from '@/stores/auth'

const route = useRoute()
const authStore = useAuthStore()
const isChannelAccount = computed(() =>
  authStore.user?.role === 'channel' || authStore.user?.is_channel_account
)
const canWriteFinance = computed(() => !isChannelAccount.value)
const activeTab = ref('commissions')
const channels = ref([])
const currencySummary = ref([])
const settlementSummary = ref(null)
const summaryLoading = ref(false)
const commissions = ref([])
const settlements = ref([])
const ledgers = ref([])
const selectedCommissions = ref([])
const commissionLoading = ref(false)
const settlementLoading = ref(false)
const ledgerLoading = ref(false)
const commissionPage = ref(1)
const settlementPage = ref(1)
const ledgerPage = ref(1)
const commissionTotal = ref(0)
const settlementTotal = ref(0)
const ledgerTotal = ref(0)
const settlementDialogVisible = ref(false)
const settlementCreating = ref(false)
const payDialogVisible = ref(false)
const settlementSaving = ref(false)
const activeSettlement = ref(null)
const settlementDetailVisible = ref(false)
const settlementDetailLoading = ref(false)
const settlementDetail = ref(null)
const settlementCommissions = ref([])
const adjustmentDialogVisible = ref(false)
const adjustmentSaving = ref(false)
const adjustmentFormRef = ref(null)
const payoutChannel = ref(route.query.channel ? Number(route.query.channel) : '')
const payoutProfile = ref(null)
const payoutLoading = ref(false)
const payoutEditing = ref(false)
const payoutSaving = ref(false)
const payoutFormRef = ref(null)

const commissionSearch = reactive({
  channel: route.query.channel ? Number(route.query.channel) : '',
  status: '',
  currency: '',
})
const settlementSearch = reactive({
  channel: route.query.channel ? Number(route.query.channel) : '',
  status: '',
})
const ledgerSearch = reactive({
  channel: route.query.channel ? Number(route.query.channel) : '',
  currency: '',
  event_type: '',
  date: '',
})
const settlementForm = reactive({ remark: '' })
const payForm = reactive({ payment_method: '', payment_reference: '' })
const adjustmentForm = reactive({
  channel: route.query.channel ? Number(route.query.channel) : '',
  currency: 'CNY',
  delta: 0,
  reason: '',
  reference: '',
  evidence: '',
})
const payoutForm = reactive({
  payment_method: 'bank_transfer',
  account_name: '',
  account_number: '',
  recipient_name: '',
  bank_name: '',
  bank_branch: '',
  tax_id: '',
  is_active: true,
})

const commissionStatuses = [
  { value: 'pending', label: '待结算' },
  { value: 'settling', label: '结算中' },
  { value: 'paid', label: '已结算' },
  { value: 'void', label: '已作废' },
]
const settlementStatuses = [
  { value: 'draft', label: '草稿' },
  { value: 'confirmed', label: '已确认' },
  { value: 'paid', label: '已打款' },
  { value: 'cancelled', label: '已取消' },
]
const ledgerEventTypes = [
  { value: 'accrual', label: '佣金计提' },
  { value: 'void', label: '佣金作废' },
  { value: 'settlement_created', label: '发起结算' },
  { value: 'payout', label: '结算打款' },
  { value: 'settlement_cancelled', label: '取消结算' },
  { value: 'adjustment', label: '人工调整' },
]
const paymentMethods = [
  { value: 'bank_transfer', label: '银行转账' },
  { value: 'alipay', label: '支付宝' },
  { value: 'wechat', label: '微信支付' },
  { value: 'paypal', label: 'PayPal' },
  { value: 'other', label: '其他' },
]
const adjustmentRules = {
  channel: [{ required: true, message: '请选择渠道', trigger: 'change' }],
  currency: [{ required: true, message: '请输入币种', trigger: 'blur' }],
  delta: [{
    validator: (_rule, value, callback) => Number(value) !== 0 ? callback() : callback(new Error('调整金额不能为 0')),
    trigger: 'change',
  }],
  reason: [{ required: true, message: '请填写调整原因', trigger: 'blur' }],
  reference: [{ required: true, message: '请填写关联单号', trigger: 'blur' }],
  evidence: [{ required: true, message: '请填写凭证依据', trigger: 'blur' }],
}
const payoutRules = {
  payment_method: [{ required: true, message: '请选择收款方式', trigger: 'change' }],
  account_name: [{ required: true, message: '请输入账户名称', trigger: 'blur' }],
  account_number: [{ required: true, message: '请输入收款账号', trigger: 'blur' }],
  recipient_name: [{ required: true, message: '请输入收款人', trigger: 'blur' }],
}
const commissionStatusType = { pending: 'warning', settling: 'primary', paid: 'success', void: 'info' }
const settlementStatusType = { draft: 'info', confirmed: 'primary', paid: 'success', cancelled: 'info' }

const canCreateSettlement = computed(() => {
  if (!selectedCommissions.value.length) return false
  const [first] = selectedCommissions.value
  return selectedCommissions.value.every((item) =>
    item.status === 'pending'
    && item.channel === first.channel
    && item.currency === first.currency,
  )
})
const settlementPreview = computed(() => {
  if (!selectedCommissions.value.length) return '请选择待结算佣金'
  const [first] = selectedCommissions.value
  const total = selectedCommissions.value.reduce((sum, item) => sum + Number(item.commission_amount || 0), 0)
  return `${first.channel_name} · ${selectedCommissions.value.length} 笔 · ${money(total, first.currency)}`
})
const payoutMaskedAccount = computed(() => {
  const profile = payoutProfile.value || {}
  return profile.masked_account_number || profile.account_number_masked || maskAccount(profile.account_number)
})
const settlementPayout = computed(() =>
  settlementDetail.value?.payout_snapshot
  || settlementDetail.value?.payout_profile_snapshot
  || settlementDetail.value?.payment_profile_snapshot
  || {},
)
const settlementCustomerSubtotals = computed(() =>
  settlementDetail.value?.customer_subtotals
  || settlementDetail.value?.subtotals_by_customer
  || [],
)
const settlementServiceSubtotals = computed(() =>
  settlementDetail.value?.service_subtotals
  || settlementDetail.value?.subtotals_by_service
  || [],
)
const settlementTimeline = computed(() =>
  settlementDetail.value?.timeline
  || settlementDetail.value?.operation_timeline
  || settlementDetail.value?.operations
  || [],
)

function money(value, currency = 'CNY') {
  return `${currency || 'CNY'} ${Number(value || 0).toFixed(2)}`
}
function signedMoney(value, currency = 'CNY') {
  const amount = Number(value || 0)
  return `${amount > 0 ? '+' : ''}${money(amount, currency)}`
}
function amountClass(value) {
  const amount = Number(value || 0)
  return amount > 0 ? 'positive-amount' : amount < 0 ? 'negative-amount' : ''
}
function dateTime(value) {
  return value ? dayjs(value).format('YYYY-MM-DD HH:mm') : '-'
}
function cleanParams(params) {
  return Object.fromEntries(Object.entries(params).filter(([, value]) => value !== '' && value !== null && value !== undefined))
}
function isCommissionSelectable(row) {
  return row.status === 'pending'
}
function firstValue(source, keys, fallback = 0) {
  for (const key of keys) {
    if (source?.[key] !== undefined && source?.[key] !== null) return source[key]
    if (source?.billing_snapshot?.[key] !== undefined && source?.billing_snapshot?.[key] !== null) {
      return source.billing_snapshot[key]
    }
  }
  return fallback
}
function commissionServices(row) {
  return row.service_items_snapshot || row.service_items || row.billing_items || row.billing_snapshot?.items || []
}
function paymentSummary(row) {
  const payment = row.payment_summary_snapshot || row.payment_summary || row.payment_snapshot || {}
  if (typeof payment === 'string') return payment
  const method = payment.payment_method_label || payment.method_label || payment.payment_method || payment.method
  const amount = payment.paid_amount ?? payment.amount
  const paidAt = payment.paid_at || payment.created_at
  return [
    method,
    amount !== undefined ? money(amount, payment.currency || row.currency) : '',
    paidAt ? dateTime(paidAt) : '',
    payment.transaction_no || payment.reference || '',
  ].filter(Boolean).join(' · ') || '-'
}
function userName(user) {
  if (!user) return '系统'
  if (typeof user === 'string') return user
  return user.full_name || user.username || user.name || '系统'
}
function eventTypeLabel(value) {
  return ledgerEventTypes.find((item) => item.value === value)?.label || value || '-'
}
function paymentMethodLabel(value) {
  return paymentMethods.find((item) => item.value === value)?.label || value || '-'
}
function timelineEventLabel(value) {
  return {
    created: '创建结算单',
    confirmed: '确认结算单',
    paid: '登记打款',
    cancelled: '取消结算单',
  }[value] || value || '状态变更'
}
function maskAccount(value) {
  if (!value) return '-'
  const text = String(value)
  if (text.includes('*')) return text
  if (text.length <= 4) return '*'.repeat(text.length)
  return `${text.slice(0, 2)}${'*'.repeat(Math.min(text.length - 4, 10))}${text.slice(-2)}`
}
function listPayload(data) {
  return Array.isArray(data) ? data : data?.results || []
}

async function loadChannels() {
  const response = await getCustomers({ customer_type: 'channel', page_size: 1000 })
  channels.value = response?.results || []
}
async function loadSummary() {
  summaryLoading.value = true
  try {
    const channel = commissionSearch.channel || ledgerSearch.channel || ''
    const params = cleanParams({ channel })
    const [commissionData, financeData] = await Promise.all([
      getChannelCommissionSummary(params),
      getChannelFinanceLedgerSummary(params).catch(() => null),
    ])
    const summaryMap = new Map()
    const mergeItem = (item) => {
      const currency = item.currency || 'CNY'
      summaryMap.set(currency, { ...summaryMap.get(currency), ...item, currency })
    }
    ;(commissionData?.by_currency || []).forEach(mergeItem)
    ;(financeData?.by_currency || financeData?.results || (Array.isArray(financeData) ? financeData : [])).forEach(mergeItem)
    ;(financeData?.accounts || []).forEach((account) => {
      const current = summaryMap.get(account.currency) || { currency: account.currency }
      summaryMap.set(account.currency, {
        ...current,
        balance: Number(current.balance || 0) + Number(account.balance || 0),
      })
    })
    if (financeData?.currency) mergeItem(financeData)
    currencySummary.value = Array.from(summaryMap.values()).map((item) => ({
      ...item,
      payable_balance: item.payable_balance ?? item.balance ?? (
        Number(item.pending_amount || 0) + Number(item.settling_amount || 0) + Number(item.adjustment_amount || 0)
      ),
      adjustment_amount: item.adjustment_amount ?? item.adjusted_amount ?? item.adjustment ?? 0,
    }))
    settlementSummary.value = {
      ...(commissionData?.settlement_totals || {}),
      ...(financeData?.settlement_totals || {}),
    }
    if (!settlementSummary.value.currency) settlementSummary.value = null
  } finally {
    summaryLoading.value = false
  }
}
async function loadCommissions() {
  commissionLoading.value = true
  selectedCommissions.value = []
  try {
    const data = await getChannelCommissions(cleanParams({
      ...commissionSearch,
      page: commissionPage.value,
      page_size: 20,
    }))
    commissions.value = data?.results || []
    commissionTotal.value = data?.count || 0
  } finally {
    commissionLoading.value = false
  }
}
async function loadSettlements() {
  settlementLoading.value = true
  try {
    const data = await getChannelSettlements(cleanParams({
      ...settlementSearch,
      page: settlementPage.value,
      page_size: 20,
    }))
    settlements.value = data?.results || []
    settlementTotal.value = data?.count || 0
  } finally {
    settlementLoading.value = false
  }
}
async function loadLedgers() {
  ledgerLoading.value = true
  try {
    const data = await getChannelFinanceLedgers(cleanParams({
      channel: ledgerSearch.channel,
      currency: ledgerSearch.currency,
      event_type: ledgerSearch.event_type,
      start_date: ledgerSearch.date,
      end_date: ledgerSearch.date,
      page: ledgerPage.value,
      page_size: 20,
    }))
    ledgers.value = listPayload(data).map((item) => ({
      ...item,
      reference_no: item.snapshot?.reference || item.snapshot?.settlement_no || item.snapshot?.bill_no,
      reason: item.snapshot?.reason,
      evidence: item.snapshot?.evidence,
      payment_reference: item.snapshot?.payment_reference,
    }))
    ledgerTotal.value = data?.count ?? ledgers.value.length
  } finally {
    ledgerLoading.value = false
  }
}
function searchCommissions() {
  commissionPage.value = 1
  loadCommissions()
  loadSummary()
}
function searchSettlements() {
  settlementPage.value = 1
  loadSettlements()
}
function searchLedgers() {
  ledgerPage.value = 1
  loadLedgers()
  loadSummary()
}
function resetCommissionSearch() {
  Object.assign(commissionSearch, { channel: '', status: '', currency: '' })
  commissionPage.value = 1
  loadCommissions()
  loadSummary()
}
function resetLedgerSearch() {
  Object.assign(ledgerSearch, { channel: '', currency: '', event_type: '', date: '' })
  ledgerPage.value = 1
  loadLedgers()
  loadSummary()
}
function openSettlementDialog() {
  if (!canCreateSettlement.value) {
    ElMessage.warning('请选择同一渠道、同一币种的待结算佣金')
    return
  }
  settlementForm.remark = ''
  settlementDialogVisible.value = true
}
async function createSettlement() {
  settlementCreating.value = true
  try {
    await createChannelSettlement({
      commission_ids: selectedCommissions.value.map((item) => item.id),
      remark: settlementForm.remark,
    })
    ElMessage.success('结算单已创建')
    settlementDialogVisible.value = false
    activeTab.value = 'settlements'
    await Promise.all([loadCommissions(), loadSettlements(), loadSummary()])
  } catch (error) {
    ElMessage.error(error?.response?.data?.detail || '创建结算单失败')
  } finally {
    settlementCreating.value = false
  }
}
async function voidCommission(row) {
  try {
    const { value } = await ElMessageBox.prompt('请填写作废原因', `作废佣金 ${row.bill_no}`, {
      inputPattern: /.+/,
      inputErrorMessage: '作废原因不能为空',
    })
    await voidChannelCommission(row.id, { reason: value })
    ElMessage.success('佣金已作废')
    await Promise.all([loadCommissions(), loadSummary()])
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') ElMessage.error(error?.response?.data?.detail || '作废失败')
  }
}
async function confirmSettlement(row) {
  try {
    await ElMessageBox.confirm(`确认结算单 ${row.settlement_no}？`, '确认结算', { type: 'warning' })
    await confirmChannelSettlement(row.id)
    ElMessage.success('结算单已确认')
    loadSettlements()
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') ElMessage.error(error?.response?.data?.detail || '确认失败')
  }
}
function openPaySettlement(row) {
  activeSettlement.value = row
  payForm.payment_method = row.payment_method || 'bank_transfer'
  payForm.payment_reference = row.payment_reference || ''
  payDialogVisible.value = true
}
async function markSettlementPaid() {
  if (!activeSettlement.value) return
  if (!payForm.payment_method || !payForm.payment_reference) {
    ElMessage.warning('请完整填写打款方式和打款凭证')
    return
  }
  settlementSaving.value = true
  try {
    await markChannelSettlementPaid(activeSettlement.value.id, payForm)
    ElMessage.success('已登记渠道打款')
    payDialogVisible.value = false
    await Promise.all([loadCommissions(), loadSettlements(), loadSummary()])
  } catch (error) {
    ElMessage.error(error?.response?.data?.detail || '登记打款失败')
  } finally {
    settlementSaving.value = false
  }
}
async function cancelSettlement(row) {
  try {
    const { value } = await ElMessageBox.prompt('取消原因（可选）', `取消结算单 ${row.settlement_no}`, {
      inputValue: '',
    })
    await cancelChannelSettlement(row.id, { reason: value || '' })
    ElMessage.success('结算单已取消，佣金已退回待结算')
    await Promise.all([loadCommissions(), loadSettlements(), loadSummary()])
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') ElMessage.error(error?.response?.data?.detail || '取消失败')
  }
}
async function openSettlementDetail(row) {
  settlementDetailVisible.value = true
  settlementDetailLoading.value = true
  settlementDetail.value = row
  settlementCommissions.value = []
  try {
    const data = await getChannelSettlementDetail(row.id)
    settlementDetail.value = { ...row, ...data }
    settlementCommissions.value = data?.commissions?.results || data?.commissions || data?.commission_items || []
  } catch (error) {
    // 兼容后端 detail 接口尚未上线时的旧佣金明细接口。
    const data = await getChannelSettlementCommissions(row.id, { page_size: 200 })
    settlementCommissions.value = listPayload(data)
    if (error?.response?.status !== 404) {
      ElMessage.warning('结算扩展详情暂不可用，已展示佣金明细')
    }
  } finally {
    settlementDetailLoading.value = false
  }
}

function openAdjustmentDialog() {
  Object.assign(adjustmentForm, {
    channel: ledgerSearch.channel || commissionSearch.channel || '',
    currency: ledgerSearch.currency || 'CNY',
    delta: 0,
    reason: '',
    reference: '',
    evidence: '',
  })
  adjustmentDialogVisible.value = true
}
async function submitAdjustment() {
  const valid = await adjustmentFormRef.value?.validate().catch(() => false)
  if (!valid) return
  adjustmentSaving.value = true
  try {
    await adjustChannelFinanceLedger({
      ...adjustmentForm,
      idempotency_key: `ui-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`,
    })
    ElMessage.success('渠道余额调整已记录')
    adjustmentDialogVisible.value = false
    await Promise.all([loadLedgers(), loadSummary()])
  } catch (error) {
    ElMessage.error(error?.response?.data?.detail || '人工调整失败')
  } finally {
    adjustmentSaving.value = false
  }
}

async function loadPayoutProfile() {
  payoutEditing.value = false
  payoutProfile.value = null
  if (!payoutChannel.value) return
  payoutLoading.value = true
  try {
    payoutProfile.value = await getChannelPayoutProfile(payoutChannel.value)
  } catch (error) {
    if (error?.response?.status !== 404) {
      ElMessage.error(error?.response?.data?.detail || '收款资料加载失败')
    }
  } finally {
    payoutLoading.value = false
  }
}
function startEditPayout() {
  const profile = payoutProfile.value || {}
  Object.assign(payoutForm, {
    payment_method: profile.payment_method || 'bank_transfer',
    account_name: profile.account_name || '',
    account_number: '',
    recipient_name: profile.recipient_name || profile.account_name || '',
    bank_name: profile.bank_name || '',
    bank_branch: profile.bank_branch || '',
    tax_id: profile.tax_id || '',
    is_active: profile.is_active ?? true,
  })
  payoutEditing.value = true
}
async function savePayoutProfile() {
  const valid = await payoutFormRef.value?.validate().catch(() => false)
  if (!valid) return
  payoutSaving.value = true
  try {
    const payload = { ...payoutForm }
    if (!payload.account_number) delete payload.account_number
    payoutProfile.value = await updateChannelPayoutProfile(payoutChannel.value, payload)
    payoutEditing.value = false
    ElMessage.success('收款资料已保存')
  } catch (error) {
    ElMessage.error(error?.response?.data?.detail || '收款资料保存失败')
  } finally {
    payoutSaving.value = false
  }
}

watch(activeTab, (tab) => {
  if (tab === 'settlements') loadSettlements()
  if (tab === 'ledgers') loadLedgers()
  if (tab === 'payout' && payoutChannel.value) loadPayoutProfile()
})
onMounted(async () => {
  await Promise.all([loadChannels(), loadSummary(), loadCommissions()])
})
</script>

<style scoped>
.page-container { min-height: 100%; }
.page-header { display:flex; justify-content:space-between; align-items:flex-start; gap:16px; margin-bottom:16px; }
.page-title { margin:0; color:#1a1a2e; font-size:18px; font-weight:700; }
.page-subtitle { margin:6px 0 0; color:#909399; font-size:13px; }
.summary-row { margin-bottom:16px; }
.summary-card { min-height:112px; }
.currency-title { margin-bottom:12px; color:#303133; font-size:14px; font-weight:700; }
.summary-values { display:grid; grid-template-columns:repeat(5, minmax(110px, 1fr)); gap:16px; }
.summary-values div { display:flex; min-width:0; flex-direction:column; gap:4px; }
.summary-values span, .muted { color:#909399; font-size:12px; }
.summary-values strong { color:#409eff; font-size:15px; }
.empty-summary { display:flex; align-items:center; color:#909399; }
.search-card :deep(.el-card__body) { padding:16px 16px 0; }
.table-card { margin-top:12px; }
.pagination { margin-top:16px; justify-content:flex-end; }
.commission-amount { color:#e6a23c; }
.success-text, .positive-amount { color:#67c23a !important; }
.negative-amount { color:#f56c6c !important; }
.expand-panel { padding:14px 48px 18px; background:#fafcff; }
.subsection-title { margin:18px 0 10px; color:#303133; font-size:14px; font-weight:700; }
.dialog-form { margin-top:18px; }
.payout-card { max-width:980px; }
.edit-form { max-width:860px; }
.form-actions { margin-top:18px; text-align:right; }
.detail-grid { margin-top:4px; }
.settlement-timeline { padding:4px 12px; }
.timeline-detail { margin-left:10px; color:#606266; font-size:13px; }
@media (max-width: 768px) {
  .page-header { align-items:stretch; flex-direction:column; }
  .summary-values { grid-template-columns:repeat(2, 1fr); }
  .expand-panel { padding:10px; }
}
</style>
