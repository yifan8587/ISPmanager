<template>
  <div class="page-container">
    <div class="page-header">
      <h2 class="page-title">通知 / IM 对接</h2>
      <div class="hint">对内告警走 IM；催缴 / 逾期 / 开通成功 / 工单回复可在打开「启用邮件通知」后发客户邮箱。门户客服入口请在「系统设置 → 对外客服」填写。</div>
    </div>

    <el-card shadow="never">
      <el-tabs v-model="activeTab">
        <el-tab-pane label="邮件" name="email">
          <p class="hint" style="margin-top:0">
            在「系统设置 → 对内通知」填写客服邮箱、邮件服务器地址、端口、账号密码，并打开「启用邮件通知」。
            未填服务器时回退环境变量 <code>DJANGO_EMAIL_*</code>。测试邮件不要求先打开总开关。
          </p>
          <el-form :inline="true" @submit.prevent>
            <el-form-item label="测试收件人">
              <el-input v-model="emailTestTo" placeholder="留空则发到当前账号或运维抄送" style="width:280px" />
            </el-form-item>
            <el-form-item>
              <el-button type="primary" :disabled="!canManage" :loading="emailTesting" @click="sendTestEmail">发送测试邮件</el-button>
            </el-form-item>
          </el-form>
        </el-tab-pane>
        <!-- ═══════════ IM 通道 ═══════════ -->
        <el-tab-pane label="IM 通道" name="channels">
          <div class="toolbar">
            <el-select v-model="channelFilter.channel_type" placeholder="通道类型" clearable style="width:160px" @change="loadChannels">
              <el-option v-for="t in channelTypes" :key="t.value" :label="t.label" :value="t.value" />
            </el-select>
            <el-button type="primary" :disabled="!canManage" @click="openChannelDialog()">新增通道</el-button>
          </div>
          <el-table :data="channels" v-loading="channelLoading" border stripe size="small">
            <el-table-column prop="name" label="名称" min-width="140" />
            <el-table-column prop="channel_type_label" label="类型" width="120">
              <template #default="{ row }">
                <el-tag size="small" :type="typeTagMap[row.channel_type] || 'info'">{{ row.channel_type_label }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="接口人" width="90" align="center">
              <template #default="{ row }">{{ row.recipient_count }} 人</template>
            </el-table-column>
            <el-table-column label="启用" width="80" align="center">
              <template #default="{ row }">
                <el-switch v-model="row.enabled" :disabled="!canManage" @change="toggleChannel(row)" />
              </template>
            </el-table-column>
            <el-table-column label="测试状态" width="160">
              <template #default="{ row }">
                <el-tag size="small" :type="testTagMap[row.last_test_status] || 'info'">{{ row.last_test_status_label }}</el-tag>
                <div v-if="row.last_test_at" class="muted">{{ formatTime(row.last_test_at) }}</div>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="280" fixed="right">
              <template #default="{ row }">
                <el-button link type="success" :disabled="!canManage" @click="testChannel(row)">测试</el-button>
                <el-button link type="primary" :disabled="!canManage" @click="openSendDialog(row)">发送</el-button>
                <el-button link type="primary" :disabled="!canManage" @click="openChannelDialog(row)">编辑</el-button>
                <el-button link type="danger" :disabled="!canManage" @click="removeChannel(row)">删除</el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-tab-pane>

        <!-- ═══════════ 通知规则 ═══════════ -->
        <el-tab-pane label="通知规则" name="rules">
          <div class="toolbar">
            <el-select v-model="ruleFilter.event" placeholder="触发事件" clearable style="width:180px" @change="loadRules">
              <el-option v-for="e in events" :key="e.value" :label="e.label" :value="e.value" />
            </el-select>
            <el-button type="primary" :disabled="!canManage" @click="openRuleDialog()">新增规则</el-button>
          </div>
          <el-table :data="rules" v-loading="ruleLoading" border stripe size="small">
            <el-table-column prop="name" label="规则名称" min-width="140" />
            <el-table-column label="触发事件" min-width="210">
              <template #default="{ row }">
                <el-tag
                  v-for="event in ruleEventLabels(row)"
                  :key="event.value"
                  size="small"
                  type="warning"
                  style="margin:2px 4px 2px 0"
                >
                  {{ event.label }}
                </el-tag>
                <div v-if="actionFilterLabels(row)" class="muted">{{ actionFilterLabels(row) }}</div>
              </template>
            </el-table-column>
            <el-table-column label="推送通道" min-width="200">
              <template #default="{ row }">
                <el-tag v-for="c in row.channel_names" :key="c.id" size="small" style="margin:2px 4px 2px 0">{{ c.name }}</el-tag>
                <span v-if="!row.channel_names.length" class="muted">未配置通道</span>
              </template>
            </el-table-column>
            <el-table-column label="格式" width="100">
              <template #default="{ row }">
                <el-tag size="small" :type="row.format_mode === 'custom' ? 'success' : 'info'">
                  {{ row.format_mode_label || '详细格式' }}
                </el-tag>
                <div v-if="row.dedupe_seconds" class="muted">{{ row.dedupe_seconds }} 秒去重</div>
              </template>
            </el-table-column>
            <el-table-column label="启用" width="80" align="center">
              <template #default="{ row }">
                <el-switch v-model="row.enabled" :disabled="!canManage" @change="toggleRule(row)" />
              </template>
            </el-table-column>
            <el-table-column label="操作" width="240" fixed="right">
              <template #default="{ row }">
                <el-button link type="success" :disabled="!canManage" @click="openDispatchDialog(row)">手动触发</el-button>
                <el-button link type="primary" :disabled="!canManage" @click="openRuleDialog(row)">编辑</el-button>
                <el-button link type="danger" :disabled="!canManage" @click="removeRule(row)">删除</el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-tab-pane>

        <!-- ═══════════ 发送日志 ═══════════ -->
        <el-tab-pane label="发送日志" name="logs">
          <div class="toolbar">
            <el-select v-model="logFilter.success" placeholder="结果" clearable style="width:120px" @change="loadLogs">
              <el-option label="成功" :value="true" />
              <el-option label="失败" :value="false" />
            </el-select>
            <el-select v-model="logFilter.event" placeholder="事件" clearable style="width:160px" @change="loadLogs">
              <el-option v-for="e in events" :key="e.value" :label="e.label" :value="e.value" />
            </el-select>
            <el-button @click="loadLogs">刷新</el-button>
          </div>
          <el-table :data="logs" v-loading="logLoading" border stripe size="small">
            <el-table-column prop="created_at" label="时间" width="160">
              <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
            </el-table-column>
            <el-table-column prop="channel_name" label="通道" width="130" />
            <el-table-column prop="event" label="事件" width="130" />
            <el-table-column prop="title" label="标题" min-width="140" />
            <el-table-column prop="target" label="接收目标" width="140" show-overflow-tooltip />
            <el-table-column label="结果" width="80" align="center">
              <template #default="{ row }">
                <el-tag size="small" :type="row.success ? 'success' : 'danger'">{{ row.success ? '成功' : '失败' }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="response" label="响应/错误" min-width="200" show-overflow-tooltip />
          </el-table>
        </el-tab-pane>
      </el-tabs>
    </el-card>

    <!-- 通道 编辑/新增 -->
    <el-dialog v-model="channelDialog" :title="channelForm.id ? '编辑通道' : '新增通道'" width="720px" :close-on-click-modal="false">
      <el-form :model="channelForm" label-width="120px">
        <el-form-item label="通道名称" required>
          <el-input v-model="channelForm.name" placeholder="如：运维告警群-钉钉" />
        </el-form-item>
        <el-form-item label="通道类型" required>
          <el-select v-model="channelForm.channel_type" style="width:100%" :disabled="!!channelForm.id">
            <el-option v-for="t in channelTypes" :key="t.value" :label="t.label" :value="t.value" />
          </el-select>
        </el-form-item>

        <!-- Telegram -->
        <template v-if="channelForm.channel_type === 'telegram'">
          <el-form-item label="Bot Token">
            <el-input v-model="channelForm.bot_token" placeholder="留空表示不修改已保存的 Token" />
          </el-form-item>
          <el-form-item label="对内接收 chat_id">
            <el-input v-model="channelForm.default_target" placeholder="运维群/账号 chat_id，多个用英文逗号分隔" />
            <div class="form-tip">仅用于对内告警，不会出现在门户客服弹窗。对外 Telegram 请到「系统设置 → 对外客服」填写用户名或 t.me 链接。</div>
          </el-form-item>
        </template>

        <!-- 企业微信 -->
        <template v-else-if="channelForm.channel_type === 'wecom'">
          <el-alert type="info" :closable="false" show-icon style="margin-bottom:12px"
            title="企业微信自建应用：系统自动获取 access_token 后调用消息发送接口，无需填写 Webhook 地址或 Token。" />
          <el-form-item label="CorpID" required>
            <el-input v-model="channelForm.corp_id" placeholder="例如 wwxxxxxxxxxxxxxxxx" />
          </el-form-item>
          <el-form-item label="AgentID" required>
            <el-input v-model="channelForm.agent_id" placeholder="企业微信自建应用的数字 AgentID，例如 1000002" />
          </el-form-item>
          <el-form-item label="应用 Secret" required>
            <el-input v-model="channelForm.corp_secret" show-password placeholder="企业微信自建应用 Secret；编辑时留空表示不修改" />
            <div class="form-tip">Secret 仅在保存时提交，接口读取时不会返回明文。</div>
          </el-form-item>
          <el-form-item label="接收员工 UserID" required>
            <el-input v-model="channelForm.default_target" placeholder="企业微信通讯录 UserID；多个用 | 分隔，@all 表示全员" />
            <div class="form-tip">不是员工姓名；员工必须在该应用的可见范围内。</div>
          </el-form-item>
        </template>

        <!-- 企业微信群机器人 Webhook -->
        <template v-else-if="channelForm.channel_type === 'wecom_webhook'">
          <el-alert
            type="info"
            :closable="false"
            show-icon
            style="margin-bottom:12px"
            title="企业微信群机器人 Webhook：系统按 msgtype=text、text.content 的官方格式发送。无需 CorpID、AgentID、Secret 或接收人。"
          />
          <el-form-item label="机器人 Webhook" required>
            <el-input
              v-model="channelForm.webhook_url"
              placeholder="https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=..."
            />
            <div class="form-tip">
              请填写群聊中“群机器人”配置页生成的地址；自建应用消息请改选“企业微信”通道。
            </div>
          </el-form-item>
        </template>

        <!-- 企业微信客服 -->
        <template v-else-if="channelForm.channel_type === 'wecom_kf'">
          <el-alert type="warning" :closable="false" show-icon style="margin-bottom:12px"
            title="客服链接仅用于客户进入会话；主动消息还必须填写客户 external_userid，并受企业微信客服会话窗口限制。" />
          <el-form-item label="CorpID" required>
            <el-input v-model="channelForm.corp_id" placeholder="企业微信企业 ID" />
          </el-form-item>
          <el-form-item label="应用 Secret" required>
            <el-input v-model="channelForm.corp_secret" placeholder="配置为“微信客服可调用接口”的自建应用 Secret；留空不修改" show-password />
          </el-form-item>
          <el-form-item label="Secret 环境变量名">
            <el-input v-model="channelForm.wecom_secret_env" placeholder="推荐：WECOM_KF_SECRET（服务器环境变量，不保存明文）" />
          </el-form-item>
          <el-form-item label="客服账号 OpenKfId" required>
            <el-input v-model="channelForm.wecom_kf_id" placeholder="例如 kfc199e71f492875736" />
          </el-form-item>
          <el-form-item label="客服入口链接">
            <el-input v-model="channelForm.wecom_kf_url" placeholder="https://work.weixin.qq.com/kfid/..." />
            <div class="form-tip">供本通道发消息使用。门户对外展示请到「系统设置 → 对外客服」填写「企业微信客服入口」。</div>
          </el-form-item>
          <el-form-item label="默认 external_userid">
            <el-input v-model="channelForm.default_target" placeholder="仅用于已建立客服会话的单个客户；多个用逗号分隔" />
          </el-form-item>
        </template>

        <!-- 企业微信智能机器人 -->
        <template v-else-if="channelForm.channel_type === 'wecom_aibot'">
          <el-alert type="warning" :closable="false" show-icon style="margin-bottom:12px"
            title="智能机器人采用 WebSocket 长连接，不使用 CorpID、AgentID 或 Webhook。接收成员必须先向机器人发送过消息；保存后还需启动机器人连接服务。" />
          <el-form-item label="机器人 BotID" required>
            <el-input v-model="channelForm.wecom_aibot_id" placeholder="企业微信智能机器人配置页中的 BotID" />
          </el-form-item>
          <el-form-item label="长连接 Secret" required>
            <el-input v-model="channelForm.wecom_aibot_secret" show-password placeholder="智能机器人长连接专用 Secret；编辑时留空表示不修改" />
            <div class="form-tip">此 Secret 仅用于 WebSocket 的 aibot_subscribe 鉴权，不是自建应用 Secret。</div>
          </el-form-item>
          <el-form-item label="接收成员 UserID" required>
            <el-input v-model="channelForm.default_target" placeholder="企业微信成员 UserID；多个用 | 分隔" />
            <div class="form-tip">成员需要先向该机器人发过消息；机器人创建者非超级管理员时，回调中的 UserID 可能是加密值。</div>
          </el-form-item>
          <el-alert type="info" :closable="false"
            title="部署命令：python manage.py run_wecom_aibot --channel &lt;通道ID&gt;。连接成功后，测试和通知将写入队列并由该服务确认投递。" />
        </template>

        <!-- 钉钉 -->
        <template v-else-if="channelForm.channel_type === 'dingtalk'">
          <el-form-item label="群机器人 Webhook" required>
            <el-input v-model="channelForm.webhook_url" placeholder="https://oapi.dingtalk.com/robot/send?access_token=..." />
          </el-form-item>
          <el-form-item label="加签密钥">
            <el-input v-model="channelForm.secret" placeholder="安全设置-加签的 SEC 开头密钥，留空表示不修改" />
          </el-form-item>
          <el-form-item label="@手机号">
            <el-input v-model="channelForm.default_target" placeholder="需 @ 的手机号，多个用逗号分隔" />
          </el-form-item>
        </template>

        <!-- 自定义 Webhook -->
        <template v-else-if="channelForm.channel_type === 'webhook'">
          <el-form-item label="Webhook 地址" required>
            <el-input v-model="channelForm.webhook_url" placeholder="POST {title, content, target}" />
          </el-form-item>
          <el-form-item label="鉴权头(可选)">
            <el-input v-model="channelForm.secret" placeholder="作为 Authorization 头发送，留空表示不修改" />
          </el-form-item>
        </template>

        <!-- 接口人列表 -->
        <el-form-item label="接口人列表" v-if="channelForm.channel_type && !['wecom', 'wecom_webhook', 'wecom_aibot'].includes(channelForm.channel_type)">
          <div style="width:100%">
            <div v-for="(r, idx) in channelForm.recipients" :key="idx" class="recipient-row">
              <el-input v-model="r.name" placeholder="姓名" style="width:160px" />
              <el-input v-model="r.target" :placeholder="targetPlaceholder" style="flex:1" />
              <el-button link type="danger" @click="channelForm.recipients.splice(idx, 1)">移除</el-button>
            </div>
            <el-button size="small" @click="channelForm.recipients.push({ name: '', target: '' })">+ 添加接口人</el-button>
            <div class="muted">接口人为空时，将使用上方默认接收目标。</div>
          </div>
        </el-form-item>

        <el-form-item label="启用">
          <el-switch v-model="channelForm.enabled" />
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="channelForm.remark" type="textarea" :rows="2" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="channelDialog = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="saveChannel">保存</el-button>
      </template>
    </el-dialog>

    <!-- 发送自定义消息 -->
    <el-dialog v-model="sendDialog" title="发送消息" width="520px">
      <el-form :model="sendForm" label-width="80px">
        <el-form-item label="通道">{{ sendForm.channelName }}</el-form-item>
        <el-form-item label="标题"><el-input v-model="sendForm.title" /></el-form-item>
        <el-form-item label="内容"><el-input v-model="sendForm.content" type="textarea" :rows="4" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="sendDialog = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="doSend">发送</el-button>
      </template>
    </el-dialog>

    <!-- 规则 编辑/新增 -->
    <el-dialog v-model="ruleDialog" :title="ruleForm.id ? '编辑规则' : '新增规则'" width="860px" :close-on-click-modal="false">
      <el-form :model="ruleForm" label-width="100px">
        <el-form-item label="规则名称" required>
          <el-input v-model="ruleForm.name" />
        </el-form-item>
        <el-form-item label="触发事件" required>
          <el-select
            v-model="ruleForm.events"
            multiple
            filterable
            collapse-tags
            collapse-tags-tooltip
            style="width:100%"
            placeholder="选择一个或多个触发事件"
            @change="onRuleEventsChange"
          >
            <el-option v-for="e in events" :key="e.value" :label="e.label" :value="e.value" />
          </el-select>
          <div class="form-tip">
            任一选中事件发生时都会命中本规则。勾选「资源异动」会同时覆盖 VPS / 边缘加速 / POP / IP / 带宽，无需再逐条勾选。
          </div>
          <div v-if="selectedEventMetas.length" class="event-guidance-list">
            <div v-for="event in selectedEventMetas" :key="event.value" class="event-guidance">
              <el-tag size="small" type="warning">{{ event.label }}</el-tag>
              <span>{{ event.description }}</span>
              <div v-if="event.covers?.length" class="event-template-hint">
                同时覆盖：{{ event.covers.map((item) => item.label).join('、') }}
              </div>
              <div class="event-template-hint">
                默认标题：<code>{{ event.default_title_template }}</code>
              </div>
            </div>
          </div>
        </el-form-item>
        <el-form-item label="动作筛选">
          <div class="event-action-filters">
            <div v-for="event in selectedEventMetas" :key="event.value" class="event-action-filter">
              <div class="event-action-label">
                <el-tag size="small" type="warning">{{ event.label }}</el-tag>
                <span>留空表示接收该事件的全部动作</span>
              </div>
              <el-select
                v-model="ruleForm.event_action_filters[event.value]"
                multiple
                clearable
                collapse-tags
                collapse-tags-tooltip
                style="width:100%"
                :disabled="!event.actions?.length"
                :placeholder="`选择“${event.label}”需要推送的动作`"
              >
                <el-option
                  v-for="action in event.actions || []"
                  :key="action.value"
                  :label="action.label"
                  :value="action.value"
                />
              </el-select>
            </div>
            <span v-if="!selectedEventMetas.length" class="muted">请先选择触发事件。</span>
          </div>
          <div class="form-tip">例如只接收“审批通过”“支付成功”等关键动作，避免无关变更打扰。</div>
        </el-form-item>
        <el-form-item label="推送通道">
          <el-select v-model="ruleForm.channels" multiple style="width:100%" placeholder="选择 IM 通道">
            <el-option v-for="c in allChannels" :key="c.id" :label="`${c.name}（${c.channel_type_label}）`" :value="c.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="通知格式">
          <el-radio-group v-model="ruleForm.format_mode">
            <el-radio-button v-for="mode in formatModes" :key="mode.value" :value="mode.value">{{ mode.label }}</el-radio-button>
          </el-radio-group>
          <div class="form-tip">
            精简格式适合告警群；详细格式会自动包含具体触发事件、触发前原始情况、本次变更、变更后情况、操作人员、操作内容和触发时间。
          </div>
        </el-form-item>
        <el-form-item label="标题模板">
          <el-input
            v-model="ruleForm.title_template"
            :placeholder="titleTemplateHint"
            maxlength="256"
            show-word-limit
          />
          <div class="form-tip">
            留空时会根据实际命中的事件采用对应默认标题；多事件规则建议使用通用变量，例如
            <code>【{event_label}】{action_label} · {target_name}</code>。
          </div>
        </el-form-item>
        <el-form-item label="正文模板">
          <el-input
            v-model="ruleForm.template"
            type="textarea"
            :rows="7"
            :placeholder="bodyTemplateHint"
          />
          <div class="template-actions">
            <span class="form-tip">模板变量使用 <code>{变量名}</code>；敏感字段会在后端自动脱敏。</span>
            <el-button size="small" plain @click="applyAuditTemplate">填入完整审计正文</el-button>
          </div>
          <div v-if="selectedEventMetas.length" class="form-tip">
            <template v-for="event in selectedEventMetas" :key="event.value">
              <el-tag size="small" type="warning">{{ event.label }}</el-tag>
              <span> {{ event.format_guidance }} </span>
            </template>
          </div>
          <div v-if="templateVariables.length" class="variable-list">
            <el-button
              v-for="variable in templateVariables"
              :key="variable.key"
              size="small"
              text
              class="variable-chip"
              :title="variable.description"
              @click="appendTemplateVariable(variable.key)"
            >
              {{ '{' + variable.key + '}' }} · {{ variable.label }}
            </el-button>
          </div>
        </el-form-item>
        <el-form-item label="重复抑制">
          <el-input-number v-model="ruleForm.dedupe_seconds" :min="0" :max="86400" style="width:180px" />
          <span class="inline-tip">秒，填 0 表示不抑制。同一事件、对象和动作在窗口内只发送一次。</span>
        </el-form-item>
        <el-form-item label="启用"><el-switch v-model="ruleForm.enabled" /></el-form-item>
        <el-form-item label="备注"><el-input v-model="ruleForm.remark" /></el-form-item>
        <el-form-item label="格式预览">
          <div class="preview-box" v-loading="previewLoading">
            <div class="preview-toolbar">
              <span>使用所选事件的示例数据渲染，不会实际发送消息。</span>
              <el-select
                v-if="selectedEventMetas.length > 1"
                v-model="previewEvent"
                size="small"
                style="width:180px"
                @change="previewResult = null"
              >
                <el-option v-for="event in selectedEventMetas" :key="event.value" :label="event.label" :value="event.value" />
              </el-select>
              <el-button size="small" type="primary" plain @click="previewRule">刷新预览</el-button>
            </div>
            <template v-if="previewResult">
              <div class="preview-title">{{ previewResult.title || '（无标题）' }}</div>
              <pre class="preview-content">{{ previewResult.content || '（无正文）' }}</pre>
            </template>
            <span v-else class="muted">点击“刷新预览”查看通知效果。</span>
          </div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="ruleDialog = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="saveRule">保存</el-button>
      </template>
    </el-dialog>

    <!-- 手动触发 -->
    <el-dialog v-model="dispatchDialog" title="手动触发通知" width="520px">
      <el-form :model="dispatchForm" label-width="80px">
        <el-form-item label="事件">
          <el-select v-model="dispatchForm.event" style="width:100%">
            <el-option
              v-for="event in dispatchForm.events"
              :key="event.value"
              :label="event.label"
              :value="event.value"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="标题"><el-input v-model="dispatchForm.title" /></el-form-item>
        <el-form-item label="内容"><el-input v-model="dispatchForm.content" type="textarea" :rows="4" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dispatchDialog = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="doDispatch">触发</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useAuthStore } from '@/stores/auth'
import {
  getIMChannels, getIMChannelTypes, createIMChannel, updateIMChannel, deleteIMChannel,
  testIMChannel, sendIMChannel,
  getNotificationRules, getNotificationEvents, createNotificationRule, updateNotificationRule,
  deleteNotificationRule, previewNotification, dispatchNotification, testNotificationEmail,
  getIMLogs,
} from '@/api/system'

const authStore = useAuthStore()
const canManage = computed(() => authStore.hasPermission('system.notify.manage') || authStore.hasPermission('system.manage') || authStore.user?.is_superuser)

const activeTab = ref('channels')
const emailTestTo = ref('')
const emailTesting = ref(false)

async function sendTestEmail() {
  emailTesting.value = true
  try {
    const result = await testNotificationEmail({ to: emailTestTo.value })
    const hostHint = result.smtp_host ? `（${result.smtp_host}:${result.smtp_port}）` : ''
    ElMessage.success(`已投递：${(result.recipients || []).join(', ') || '成功'}${hostHint}`)
  } catch (e) {
    const payload = e.response?.data || {}
    ElMessage.error(payload.detail || payload.message || '测试邮件发送失败，请检查邮件服务器地址、端口和账号')
  } finally {
    emailTesting.value = false
  }
}
const typeTagMap = { telegram: 'primary', wecom: 'success', wecom_webhook: 'success', wecom_kf: 'success', wecom_aibot: 'success', dingtalk: 'warning', webhook: 'info' }
const testTagMap = { success: 'success', failed: 'danger', never: 'info' }

const channelTypes = ref([])
const events = ref([])

// ── 通道 ──
const channels = ref([])
const allChannels = ref([])
const channelLoading = ref(false)
const channelFilter = reactive({ channel_type: '' })
const channelDialog = ref(false)
const channelForm = reactive({})
const saving = ref(false)

function defaultChannel() {
  return {
    id: null, name: '', channel_type: 'telegram', enabled: true,
    webhook_url: '', secret: '', bot_token: '',
    corp_id: '', agent_id: '', corp_secret: '', wecom_secret_env: '',
    wecom_kf_id: '', wecom_kf_url: '', wecom_aibot_id: '', wecom_aibot_secret: '',
    default_target: '', recipients: [], remark: '',
  }
}

const targetPlaceholder = computed(() => {
  const m = {
    telegram: 'chat_id', wecom: '成员账号 touser',
    wecom_webhook: '群机器人无需接收标识',
    wecom_kf: '客户 external_userid', dingtalk: '手机号', webhook: '目标标识(可选)',
    wecom_aibot: '已会话成员 UserID',
  }
  return m[channelForm.channel_type] || '接收标识'
})

async function loadChannels() {
  channelLoading.value = true
  try {
    const params = {}
    if (channelFilter.channel_type) params.channel_type = channelFilter.channel_type
    const res = await getIMChannels(params)
    channels.value = res.results || res
  } finally {
    channelLoading.value = false
  }
}

async function loadAllChannels() {
  const res = await getIMChannels({ page_size: 200 })
  allChannels.value = res.results || res
}

function openChannelDialog(row) {
  Object.assign(channelForm, defaultChannel())
  if (row) {
    Object.assign(channelForm, {
      ...row,
      bot_token: '', secret: '', corp_secret: '', wecom_aibot_secret: '',
      recipients: JSON.parse(JSON.stringify(row.recipients || [])),
    })
  }
  channelDialog.value = true
}

async function saveChannel() {
  if (!channelForm.name || !channelForm.channel_type) {
    ElMessage.warning('请填写名称与通道类型')
    return
  }
  saving.value = true
  try {
    const payload = { ...channelForm }
    if (payload.id) {
      // 不回显的敏感字段，空字符串则不提交（后端会保留原值）
      ;['bot_token', 'secret', 'corp_secret', 'wecom_aibot_secret'].forEach((f) => { if (!payload[f]) delete payload[f] })
      await updateIMChannel(payload.id, payload)
    } else {
      await createIMChannel(payload)
    }
    ElMessage.success('已保存')
    channelDialog.value = false
    await Promise.all([loadChannels(), loadAllChannels()])
  } catch (e) {
    const data = e?.response?.data
    const validationMessage = data && typeof data === 'object'
      ? Object.values(data).flat().find((value) => typeof value === 'string')
      : ''
    ElMessage.error(validationMessage || data?.detail || '保存失败')
  } finally {
    saving.value = false
  }
}

async function toggleChannel(row) {
  try {
    await updateIMChannel(row.id, { enabled: row.enabled })
  } catch (e) {
    row.enabled = !row.enabled
    ElMessage.error('更新失败')
  }
}

async function removeChannel(row) {
  await ElMessageBox.confirm(`确认删除通道「${row.name}」?`, '提示', { type: 'warning' })
  await deleteIMChannel(row.id)
  ElMessage.success('已删除')
  await Promise.all([loadChannels(), loadAllChannels()])
}

async function testChannel(row) {
  const loading = ElMessage({ message: '正在发送测试消息...', type: 'info', duration: 0 })
  try {
    const res = await testIMChannel(row.id)
    loading.close()
    if (res.success) ElMessage.success(res.queued ? `测试消息已入队（${res.queued} 条），等待机器人投递` : `测试成功（发送 ${res.sent} 条）`)
    else ElMessage.error(`测试失败：${res.last_response || '无可用接收目标'}`)
    await loadChannels()
  } catch (e) {
    loading.close()
    ElMessage.error('测试请求失败')
  }
}

// ── 发送自定义消息 ──
const sendDialog = ref(false)
const sendForm = reactive({ id: null, channelName: '', title: 'ISP Manager 通知', content: '' })
function openSendDialog(row) {
  Object.assign(sendForm, { id: row.id, channelName: row.name, title: 'ISP Manager 通知', content: '' })
  sendDialog.value = true
}
async function doSend() {
  if (!sendForm.content) { ElMessage.warning('请填写内容'); return }
  saving.value = true
  try {
    const res = await sendIMChannel(sendForm.id, { title: sendForm.title, content: sendForm.content })
    ElMessage[res.failed ? 'warning' : 'success'](res.queued ? `已入队 ${res.queued} 条，等待机器人投递` : `发送完成：成功 ${res.sent}，失败 ${res.failed}`)
    sendDialog.value = false
  } catch (e) {
    ElMessage.error('发送失败')
  } finally {
    saving.value = false
  }
}

// ── 规则 ──
const rules = ref([])
const ruleLoading = ref(false)
const ruleFilter = reactive({ event: '' })
const ruleDialog = ref(false)
const ruleForm = reactive({})
const formatModes = [
  { value: 'compact', label: '精简格式' },
  { value: 'detailed', label: '详细格式' },
  { value: 'custom', label: '自定义格式' },
]
const previewLoading = ref(false)
const previewResult = ref(null)
const previewEvent = ref('')
const selectedEventMetas = computed(() => {
  const selected = new Set(ruleForm.events || [])
  return events.value.filter((item) => selected.has(item.value))
})
const titleTemplateHint = computed(() => {
  if (selectedEventMetas.value.length === 1) {
    return selectedEventMetas.value[0].default_title_template || '留空使用该事件的默认标题格式'
  }
  return '留空时按实际命中的事件使用对应默认标题'
})
const bodyTemplateHint = computed(() => (
  ruleForm.format_mode === 'custom'
    ? '自定义格式必须填写；可点击“填入完整审计正文”后再按需调整'
    : '留空使用详细/精简默认格式；填写后将使用自定义正文'
))
const templateVariables = computed(() => {
  const seen = new Set()
  return selectedEventMetas.value.flatMap((event) => event.variables || []).filter((variable) => {
    if (seen.has(variable.key)) return false
    seen.add(variable.key)
    return true
  })
})

function defaultRule() {
  return {
    id: null,
    name: '',
    events: ['resource_change'],
    enabled: true,
    channels: [],
    action_filters: [],
    event_action_filters: { resource_change: [] },
    format_mode: 'detailed',
    title_template: '',
    template: '',
    dedupe_seconds: 0,
    remark: '',
  }
}
async function loadRules() {
  ruleLoading.value = true
  try {
    const params = {}
    if (ruleFilter.event) params.event = ruleFilter.event
    const res = await getNotificationRules(params)
    rules.value = res.results || res
  } finally {
    ruleLoading.value = false
  }
}
function openRuleDialog(row) {
  Object.assign(ruleForm, defaultRule())
  if (row) {
    const rowEvents = row.events?.length ? [...row.events] : [row.event]
    const eventActionFilters = JSON.parse(JSON.stringify(row.event_action_filters || {}))
    if (!eventActionFilters[rowEvents[0]]) {
      eventActionFilters[rowEvents[0]] = [...(row.action_filters || [])]
    }
    Object.assign(ruleForm, {
      id: row.id, name: row.name, events: rowEvents, enabled: row.enabled,
      channels: (row.channel_names || []).map((c) => c.id),
      action_filters: [...(row.action_filters || [])],
      event_action_filters: eventActionFilters,
      format_mode: row.format_mode || 'detailed',
      title_template: row.title_template || '',
      template: row.template || '',
      dedupe_seconds: row.dedupe_seconds || 0,
      remark: row.remark || '',
    })
  }
  normalizeEventActionFilters()
  previewEvent.value = ruleForm.events[0] || ''
  previewResult.value = null
  ruleDialog.value = true
}
function normalizeEventActionFilters() {
  const existing = ruleForm.event_action_filters || {}
  const normalized = {}
  for (const eventValue of ruleForm.events || []) {
    const meta = events.value.find((event) => event.value === eventValue)
    const allowed = new Set((meta?.actions || []).map((action) => action.value))
    const values = Array.isArray(existing[eventValue]) ? existing[eventValue] : []
    normalized[eventValue] = values.filter((action) => allowed.has(action))
  }
  ruleForm.event_action_filters = normalized
  ruleForm.action_filters = [...(normalized[ruleForm.events?.[0]] || [])]
}
function onRuleEventsChange() {
  normalizeEventActionFilters()
  if (!ruleForm.events.includes(previewEvent.value)) {
    previewEvent.value = ruleForm.events[0] || ''
  }
  previewResult.value = null
}
function appendTemplateVariable(key) {
  ruleForm.template = `${ruleForm.template || ''}{${key}}`
}
function applyAuditTemplate() {
  const template = selectedEventMetas.value[0]?.recommended_body_template
  if (!template) {
    ElMessage.warning('请先选择触发事件')
    return
  }
  ruleForm.template = template
  ruleForm.format_mode = 'custom'
  previewResult.value = null
}
function ruleEventLabels(row) {
  if (row.event_labels?.length) return row.event_labels
  const event = events.value.find((item) => item.value === row.event)
  return event ? [{ value: event.value, label: event.label }] : []
}
function actionFilterLabels(row) {
  const eventFilters = row.event_action_filters || {}
  return ruleEventLabels(row).map((event) => {
    const meta = events.value.find((item) => item.value === event.value)
    const labels = new Map((meta?.actions || []).map((item) => [item.value, item.label]))
    const actions = eventFilters[event.value] ?? (
      event.value === row.event ? row.action_filters : []
    )
    if (!actions?.length) return ''
    return `${event.label}：${actions.map((action) => labels.get(action) || action).join('、')}`
  }).filter(Boolean).join('；')
}
async function previewRule() {
  const event = previewEvent.value || ruleForm.events?.[0]
  if (!event) {
    ElMessage.warning('请先选择触发事件')
    return
  }
  const eventMeta = events.value.find((item) => item.value === event)
  previewLoading.value = true
  try {
    const res = await previewNotification({
      event,
      title_template: ruleForm.title_template || '',
      template: ruleForm.template || '',
      format_mode: ruleForm.format_mode || 'detailed',
      context: eventMeta?.sample_context || {},
    })
    previewResult.value = res
  } catch (e) {
    previewResult.value = null
    ElMessage.error(e?.response?.data?.template?.[0] || e?.response?.data?.detail || '预览失败，请检查模板格式')
  } finally {
    previewLoading.value = false
  }
}
async function saveRule() {
  if (!ruleForm.name || !ruleForm.events?.length) { ElMessage.warning('请填写名称并至少选择一个触发事件'); return }
  if (ruleForm.format_mode === 'custom' && !ruleForm.template?.trim()) {
    ElMessage.warning('自定义格式必须填写正文模板')
    return
  }
  normalizeEventActionFilters()
  const payload = {
    ...ruleForm,
    event: ruleForm.events[0],
    events: [...ruleForm.events],
    event_action_filters: JSON.parse(JSON.stringify(ruleForm.event_action_filters || {})),
    action_filters: [...(ruleForm.event_action_filters?.[ruleForm.events[0]] || [])],
  }
  saving.value = true
  try {
    if (ruleForm.id) await updateNotificationRule(ruleForm.id, payload)
    else await createNotificationRule(payload)
    ElMessage.success('已保存')
    ruleDialog.value = false
    await loadRules()
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '保存失败')
  } finally {
    saving.value = false
  }
}
async function toggleRule(row) {
  try {
    await updateNotificationRule(row.id, { enabled: row.enabled })
  } catch (e) {
    row.enabled = !row.enabled
    ElMessage.error('更新失败')
  }
}
async function removeRule(row) {
  await ElMessageBox.confirm(`确认删除规则「${row.name}」?`, '提示', { type: 'warning' })
  await deleteNotificationRule(row.id)
  ElMessage.success('已删除')
  await loadRules()
}

