<template>
  <div class="page-container">
    <div v-show="!blockDetailVisible && !logDrawerVisible">
    <div class="page-header">
      <h2 class="page-title">IP资源管理</h2>
      <el-text type="info" size="small" style="margin-left:8px;">仅管理 POP 业务 IP；隧道段请到「隧道IP段」</el-text>
      <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;">
        <ExcelImportExport
          :export-url="excelUrls.exportUrl"
          :template-url="excelUrls.templateUrl"
          :import-url="excelUrls.importUrl"
          :filename="excelUrls.filename"
          :params="excelParams"
          @imported="onExcelImported"
        />
        <el-button :type="activeTab==='blocks'?'primary':''" @click="activeTab='blocks'">IP地址块</el-button>
        <el-button :type="activeTab==='addresses'?'primary':''" @click="activeTab='addresses'">IP地址列表</el-button>
      </div>
    </div>

    <!-- ═══════════════ IP地址块 ═══════════════ -->
    <template v-if="activeTab==='blocks'">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;flex-wrap:wrap;gap:8px;">
        <el-form :model="blockSearch" inline style="margin:0;">
          <el-form-item label="资源位置">
            <ResourceLocationFilter
              :location="location"
              :regions="regions"
              :zones="filteredZones"
              :pop-points="filteredPopPoints"
              :pop-label="popLabel"
              :loading="loadingLocation"
              @update="applyLocation"
              @change="reloadBlockLocation"
            />
          </el-form-item>
          <el-form-item label="IP版本">
            <el-select v-model="blockSearch.ip_version" clearable placeholder="全部" style="width:90px" @change="loadBlocks">
              <el-option label="IPv4" value="v4"/><el-option label="IPv6" value="v6"/>
            </el-select>
          </el-form-item>
          <el-form-item label="网络类型">
            <el-select v-model="blockSearch.network_type" clearable placeholder="全部" style="width:120px" @change="loadBlocks">
              <el-option v-for="t in networkTypeOptions" :key="t.code" :label="t.name" :value="t.code"/>
            </el-select>
          </el-form-item>
          <el-form-item label="ASN/企业性质">
            <el-select v-model="blockSearch.asn_nature" clearable placeholder="全部" style="width:120px" @change="loadBlocks">
              <el-option v-for="t in asnNatureOptions" :key="t.value" :label="t.label" :value="t.value"/>
            </el-select>
          </el-form-item>
          <el-form-item>
            <el-input v-model="blockSearch.search" placeholder="CIDR / 描述" clearable style="width:160px" @keyup.enter="loadBlocks"/>
          </el-form-item>
          <el-form-item>
            <el-button type="primary" :icon="Search" @click="loadBlocks">搜索</el-button>
          </el-form-item>
        </el-form>
        <el-button type="primary" :icon="Plus" @click="openBlockDialog()">新增IP块</el-button>
      </div>

      <el-card shadow="never">
        <el-table :data="groupedBlocks" v-loading="loadingBlocks" stripe border :span-method="locationSpanMethod">
          <el-table-column label="CIDR" width="220">
            <template #default="{row}">
              <ResourceTableGroupLabel v-if="isLocationGroup(row)" :row="row" />
              <template v-else>
                <span>{{ row.cidr }}</span>
                <el-tag v-if="(row.merged_count || 1) > 1" size="small" type="info" effect="plain" style="margin-left:6px;">
                  合并 {{ row.merged_count }} 条
                </el-tag>
              </template>
            </template>
          </el-table-column>
          <el-table-column label="版本" width="72">
            <template #default="{row}">
              <el-tag size="small" :type="row.ip_version==='v6'?'warning':'primary'">
                {{ row.ip_version==='v6'?'IPv6':'IPv4' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="POP节点" width="160">
            <template #default="{row}">
              <div>{{ getPopName(row.pop_point) }}</div>
              <el-tag v-if="row.ip_delivery_mode === 'nat'" size="small" type="warning">NAT 交付</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="掩码 / 网关" min-width="160">
            <template #default="{row}">
              <div>{{ row.netmask || (row.prefix_len != null ? '/' + row.prefix_len : '-') }}</div>
              <div style="color:#909399;font-size:12px;">{{ row.gateway || '-' }}</div>
            </template>
          </el-table-column>
          <el-table-column label="区域 / 城市" min-width="150">
            <template #default="{ row }">
              {{ [row.region_name, row.country_name, row.city_name].filter(Boolean).join(' / ') || '-' }}
            </template>
          </el-table-column>
          <el-table-column prop="ip_attribute_label" label="IP属性" width="90"/>
          <el-table-column label="网络类型" width="100">
            <template #default="{row}">
              <el-tag size="small" :type="row.network_type === 'home_broadband' ? 'warning' : 'primary'">
                {{ row.network_type_label || row.network_type || '-' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="ASN/企业性质" width="120">
            <template #default="{row}">
              <el-tag size="small" effect="plain" type="info">{{ row.asn_nature_label || '-' }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="IP使用" min-width="250">
            <template #default="{row}">
              <div v-if="row.ip_stats?.total > 0">
                <div style="display:flex;align-items:center;gap:8px;margin-bottom:4px;">
                  <el-progress :percentage="calcBlockPct(row)" :stroke-width="8" :color="getBlockColor(row)" style="flex:1;"/>
                  <span style="font-size:11px;color:#909399;white-space:nowrap;">
                    {{ row.ip_stats?.assigned||0 }}/{{ row.ip_stats?.total }}
                  </span>
                </div>
                <div style="display:flex;gap:3px;flex-wrap:wrap;">
                  <el-tag size="small" type="success">可用 {{ row.ip_stats?.available||0 }}</el-tag>
                  <el-tag size="small" type="primary">已分配 {{ row.ip_stats?.assigned||0 }}</el-tag>
                  <el-tag size="small" type="warning" v-if="row.ip_stats?.reserved">预留 {{ row.ip_stats.reserved }}</el-tag>
                  <el-tag size="small" type="danger" v-if="row.ip_stats?.blocked">封禁 {{ row.ip_stats.blocked }}</el-tag>
                </div>
              </div>
              <el-button v-else size="small" type="warning" :icon="MagicStick" @click="triggerGenerate(row)">解析IP</el-button>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="205" fixed="right">
            <template #default="{row}">
              <el-button link type="primary" size="small" @click="viewBlockDetail(row)">
                <el-icon><List/></el-icon> IP列表
              </el-button>
              <el-button link size="small" @click="triggerGenerate(row)" title="解析/重新解析IP">
                <el-icon><MagicStick/></el-icon>
              </el-button>
              <el-button link type="primary" size="small" @click="openBlockDialog(row)">编辑</el-button>
              <el-button link type="danger" size="small" @click="handleDeleteBlock(row)">删除</el-button>
            </template>
          </el-table-column>
        </el-table>
        <el-pagination v-model:current-page="blockPage" :total="blockTotal"
          layout="total,prev,pager,next" class="pagination" @change="loadBlocks"/>
      </el-card>
    </template>

    <!-- ═══════════════ IP地址列表（全局）═══════════════ -->
    <template v-if="activeTab==='addresses'">
      <el-card shadow="never" class="search-card">
        <el-form :model="addrSearch" inline>
          <el-form-item label="资源位置">
            <ResourceLocationFilter
              :location="location"
              :regions="regions"
              :zones="filteredZones"
              :pop-points="filteredPopPoints"
              :pop-label="popLabel"
              :loading="loadingLocation"
              @update="applyLocation"
              @change="reloadAddressLocation"
            />
          </el-form-item>
          <el-form-item label="IP块">
            <el-select v-model="addrSearch.ip_block" clearable filterable placeholder="全部" style="width:170px">
              <el-option v-for="b in blocks" :key="b.id" :label="b.cidr" :value="b.id"/>
            </el-select>
          </el-form-item>
          <el-form-item label="状态">
            <el-select v-model="addrSearch.status" clearable placeholder="全部" style="width:110px">
              <el-option v-for="s in statusOptions" :key="s.value" :label="s.label" :value="s.value"/>
            </el-select>
          </el-form-item>
          <el-form-item>
            <el-input v-model="addrSearch.search" placeholder="IP地址" clearable style="width:150px" @keyup.enter="loadAddresses"/>
          </el-form-item>
        <el-form-item>
          <el-button type="primary" :icon="Search" @click="loadAddresses">搜索</el-button>
          <el-button @click="resetAddrSearch">重置</el-button>
          <el-button type="primary" :icon="Plus" @click="openAddIp">录入 IP</el-button>
        </el-form-item>
        </el-form>
      </el-card>
      <el-card shadow="never" style="margin-top:12px;">
        <el-table :data="groupedAddresses" v-loading="loadingAddresses" stripe border :span-method="locationSpanMethod">
          <el-table-column label="IP地址" width="155">
            <template #default="{ row }">
              <ResourceTableGroupLabel v-if="isLocationGroup(row)" :row="row" />
              <span v-else>{{ row.address }}</span>
            </template>
          </el-table-column>
          <el-table-column label="/掩码" width="68" align="center">
            <template #default="{row}">{{ row.effective_prefix_len ?? row.prefix_len }}</template>
          </el-table-column>
          <el-table-column label="网关" width="140">
            <template #default="{row}">{{ row.effective_gateway || row.gateway || '-' }}</template>
          </el-table-column>
          <el-table-column prop="block_cidr" label="所属IP块" width="155"/>
          <el-table-column label="网络类型" width="100">
            <template #default="{row}">
              <el-tag size="small" :type="row.network_type === 'home_broadband' ? 'warning' : 'primary'">
                {{ row.network_type_label || row.network_type || '-' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="ASN/企业性质" width="120">
            <template #default="{row}">
              <el-tag size="small" effect="plain" type="info">{{ row.asn_nature_label || '-' }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="区域 / POP" min-width="170">
            <template #default="{ row }">
              <div>{{ [row.region_name, row.country_name, row.city_name].filter(Boolean).join(' / ') || '-' }}</div>
              <div style="color:#909399;font-size:12px;">
                {{ row.pop_name || '-' }}
                <el-tag v-if="row.ip_delivery_mode === 'nat'" size="small" type="warning" style="margin-left:4px">NAT 交付</el-tag>
              </div>
            </template>
          </el-table-column>
          <el-table-column prop="status" label="状态" width="98">
            <template #default="{row}">
              <el-tag :type="statusMap[row.status]?.type" size="small">{{ statusMap[row.status]?.label }}</el-tag>
            </template>
          </el-table-column>
          <!-- 分配目标（内联模板，不用defineComponent） -->
          <el-table-column label="分配目标" min-width="190">
            <template #default="{row}">
              <div v-if="row.assigned_vps_info" style="display:flex;align-items:center;gap:4px;flex-wrap:wrap;">
                <el-tag type="primary" size="small">{{ row.assigned_vps_info.vps_no }}</el-tag>
                <span style="font-size:12px;">{{ row.assigned_vps_info.name }}</span>
                <el-tag v-if="row.is_primary" type="success" size="small">主IP</el-tag>
                <span v-if="row.assigned_vps_info.customer_name" style="color:#909399;font-size:12px;">{{ row.assigned_vps_info.customer_name }}</span>
              </div>
              <div v-else-if="row.assigned_edge_info" style="display:flex;align-items:center;gap:4px;flex-wrap:wrap;">
                <el-tag type="success" size="small">{{ row.assigned_edge_info.edge_no }}</el-tag>
                <span style="font-size:12px;">{{ row.assigned_edge_info.name }}</span>
                <el-tag size="small">{{ protocolMap[row.assigned_edge_info.protocol]||row.assigned_edge_info.protocol }}</el-tag>
                <el-tag v-if="row.is_primary" type="success" size="small">主IP</el-tag>
                <span v-if="row.assigned_edge_info.customer_name" style="color:#909399;font-size:12px;">{{ row.assigned_edge_info.customer_name }}</span>
              </div>
              <span v-else style="color:#C0C4CC;font-size:12px;">未分配</span>
            </template>
          </el-table-column>
          <el-table-column label="接口" width="120">
            <template #default="{row}">{{ row.interface_code || '-' }}</template>
          </el-table-column>
          <el-table-column prop="description" label="描述" min-width="110"/>
          <!-- 操作列（直接内联按钮，v-if 控制显示）-->
          <el-table-column label="操作" width="230" fixed="right">
            <template #default="{row}">
              <!-- 分配：可用/预留状态显示 -->
              <el-button
                v-if="['available','reserved'].includes(row.status)"
                link type="primary" size="small"
                @click="openAssign(row,'global')"
              >分配</el-button>
              <!-- 回收：已分配状态显示 -->
              <el-button
                v-if="row.status==='assigned'"
                link type="warning" size="small"
                @click="doRelease(row,'global')"
              >回收</el-button>
              <!-- 状态变更：非系统IP均可 -->
              <el-button
                v-if="row.status!=='system'"
                link size="small"
                @click="openStatusChange(row,'global')"
              >改状态</el-button>
              <!-- 编辑描述 -->
              <el-button
                link size="small"
                @click="openEditDesc(row,'global')"
              >备注</el-button>
              <!-- 日志 -->
              <el-button
                link type="info" size="small" :icon="Clock"
                @click="openIPLog(row,'global')"
              >日志</el-button>
            </template>
          </el-table-column>
        </el-table>
        <el-pagination v-model:current-page="addrPage" :total="addrTotal"
          layout="total,prev,pager,next" class="pagination" @change="loadAddresses"/>
      </el-card>
    </template>
    </div><!-- /列表区（整页详情时隐藏） -->

    <!-- ═══════════════ IP块 创建/编辑 ═══════════════ -->
    <el-dialog v-model="blockDialogVisible" :title="editBlock?'编辑IP地址块':'新增IP地址块'" width="520px" destroy-on-close>
      <el-form ref="blockFormRef" :model="blockForm" :rules="blockRules" label-width="100px">
        <el-form-item label="CIDR" prop="cidr">
          <el-input v-model="blockForm.cidr" placeholder="如 103.1.2.0/24 或 2001:db8::/48"/>
        </el-form-item>
        <el-form-item label="IP版本" prop="ip_version">
          <el-radio-group v-model="blockForm.ip_version">
            <el-radio value="v4">IPv4</el-radio>
            <el-radio value="v6">IPv6</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="POP节点" prop="pop_point">
          <el-select v-model="blockForm.pop_point" filterable placeholder="选择POP节点" style="width:100%">
            <el-option v-for="p in popPoints" :key="p.id" :label="popLabel(p)" :value="p.id"/>
          </el-select>
        </el-form-item>
        <el-form-item label="运营商">
          <el-select v-model="blockForm.carrier" filterable clearable placeholder="可选" style="width:100%">
            <el-option v-for="c in carriers" :key="c.id" :label="`${c.code||''} - ${c.name}`" :value="c.id"/>
          </el-select>
        </el-form-item>
        <el-form-item label="IP属性">
          <el-select v-model="blockForm.ip_attribute" clearable style="width:100%">
            <el-option v-for="t in ipAttributeOptions" :key="t.value" :label="t.label" :value="t.value"/>
          </el-select>
        </el-form-item>
        <el-form-item label="网络类型" prop="network_type">
          <div style="display:flex;gap:8px;width:100%;">
            <el-select v-model="blockForm.network_type" filterable style="flex:1;">
              <el-option v-for="t in networkTypeSelectOptions" :key="t.code" :label="t.name" :value="t.code"/>
            </el-select>
            <el-button @click="openNetworkTypeDialog">管理</el-button>
          </div>
        </el-form-item>
        <el-form-item label="ASN/企业性质" prop="asn_nature">
          <el-select v-model="blockForm.asn_nature" style="width:100%">
            <el-option v-for="t in asnNatureOptions" :key="t.value" :label="t.label" :value="t.value"/>
          </el-select>
        </el-form-item>
        <el-form-item label="本段网关">
          <el-input v-model="blockForm.gateway" placeholder="如 103.1.2.1，供后续 VPS 网卡配置"/>
        </el-form-item>
        <el-form-item label="描述"><el-input v-model="blockForm.description"/></el-form-item>
        <el-form-item label="状态">
          <el-switch v-model="blockForm.is_active" active-text="启用" inactive-text="停用"/>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="blockDialogVisible=false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="handleSaveBlock">
          {{ editBlock?'保存':'创建并解析IP' }}
        </el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="networkTypeDialogVisible" title="管理网络类型" width="560px" destroy-on-close>
      <div style="display:flex;gap:8px;margin-bottom:12px;">
        <el-input v-model="networkTypeForm.code" placeholder="代码，如 idc" style="width:140px"/>
        <el-input v-model="networkTypeForm.name" placeholder="名称，如 IDC" style="flex:1"/>
        <el-input-number v-model="networkTypeForm.sort_order" :min="0" controls-position="right" style="width:110px"/>
        <el-button type="primary" :loading="networkTypeSaving" @click="saveNetworkType">新增</el-button>
      </div>
      <el-table :data="networkTypeAll" size="small" border>
        <el-table-column prop="code" label="代码" width="140"/>
        <el-table-column label="名称">
          <template #default="{row}">
            <el-input v-model="row.name" size="small" @change="updateNetworkTypeRow(row)"/>
          </template>
        </el-table-column>
        <el-table-column label="排序" width="90">
          <template #default="{row}">
            <el-input-number v-model="row.sort_order" :min="0" size="small" controls-position="right" style="width:80px" @change="updateNetworkTypeRow(row)"/>
          </template>
        </el-table-column>
        <el-table-column label="启用" width="70">
          <template #default="{row}">
            <el-switch v-model="row.is_active" @change="updateNetworkTypeRow(row)"/>
          </template>
        </el-table-column>
        <el-table-column label="" width="70">
          <template #default="{row}">
            <el-button link type="danger" size="small" @click="removeNetworkType(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-dialog>

    <!-- ═══════════════ IP解析预览 ═══════════════ -->
    <el-dialog v-model="generateDialogVisible" title="解析IP地址" width="560px" :close-on-click-modal="false">
      <div v-if="previewLoading" style="text-align:center;padding:40px 0;">
        <el-icon class="is-loading" :size="36"><Loading/></el-icon>
        <p style="color:#606266;margin-top:12px;">正在分析 {{ currentBlock?.cidr }}…</p>
      </div>
      <template v-else-if="previewData && !generateResult">
        <el-descriptions :column="2" border size="small" style="margin-bottom:16px;">
          <el-descriptions-item label="CIDR"><el-tag>{{ previewData.cidr }}</el-tag></el-descriptions-item>
          <el-descriptions-item label="前缀长度">/ {{ previewData.prefix_len }}</el-descriptions-item>
          <el-descriptions-item label="可用主机数">
            <el-text type="success" bold>{{ previewData.usable_hosts?.toLocaleString() }}</el-text>
          </el-descriptions-item>
          <el-descriptions-item label="已在库中">
            <el-text :type="previewData.already_in_db>0?'warning':'info'">{{ previewData.already_in_db }}</el-text>
          </el-descriptions-item>
          <el-descriptions-item label="本次新增" :span="2">
            <el-text type="primary" bold style="font-size:16px;">{{ previewData.would_create?.toLocaleString() }}</el-text>
            <el-text type="info" style="margin-left:6px;font-size:12px;">条IP记录</el-text>
          </el-descriptions-item>
        </el-descriptions>
        <el-alert v-if="previewData.exceeds_limit" type="error" :closable="false"
          :title="`地址数 ${previewData.usable_hosts?.toLocaleString()} 超过单次上限 ${previewData.limit?.toLocaleString()}，请拆分为更小段`"/>
        <template v-if="!previewData.exceeds_limit && previewData.would_create>0">
          <el-form label-width="120px">
            <el-form-item label="包含网络地址" v-if="previewData.ip_version!=='v6'">
              <el-switch v-model="genOpts.include_network"/>
              <el-text type="info" size="small" style="margin-left:8px;">{{ previewData.network_address }}</el-text>
            </el-form-item>
            <el-form-item label="包含广播地址" v-if="previewData.ip_version!=='v6'&&previewData.prefix_len<31">
              <el-switch v-model="genOpts.include_broadcast"/>
              <el-text type="info" size="small" style="margin-left:8px;">{{ previewData.broadcast_address }}</el-text>
            </el-form-item>
            <el-form-item label="统一描述">
              <el-input v-model="genOpts.description" placeholder="批量设置IP描述（可选）" style="width:260px"/>
            </el-form-item>
          </el-form>
        </template>
        <el-alert v-if="previewData.would_create===0&&!previewData.exceeds_limit"
          type="success" :closable="false" title="所有IP均已在库中，无需重新生成"/>
      </template>
      <!-- 生成结果 -->
      <el-result v-if="generateResult" icon="success"
        :title="`解析完成：${generateResult.cidr}`"
        :sub-title="`新增 ${generateResult.created} 条，跳过 ${generateResult.skipped} 条，库中共 ${generateResult.total_in_db} 条`">
        <template #extra>
          <el-button type="primary" @click="viewBlockAddresses">查看IP列表</el-button>
        </template>
      </el-result>
      <template #footer>
        <el-button @click="closeGenerateDialog">{{ generateResult?'关闭':'取消' }}</el-button>
        <el-button
          v-if="!generateResult && previewData && !previewData.exceeds_limit && previewData.would_create>0"
          type="primary" :loading="generating" @click="doGenerate"
        >确认解析（新增 {{ previewData.would_create?.toLocaleString() }} 条）</el-button>
      </template>
    </el-dialog>

    <!-- ═══════════════ IP分配弹窗 ═══════════════ -->
    <el-dialog v-model="assignDialogVisible" title="分配IP地址" width="660px" destroy-on-close>
      <el-alert type="info" :closable="false" style="margin-bottom:16px;">
        <template #title>
          正在分配：<el-tag type="primary" style="margin:0 4px;">{{ currentIP?.address }}</el-tag>
          当前状态：<el-tag :type="statusMap[currentIP?.status]?.type">{{ statusMap[currentIP?.status]?.label }}</el-tag>
          <el-tag v-if="assignPopId" type="info" size="small" style="margin-left:8px;">
            仅 {{ getPopName(assignPopId) }} 下资源
          </el-tag>
        </template>
      </el-alert>

      <el-form :model="assignForm" label-width="90px">
        <!-- 目标类型 -->
        <el-form-item label="分配至">
          <el-radio-group v-model="assignForm.target_type" @change="assignForm.target_id=null;assignSearch=''">
            <el-radio-button value="vps">
              <el-icon style="margin-right:4px;"><Monitor/></el-icon>VPS主机
            </el-radio-button>
            <el-radio-button value="edge">
              <el-icon style="margin-right:4px;"><Connection/></el-icon>边缘加速
            </el-radio-button>
          </el-radio-group>
        </el-form-item>

        <!-- 搜索框 -->
        <el-form-item label="搜索">
          <el-input
            v-model="assignSearch"
            :placeholder="assignForm.target_type==='vps'?'搜索 VPS 名称/编号/客户':'搜索 边缘节点名称/编号'"
            clearable style="width:100%"
            :prefix-icon="Search"
          />
        </el-form-item>

        <!-- VPS 选择列表 -->
        <el-form-item label="选择VPS" v-if="assignForm.target_type==='vps'">
          <div class="assign-list" v-loading="loadingResources">
            <template v-if="filteredVPSList.length">
              <div
                v-for="v in filteredVPSList" :key="v.id"
                class="assign-item" :class="{selected:assignForm.target_id===v.id}"
                @click="assignForm.target_id=v.id"
              >
                <div class="assign-item-main">
                  <el-tag type="primary" size="small">{{ v.vps_no }}</el-tag>
                  <span class="assign-name">{{ v.name }}</span>
                  <el-tag size="small" :type="vpsStatusTag[v.status]?.type||''">
                    {{ vpsStatusTag[v.status]?.label||v.status }}
                  </el-tag>
                </div>
                <div class="assign-sub">
                  {{ v.vcpu }}核 · {{ (v.ram_mb/1024).toFixed(0) }}G内存 · {{ v.pop_name||'-' }}
                  <span style="color:#909399;"> · {{ v.customer_name||'无客户' }}</span>
                </div>
              </div>
            </template>
            <div v-else class="assign-empty">
              {{ loadingResources?'加载中…':assignSearch?'无匹配结果':'暂无VPS数据' }}
            </div>
          </div>
        </el-form-item>

        <!-- 边缘加速 选择列表 -->
        <el-form-item label="选择节点" v-if="assignForm.target_type==='edge'">
          <div class="assign-list" v-loading="loadingResources || loadingEdgeDetail">
            <template v-if="filteredEdgeList.length">
              <div
                v-for="e in filteredEdgeList" :key="e.id"
                class="assign-item" :class="{selected:assignForm.target_id===e.id}"
                @click="selectEdgeForAssign(e.id)"
              >
                <div class="assign-item-main">
                  <el-tag type="success" size="small">{{ e.edge_no }}</el-tag>
                  <span class="assign-name">{{ e.name }}</span>
                  <el-tag size="small">{{ protocolMap[e.protocol]||e.protocol }}</el-tag>
                  <el-tag size="small" :type="edgeStatusTag[e.status]?.type||''">
                    {{ edgeStatusTag[e.status]?.label||e.status }}
                  </el-tag>
                </div>
                <div class="assign-sub">
                  {{ e.pop_name||'-' }}
                  <span style="color:#909399;"> · {{ e.customer_name||'无客户' }}</span>
                </div>
              </div>
            </template>
            <div v-else class="assign-empty">
              {{ loadingResources?'加载中…':assignSearch?'无匹配结果':'暂无边缘节点数据' }}
            </div>
          </div>
        </el-form-item>

        <!-- 已选中信息 -->
        <el-form-item label="已选择">
          <template v-if="assignForm.target_id">
            <el-tag type="success" size="large">
              {{ assignForm.target_type==='vps'
                ? vpsList.find(v=>v.id===assignForm.target_id)?.name
                : edgeList.find(e=>e.id===assignForm.target_id)?.name
              }} (#{{ assignForm.target_id }})
            </el-tag>
          </template>
          <el-text type="info" v-else>请从列表中点击选择</el-text>
        </el-form-item>

        <el-form-item label="设为主IP">
          <el-switch v-model="assignForm.is_primary"/>
          <el-text type="info" size="small" style="margin-left:8px;">设为主IP后将优先用于对外通信</el-text>
        </el-form-item>

        <el-form-item label="分配时间">
          <el-date-picker
            v-model="assignForm.assigned_at"
            type="datetime"
            placeholder="默认为当前时间"
            format="YYYY-MM-DD HH:mm"
            value-format="YYYY-MM-DDTHH:mm:ssZ"
            style="width:240px;"
          />
        </el-form-item>

        <el-form-item v-if="assignForm.target_type==='edge'" label="联动路由">
          <el-switch v-model="assignForm.create_route"/>
          <el-text type="info" size="small" style="margin-left:8px;">
            分配后自动创建 host 路由并下发到 POP 节点（需边缘已下发 tunnel）
          </el-text>
        </el-form-item>

        <template v-if="assignForm.target_type==='edge' && assignForm.create_route">
          <el-divider content-position="left">路由参数</el-divider>
          <el-alert
            v-if="assignForm.target_id && !selectedEdgeDetail?.node_ifname"
            type="warning" :closable="false" style="margin-bottom:12px;"
            title="该边缘尚未下发到节点（缺少 node_ifname），联动路由可能失败"
          />
          <el-alert
            v-if="edgeRouteMeta.is_wireguard"
            type="info" :closable="false" style="margin-bottom:12px;"
            title="WireGuard 路由将通过 wg-quick PostUp/PostDown 下发（非 netplan 静态路由）"
          />
          <el-form-item label="隧道本端" v-if="edgeRouteMeta.tunnel_local_ip">
            <el-tag type="info">{{ edgeRouteMeta.tunnel_local_ip }}</el-tag>
            <el-text v-if="edgeRouteMeta.tunnel_cidr" type="info" size="small" style="margin-left:8px;">
              {{ edgeRouteMeta.tunnel_cidr }}
            </el-text>
          </el-form-item>
          <el-form-item label="目标 CIDR" required>
            <el-input v-model="assignRouteForm.dest_cidr" placeholder="如 10.20.30.0/24 或 host/32"/>
          </el-form-item>
          <el-form-item label="出接口">
            <el-input v-model="assignRouteForm.interface_name" placeholder="隧道接口名（node_ifname）"/>
          </el-form-item>
          <el-form-item label="下一跳网关">
            <el-input
              v-model="assignRouteForm.gateway"
              :placeholder="edgeRouteMeta.peer_gateway
                ? `建议：${edgeRouteMeta.peer_gateway}（隧道对端）`
                : '可选；GRE/VXLAN 通常为隧道对端 IP'"
            />
            <el-text
              v-if="edgeRouteMeta.peer_gateway && assignRouteForm.gateway === edgeRouteMeta.peer_gateway"
              type="success" size="small" style="margin-left:8px;"
            >已自动填入隧道对端</el-text>
          </el-form-item>
          <el-form-item label="on-link">
            <el-switch v-model="assignRouteForm.on_link" :disabled="!!assignRouteForm.gateway && !edgeRouteMeta.is_wireguard"/>
            <el-text type="info" size="small" style="margin-left:8px;">PtP 隧道场景建议开启</el-text>
          </el-form-item>
          <el-row :gutter="12">
            <el-col :span="12">
              <el-form-item label="metric">
                <el-input-number v-model="assignRouteForm.metric" :min="0" :max="9999" style="width:100%"/>
              </el-form-item>
            </el-col>
            <el-col :span="12">
              <el-form-item label="route table">
                <el-input-number v-model="assignRouteForm.route_table" :min="0" :max="255" style="width:100%"/>
              </el-form-item>
            </el-col>
          </el-row>
          <el-form-item label="netplan 类">
            <el-select v-model="assignRouteForm.netplan_device_class" style="width:220px">
              <el-option label="tunnels（隧道）" value="tunnels"/>
              <el-option label="ethernets" value="ethernets"/>
              <el-option label="bridges" value="bridges"/>
              <el-option label="bonds" value="bonds"/>
              <el-option label="vlans" value="vlans"/>
            </el-select>
          </el-form-item>
          <el-form-item label="备注">
            <el-input v-model="assignRouteForm.remark" placeholder="可选"/>
          </el-form-item>
        </template>

        <el-form-item label="同步节点">
          <el-switch v-model="assignForm.push_to_node"/>
        </el-form-item>
      </el-form>

      <template #footer>
        <el-button @click="assignDialogVisible=false">取消</el-button>
        <el-button type="primary" :loading="assigning" :disabled="!assignForm.target_id" @click="doAssign">
          确认分配
        </el-button>
      </template>
    </el-dialog>

    <!-- ═══════════════ 录入 IP（对齐 vpop ResourceIpAddresses） ═══════════════ -->
    <el-dialog v-model="addIpDlg" title="录入 IP" width="560px" destroy-on-close>
      <el-form label-width="100px">
        <el-form-item label="所属 IP 块" required>
          <el-select v-model="addIpForm.ip_block" filterable placeholder="选择地址块" style="width:100%" @change="onAddIpBlockChange">
            <el-option v-for="b in blocks" :key="b.id"
              :label="`${b.cidr} · ${blockLocationLabel(b)}`" :value="b.id"/>
          </el-select>
        </el-form-item>
        <el-alert
          v-if="addIpNatHint"
          type="warning"
          :closable="false"
          show-icon
          style="margin-bottom:12px"
          title="该 POP 仅支持 NAT 交付"
          description="业务 IP 不能公网直连，分配给 VPS / 产品时会提示 NAT 交付。"
        />
        <el-form-item label="录入方式">
          <el-radio-group v-model="addIpForm.mode">
            <el-radio-button value="single">单个 IP</el-radio-button>
            <el-radio-button value="list">多个不连续</el-radio-button>
            <el-radio-button value="range">起始-结束</el-radio-button>
            <el-radio-button value="cidr">CIDR 网段</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <template v-if="addIpForm.mode==='single'">
          <el-form-item label="IP 地址" required>
            <el-input v-model="addIpForm.address" placeholder="如 10.0.0.10"/>
          </el-form-item>
        </template>
        <template v-else-if="addIpForm.mode==='list'">
          <el-form-item label="IP 列表" required>
            <el-input v-model="addIpForm.addresses_text" type="textarea" :rows="5"
              placeholder="多个不连续地址，空格/逗号/换行分隔"/>
          </el-form-item>
        </template>
        <template v-else-if="addIpForm.mode==='range'">
          <el-form-item label="起始 IP" required>
            <el-input v-model="addIpForm.range_start" placeholder="如 10.0.0.10"/>
          </el-form-item>
          <el-form-item label="结束 IP" required>
            <el-input v-model="addIpForm.range_end" placeholder="如 10.0.0.30（含）"/>
          </el-form-item>
        </template>
        <template v-else>
          <el-form-item label="CIDR" required>
            <el-input v-model="addIpForm.cidr" placeholder="如 103.1.2.0/28，须属于所选地址块"/>
          </el-form-item>
          <el-form-item label="包含网络/广播">
            <el-checkbox v-model="addIpForm.include_network">网络地址</el-checkbox>
            <el-checkbox v-model="addIpForm.include_broadcast">广播地址</el-checkbox>
          </el-form-item>
        </template>
        <el-form-item label="前缀长度">
          <el-input-number v-model="addIpForm.prefix_len" :min="1" :max="128" :controls="false" placeholder="留空用 CIDR/地址块" style="width:160px"/>
          <span style="margin-left:8px;color:#909399;font-size:12px;">写入每条 IP 的掩码，供 VPS 配置</span>
        </el-form-item>
        <el-form-item label="本段网关">
          <el-input v-model="addIpForm.gateway" placeholder="如 103.1.2.1"/>
        </el-form-item>
        <el-form-item label="初始状态">
          <el-radio-group v-model="addIpForm.state">
            <el-radio-button value="available">可用</el-radio-button>
            <el-radio-button value="reserved">预留</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="网段标签">
          <el-input v-model="addIpForm.subnet_label" placeholder="如 client-vlan10（可选）"/>
        </el-form-item>
        <el-form-item label="同步节点">
          <el-switch v-model="addIpForm.push_to_node"/>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="addIpDlg=false">取消</el-button>
        <el-button type="primary" :loading="addIpBusy" @click="submitAddIp">
          {{ addIpForm.mode==='single' ? '创建' : '批量创建' }}
        </el-button>
      </template>
    </el-dialog>

    <!-- ═══════════════ IP状态变更弹窗 ═══════════════ -->
    <el-dialog v-model="statusDialogVisible" title="变更IP状态" width="420px" destroy-on-close>
      <el-alert type="info" :closable="false" style="margin-bottom:16px;">
        <template #title>
          IP：<el-tag type="primary" style="margin:0 4px;">{{ currentIP?.address }}</el-tag>
          当前：<el-tag :type="statusMap[currentIP?.status]?.type">{{ statusMap[currentIP?.status]?.label }}</el-tag>
        </template>
      </el-alert>
      <el-form :model="statusForm" label-width="90px">
        <el-form-item label="变更为">
          <div style="display:flex;flex-direction:column;gap:8px;width:100%;">
            <div
              v-for="s in changeableStatuses" :key="s.value"
              class="status-option"
              :class="{
                'status-selected': statusForm.status===s.value,
                'status-disabled': currentIP?.status===s.value
              }"
              @click="currentIP?.status!==s.value && (statusForm.status=s.value)"
            >
              <el-tag :type="s.type" size="small" style="width:60px;text-align:center;">{{ s.label }}</el-tag>
              <span class="status-desc">{{ s.desc }}</span>
              <el-icon v-if="statusForm.status===s.value" color="#409EFF" style="margin-left:auto;"><Select/></el-icon>
              <el-text v-if="currentIP?.status===s.value" type="info" size="small" style="margin-left:auto;">当前状态</el-text>
            </div>
          </div>
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="statusForm.description" type="textarea" :rows="2" placeholder="变更原因（可选）"/>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="statusDialogVisible=false">取消</el-button>
        <el-button type="primary" :loading="statusChanging" :disabled="!statusForm.status||statusForm.status===currentIP?.status" @click="doStatusChange">
          确认变更
        </el-button>
      </template>
    </el-dialog>

    <!-- ═══════════════ 编辑描述弹窗 ═══════════════ -->
    <el-dialog v-model="editDescVisible" title="编辑IP备注" width="400px" destroy-on-close>
      <el-form :model="editDescForm" label-width="70px">
        <el-form-item label="IP地址">
          <el-tag type="primary">{{ currentIP?.address }}</el-tag>
        </el-form-item>
        <el-form-item label="备注描述">
          <el-input v-model="editDescForm.description" type="textarea" :rows="3" placeholder="输入备注说明"/>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editDescVisible=false">取消</el-button>
        <el-button type="primary" :loading="editDescSaving" @click="doEditDesc">保存</el-button>
      </template>
    </el-dialog>

    <!-- ═══════════════ IP 操作日志（整页） ═══════════════ -->
    <div v-if="logDrawerVisible" class="ip-detail-page">
      <div class="ip-detail-header">
        <el-button :icon="ArrowLeft" @click="closeIPLog">返回</el-button>
        <el-icon :size="18" color="#409EFF"><Clock/></el-icon>
        <span style="font-weight:700;font-size:15px;">IP 操作日志</span>
        <el-tag type="primary" size="small">{{ currentIP?.address }}</el-tag>
        <el-tag :type="statusMap[currentIP?.status]?.type" size="small">{{ statusMap[currentIP?.status]?.label }}</el-tag>
      </div>

      <div v-loading="loadingLogs">
        <!-- 当前状态摘要 -->
        <el-descriptions :column="2" border size="small" style="margin-bottom:16px;">
          <el-descriptions-item label="IP地址">{{ currentIP?.address }}</el-descriptions-item>
          <el-descriptions-item label="所属IP块">{{ currentIP?.block_cidr || '-' }}</el-descriptions-item>
          <el-descriptions-item label="当前状态">
            <el-tag :type="statusMap[currentIP?.status]?.type" size="small">{{ statusMap[currentIP?.status]?.label }}</el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="最近分配时间">
            {{ currentIP?.assigned_at ? formatDate(currentIP.assigned_at) : '未分配' }}
          </el-descriptions-item>
          <el-descriptions-item label="分配目标" :span="2">
            <template v-if="currentIP?.assigned_vps_info">
              <el-tag type="primary" size="small">{{ currentIP.assigned_vps_info.vps_no }}</el-tag>
              <span style="margin-left:6px;">{{ currentIP.assigned_vps_info.name }}</span>
              <el-tag v-if="currentIP.is_primary" type="success" size="small" style="margin-left:4px;">主IP</el-tag>
            </template>
            <template v-else-if="currentIP?.assigned_edge_info">
              <el-tag type="success" size="small">{{ currentIP.assigned_edge_info.edge_no }}</el-tag>
              <span style="margin-left:6px;">{{ currentIP.assigned_edge_info.name }}</span>
              <el-tag v-if="currentIP.is_primary" type="success" size="small" style="margin-left:4px;">主IP</el-tag>
            </template>
            <span v-else style="color:#C0C4CC;">未分配</span>
          </el-descriptions-item>
        </el-descriptions>

        <!-- 操作日志时间线 -->
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;">
          <span style="font-weight:600;color:#303133;">操作历史（共 {{ logTotal }} 条）</span>
          <el-button size="small" :icon="Refresh" @click="loadIPLogs">刷新</el-button>
        </div>

        <el-empty v-if="!loadingLogs && ipLogs.length===0" description="暂无操作日志" :image-size="80"/>

        <el-timeline v-else>
          <el-timeline-item
            v-for="log in ipLogs" :key="log.id"
            :timestamp="formatDate(log.created_at)"
            :type="logActionType(log.action_type)"
            placement="top"
          >
            <el-card shadow="never" class="log-card">
              <div class="log-card-header">
                <el-tag :type="logActionType(log.action_type)" size="small" style="margin-right:8px;">
                  {{ log.action_type_label || log.action_type }}
                </el-tag>
                <span class="log-op">{{ log.operator_name || '系统' }}</span>
              </div>
              <div class="log-desc" v-if="log.description">{{ log.description }}</div>
              <div class="log-extra" v-if="formatLogDiff(log.old_value, log.new_value)">
                <span class="log-val new">{{ formatLogDiff(log.old_value, log.new_value) }}</span>
              </div>
              <!-- VPS / Edge 关联 -->
              <div class="log-extra" v-if="log.vps||log.edge" style="margin-top:4px;">
                <el-tag v-if="log.vps" type="primary" size="small">VPS #{{ log.vps }}</el-tag>
                <el-tag v-if="log.edge" type="success" size="small" style="margin-left:4px;">Edge #{{ log.edge }}</el-tag>
              </div>
            </el-card>
          </el-timeline-item>
        </el-timeline>

        <el-pagination
          v-if="logTotal>20"
          v-model:current-page="logPage"
          :total="logTotal" :page-size="20"
          layout="total,prev,pager,next"
          style="margin-top:12px;"
          @change="loadIPLogs"
        />
      </div>
    </div><!-- /IP操作日志整页 -->

    <!-- ═══════════════ IP块详情（整页） ═══════════════ -->
    <div v-if="blockDetailVisible && !logDrawerVisible" class="ip-detail-page">
      <div class="ip-detail-header">
        <el-button :icon="ArrowLeft" @click="closeBlockDetail">返回</el-button>
        <span style="font-weight:700;font-size:16px;">{{ currentBlock?.cidr }}</span>
        <el-tag :type="currentBlock?.ip_version==='v6'?'warning':'primary'" size="small">
          {{ currentBlock?.ip_version==='v6'?'IPv6':'IPv4' }}
        </el-tag>
        <el-tag type="info" size="small">{{ getPopName(currentBlock?.pop_point) }}</el-tag>
        <el-tag v-if="currentBlock?.ip_delivery_mode === 'nat'" type="warning" size="small">NAT 交付</el-tag>
        <el-tag v-if="currentBlock?.gateway" type="info" size="small" effect="plain">
          网关 {{ currentBlock.gateway }}
        </el-tag>
        <el-tag v-if="currentBlock?.network_type" size="small" :type="currentBlock.network_type === 'home_broadband' ? 'warning' : 'primary'">
          {{ currentBlock.network_type_label || currentBlock.network_type }}
        </el-tag>
        <el-tag v-if="currentBlock?.asn_nature" size="small" effect="plain" type="info">
          {{ currentBlock.asn_nature_label || currentBlock.asn_nature }}
        </el-tag>
        <el-tag v-if="(currentBlock?.merged_count || 1) > 1" type="info" size="small" effect="plain">
          已合并 {{ currentBlock.merged_count }} 条同 CIDR 记录
        </el-tag>
        <div style="margin-left:auto;display:flex;gap:8px;">
          <el-button size="small" type="primary" :icon="Plus" @click="openAddIp(currentBlock?.id)">录入 IP</el-button>
          <el-button size="small" :icon="MagicStick" @click="triggerGenerate(currentBlock)">重新解析</el-button>
          <el-button size="small" type="primary" :icon="Refresh" @click="loadDetailAddresses">刷新</el-button>
        </div>
      </div>

      <!-- 统计卡片 -->
      <div class="stat-row" v-if="currentBlock?.ip_stats?.total > 0">
        <div class="stat-card total">
          <div class="sc-num">{{ currentBlock.ip_stats.total }}</div>
          <div class="sc-lbl">总计</div>
        </div>
        <div class="stat-card avail">
          <div class="sc-num">{{ currentBlock.ip_stats.available }}</div>
          <div class="sc-lbl">可用</div>
        </div>
        <div class="stat-card assigned">
          <div class="sc-num">{{ currentBlock.ip_stats.assigned }}</div>
          <div class="sc-lbl">已分配</div>
        </div>
        <div class="stat-card reserved">
          <div class="sc-num">{{ currentBlock.ip_stats.reserved||0 }}</div>
          <div class="sc-lbl">预留</div>
        </div>
        <div class="stat-card blocked">
          <div class="sc-num">{{ currentBlock.ip_stats.blocked||0 }}</div>
          <div class="sc-lbl">封禁</div>
        </div>
      </div>
      <el-alert v-else type="warning" :closable="false" style="margin-bottom:12px;"
        title="该地址块尚无IP，请点击右上角「重新解析」"/>

      <!-- 过滤栏 -->
      <div style="display:flex;align-items:center;gap:8px;margin:10px 0;">
        <el-select v-model="detailSearch.status" clearable placeholder="全部状态" style="width:120px" @change="loadDetailAddresses">
          <el-option v-for="s in statusOptions" :key="s.value" :label="s.label" :value="s.value"/>
        </el-select>
        <el-input v-model="detailSearch.search" placeholder="搜索IP地址" clearable style="width:180px" @keyup.enter="loadDetailAddresses"/>
        <el-button type="primary" size="small" :icon="Search" @click="loadDetailAddresses">搜索</el-button>
        <el-text type="info" size="small" style="margin-left:auto;">共 {{ detailTotal }} 条</el-text>
      </div>

      <!-- IP地址表格（含操作列） -->
      <el-table :data="detailAddresses" v-loading="loadingDetail" stripe border size="small">
        <el-table-column prop="address" label="IP地址" width="155"/>
        <el-table-column label="/掩码" width="62" align="center">
          <template #default="{row}">{{ row.effective_prefix_len ?? row.prefix_len }}</template>
        </el-table-column>
        <el-table-column label="网关" width="140">
          <template #default="{row}">{{ row.effective_gateway || row.gateway || currentBlock?.gateway || '-' }}</template>
        </el-table-column>
        <el-table-column prop="status" label="状态" width="98">
          <template #default="{row}">
            <el-tag :type="statusMap[row.status]?.type" size="small">{{ statusMap[row.status]?.label }}</el-tag>
          </template>
        </el-table-column>
        <!-- 分配目标 -->
        <el-table-column label="分配目标" min-width="175">
          <template #default="{row}">
            <div v-if="row.assigned_vps_info" style="display:flex;align-items:center;gap:4px;flex-wrap:wrap;">
              <el-tag type="primary" size="small">{{ row.assigned_vps_info.vps_no }}</el-tag>
              <span style="font-size:12px;">{{ row.assigned_vps_info.name }}</span>
              <el-tag v-if="row.is_primary" type="success" size="small">主IP</el-tag>
              <span v-if="row.assigned_vps_info.customer_name" style="color:#909399;font-size:12px;">{{ row.assigned_vps_info.customer_name }}</span>
            </div>
            <div v-else-if="row.assigned_edge_info" style="display:flex;align-items:center;gap:4px;flex-wrap:wrap;">
              <el-tag type="success" size="small">{{ row.assigned_edge_info.edge_no }}</el-tag>
              <span style="font-size:12px;">{{ row.assigned_edge_info.name }}</span>
              <el-tag size="small">{{ protocolMap[row.assigned_edge_info.protocol]||row.assigned_edge_info.protocol }}</el-tag>
              <el-tag v-if="row.is_primary" type="success" size="small">主IP</el-tag>
              <span v-if="row.assigned_edge_info.customer_name" style="color:#909399;font-size:12px;">{{ row.assigned_edge_info.customer_name }}</span>
            </div>
            <span v-else style="color:#C0C4CC;font-size:12px;">未分配</span>
          </template>
        </el-table-column>
        <el-table-column label="接口" width="120">
          <template #default="{row}">{{ row.interface_code || '-' }}</template>
        </el-table-column>
        <el-table-column prop="description" label="描述" min-width="90"/>
        <el-table-column label="分配时间" width="140">
          <template #default="{row}">
            <span style="font-size:11px;">{{ row.assigned_at?formatDate(row.assigned_at):'-' }}</span>
          </template>
        </el-table-column>
        <!-- ★ 操作列 - 直接内联按钮 ★ -->
        <el-table-column label="操作" width="230" fixed="right">
          <template #default="{row}">
            <!-- 分配 -->
            <el-button
              v-if="['available','reserved'].includes(row.status)"
              link type="primary" size="small"
              @click="openAssign(row,'detail')"
            >分配</el-button>
            <!-- 回收 -->
            <el-button
              v-if="row.status==='assigned'"
              link type="warning" size="small"
              @click="doRelease(row,'detail')"
            >回收</el-button>
            <!-- 改状态 -->
            <el-button
              v-if="row.status!=='system'"
              link size="small"
              @click="openStatusChange(row,'detail')"
            >改状态</el-button>
            <!-- 备注 -->
            <el-button
              link size="small"
              @click="openEditDesc(row,'detail')"
            >备注</el-button>
            <!-- 日志 -->
            <el-button
              link type="info" size="small" :icon="Clock"
              @click="openIPLog(row,'detail')"
            >日志</el-button>
          </template>
        </el-table-column>
      </el-table>

      <el-pagination v-model:current-page="detailPage" :total="detailTotal" :page-size="50"
        layout="total,prev,pager,next" style="margin-top:12px;" @change="loadDetailAddresses"/>
    </div><!-- /IP块详情整页 -->
  </div>
</template>

<script setup>
import { ref, reactive, onMounted, watch, computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  Plus, Search, MagicStick, List, Loading,
  Monitor, Connection, Select, Refresh, Clock, ArrowLeft
} from '@element-plus/icons-vue'
import {
  getIPBlocks, createIPBlock, updateIPBlock, deleteIPBlock,
  getIPAddresses, updateIPAddress,
  getCarriers, getNetworkTypes, createNetworkType, updateNetworkType, deleteNetworkType,
  previewBlockIPs, generateBlockIPs,
  assignIPAddress, releaseIPAddress, bulkCreateIPs,
  getVPS, getEdgeAccelerations, getEdgeDetail,
  getIPAddressLogs
} from '@/api/resource'
import { useRegionGroupedRows, useResourceLocation } from '@/composables/useResourceLocation'
import ResourceLocationFilter from './components/ResourceLocationFilter.vue'
import ResourceTableGroupLabel from './components/ResourceTableGroupLabel.vue'
import ExcelImportExport from '@/components/ExcelImportExport.vue'
import { formatLogDiff } from '@/utils/resourceLog'

const route = useRoute()
const router = useRouter()

// ── 状态变量 ────────────────────────────────────────────────
const activeTab          = ref('blocks')
const loadingBlocks      = ref(false)
const loadingAddresses   = ref(false)
const loadingDetail      = ref(false)
const loadingResources   = ref(false)
const saving             = ref(false)
const assigning          = ref(false)
const generating         = ref(false)
const statusChanging     = ref(false)
const previewLoading     = ref(false)
const editDescSaving     = ref(false)
const loadingLogs        = ref(false)

const networkTypeDialogVisible = ref(false)
const networkTypeSaving = ref(false)
const networkTypeAll = ref([])
const networkTypeForm = reactive({ code: '', name: '', sort_order: 0 })
const blockDialogVisible    = ref(false)
const generateDialogVisible = ref(false)
const assignDialogVisible   = ref(false)
const statusDialogVisible   = ref(false)
const editDescVisible       = ref(false)
const blockDetailVisible    = ref(false)
const logDrawerVisible      = ref(false)

const blockFormRef = ref(null)
const editBlock    = ref(null)
const currentBlock = ref(null)
const currentIP    = ref(null)
// 'global' = 全局IP列表  'detail' = 块详情抽屉
const opContext    = ref('global')

// 数据
const blocks     = ref([])
const blockTotal = ref(0)
const blockPage  = ref(1)
const addresses  = ref([])
const addrTotal  = ref(0)
const addrPage   = ref(1)
const detailAddresses = ref([])
const detailTotal     = ref(0)
const detailPage      = ref(1)

// 过滤参数
const blockSearch  = reactive({ search: '', ip_version: '', network_type: '', asn_nature: '' })
const addrSearch   = reactive({ status: '', ip_block: '', search: '' })
const detailSearch = reactive({ status: '', search: '' })

// 资源列表（分配用）
const carriers  = ref([])
const vpsList   = ref([])
const edgeList  = ref([])
const {
  location, regions, filteredZones, filteredPopPoints,
  popPoints, loadingLocation, popLabel,
  resetLocation, locationParams, loadLocationOptions,
} = useResourceLocation()
const excelUrls = computed(() => activeTab.value === 'addresses' ? {
  exportUrl: '/api/resource/ip-addresses/export/',
  templateUrl: '/api/resource/ip-addresses/import-template/',
  importUrl: '/api/resource/ip-addresses/import/',
  filename: 'IP地址.xlsx',
} : {
  exportUrl: '/api/resource/ip-blocks/export/',
  templateUrl: '/api/resource/ip-blocks/import-template/',
  importUrl: '/api/resource/ip-blocks/import/',
  filename: 'IP地址块.xlsx',
})
const excelParams = computed(() => {
  const params = { ...locationParams() }
  if (activeTab.value === 'addresses') {
    if (addrSearch.status) params.status = addrSearch.status
    if (addrSearch.search) params.search = addrSearch.search
    if (addrSearch.ip_block) {
      const block = blocks.value.find((b) => b.id === addrSearch.ip_block)
      if (block) {
        params.ip_block__cidr = block.cidr
        params.ip_block__pop_point = block.pop_point
      } else {
        params.ip_block = addrSearch.ip_block
      }
    }
  } else {
    if (blockSearch.search) params.search = blockSearch.search
    if (blockSearch.ip_version) params.ip_version = blockSearch.ip_version
    if (blockSearch.network_type) params.network_type = blockSearch.network_type
    if (blockSearch.asn_nature) params.asn_nature = blockSearch.asn_nature
  }
  return params
})
function onExcelImported() {
  if (activeTab.value === 'addresses') loadAddresses()
  else loadBlocks()
}
const {
  groupedRows: groupedBlocks,
  isLocationGroup,
  locationSpanMethod,
} = useRegionGroupedRows(blocks, 10)
const {
  groupedRows: groupedAddresses,
} = useRegionGroupedRows(addresses, 10)

// 解析相关
const previewData    = ref(null)
const generateResult = ref(null)
const genOpts = reactive({ include_network: false, include_broadcast: false, description: '' })

// 分配相关
const assignForm   = reactive({
  target_type: 'vps', target_id: null, is_primary: false, assigned_at: null,
  push_to_node: true, create_route: true,
})
const assignRouteForm = reactive({
  dest_cidr: '', gateway: '', on_link: true, metric: null, route_table: null,
  netplan_device_class: 'tunnels', interface_name: '', remark: '',
})
const edgeRouteMeta = reactive({
  tunnel_cidr: '', tunnel_local_ip: '', peer_gateway: '', is_wireguard: false,
})
const assignSearch = ref('')
const assignPopId  = ref(null)
const selectedEdgeDetail = ref(null)
const loadingEdgeDetail = ref(false)

// 录入 IP
const addIpDlg = ref(false)
const addIpBusy = ref(false)
const addIpForm = reactive({
  mode: 'single', ip_block: null, address: '', range_start: '', range_end: '',
  addresses_text: '', cidr: '', include_network: false, include_broadcast: false,
  prefix_len: undefined, gateway: '',
  state: 'available', subnet_label: '', push_to_node: true,
})
const addIpSelectedBlock = computed(() =>
  blocks.value.find((b) => b.id === addIpForm.ip_block) || null,
)
const addIpNatHint = computed(() => addIpSelectedBlock.value?.ip_delivery_mode === 'nat')

// 状态变更相关
const statusForm = reactive({ status: '', description: '' })

// 描述编辑相关
const editDescForm = reactive({ description: '' })

// 日志相关
const ipLogs      = ref([])
const logTotal    = ref(0)
const logPage     = ref(1)

// IP块表单
const blockForm = reactive({
  cidr: '', ip_version: 'v4', pop_point: null,
  carrier: null, ip_attribute: '', network_type: 'idc', asn_nature: 'isp',
  gateway: '', description: '', is_active: true
})

// ── 常量 ────────────────────────────────────────────────────
const statusOptions = [
  { value: 'available', label: '可用' }, { value: 'assigned', label: '已分配' },
  { value: 'reserved',  label: '预留' }, { value: 'blocked',  label: '封禁'  },
  { value: 'system',    label: '系统' }
]
const statusMap = {
  available: { label: '可用',   type: 'success' },
  assigned:  { label: '已分配', type: 'primary' },
  reserved:  { label: '预留',   type: 'warning' },
  blocked:   { label: '封禁',   type: 'danger'  },
  system:    { label: '系统',   type: 'info'    }
}
const changeableStatuses = [
  { value: 'available', label: '可用',   type: 'success', desc: '恢复为可分配状态，任何人可申请' },
  { value: 'reserved',  label: '预留',   type: 'warning', desc: '内部预留，暂不对外分配'          },
  { value: 'blocked',   label: '封禁',   type: 'danger',  desc: '标记封禁，禁止使用与分配'        },
]
const ipAttributeOptions = [
  { value: 'native', label: '原生' }, { value: 'broadcast', label: '广播' }
]
const networkTypeOptions = ref([])
const asnNatureOptions = [
  { value: 'isp', label: '单ISP' },
  { value: 'dual_isp', label: '双ISP' },
  { value: 'non_isp', label: '非ISP' },
]
const networkTypeSelectOptions = computed(() => {
  const list = networkTypeOptions.value.filter((t) => t.is_active !== false)
  const current = blockForm.network_type
  if (current && !list.some((t) => t.code === current)) {
    const extra = networkTypeAll.value.find((t) => t.code === current)
    if (extra) return [...list, extra]
  }
  return list
})
const protocolMap    = { gre: 'GRE', wireguard: 'WireGuard', vxlan: 'VXLAN', socks5: 'Socks5' }
const vpsStatusTag   = {
  running:   { label: '运行中', type: 'success' }, stopped:   { label: '已停止', type: 'info'    },
  suspended: { label: '已暂停', type: 'warning' }, creating:  { label: '创建中', type: 'primary' },
  error:     { label: '故障',   type: 'danger'  }
}
const edgeStatusTag  = {
  active:  { label: '运行中', type: 'success' }, stopped: { label: '已停止', type: 'info'    },
  pending: { label: '待配置', type: 'warning' }, error:   { label: '故障',   type: 'danger'  }
}

const blockRules = {
  cidr:      [{ required: true, message: '请输入CIDR', trigger: 'blur' }],
  ip_version:[{ required: true, message: '请选择IP版本'   }],
  pop_point: [{ required: true, message: '请选择POP节点'  }],
  network_type: [{ required: true, message: '请选择网络类型' }],
  asn_nature: [{ required: true, message: '请选择ASN/企业性质' }],
}

// ── computed 过滤列表 ──────────────────────────────────────
const filteredVPSList = computed(() => {
  let list = vpsList.value
  if (assignPopId.value) list = list.filter(v => v.pop_point === assignPopId.value)
  const q = assignSearch.value.toLowerCase().trim()
  if (!q) return list
  return list.filter(v =>
    v.name?.toLowerCase().includes(q) ||
    v.vps_no?.toLowerCase().includes(q) ||
    v.customer_name?.toLowerCase().includes(q) ||
    v.mgmt_ip?.includes(q)
  )
})
const filteredEdgeList = computed(() => {
  let list = edgeList.value
  if (assignPopId.value) list = list.filter(e => e.pop_point === assignPopId.value)
  const q = assignSearch.value.toLowerCase().trim()
  if (!q) return list
  return list.filter(e =>
    e.name?.toLowerCase().includes(q) ||
    e.edge_no?.toLowerCase().includes(q) ||
    e.pop_name?.toLowerCase().includes(q) ||
    (protocolMap[e.protocol]||e.protocol).toLowerCase().includes(q)
  )
})

// ── 工具函数 ──────────────────────────────────────────────
const getPopName    = id => popPoints.value.find(p => p.id === id)?.name || (id ? `POP#${id}` : '-')
const blockLocationLabel = block => {
  const locationText = [block?.region_name, block?.country_name, block?.city_name]
    .filter(Boolean)
    .join(' / ')
  return [locationText, getPopName(block?.pop_point)].filter(Boolean).join(' / ')
}
const calcBlockPct  = r  => { const t=r.ip_stats?.total||0; return t ? Math.round((r.ip_stats?.assigned||0)/t*100) : 0 }
const getBlockColor = r  => { const p=calcBlockPct(r); return p>=90?'#F56C6C':p>=70?'#E6A23C':'#67C23A' }
const formatDate    = dt => {
  if (!dt) return '-'
  return new Date(dt).toLocaleString('zh-CN',{hour12:false}).replace(/\//g,'-').replace(',','')
}
// 日志动作类型 → Element Plus timeline type
const logActionType = type => {
  const map = {
    ip_assign:  'success',
    ip_release: 'warning',
    ip_status:  'info',
    create:     'primary',
    update:     'info',
    delete:     'danger',
  }
  return map[type] || 'info'
}

function applyLocation(next) {
  Object.assign(location, next)
}

function reloadBlockLocation() {
  blockPage.value = 1
  loadBlocks()
}

function reloadAddressLocation() {
  addrPage.value = 1
  loadAddresses()
}

function resetAssignRouteForm() {
  Object.assign(assignRouteForm, {
    dest_cidr: '', gateway: '', on_link: true, metric: null, route_table: null,
    netplan_device_class: 'tunnels', interface_name: '', remark: '',
  })
  Object.assign(edgeRouteMeta, {
    tunnel_cidr: '', tunnel_local_ip: '', peer_gateway: '', is_wireguard: false,
  })
  selectedEdgeDetail.value = null
}

function applyEdgeRouteHints(detail, ip) {
  if (!detail || !ip) return
  const hints = detail.route_assign_hints || {}
  const prefix = ip.prefix_len || 32
  Object.assign(edgeRouteMeta, {
    tunnel_cidr: hints.tunnel_cidr || '',
    tunnel_local_ip: hints.tunnel_local_ip || '',
    peer_gateway: hints.peer_gateway || '',
    is_wireguard: !!hints.is_wireguard,
  })
  Object.assign(assignRouteForm, {
    dest_cidr: `${ip.address}/${prefix}`,
    gateway: hints.peer_gateway || '',
    on_link: true,
    metric: null,
    route_table: null,
    netplan_device_class: hints.netplan_device_class || 'tunnels',
    interface_name: detail.node_ifname || hints.ifname || '',
    remark: '',
  })
}

async function selectEdgeForAssign(edgeId) {
  assignForm.target_id = edgeId
  resetAssignRouteForm()
  assignForm.target_id = edgeId
  if (!assignForm.create_route) return
  loadingEdgeDetail.value = true
  try {
    const detail = await getEdgeDetail(edgeId)
    selectedEdgeDetail.value = detail
    applyEdgeRouteHints(detail, currentIP.value)
  } catch (e) {
    ElMessage.warning('加载边缘详情失败，路由参数需手动填写')
  } finally {
    loadingEdgeDetail.value = false
  }
}

// ── 刷新当前上下文的IP列表 ──────────────────────────────────
function refreshIPList() {
  loadBlocks()
  if (opContext.value === 'detail') loadDetailAddresses()
  else loadAddresses()
}

// ── 数据加载 ───────────────────────────────────────────────
async function loadBlocks() {
  loadingBlocks.value = true
  try {
    const p = { page: blockPage.value, page_size: 20, ...locationParams() }
    if (blockSearch.search)     p.search     = blockSearch.search
    if (blockSearch.ip_version) p.ip_version = blockSearch.ip_version
    if (blockSearch.network_type) p.network_type = blockSearch.network_type
    if (blockSearch.asn_nature) p.asn_nature = blockSearch.asn_nature
    const res = await getIPBlocks(p)
    blocks.value     = res?.results || []
    blockTotal.value = res?.count   || 0
  } finally { loadingBlocks.value = false }
}

async function loadAddresses() {
  loadingAddresses.value = true
  try {
    const p = { page: addrPage.value, page_size: 50, ...locationParams() }
    if (addrSearch.status)   p.status   = addrSearch.status
    if (addrSearch.ip_block) {
      const block = blocks.value.find(b => b.id === addrSearch.ip_block)
      if (block) {
        p['ip_block__cidr'] = block.cidr
        p['ip_block__pop_point'] = block.pop_point
      } else {
        p.ip_block = addrSearch.ip_block
      }
    }
    if (addrSearch.search)   p.search   = addrSearch.search
    const res = await getIPAddresses(p)
    addresses.value = res?.results || []
    addrTotal.value = res?.count   || 0
  } finally { loadingAddresses.value = false }
}

async function loadDetailAddresses() {
  if (!currentBlock.value) return
  loadingDetail.value = true
  try {
    const p = {
      ip_block__cidr: currentBlock.value.cidr,
      ip_block__pop_point: currentBlock.value.pop_point,
      page: detailPage.value, page_size: 50,
    }
    if (detailSearch.status) p.status = detailSearch.status
    if (detailSearch.search) p.search = detailSearch.search
    const res = await getIPAddresses(p)
    detailAddresses.value = res?.results || []
    detailTotal.value     = res?.count   || 0
  } finally { loadingDetail.value = false }
}

// ── IP块 CRUD ─────────────────────────────────────────────
function openBlockDialog(row = null) {
  editBlock.value = row
  if (row) {
    Object.assign(blockForm, {
      cidr: row.cidr, ip_version: row.ip_version, pop_point: row.pop_point,
      carrier: row.carrier||null, ip_attribute: row.ip_attribute||'',
      network_type: row.network_type || 'idc', asn_nature: row.asn_nature || 'isp',
      gateway: row.gateway || '',
      description: row.description||'', is_active: row.is_active !== false
    })
  } else {
    Object.assign(blockForm, {
      cidr:'', ip_version:'v4', pop_point:location.pop_point || null,
      carrier:null, ip_attribute:'', network_type:'idc', asn_nature:'isp',
      gateway:'', description:'', is_active:true,
    })
  }
  blockDialogVisible.value = true
}

async function handleSaveBlock() {
  const valid = await blockFormRef.value.validate().catch(() => false)
  if (!valid) return
  saving.value = true
  try {
    const payload = { ...blockForm, gateway: blockForm.gateway || null }
    if (editBlock.value) {
      await updateIPBlock(editBlock.value.id, payload)
      ElMessage.success('已更新')
      blockDialogVisible.value = false
      loadBlocks()
    } else {
      const newBlock = await createIPBlock(payload)
      ElMessage.success('IP地址块创建成功')
      blockDialogVisible.value = false
      await loadBlocks()
      const created = blocks.value.find(b => b.id === newBlock.id) || newBlock
      triggerGenerate(created)
    }
  } finally { saving.value = false }
}

async function handleDeleteBlock(row) {
  const hasIPs = row.ip_stats?.total > 0
  const memberIds = (row.member_ids?.length ? row.member_ids : [row.id])
  await ElMessageBox.confirm(
    hasIPs
      ? `确定删除 "${row.cidr}"？该 CIDR 下 ${row.ip_stats.total} 个IP地址${memberIds.length > 1 ? `（${memberIds.length} 条地址块）` : ''}将一并删除！`
      : `确定删除 "${row.cidr}"？`,
    '警告', { type:'warning', confirmButtonText:'确认删除', confirmButtonClass:'el-button--danger' }
  )
  for (const id of memberIds) {
    await deleteIPBlock(id)
  }
  ElMessage.success('已删除')
  loadBlocks()
}

// ── IP解析 ─────────────────────────────────────────────────
async function triggerGenerate(block) {
  if (!block) return
  currentBlock.value   = block
  previewData.value    = null
  generateResult.value = null
  previewLoading.value = true
  Object.assign(genOpts, { include_network:false, include_broadcast:false, description:'' })
  generateDialogVisible.value = true
  try {
    previewData.value = await previewBlockIPs(block.id)
  } catch (e) {
    ElMessage.error('预览失败：' + (e?.response?.data?.detail || e.message))
    generateDialogVisible.value = false
  } finally { previewLoading.value = false }
}

async function doGenerate() {
  generating.value = true
  try {
    const res = await generateBlockIPs(currentBlock.value.id, { ...genOpts })
    generateResult.value = res
    ElMessage.success(res.message)
    loadBlocks()
    if (blockDetailVisible.value) loadDetailAddresses()
  } catch (e) {
    ElMessage.error('解析失败：' + (e?.response?.data?.detail || e.message))
  } finally { generating.value = false }
}

function closeGenerateDialog() {
  generateDialogVisible.value = false
  previewData.value = null; generateResult.value = null
}
function viewBlockAddresses() {
  generateDialogVisible.value = false
  viewBlockDetail(currentBlock.value)
}
function viewBlockDetail(block) {
  currentBlock.value = block
  Object.assign(detailSearch, { status:'', search:'' })
  detailPage.value = 1
  blockDetailVisible.value = true
  loadDetailAddresses()
  if (route.name !== 'IPBlockDetail' || String(route.params.id) !== String(block.id)) {
    router.push(`/resource/ip/blocks/${block.id}`)
  }
}
function closeBlockDetail() {
  blockDetailVisible.value = false
  router.push('/resource/ip')
}

function onAddIpBlockChange(id) {
  const blk = blocks.value.find((b) => b.id === id)
  if (blk && !addIpForm.gateway) addIpForm.gateway = blk.gateway || ''
}

function openAddIp(presetBlockId = null) {
  if (!blocks.value.length) loadBlocks()
  const blockId = presetBlockId || addrSearch.ip_block || currentBlock.value?.id || blocks.value[0]?.id || null
  const blk = blocks.value.find((b) => b.id === blockId) || currentBlock.value
  Object.assign(addIpForm, {
    mode: 'single', ip_block: blockId,
    address: '', range_start: '', range_end: '', addresses_text: '',
    cidr: '', include_network: false, include_broadcast: false,
    prefix_len: undefined, gateway: blk?.gateway || '',
    state: 'available', subnet_label: '', push_to_node: true,
  })
  addIpDlg.value = true
}

function parseAddrText(text) {
  if (!text) return []
  return String(text).split(/[\s,;\n\t]+/).map(s => s.trim()).filter(Boolean)
}

async function submitAddIp() {
  if (!addIpForm.ip_block) {
    ElMessage.warning('请选择所属 IP 块')
    return
  }
  addIpBusy.value = true
  try {
    const payload = {
      ip_block: addIpForm.ip_block,
      mode: addIpForm.mode,
      state: addIpForm.state,
      subnet_label: addIpForm.subnet_label,
      push_to_node: addIpForm.push_to_node,
    }
    if (addIpForm.prefix_len) payload.prefix_len = addIpForm.prefix_len
    if (addIpForm.gateway) payload.gateway = addIpForm.gateway
    if (addIpForm.mode === 'single') {
      if (!addIpForm.address) { ElMessage.warning('请填写 IP'); return }
      payload.address = addIpForm.address
    } else if (addIpForm.mode === 'range') {
      if (!addIpForm.range_start || !addIpForm.range_end) {
        ElMessage.warning('请填写起止 IP'); return
      }
      payload.start = addIpForm.range_start
      payload.end = addIpForm.range_end
    } else if (addIpForm.mode === 'cidr') {
      if (!addIpForm.cidr) { ElMessage.warning('请填写 CIDR'); return }
      payload.cidr = addIpForm.cidr
      payload.include_network = addIpForm.include_network
      payload.include_broadcast = addIpForm.include_broadcast
    } else {
      const items = parseAddrText(addIpForm.addresses_text)
      if (!items.length) { ElMessage.warning('请输入至少一个 IP'); return }
      payload.addresses = items
    }
    const res = await bulkCreateIPs(payload)
    const ok = res?.created?.length || 0
    const sk = res?.skipped?.length || 0
    ElMessage.success(`已录入 ${ok} 条${sk ? `，跳过 ${sk}` : ''}`)
    addIpDlg.value = false
    loadAddresses()
    loadBlocks()
    if (blockDetailVisible.value) loadDetailAddresses()
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '录入失败')
  } finally { addIpBusy.value = false }
}

// ── IP 分配 ─────────────────────────────────────────────────
async function openAssign(ip, ctx) {
  currentIP.value = ip
  opContext.value = ctx
  assignPopId.value = ip.pop_point || currentBlock.value?.pop_point || null
  Object.assign(assignForm, {
    target_type: 'vps', target_id: null, is_primary: false, assigned_at: null,
    push_to_node: true, create_route: true,
  })
  resetAssignRouteForm()
  assignSearch.value = ''
  assignDialogVisible.value = true
  loadingResources.value = true
  try {
    const params = { page_size: 300 }
    if (assignPopId.value) params.pop_point = assignPopId.value
    const [vRes, eRes] = await Promise.allSettled([
      getVPS(params),
      getEdgeAccelerations(params),
    ])
    vpsList.value  = vRes.status === 'fulfilled'  ? vRes.value?.results  || [] : []
    edgeList.value = eRes.status === 'fulfilled'  ? eRes.value?.results  || [] : []
  } finally { loadingResources.value = false }
}

async function doAssign() {
  if (!assignForm.target_id) { ElMessage.warning('请先从列表中选择目标'); return }
  if (assignForm.target_type === 'edge' && assignForm.create_route && !assignRouteForm.dest_cidr) {
    ElMessage.warning('已开启联动路由，请填写目标 CIDR')
    return
  }
  assigning.value = true
  try {
    const payload = {
      target_type: assignForm.target_type,
      target_id: assignForm.target_id,
      is_primary: assignForm.is_primary,
      push_to_node: assignForm.push_to_node,
      create_route: assignForm.target_type === 'edge' ? assignForm.create_route : false,
    }
    if (assignForm.assigned_at) payload.assigned_at = assignForm.assigned_at
    if (assignForm.target_type === 'edge' && assignForm.create_route) {
      payload.route = {
        dest_cidr: assignRouteForm.dest_cidr,
        gateway: assignRouteForm.gateway || null,
        on_link: assignRouteForm.on_link,
        metric: assignRouteForm.metric,
        route_table: assignRouteForm.route_table,
        netplan_device_class: assignRouteForm.netplan_device_class || 'tunnels',
        interface_name: assignRouteForm.interface_name || selectedEdgeDetail.value?.node_ifname || '',
        remark: assignRouteForm.remark,
      }
    }
    const res = await assignIPAddress(currentIP.value.id, payload)
    const targetName = assignForm.target_type === 'vps'
      ? vpsList.value.find(v => v.id === assignForm.target_id)?.name
      : edgeList.value.find(e => e.id === assignForm.target_id)?.name
    const routeHint = res?.node_push?.route_id
      ? (edgeRouteMeta.is_wireguard ? '，WireGuard 路由已下发' : '，路由已下发')
      : ''
    ElMessage.success(`IP ${currentIP.value.address} 已分配给 ${targetName}${routeHint}`)
    assignDialogVisible.value = false
    refreshIPList()
  } catch (e) {
    ElMessage.error('分配失败：' + (e?.response?.data?.detail || e.message))
  } finally { assigning.value = false }
}

// ── IP 回收 ─────────────────────────────────────────────────
async function doRelease(ip, ctx) {
  opContext.value = ctx
  const target = ip.assigned_vps_info
    ? `VPS ${ip.assigned_vps_info.vps_no}（${ip.assigned_vps_info.name}）`
    : ip.assigned_edge_info
      ? `Edge ${ip.assigned_edge_info.edge_no}（${ip.assigned_edge_info.name}）`
      : '未知目标'
  try {
    await ElMessageBox.confirm(
      `确定回收 IP ${ip.address} 的分配？\n当前分配至：${target}\n\n将同步释放节点绑定并删除关联路由（如有）`,
      '回收确认', { type:'warning', confirmButtonText:'确认回收' }
    )
    const res = await releaseIPAddress(ip.id, { push_to_node: true })
    const removed = res?.node_push?.node_result?.removed_routes?.length || 0
    ElMessage.success(
      removed > 0
        ? `IP ${ip.address} 已回收，并删除节点路由 ${removed} 条`
        : `IP ${ip.address} 已回收，状态恢复为可用`,
    )
    refreshIPList()
  } catch (e) {
    if (e !== 'cancel') {
      ElMessage.error('回收失败：' + (e?.response?.data?.detail || e.message))
    }
  }
}

// ── IP 状态变更 ──────────────────────────────────────────────
function openStatusChange(ip, ctx) {
  currentIP.value = ip
  opContext.value = ctx
  if (ip.status === 'assigned') {
    ElMessage.warning('已分配的IP请先「回收」后再变更状态')
    return
  }
  // 预选与当前不同的第一个状态
  const next = changeableStatuses.find(s => s.value !== ip.status)
  statusForm.status      = next?.value || 'available'
  statusForm.description = ip.description || ''
  statusDialogVisible.value = true
}

async function doStatusChange() {
  if (!statusForm.status || statusForm.status === currentIP.value?.status) return
  statusChanging.value = true
  try {
    const payload = { status: statusForm.status }
    if (statusForm.description !== undefined) payload.description = statusForm.description
    await updateIPAddress(currentIP.value.id, payload)
    ElMessage.success(
      `IP ${currentIP.value.address} 状态已变更为「${statusMap[statusForm.status]?.label}」`
    )
    statusDialogVisible.value = false
    refreshIPList()
  } catch (e) {
    ElMessage.error('变更失败：' + (e?.response?.data?.detail || e.message))
  } finally { statusChanging.value = false }
}

// ── IP 备注编辑 ──────────────────────────────────────────────
function openEditDesc(ip, ctx) {
  currentIP.value        = ip
  opContext.value        = ctx
  editDescForm.description = ip.description || ''
  editDescVisible.value  = true
}

async function doEditDesc() {
  editDescSaving.value = true
  try {
    await updateIPAddress(currentIP.value.id, { description: editDescForm.description })
    ElMessage.success('备注已更新')
    editDescVisible.value = false
    refreshIPList()
  } catch (e) {
    ElMessage.error('更新失败：' + (e?.response?.data?.detail || e.message))
  } finally { editDescSaving.value = false }
}

// ── IP 日志 ──────────────────────────────────────────────────
async function openIPLog(ip, ctx) {
  currentIP.value   = ip
  opContext.value   = ctx
  ipLogs.value      = []
  logTotal.value    = 0
  logPage.value     = 1
  logDrawerVisible.value = true
  if (route.name !== 'IPAddressLogs' || String(route.params.id) !== String(ip.id)) {
    router.push(`/resource/ip/addresses/${ip.id}/logs`)
  }
  await loadIPLogs()
}
function closeIPLog() {
  logDrawerVisible.value = false
  if (opContext.value === 'detail' && currentBlock.value) {
    router.push(`/resource/ip/blocks/${currentBlock.value.id}`)
  } else {
    router.push('/resource/ip')
  }
}

async function loadIPLogs() {
  if (!currentIP.value) return
  loadingLogs.value = true
  try {
    const res = await getIPAddressLogs(currentIP.value.id, { page: logPage.value, page_size: 20 })
    ipLogs.value   = res?.results || []
    logTotal.value = res?.count   || 0
  } catch (e) {
    ElMessage.error('加载日志失败：' + (e?.response?.data?.detail || e.message))
  } finally { loadingLogs.value = false }
}

// ── 辅助 ─────────────────────────────────────────────────────
function resetAddrSearch() {
  Object.assign(addrSearch, { status:'', ip_block:'', search:'' })
  resetLocation()
  loadAddresses()
}

watch(activeTab, tab => { if (tab === 'addresses') loadAddresses() })

watch(() => assignForm.target_type, () => {
  assignForm.target_id = null
  assignSearch.value = ''
  resetAssignRouteForm()
})

watch(() => assignForm.create_route, async (on) => {
  if (!on || assignForm.target_type !== 'edge' || !assignForm.target_id) return
  if (selectedEdgeDetail.value?.id === assignForm.target_id) {
    applyEdgeRouteHints(selectedEdgeDetail.value, currentIP.value)
    return
  }
  await selectEdgeForAssign(assignForm.target_id)
})

// 路由回退（返回列表）时关闭整页详情
watch(() => route.name, (name) => {
  if (name === 'IPManage') {
    blockDetailVisible.value = false
    logDrawerVisible.value = false
  }
})

async function loadNetworkTypes() {
  const res = await getNetworkTypes({ page_size: 200 })
  const list = res?.results || res || []
  networkTypeAll.value = list
  networkTypeOptions.value = list
}

function openNetworkTypeDialog() {
  networkTypeForm.code = ''
  networkTypeForm.name = ''
  networkTypeForm.sort_order = networkTypeAll.value.length
  networkTypeDialogVisible.value = true
  loadNetworkTypes().catch(() => {})
}

async function saveNetworkType() {
  if (!networkTypeForm.code || !networkTypeForm.name) {
    ElMessage.warning('请填写代码和名称')
    return
  }
  networkTypeSaving.value = true
  try {
    await createNetworkType({
      code: networkTypeForm.code,
      name: networkTypeForm.name,
      sort_order: networkTypeForm.sort_order || 0,
      is_active: true,
    })
    ElMessage.success('已新增网络类型')
    networkTypeForm.code = ''
    networkTypeForm.name = ''
    await loadNetworkTypes()
  } finally {
    networkTypeSaving.value = false
  }
}

async function updateNetworkTypeRow(row) {
  await updateNetworkType(row.id, {
    name: row.name,
    sort_order: row.sort_order,
    is_active: row.is_active,
  })
  await loadNetworkTypes()
}

async function removeNetworkType(row) {
  await ElMessageBox.confirm(`确定删除网络类型「${row.name}」？`, '提示', { type: 'warning' })
  try {
    await deleteNetworkType(row.id)
    ElMessage.success('已删除')
    await loadNetworkTypes()
  } catch (e) {
    /* 仍被地址块引用时后端会拒绝，拦截器已提示 */
  }
}

onMounted(async () => {
  loadBlocks()
  const [, cRes] = await Promise.allSettled([
    loadLocationOptions(),
    getCarriers({ page_size: 200 }),
    loadNetworkTypes(),
  ])
  carriers.value  = cRes.status === 'fulfilled' ? cRes.value?.results || [] : []

  // 直接访问详情/日志路由但缺少上下文数据（如刷新）时回退到列表
  if ((route.name === 'IPBlockDetail' && !currentBlock.value) ||
      (route.name === 'IPAddressLogs' && !currentIP.value)) {
    router.replace('/resource/ip')
  }
})
</script>

<style scoped>
.page-container { min-height: 100%; }
.page-header { display:flex; align-items:center; justify-content:space-between; margin-bottom:16px; }
.page-title  { font-size:18px; font-weight:700; color:#1a1a2e; margin:0; }
.search-card :deep(.el-card__body) { padding:16px 16px 0; }
.pagination { margin-top:16px; justify-content:flex-end; }

/* ── 统计卡片行 ── */
.stat-row { display:flex; gap:10px; margin-bottom:16px; }
.stat-card {
  flex:1; text-align:center; padding:10px 6px;
  border-radius:8px; border:1px solid #e4e7ed; background:#f5f7fa;
}
.stat-card.avail    { border-color:#67C23A; background:#f0f9eb; }
.stat-card.assigned { border-color:#409EFF; background:#ecf5ff; }
.stat-card.reserved { border-color:#E6A23C; background:#fdf6ec; }
.stat-card.blocked  { border-color:#F56C6C; background:#fef0f0; }
.sc-num { font-size:20px; font-weight:700; color:#303133; line-height:1.3; }
.stat-card.avail    .sc-num { color:#67C23A; }
.stat-card.assigned .sc-num { color:#409EFF; }
.stat-card.reserved .sc-num { color:#E6A23C; }
.stat-card.blocked  .sc-num { color:#F56C6C; }
.sc-lbl { font-size:11px; color:#909399; margin-top:3px; }

/* ── 分配弹窗列表 ── */
.assign-list {
  width: 100%; max-height:280px; overflow-y:auto;
  border:1px solid #e4e7ed; border-radius:6px;
}
.assign-item {
  padding:10px 14px; cursor:pointer;
  border-bottom:1px solid #f0f2f5; transition:background .15s;
}
.assign-item:last-child { border-bottom:none; }
.assign-item:hover { background:#f5f7fa; }
.assign-item.selected {
  background:#ecf5ff;
  border-left:3px solid #409EFF;
}
.assign-item-main { display:flex; align-items:center; gap:6px; margin-bottom:4px; }
.assign-name {
  font-size:13px; font-weight:600; flex:1;
  overflow:hidden; text-overflow:ellipsis; white-space:nowrap;
}
.assign-sub { font-size:11px; color:#909399; padding-left:2px; }
.assign-empty { text-align:center; color:#C0C4CC; padding:24px 0; font-size:13px; }

/* ── IP 日志卡片 ── */
.log-card { padding: 10px 14px !important; }
.log-card :deep(.el-card__body) { padding: 0; }
.log-card-header { display:flex; align-items:center; margin-bottom:6px; }
.log-op { font-size:12px; color:#606266; }
.log-desc { font-size:13px; color:#303133; margin-bottom:4px; line-height:1.5; }
.log-extra { display:flex; align-items:center; flex-wrap:wrap; gap:4px; margin-top:4px; }
.log-val { font-size:12px; padding:2px 6px; border-radius:4px; }
.log-val.old { background:#fef0f0; color:#F56C6C; }
.log-val.new { background:#f0f9eb; color:#67C23A; }

/* ── 状态选项 ── */
.status-option {
  display:flex; align-items:center; gap:12px;
  padding:10px 14px; border:1px solid #e4e7ed;
  border-radius:6px; cursor:pointer; transition:all .15s;
}
.status-option:hover { border-color:#409EFF; background:#f5f9ff; }
.status-option.status-selected { border-color:#409EFF; background:#ecf5ff; }
.status-option.status-disabled { opacity:.5; cursor:not-allowed; background:#f5f7fa; }
.status-desc { font-size:12px; color:#606266; flex:1; }

/* ── 整页详情（IP块详情 / 操作日志） ── */
.ip-detail-page { padding: 4px 2px; }
.ip-detail-header {
  display:flex; align-items:center; gap:10px;
  padding-bottom:14px; margin-bottom:16px;
  border-bottom:1px solid #ebeef5;
}
</style>
