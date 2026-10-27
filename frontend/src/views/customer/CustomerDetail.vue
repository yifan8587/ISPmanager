<template>
  <div class="page-container">
    <!-- 页头 -->
    <div class="page-header">
      <div class="header-left">
        <el-button :icon="ArrowLeft" @click="$router.back()">返回</el-button>
        <h2 class="page-title">{{ customer?.name || '客户详情' }}</h2>
        <el-tag v-if="customer" size="small">{{ getTypeLabel(customer.customer_type) }}</el-tag>
      </div>
      <el-button
        v-if="customer && !customer.account && !customer.account_username"
        @click="openPortalDialog"
      >开通账号</el-button>
      <el-button type="primary" @click="openEditDialog">编辑信息</el-button>
    </div>

    <!-- 概览统计 -->
    <el-row :gutter="12" class="stat-row" v-if="customer">
      <el-col :xs="12" :sm="6">
        <div class="stat-card">
          <span class="stat-num">{{ serviceStats.vpsCount }}</span>
          <span class="stat-lbl">VPS</span>
        </div>
      </el-col>
      <el-col :xs="12" :sm="6">
        <div class="stat-card">
          <span class="stat-num">{{ serviceStats.edgeCount }}</span>
          <span class="stat-lbl">边缘加速</span>
        </div>
      </el-col>
      <el-col :xs="12" :sm="6">
        <div class="stat-card">
          <span class="stat-num">{{ serviceStats.totalBandwidth }}</span>
          <span class="stat-lbl">总带宽 (Mbps)</span>
        </div>
      </el-col>
      <el-col :xs="12" :sm="6">
        <div class="stat-card">
          <span class="stat-num" :class="customer.balance < 0 ? 'text-danger' : 'text-success'">
            ¥{{ Number(customer.balance || 0).toFixed(0) }}
          </span>
          <span class="stat-lbl">账户余额</span>
        </div>
      </el-col>
    </el-row>

    <div v-loading="loading" class="detail-sections">
      <!-- 基础信息 -->
      <el-card shadow="never" class="section-card">
        <template #header>
          <span class="card-title"><el-icon><User /></el-icon> 基础信息</span>
        </template>
        <el-descriptions v-if="customer" :column="3" border size="small">
          <el-descriptions-item label="客户编号">{{ customer.customer_no }}</el-descriptions-item>
          <el-descriptions-item label="客户名称">{{ customer.name }}</el-descriptions-item>
          <el-descriptions-item label="客户类型">
            <el-tag size="small">{{ getTypeLabel(customer.customer_type) }}</el-tag>
          </el-descriptions-item>
          <template v-if="customer.customer_type === 'channel' && customer.channel_profile">
            <el-descriptions-item label="推广优惠码">
              <el-tag type="success" size="small">{{ customer.channel_profile.promotion_code }}</el-tag>
              <el-tag
                size="small"
                :type="customer.channel_profile.is_active ? 'primary' : 'info'"
                style="margin-left:6px"
              >
                {{ customer.channel_profile.is_active ? '启用' : '停用' }}
              </el-tag>
            </el-descriptions-item>
            <el-descriptions-item label="推广优惠 / 提成">
              {{ Number(customer.channel_profile.promotion_discount_rate || 0) }}%
              / {{ Number(customer.channel_profile.commission_rate || 0) }}%
              <el-button link type="primary" size="small" style="margin-left:6px" @click="goChannelSettlement">查看结算</el-button>
            </el-descriptions-item>
            <el-descriptions-item label="注册授信">
              <template v-if="customer.channel_profile.reputation_grant_mode && customer.channel_profile.reputation_grant_mode !== 'none'">
                {{ reputationModeLabel(customer.channel_profile.reputation_grant_mode) }}
                · {{ Number(customer.channel_profile.reputation_grant_amount || 0) }}
              </template>
              <span v-else>不授信</span>
            </el-descriptions-item>
          </template>
          <template v-else-if="customer.channel_referral">
            <el-descriptions-item label="所属推广渠道">
              {{ customer.channel_referral.channel_name }}（{{ customer.channel_referral.promotion_code }}）
            </el-descriptions-item>
            <el-descriptions-item label="注册优惠">
              {{ Number(customer.channel_referral.promotion_discount_rate || 0) }}%
            </el-descriptions-item>
          </template>
          <el-descriptions-item label="公司名称">{{ customer.company_name || '-' }}</el-descriptions-item>
          <el-descriptions-item label="联系人">{{ customer.contact_name }}</el-descriptions-item>
          <el-descriptions-item label="联系电话">{{ customer.contact_phone }}</el-descriptions-item>
          <el-descriptions-item label="联系邮箱">{{ customer.contact_email || '-' }}</el-descriptions-item>
          <el-descriptions-item label="登录账号">{{ customer.account_username || (customer.account ? `#${customer.account}` : '未开通') }}</el-descriptions-item>
          <el-descriptions-item label="IM 账号" :span="2">
            <template v-if="customer.im_accounts && customer.im_accounts.length">
              <el-tag
                v-for="im in customer.im_accounts"
                :key="im.id"
                size="small"
                :type="im.is_primary ? 'primary' : 'info'"
                style="margin:1px 6px 1px 0"
              >
                {{ im.im_type_label }}：{{ im.account || im.group }}
                <template v-if="im.label"> ({{ im.label }})</template>
                <template v-if="im.is_primary"> · 主</template>
              </el-tag>
            </template>
            <span v-else>-</span>
          </el-descriptions-item>
          <el-descriptions-item label="账户余额">
            <span :class="customer.balance < 0 ? 'text-danger' : 'text-success'">
              ¥{{ Number(customer.balance || 0).toFixed(2) }}
            </span>
            <el-button v-if="!isChannelAccount" link type="primary" size="small" style="margin-left:8px" @click="openAdjust">调账</el-button>
          </el-descriptions-item>
          <el-descriptions-item label="信用额度">¥{{ Number(customer.credit_limit || 0).toFixed(2) }}</el-descriptions-item>
          <el-descriptions-item label="信誉值">
            <template v-if="customer.reputation_mode && customer.reputation_mode !== 'none'">
              {{ reputationModeLabel(customer.reputation_mode) }}
              · 余额 {{ Number(customer.reputation_balance || 0) }} / {{ Number(customer.reputation_quota || 0) }}
            </template>
            <span v-else>关闭</span>
          </el-descriptions-item>
          <el-descriptions-item label="待收账款">
            <span :class="Number(customer.outstanding_total) > 0 ? 'text-warning' : ''">
              ¥{{ Number(customer.outstanding_total || 0).toFixed(2) }}
            </span>
          </el-descriptions-item>
          <el-descriptions-item label="销售经理">{{ salesManagerName }}</el-descriptions-item>
          <el-descriptions-item label="所在地区">{{ regionText }}</el-descriptions-item>
          <el-descriptions-item label="创建时间">{{ formatDate(customer.created_at) }}</el-descriptions-item>
          <el-descriptions-item label="备注" :span="3">{{ customer.remark || '-' }}</el-descriptions-item>
        </el-descriptions>
      </el-card>

      <!-- 渠道推广概览 -->
      <el-card v-if="customer?.customer_type === 'channel'" shadow="never" class="section-card" v-loading="channelOverviewLoading">
        <template #header>
          <div class="card-header-row">
            <span class="card-title"><el-icon><Share /></el-icon> 渠道推广概览</span>
            <el-button link type="primary" size="small" @click="goChannelSettlement">查看佣金与结算</el-button>
          </div>
        </template>
        <el-row :gutter="12" class="channel-stat-row">
          <el-col :xs="12" :sm="6">
            <div class="channel-stat"><span>{{ referredCustomerCount }}</span><small>推荐客户数</small></div>
          </el-col>
          <el-col v-for="item in channelCommissionSummary" :key="`${item.currency}-pending`" :xs="12" :sm="6">
            <div class="channel-stat">
              <span>{{ item.currency }} {{ Number(item.pending_amount || 0).toFixed(2) }}</span>
              <small>待结算佣金</small>
            </div>
          </el-col>
          <el-col v-for="item in channelCommissionSummary" :key="`${item.currency}-paid`" :xs="12" :sm="6">
            <div class="channel-stat success">
              <span>{{ item.currency }} {{ Number(item.paid_amount || 0).toFixed(2) }}</span>
              <small>已打款佣金</small>
            </div>
          </el-col>
        </el-row>
        <div class="referral-header">
          <strong>推荐客户</strong>
          <span>展示通过该渠道绑定的客户</span>
        </div>
        <el-table :data="referredCustomers" size="small" stripe border>
          <el-table-column prop="customer_no" label="客户编号" width="140" />
          <el-table-column prop="name" label="客户名称" min-width="160" />
          <el-table-column prop="contact_name" label="联系人" width="120" />
          <el-table-column prop="contact_phone" label="联系电话" width="140" />
          <el-table-column label="推广码" width="120">
            <template #default="{ row }">{{ row.channel_referral?.promotion_code || customer.channel_profile?.promotion_code || '-' }}</template>
          </el-table-column>
          <el-table-column label="绑定时间" width="155">
            <template #default="{ row }">{{ formatDate(row.channel_referral?.created_at || row.created_at) }}</template>
          </el-table-column>
          <el-table-column label="操作" width="75" fixed="right">
            <template #default="{ row }"><el-button link type="primary" size="small" @click="router.push(`/customer/${row.id}`)">详情</el-button></template>
          </el-table-column>
        </el-table>
        <el-empty v-if="!channelOverviewLoading && !referredCustomers.length" description="暂无推荐客户" :image-size="56" />
      </el-card>

      <!-- 业务详情 -->
      <el-card shadow="never" class="section-card" v-loading="servicesLoading">
        <template #header>
          <div class="card-header-row">
            <span class="card-title"><el-icon><Monitor /></el-icon> 业务详情</span>
            <span class="card-subtitle">按 POP 节点所属地区/国家分组展示</span>
          </div>
        </template>

        <el-empty v-if="!servicesLoading && serviceGroups.length === 0" description="暂无关联业务资源" :image-size="72" />

        <div v-for="group in serviceGroups" :key="group.key" class="region-group">
          <div class="region-header">
            <el-icon><Location /></el-icon>
            <span class="region-name">{{ group.region }}</span>
            <el-divider direction="vertical" />
            <span class="country-name">{{ group.country }}</span>
            <el-tag size="small" type="info" style="margin-left:8px;">{{ group.items.length }} 项</el-tag>
          </div>
          <el-table :data="group.items" size="small" stripe border class="service-table">
            <el-table-column prop="typeLabel" label="类型" width="100">
              <template #default="{ row }">
                <el-tag :type="serviceTypeTag[row.type]" size="small">{{ row.typeLabel }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="serviceNo" label="编号" width="150" />
            <el-table-column prop="name" label="名称" min-width="140" show-overflow-tooltip />
            <el-table-column prop="popName" label="POP 节点" width="130" show-overflow-tooltip />
            <el-table-column prop="popCity" label="城市" width="90" />
            <el-table-column prop="bandwidth" label="带宽" width="90" align="right">
              <template #default="{ row }">{{ row.bandwidth }} Mbps</template>
            </el-table-column>
            <el-table-column prop="status" label="状态" width="90">
              <template #default="{ row }">
                <el-tag size="small" :type="row.statusType">{{ row.statusLabel }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="expireTime" label="到期时间" width="110">
              <template #default="{ row }">
                <span :class="expireCls(row.expireTime)">{{ formatDateShort(row.expireTime) }}</span>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="80" fixed="right">
              <template #default="{ row }">
                <el-button link type="primary" size="small" @click="openServiceDetail(row)">详情</el-button>
              </template>
            </el-table-column>
          </el-table>
        </div>
      </el-card>

      <el-card shadow="never" class="section-card">
        <template #header>
          <span class="card-title"><el-icon><Wallet /></el-icon> 余额流水</span>
        </template>
        <el-table :data="ledgers" size="small" stripe border v-loading="ledgerLoading">
          <el-table-column prop="created_at" label="时间" width="160">
            <template #default="{ row }">{{ formatDate(row.created_at) }}</template>
          </el-table-column>
          <el-table-column label="类型" width="110">
            <template #default="{ row }">{{ row.entry_type_label || row.entry_type }}</template>
          </el-table-column>
          <el-table-column label="变动" width="120" align="right">
            <template #default="{ row }">
              <span :class="Number(row.delta) < 0 ? 'text-danger' : 'text-success'">
                {{ Number(row.delta) > 0 ? '+' : '' }}{{ Number(row.delta || 0).toFixed(2) }}
              </span>
            </template>
          </el-table-column>
          <el-table-column label="变动后" width="120" align="right">
            <template #default="{ row }">¥{{ Number(row.balance_after || 0).toFixed(2) }}</template>
          </el-table-column>
          <el-table-column prop="bill_no" label="关联账单" width="160">
            <template #default="{ row }">{{ row.bill_no || '—' }}</template>
          </el-table-column>
          <el-table-column prop="remark" label="备注" min-width="180" />
        </el-table>
        <el-empty v-if="!ledgerLoading && !ledgers.length" description="暂无余额流水" />
      </el-card>

      <!-- 账单管理 -->
      <el-card shadow="never" class="section-card">
        <template #header>
          <div class="card-header-row">
            <span class="card-title"><el-icon><Wallet /></el-icon> 账单管理</span>
            <el-button link type="primary" size="small" @click="goBillings">查看全部账单</el-button>
          </div>
        </template>
        <el-table :data="customer?.billings || []" size="small" stripe border>
          <el-table-column prop="bill_no" label="账单编号" width="150" />
          <el-table-column prop="bill_month" label="账期" width="100" />
          <el-table-column label="类型" width="100">
            <template #default="{ row }">{{ row.billing_kind_label || '月结' }}</template>
          </el-table-column>
          <el-table-column prop="amount" label="应付金额" width="110" align="right">
            <template #default="{ row }">¥{{ Number(row.amount || 0).toFixed(2) }}</template>
          </el-table-column>
          <el-table-column prop="paid_amount" label="已付金额" width="110" align="right">
            <template #default="{ row }">¥{{ Number(row.paid_amount || 0).toFixed(2) }}</template>
          </el-table-column>
          <el-table-column prop="status" label="状态" width="90">
            <template #default="{ row }">
              <el-tag size="small" :type="billingStatusMap[row.status]?.type">
                {{ billingStatusMap[row.status]?.label || row.status_label }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="due_date" label="到期日" width="100" />
          <el-table-column prop="payment_method" label="支付方式" width="100">
            <template #default="{ row }">{{ row.payment_method_label || '-' }}</template>
          </el-table-column>
        </el-table>
        <el-empty v-if="customer && !customer.billings?.length" description="暂无账单记录" :image-size="56" />
      </el-card>

      <!-- 操作记录 -->
      <el-card shadow="never" class="section-card">
        <template #header>
          <span class="card-title"><el-icon><Clock /></el-icon> 操作记录</span>
        </template>
        <el-timeline v-if="operationLogs.length">
          <el-timeline-item
            v-for="log in operationLogs"
            :key="log.id"
            :timestamp="formatDate(log.created_at)"
            placement="top"
          >
            <div class="log-item">
              <el-tag size="small" type="info">{{ log.action_type_label }}</el-tag>
              <span class="log-operator">{{ operatorName(log) }}</span>
              <span class="log-desc">{{ log.description }}</span>
            </div>
          </el-timeline-item>
        </el-timeline>
        <el-empty v-else description="暂无操作记录" :image-size="56" />
      </el-card>
    </div>

    <!-- 编辑对话框 -->
    <el-dialog v-model="editVisible" title="编辑客户信息" width="640px" destroy-on-close>
      <el-form ref="formRef" :model="form" :rules="formRules" label-width="100px">
        <el-row :gutter="16">
          <el-col :span="12">
            <el-form-item label="客户名称" prop="name">
              <el-input v-model="form.name" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="客户类型" prop="customer_type">
              <el-select v-model="form.customer_type" style="width:100%">
                <el-option v-for="t in customerTypes" :key="t.value" :label="t.label" :value="t.value" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="联系人" prop="contact_name">
              <el-input v-model="form.contact_name" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="联系电话" prop="contact_phone">
              <el-input v-model="form.contact_phone" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="联系邮箱">
              <el-input v-model="form.contact_email" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="公司名称">
              <el-input v-model="form.company_name" />
            </el-form-item>
          </el-col>
          <el-col :span="12" v-if="!isChannelAccount">
            <el-form-item label="信用额度">
              <el-input-number v-model="form.credit_limit" :min="0" :precision="2" controls-position="right" style="width:100%" />
            </el-form-item>
          </el-col>
          <el-col :span="24" v-if="!isChannelAccount">
            <el-divider content-position="left">信誉值支付</el-divider>
          </el-col>
          <el-col :span="12" v-if="!isChannelAccount">
            <el-form-item label="信誉值模式">
              <el-select v-model="form.reputation_mode" style="width:100%">
                <el-option v-for="m in reputationModeOptions" :key="m.value" :label="m.label" :value="m.value" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="12" v-if="!isChannelAccount">
            <el-form-item label="信誉值额度">
              <el-input-number
                v-model="form.reputation_quota"
                :min="0"
                :precision="2"
                :disabled="form.reputation_mode === 'none'"
                controls-position="right"
                style="width:100%"
              />
            </el-form-item>
          </el-col>
          <el-col :span="12" v-if="!isChannelAccount">
            <el-form-item label="当前余额">
              <el-input-number
                v-model="form.reputation_balance"
                :min="0"
                :precision="2"
                :disabled="form.reputation_mode === 'none'"
                controls-position="right"
                style="width:100%"
              />
            </el-form-item>
          </el-col>
          <template v-if="form.customer_type === 'channel' && !isChannelAccount">
            <el-col :span="24">
              <el-divider content-position="left">渠道推广规则</el-divider>
            </el-col>
            <el-col :span="12">
              <el-form-item label="推广优惠码">
                <el-input v-model="form.promotion_code" placeholder="留空自动生成" />
              </el-form-item>
            </el-col>
            <el-col :span="12">
              <el-form-item label="推广状态">
                <el-switch v-model="form.channel_promotion_enabled" active-text="启用" inactive-text="停用" />
              </el-form-item>
            </el-col>
            <el-col :span="12">
              <el-form-item label="客户优惠(%)">
                <el-input-number v-model="form.promotion_discount_rate" :min="0" :max="100" :precision="2" controls-position="right" style="width:100%" />
              </el-form-item>
            </el-col>
            <el-col :span="12">
              <el-form-item label="渠道提成(%)">
                <el-input-number v-model="form.commission_rate" :min="0" :max="100" :precision="2" controls-position="right" style="width:100%" />
              </el-form-item>
            </el-col>
            <el-col :span="12">
              <el-form-item label="注册授信模式">
                <el-select v-model="form.reputation_grant_mode" style="width:100%">
                  <el-option v-for="m in reputationModeOptions" :key="m.value" :label="m.label" :value="m.value" />
                </el-select>
              </el-form-item>
            </el-col>
            <el-col :span="12">
              <el-form-item label="注册授信额度">
                <el-input-number
                  v-model="form.reputation_grant_amount"
                  :min="0"
                  :precision="2"
                  :disabled="form.reputation_grant_mode === 'none'"
                  controls-position="right"
                  style="width:100%"
                />
              </el-form-item>
            </el-col>
            <el-col :span="24">
              <el-form-item label="规则备注">
                <el-input v-model="form.channel_promotion_remark" type="textarea" :rows="2" />
              </el-form-item>
            </el-col>
          </template>
          <el-col :span="24">
            <el-divider content-position="left">IM 账号（可添加多个）</el-divider>
            <div v-for="(im, idx) in form.im_accounts" :key="idx" class="im-row">
              <el-select v-model="im.im_type" placeholder="类型" style="width:120px">
                <el-option v-for="t in imTypes" :key="t.value" :label="t.label" :value="t.value" />
              </el-select>
              <el-input v-model="im.account" placeholder="账号/ID/chat_id" style="width:170px" />
              <el-input v-model="im.group" placeholder="群组(可选)" style="width:150px" />
              <el-input v-model="im.label" placeholder="用途备注" style="width:110px" />
              <el-radio v-model="primaryIndex" :label="idx">主账号</el-radio>
              <el-button link type="danger" @click="removeIM(idx)">移除</el-button>
            </div>
            <el-button size="small" :icon="Plus" @click="addIM">添加 IM 账号</el-button>
          </el-col>
          <el-col :span="24">
            <el-form-item label="备注">
              <el-input v-model="form.remark" type="textarea" :rows="2" />
            </el-form-item>
          </el-col>
        </el-row>
      </el-form>
      <template #footer>
        <el-button @click="editVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="handleSave">确认</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="adjustVisible" title="调整账户余额" width="420px" destroy-on-close>
      <p class="card-subtitle" style="margin:0 0 12px">正数增加余额，负数扣减。不改信用额度。</p>
      <el-form label-position="top">
        <el-form-item label="调整金额">
          <el-input-number v-model="adjustForm.delta" :precision="2" :step="10" style="width:100%" />
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="adjustForm.remark" type="textarea" :rows="2" maxlength="256" show-word-limit />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="adjustVisible = false">取消</el-button>
        <el-button type="primary" :loading="adjusting" @click="submitAdjust">确认调账</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="portalVisible" title="开通登录账号" width="460px" destroy-on-close>
      <el-form ref="portalFormRef" :model="portalForm" :rules="portalRules" label-width="90px">
        <el-form-item label="用户名" prop="username">
          <el-input v-model="portalForm.username" autocomplete="off" />
        </el-form-item>
        <el-form-item label="密码" prop="password">
          <el-input v-model="portalForm.password" type="password" show-password autocomplete="new-password" />
        </el-form-item>
        <el-form-item label="邮箱">
          <el-input v-model="portalForm.email" />
        </el-form-item>
        <el-form-item label="姓名">
          <el-input v-model="portalForm.first_name" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="portalVisible = false">取消</el-button>
        <el-button type="primary" :loading="portalSaving" @click="submitPortalAccount">开通</el-button>
      </template>
    </el-dialog>

  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { ArrowLeft, User, Monitor, Wallet, Clock, Location, Plus, Share } from '@element-plus/icons-vue'
import dayjs from 'dayjs'
import {
  adjustCustomerBalance,
  createCustomerPortalAccount,
  getChannelCommissionSummary,
  getCustomer,
  getCustomerWalletLedger,
  getCustomers,
  updateCustomer,
} from '@/api/customer'
import { getVPS, getEdgeAccelerations, getPopPoints } from '@/api/resource'
import { useAuthStore } from '@/stores/auth'

const route = useRoute()
const router = useRouter()
const authStore = useAuthStore()
const isChannelAccount = computed(() =>
  authStore.user?.role === 'channel' || authStore.user?.is_channel_account
)
const loading = ref(false)
const servicesLoading = ref(false)
const saving = ref(false)
const channelOverviewLoading = ref(false)
const customer = ref(null)
const serviceGroups = ref([])
const referredCustomers = ref([])
const channelCommissionSummary = ref([])
const editVisible = ref(false)
const formRef = ref(null)
const ledgers = ref([])
const ledgerLoading = ref(false)
const adjustVisible = ref(false)
const adjusting = ref(false)
const adjustForm = reactive({ delta: 0, remark: '' })
const portalVisible = ref(false)
const portalSaving = ref(false)
const portalFormRef = ref(null)
const portalForm = reactive({ username: '', password: '', email: '', first_name: '' })
const portalRules = {
  username: [{ required: true, message: '请输入用户名' }],
  password: [{ required: true, message: '请输入密码' }, { min: 8, message: '密码至少8位' }],
}

const form = reactive({
  name: '', customer_type: 'enterprise', contact_name: '', contact_phone: '',
  contact_email: '', company_name: '', credit_limit: 0, remark: '',
  im_accounts: [],
  promotion_code: '', promotion_discount_rate: 0, commission_rate: 0,
  channel_promotion_enabled: true, channel_promotion_remark: '',
  reputation_mode: 'none', reputation_quota: 0, reputation_balance: 0,
  reputation_grant_mode: 'none', reputation_grant_amount: 0,
})
const primaryIndex = ref(-1)

const imTypes = [
  { value: 'telegram', label: 'Telegram' }, { value: 'wecom', label: '企业微信' },
  { value: 'dingtalk', label: '钉钉' }, { value: 'wechat', label: '微信' },
  { value: 'whatsapp', label: 'WhatsApp' }, { value: 'qq', label: 'QQ' }, { value: 'other', label: '其他' }
]

function addIM() {
  form.im_accounts.push({ im_type: 'telegram', account: '', group: '', label: '', is_primary: false })
  if (primaryIndex.value < 0) primaryIndex.value = form.im_accounts.length - 1
}
function removeIM(idx) {
  form.im_accounts.splice(idx, 1)
  if (primaryIndex.value === idx) primaryIndex.value = form.im_accounts.length ? 0 : -1
  else if (primaryIndex.value > idx) primaryIndex.value -= 1
}

const billingStatusMap = {
  unpaid: { label: '未付', type: 'warning' },
  paid: { label: '已付', type: 'success' },
  overdue: { label: '逾期', type: 'danger' },
  cancelled: { label: '已取消', type: 'info' }
}
const serviceTypeTag = { vps: 'primary', edge: 'success' }

const vpsStatusMap = {
  running: { label: '运行中', type: 'success' },
  stopped: { label: '已停止', type: 'info' },
  suspended: { label: '已暂停', type: 'warning' },
  creating: { label: '创建中', type: 'warning' },
  deleting: { label: '删除中', type: 'danger' },
  error: { label: '故障', type: 'danger' }
}
const edgeStatusMap = {
  active: { label: '运行中', type: 'success' },
  stopped: { label: '已停止', type: 'info' },
  pending: { label: '待配置', type: 'warning' },
  error: { label: '故障', type: 'danger' }
}

const customerTypes = [
  { value: 'personal', label: '个人' }, { value: 'enterprise', label: '企业' },
  { value: 'channel', label: '渠道商' }, { value: 'provider', label: '供应商' }, { value: 'other', label: '其他' }
]
const reputationModeOptions = [
  { value: 'none', label: '关闭（不可信誉支付）' },
  { value: 'one_time', label: '一次性' },
  { value: 'monthly', label: '按月更新' },
]
function reputationModeLabel(mode) {
  return reputationModeOptions.find((item) => item.value === mode)?.label || '关闭'
}
watch(() => form.reputation_mode, (mode, prev) => {
  if (mode === 'none') {
    form.reputation_quota = 0
    form.reputation_balance = 0
    return
  }
  if (prev === 'none' && Number(form.reputation_balance || 0) === 0) {
    form.reputation_balance = Number(form.reputation_quota || 0)
  }
})
const formRules = {
  name: [{ required: true, message: '请输入客户名称' }],
  customer_type: [{ required: true, message: '请选择客户类型' }],
  contact_name: [{ required: true, message: '请输入联系人' }],
  contact_phone: [{ required: true, message: '请输入联系电话' }]
}

const salesManagerName = computed(() =>
  customer.value?.sales_manager_info?.full_name || '-'
)
const regionText = computed(() => {
  const c = customer.value
  if (!c?.province && !c?.city) return '-'
  return [c.province, c.city].filter(Boolean).join(' / ')
})
const operationLogs = computed(() => customer.value?.operation_logs || [])
const referredCustomerCount = computed(() =>
  customer.value?.channel_profile?.referred_customer_count
  ?? referredCustomers.value.length
)
const serviceStats = computed(() => {
  let vpsCount = 0
  let edgeCount = 0
  let totalBandwidth = 0
  for (const group of serviceGroups.value) {
    for (const item of group.items) {
      if (item.type === 'vps') vpsCount++
      else edgeCount++
      totalBandwidth += item.bandwidth || 0
    }
  }
  return { vpsCount, edgeCount, totalBandwidth }
})

function getTypeLabel(type) { return customerTypes.find(t => t.value === type)?.label || type }
function formatDate(date) { return date ? dayjs(date).format('YYYY-MM-DD HH:mm') : '-' }
function formatDateShort(date) { return date ? dayjs(date).format('YYYY-MM-DD') : '无期限' }
function operatorName(log) {
  return log.operator_info?.full_name || log.operator_info?.username || '系统'
}
function goChannelSettlement() {
  router.push({ path: '/customer/channels', query: { channel: customer.value?.id } })
}
function expireCls(date) {
  if (!date) return ''
  const days = dayjs(date).diff(dayjs(), 'day')
  if (days < 0) return 'expire-red'
  if (days <= 30) return 'expire-warn'
  return ''
}

function buildPopGeoMap(popList) {
  const map = {}
  for (const pop of popList) {
    map[pop.id] = {
      region: pop.city_info?.region_name || '未知地区',
      country: pop.city_info?.country_name || '未知国家',
      city: pop.city_info?.name || '-'
    }
  }
  return map
}

function normalizeService(row, type, popGeoMap) {
  const geo = popGeoMap[row.pop_point] || {}
  const statusMap = type === 'vps' ? vpsStatusMap : edgeStatusMap
  const statusInfo = statusMap[row.status] || { label: row.status_label || row.status, type: 'info' }
  return {
    id: row.id,
    type,
    typeLabel: type === 'vps' ? 'VPS' : '边缘加速',
    serviceNo: type === 'vps' ? row.vps_no : row.edge_no,
    name: row.name,
    popName: row.pop_name || '-',
    popCity: row.pop_city || geo.city || '-',
    region: geo.region || '未知地区',
    country: geo.country || '未知国家',
    bandwidth: row.bandwidth_mbps || 0,
    status: row.status,
    statusLabel: statusInfo.label,
    statusType: statusInfo.type,
    expireTime: row.expire_time
  }
}

function groupServices(items) {
  const groupMap = new Map()
  for (const item of items) {
    const key = `${item.region}::${item.country}`
    if (!groupMap.has(key)) {
      groupMap.set(key, { key, region: item.region, country: item.country, items: [] })
    }
    groupMap.get(key).items.push(item)
  }
  return Array.from(groupMap.values()).sort((a, b) => {
    const ra = a.region.localeCompare(b.region, 'zh-CN')
    return ra !== 0 ? ra : a.country.localeCompare(b.country, 'zh-CN')
  })
}

async function loadServices() {
  servicesLoading.value = true
  try {
    const customerId = route.params.id
    const [vpsRes, edgeRes, popRes] = await Promise.all([
      getVPS({ customer: customerId, page_size: 500 }),
      getEdgeAccelerations({ customer: customerId, page_size: 500 }),
      getPopPoints({ page_size: 500 })
    ])
    const popGeoMap = buildPopGeoMap(popRes?.results || [])
    const vpsItems = (vpsRes?.results || []).map(r => normalizeService(r, 'vps', popGeoMap))
    const edgeItems = (edgeRes?.results || []).map(r => normalizeService(r, 'edge', popGeoMap))
    serviceGroups.value = groupServices([...vpsItems, ...edgeItems])
  } catch {
    serviceGroups.value = []
  } finally {
    servicesLoading.value = false
  }
}

async function loadData() {
  loading.value = true
  try {
    customer.value = await getCustomer(route.params.id)
  } finally {
    loading.value = false
  }
}

async function loadLedger() {
  ledgerLoading.value = true
  try {
    const res = await getCustomerWalletLedger(route.params.id, { page_size: 20 })
    ledgers.value = res.results || res || []
  } catch {
    ledgers.value = []
  } finally {
    ledgerLoading.value = false
  }
}

function openAdjust() {
  adjustForm.delta = 0
  adjustForm.remark = ''
  adjustVisible.value = true
}

function openPortalDialog() {
  Object.assign(portalForm, {
    username: '',
    password: '',
    email: customer.value?.contact_email || '',
    first_name: customer.value?.contact_name || '',
  })
  portalVisible.value = true
}

async function submitPortalAccount() {
  const valid = await portalFormRef.value.validate().catch(() => false)
  if (!valid || !customer.value) return
  portalSaving.value = true
  try {
    await createCustomerPortalAccount(customer.value.id, { ...portalForm })
    ElMessage.success('登录账号已开通')
    portalVisible.value = false
    await loadData()
  } catch (err) {
    const data = err?.response?.data
    const msg = data?.username?.[0] || data?.password?.[0] || data?.detail || data?.message || '开通失败'
    ElMessage.error(typeof msg === 'string' ? msg : '开通失败')
  } finally {
    portalSaving.value = false
  }
}

async function submitAdjust() {
  if (!adjustForm.delta) {
    ElMessage.warning('调整金额不能为零')
    return
  }
  if (!String(adjustForm.remark || '').trim()) {
    ElMessage.warning('请填写调账备注')
    return
  }
  adjusting.value = true
  try {
    await adjustCustomerBalance(route.params.id, {
      delta: adjustForm.delta,
      remark: adjustForm.remark.trim(),
    })
    ElMessage.success('调账成功')
    adjustVisible.value = false
    await Promise.all([loadData(), loadLedger()])
  } catch (err) {
    ElMessage.error(err?.response?.data?.detail || err?.response?.data?.message || '调账失败')
  } finally {
    adjusting.value = false
  }
}

async function loadChannelOverview() {
  if (customer.value?.customer_type !== 'channel') return
  channelOverviewLoading.value = true
  try {
    const channelId = customer.value.id
    const [customersData, summaryData] = await Promise.all([
      getCustomers({ channel_referral__channel: channelId, page_size: 200 }),
      getChannelCommissionSummary({ channel: channelId }),
    ])
    referredCustomers.value = customersData?.results || customersData || []
    channelCommissionSummary.value = summaryData?.by_currency || []
  } catch {
    referredCustomers.value = []
    channelCommissionSummary.value = []
  } finally {
    channelOverviewLoading.value = false
  }
}

function openEditDialog() {
  if (!customer.value) return
  Object.assign(form, {
    name: customer.value.name,
    customer_type: customer.value.customer_type,
    contact_name: customer.value.contact_name,
    contact_phone: customer.value.contact_phone,
    contact_email: customer.value.contact_email || '',
    company_name: customer.value.company_name || '',
    credit_limit: customer.value.credit_limit || 0,
    remark: customer.value.remark || '',
    promotion_code: customer.value.channel_profile?.promotion_code || '',
    promotion_discount_rate: Number(customer.value.channel_profile?.promotion_discount_rate || 0),
    commission_rate: Number(customer.value.channel_profile?.commission_rate || 0),
    channel_promotion_enabled: customer.value.channel_profile?.is_active ?? true,
    channel_promotion_remark: customer.value.channel_profile?.remark || '',
    reputation_mode: customer.value.reputation_mode || 'none',
    reputation_quota: Number(customer.value.reputation_quota || 0),
    reputation_balance: Number(customer.value.reputation_balance || 0),
    reputation_grant_mode: customer.value.channel_profile?.reputation_grant_mode || 'none',
    reputation_grant_amount: Number(customer.value.channel_profile?.reputation_grant_amount || 0),
    im_accounts: (customer.value.im_accounts || []).map(a => ({
      im_type: a.im_type, account: a.account || '', group: a.group || '',
      label: a.label || '', is_primary: a.is_primary
    }))
  })
  primaryIndex.value = form.im_accounts.findIndex(a => a.is_primary)
  editVisible.value = true
}

async function handleSave() {
  const valid = await formRef.value.validate().catch(() => false)
  if (!valid) return
  const payload = {
    name: form.name,
    customer_type: form.customer_type,
    contact_name: form.contact_name,
    contact_phone: form.contact_phone,
    contact_email: form.contact_email,
    company_name: form.company_name,
    remark: form.remark,
    im_accounts: form.im_accounts
      .filter(a => a.account || a.group)
      .map((a, i) => ({ ...a, is_primary: i === primaryIndex.value }))
  }
  if (!isChannelAccount.value) {
    payload.credit_limit = form.credit_limit
    payload.reputation_mode = form.reputation_mode
    payload.reputation_quota = form.reputation_mode === 'none' ? 0 : form.reputation_quota
    payload.reputation_balance = form.reputation_mode === 'none' ? 0 : form.reputation_balance
  }
  if (form.customer_type === 'channel' && !isChannelAccount.value) {
    Object.assign(payload, {
      promotion_code: form.promotion_code,
      promotion_discount_rate: form.promotion_discount_rate,
      commission_rate: form.commission_rate,
      channel_promotion_enabled: form.channel_promotion_enabled,
      channel_promotion_remark: form.channel_promotion_remark,
      reputation_grant_mode: form.reputation_grant_mode,
      reputation_grant_amount: form.reputation_grant_mode === 'none' ? 0 : form.reputation_grant_amount,
    })
  }
  saving.value = true
  try {
    await updateCustomer(route.params.id, payload)
    ElMessage.success('客户信息已更新')
    editVisible.value = false
    await loadData()
    await loadChannelOverview()
  } catch (err) {
    ElMessage.error(err?.response?.data?.message || '更新失败')
  } finally {
    saving.value = false
  }
}

function openServiceDetail(row) {
  if (row.type === 'vps') {
    router.push(`/resource/vps/${row.id}`)
  } else {
    router.push(`/resource/edge/${row.id}`)
  }
}

function goBillings() {
  router.push({ path: '/customer/billings', query: { customer: route.params.id } })
}

onMounted(async () => {
  await loadData()
  await Promise.all([loadServices(), loadChannelOverview(), loadLedger()])
})
</script>

<style scoped>
.page-container { min-height: 100%; }
.page-header {
  display: flex; align-items: center; justify-content: space-between;
  margin-bottom: 16px;
}
.header-left { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.page-title { font-size: 18px; font-weight: 700; color: #1a1a2e; margin: 0; }

.stat-row { margin-bottom: 16px; }
.stat-card {
  background: #f8f9ff; border: 1px solid #ebeef5; border-radius: 8px;
  padding: 14px 16px; text-align: center;
}
.stat-num { display: block; font-size: 22px; font-weight: 700; color: #303133; line-height: 1.2; }
.stat-lbl { display: block; font-size: 12px; color: #909399; margin-top: 4px; }

.detail-sections { display: flex; flex-direction: column; gap: 16px; }
.section-card :deep(.el-card__header) { padding: 12px 16px; }
.card-title { display: flex; align-items: center; gap: 6px; font-weight: 600; font-size: 14px; }
.card-header-row { display: flex; align-items: center; justify-content: space-between; }
.card-subtitle { font-size: 12px; color: #909399; font-weight: normal; margin-left: 12px; }
.channel-stat-row { margin-bottom: 18px; }
.channel-stat {
  display: flex; min-height: 64px; padding: 10px 14px; box-sizing: border-box;
  flex-direction: column; justify-content: center; border: 1px solid #e4e7ed;
  border-radius: 6px; background: #fafcff;
}
.channel-stat span { color: #409eff; font-size: 17px; font-weight: 700; }
.channel-stat.success span { color: #67c23a; }
.channel-stat small { margin-top: 5px; color: #909399; }
.referral-header { display: flex; align-items: baseline; gap: 10px; margin-bottom: 10px; }
.referral-header span { color: #909399; font-size: 12px; }

.region-group { margin-bottom: 20px; }
.region-group:last-child { margin-bottom: 0; }
.region-header {
  display: flex; align-items: center; gap: 6px;
  margin-bottom: 10px; font-size: 13px; color: #606266;
}
.region-name { font-weight: 600; color: #303133; }
.country-name { color: #606266; }
.service-table { margin-bottom: 4px; }

.log-item { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; font-size: 13px; }
.log-operator { font-weight: 600; color: #303133; }
.log-desc { color: #606266; }

.text-success { color: #67C23A; }
.text-danger { color: #F56C6C; }
.text-warning { color: #E6A23C; }
.expire-red { color: #F56C6C; font-weight: 600; }
.expire-warn { color: #E6A23C; }
.im-row { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; flex-wrap: wrap; }
</style>