// ── 手动触发 ──
const dispatchDialog = ref(false)
const dispatchForm = reactive({ id: null, event: '', events: [], title: 'ISP Manager 通知', content: '' })
function openDispatchDialog(row) {
  const ruleEvents = ruleEventLabels(row)
  Object.assign(dispatchForm, {
    id: row.id,
    event: ruleEvents[0]?.value || row.event,
    events: ruleEvents,
    title: 'ISP Manager 通知',
    content: '',
  })
  dispatchDialog.value = true
}
function dispatchSkipText(res) {
  const reasons = (res.skip_reasons || []).map((item) => item.detail || item.reason).filter(Boolean)
  return reasons.length ? reasons.join('；') : '没有命中已启用且绑定通道的规则'
}
async function doDispatch() {
  if (!dispatchForm.content) { ElMessage.warning('请填写内容'); return }
  saving.value = true
  try {
    const res = await dispatchNotification({
      event: dispatchForm.event,
      title: dispatchForm.title,
      content: dispatchForm.content,
      action: 'custom',
      rule_id: dispatchForm.id || undefined,
    })
    if (res.disabled) {
      ElMessage.warning('IM 通知全局开关已关闭，请到「系统设置 → 对内通知」打开')
    } else if (!res.dispatched) {
      ElMessage.warning(`未发送：${dispatchSkipText(res)}`)
    } else {
      const failed = (res.results || []).reduce((sum, item) => sum + (item.failed || 0), 0)
      const queued = (res.results || []).reduce((sum, item) => sum + (item.queued || 0), 0)
      ElMessage[failed ? 'warning' : 'success'](
        queued
          ? `已入队 ${queued} 条，等待机器人投递`
          : `已分发到 ${res.dispatched} 个通道${failed ? `，失败 ${failed}` : ''}`,
      )
    }
    dispatchDialog.value = false
    if (activeTab.value === 'logs') loadLogs()
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '触发失败')
  } finally {
    saving.value = false
  }
}

// ── 日志 ──
const logs = ref([])
const logLoading = ref(false)
const logFilter = reactive({ success: '', event: '' })
async function loadLogs() {
  logLoading.value = true
  try {
    const params = {}
    if (logFilter.success !== '') params.success = logFilter.success
    if (logFilter.event) params.event = logFilter.event
    const res = await getIMLogs(params)
    logs.value = res.results || res
  } finally {
    logLoading.value = false
  }
}

function formatTime(t) {
  if (!t) return ''
  return new Date(t).toLocaleString('zh-CN', { hour12: false })
}

watch(activeTab, (tab) => {
  if (tab === 'rules' && !rules.value.length) loadRules()
  if (tab === 'logs') loadLogs()
})

onMounted(async () => {
  const [types, evs] = await Promise.all([
    getIMChannelTypes().catch(() => []),
    getNotificationEvents().catch(() => []),
  ])
  channelTypes.value = types
  events.value = evs
  await Promise.all([loadChannels(), loadAllChannels()])
})
</script>

<style scoped>
.page-container { min-height: 100%; }
.page-header { margin-bottom: 16px; }
.page-title { font-size: 18px; font-weight: 700; color: #1a1a2e; margin: 0; }
.hint { font-size: 12px; color: #909399; margin-top: 6px; }
.toolbar { display: flex; gap: 10px; margin-bottom: 12px; }
.muted { font-size: 12px; color: #909399; margin-top: 2px; }
.form-tip { font-size: 12px; line-height: 18px; color: #909399; margin-top: 4px; }
.recipient-row { display: flex; gap: 8px; align-items: center; margin-bottom: 8px; }
.inline-tip { margin-left: 8px; font-size: 12px; color: #909399; line-height: 18px; }
.event-guidance-list { width: 100%; margin-top: 8px; }
.event-guidance { padding: 7px 9px; margin-top: 5px; border-left: 3px solid #e6a23c; background: #fdf6ec; color: #606266; font-size: 12px; line-height: 19px; }
.event-guidance .el-tag { margin-right: 6px; }
.event-template-hint { margin-top: 2px; color: #909399; }
.event-action-filters { width: 100%; display: flex; flex-direction: column; gap: 10px; }
.event-action-filter { padding: 9px; border: 1px solid #ebeef5; border-radius: 4px; background: #fafafa; }
.event-action-label { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; color: #909399; font-size: 12px; }
.template-actions { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.variable-list { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 8px; max-height: 144px; overflow-y: auto; }
.variable-chip { margin: 0; color: #409eff; }
.preview-box { width: 100%; padding: 10px 12px; border: 1px solid #e4e7ed; border-radius: 4px; background: #fafafa; }
.preview-toolbar { display: flex; justify-content: space-between; align-items: center; gap: 12px; margin-bottom: 8px; font-size: 12px; color: #909399; }
.preview-title { font-weight: 600; color: #303133; margin-bottom: 6px; }
.preview-content { margin: 0; white-space: pre-wrap; word-break: break-word; color: #606266; font: 12px/1.6 inherit; }
</style>
