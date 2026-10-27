<template>
  <div v-loading="loading && !detail" class="edge-detail-page">
    <!-- ── 标题栏 ── -->
    <div class="page-header">
      <div class="dh" v-if="detail">
        <div class="dh-left">
          <el-button :icon="ArrowLeft" @click="goBack" class="dh-back">返回</el-button>
          <el-tag :type="statusMap[detail.status]?.type" size="large">
            {{ statusMap[detail.status]?.label }}
          </el-tag>
          <el-tag :type="protoStyle[detail.protocol]?.type" size="large">
            {{ protoLabel[detail.protocol] || detail.protocol }}
          </el-tag>
          <span class="dh-no">{{ detail.edge_no }}</span>
          <span class="dh-name">{{ detail.name }}</span>
          <el-tag type="info" size="small">{{ detail.pop_name }}</el-tag>
        </div>
        <div class="dh-bw">
          <span class="bw-num">{{ detail.bandwidth_mbps }}</span>
          <span class="bw-lbl">Mbps</span>
        </div>
        <div class="dh-actions">
          <el-tag v-if="detail.protocol !== 'socks5'" :type="detail.is_synced ? 'success' : 'info'" size="small" effect="plain">
            {{ detail.is_synced ? `已下发 ${detail.node_ifname || ''}` : '未下发' }}
          </el-tag>
          <el-button size="small" :icon="Refresh" :loading="loading" @click="load">刷新</el-button>
          <el-button v-if="detail.protocol !== 'socks5'" size="small" type="warning"
            :icon="Promotion" :loading="pushing" @click="openPush">下发到节点</el-button>
          <el-button size="small" type="primary" @click="goEdit">编辑</el-button>
        </div>
      </div>
      <div v-else class="loading-tip">
        <el-button :icon="ArrowLeft" @click="goBack">返回</el-button>
        <span v-if="loading" style="margin-left:12px;"><el-icon class="is-loading"><Loading/></el-icon> 加载中…</span>
      </div>
    </div>

    <div v-if="loading && !detail" v-loading="true" style="height:280px;"/>

    <el-tabs v-else-if="detail" v-model="tab" class="dtabs">

      <!-- ★ 概览 ★ -->
      <el-tab-pane label="概览" name="overview">
        <el-row :gutter="14">
          <!-- 基础信息 -->
          <el-col :xs="24" :sm="12">
            <el-card shadow="never" class="ic" header="基础信息">
              <el-descriptions :column="1" border size="small">
                <el-descriptions-item label="加速编号">
                  <el-tag type="success" size="small">{{ detail.edge_no }}</el-tag>
                </el-descriptions-item>
                <el-descriptions-item label="状态">
                  <el-tag :type="statusMap[detail.status]?.type" size="small">{{ statusMap[detail.status]?.label }}</el-tag>
                  <el-button link type="primary" size="small" style="margin-left:8px;" @click="openStatus">变更</el-button>
                </el-descriptions-item>
                <el-descriptions-item label="隧道协议">
                  <el-tag :type="protoStyle[detail.protocol]?.type">{{ protoLabel[detail.protocol] || detail.protocol }}</el-tag>
                </el-descriptions-item>
                <el-descriptions-item label="POP节点">{{ detail.pop_name }}</el-descriptions-item>
                <el-descriptions-item label="带宽限制">{{ detail.bandwidth_mbps }} Mbps</el-descriptions-item>
                <el-descriptions-item label="关联路由表">
                  <span v-if="detail.route_table">
                    Table {{ detail.route_table.table_id }} · {{ detail.route_table.name || '-' }}
                  </span>
                  <span v-else style="color:#C0C4CC;">-</span>
                </el-descriptions-item>
              </el-descriptions>
            </el-card>
          </el-col>

          <!-- 客户与周期 -->
          <el-col :xs="24" :sm="12">
            <el-card shadow="never" class="ic" header="客户与服务周期">
              <el-descriptions :column="1" border size="small">
                <el-descriptions-item label="关联客户">{{ detail.customer_name || '-' }}</el-descriptions-item>
                <el-descriptions-item label="开通时间">{{ fmt(detail.start_time) }}</el-descriptions-item>
                <el-descriptions-item label="到期时间">
                  <span :class="expCls(detail.expire_time)">{{ fmt(detail.expire_time) || '无期限' }}</span>
                </el-descriptions-item>
                <el-descriptions-item label="创建时间">{{ fmt(detail.created_at) }}</el-descriptions-item>
                <el-descriptions-item label="更新时间">{{ fmt(detail.updated_at) }}</el-descriptions-item>
                <el-descriptions-item label="备注">{{ detail.remark || '-' }}</el-descriptions-item>
              </el-descriptions>
            </el-card>
          </el-col>

          <!-- IP汇总 -->
          <el-col :span="24" style="margin-top:14px;">
            <el-card shadow="never" class="ic" header="已分配IP地址">
              <div v-if="detail.ip_list?.length" style="display:flex;flex-wrap:wrap;gap:6px;">
                <el-tag
                  v-for="ip in detail.ip_list" :key="ip.id"
                  :type="ip.is_primary?'success':'info'" size="small"
                >
                  {{ ip.address }}/{{ ip.prefix_len }}
                  <span v-if="ip.gateway" style="margin-left:4px;font-weight:400">gw {{ ip.gateway }}</span>
                  <span v-if="ip.is_primary" style="margin-left:3px;">★</span>
                </el-tag>
              </div>
              <el-empty v-else description="暂无IP" :image-size="48"/>
            </el-card>
          </el-col>
        </el-row>
      </el-tab-pane>

      <!-- ★ 协议配置 ★ -->
      <el-tab-pane name="config" label="协议配置">
        <!-- GRE 配置 -->
        <template v-if="detail.protocol === 'gre'">
          <div class="config-header">
            <el-tag type="">GRE 隧道配置</el-tag>
            <el-button type="primary" size="small" :icon="Edit" style="margin-left:auto;" @click="openConfigEdit">
              {{ detail.gre_config ? '修改配置' : '创建配置' }}
            </el-button>
          </div>
          <div v-if="detail.gre_config" class="config-body">
            <el-descriptions :column="2" border size="small">
              <el-descriptions-item label="本端IP">
                <el-tag type="primary" size="small">{{ detail.gre_config.local_ip }}</el-tag>
              </el-descriptions-item>
              <el-descriptions-item label="远端IP">
                <el-tag type="warning" size="small">{{ detail.gre_config.remote_ip }}</el-tag>
              </el-descriptions-item>
              <el-descriptions-item label="隧道IP/CIDR">{{ detail.gre_config.tunnel_ip || '-' }}</el-descriptions-item>
              <el-descriptions-item label="GRE Key">{{ detail.gre_config.key || '-' }}</el-descriptions-item>
              <el-descriptions-item label="TTL">{{ detail.gre_config.ttl }}</el-descriptions-item>
              <el-descriptions-item label="MTU">{{ detail.gre_config.mtu ?? 1400 }}</el-descriptions-item>
              <el-descriptions-item label="PMTUD">
                <el-tag :type="detail.gre_config.pmtud?'success':'info'" size="small">
                  {{ detail.gre_config.pmtud?'启用':'禁用' }}
                </el-tag>
              </el-descriptions-item>
              <el-descriptions-item label="Checksum">
                <el-tag :type="detail.gre_config.checksum?'success':'info'" size="small">
                  {{ detail.gre_config.checksum?'启用':'禁用' }}
                </el-tag>
              </el-descriptions-item>
            </el-descriptions>
          </div>
          <el-empty v-else description="尚未配置GRE参数" :image-size="60">
            <el-button type="primary" @click="openConfigEdit">立即配置</el-button>
          </el-empty>
        </template>

        <!-- VXLAN 配置 -->
        <template v-else-if="detail.protocol === 'vxlan'">
          <div class="config-header">
            <el-tag type="primary">VXLAN 隧道配置</el-tag>
            <el-button type="primary" size="small" :icon="Edit" style="margin-left:auto;" @click="openConfigEdit">
              {{ detail.vxlan_config ? '修改配置' : '创建配置' }}
            </el-button>
          </div>
          <div v-if="detail.vxlan_config" class="config-body">
            <el-descriptions :column="2" border size="small">
              <el-descriptions-item label="VNI">
                <el-tag type="primary" size="small">{{ detail.vxlan_config.vni }}</el-tag>
              </el-descriptions-item>
              <el-descriptions-item label="UDP端口">{{ detail.vxlan_config.port }}</el-descriptions-item>
              <el-descriptions-item label="本端VTEP">{{ detail.vxlan_config.local_ip || '-' }}</el-descriptions-item>
              <el-descriptions-item label="远端VTEP">{{ detail.vxlan_config.remote_ip || '-' }}</el-descriptions-item>
              <el-descriptions-item label="隧道IP/CIDR">{{ detail.vxlan_config.tunnel_ip || '-' }}</el-descriptions-item>
              <el-descriptions-item label="TTL">{{ detail.vxlan_config.ttl }}</el-descriptions-item>
              <el-descriptions-item label="MTU">{{ detail.vxlan_config.mtu ?? 1400 }}</el-descriptions-item>
            </el-descriptions>
          </div>
          <el-empty v-else description="尚未配置VXLAN参数" :image-size="60">
            <el-button type="primary" @click="openConfigEdit">立即配置</el-button>
          </el-empty>
        </template>

        <!-- WireGuard 配置 -->
        <template v-else-if="detail.protocol === 'wireguard'">
          <div class="config-header">
            <el-tag type="success">WireGuard 配置</el-tag>
            <el-button type="primary" size="small" :icon="Edit" style="margin-left:auto;" @click="openConfigEdit">
              {{ detail.wg_config ? '修改配置' : '创建配置' }}
            </el-button>
          </div>
          <div v-if="detail.wg_config">
            <el-descriptions :column="2" border size="small" style="margin-bottom:12px;">
              <el-descriptions-item label="监听端口">
                <el-tag type="primary" size="small">{{ detail.wg_config.listen_port }}</el-tag>
              </el-descriptions-item>
              <el-descriptions-item label="接口IP/CIDR">{{ detail.wg_config.interface_ip }}</el-descriptions-item>
              <el-descriptions-item label="公钥" :span="2">
                <el-text type="info" size="small" style="word-break:break-all;">
                  {{ detail.wg_config.public_key || '-' }}
                </el-text>
              </el-descriptions-item>
              <el-descriptions-item label="对端公钥" :span="2">
                <el-text type="info" size="small" style="word-break:break-all;">
                  {{ detail.wg_config.peer_public_key || '-' }}
                </el-text>
              </el-descriptions-item>
              <el-descriptions-item label="对端地址">{{ detail.wg_config.peer_endpoint || '-' }}</el-descriptions-item>
              <el-descriptions-item label="允许网段">{{ detail.wg_config.peer_allowed_ips || '-' }}</el-descriptions-item>
              <el-descriptions-item label="Keepalive">{{ detail.wg_config.persistent_keepalive }}s</el-descriptions-item>
              <el-descriptions-item label="DNS">{{ detail.wg_config.dns || '-' }}</el-descriptions-item>
              <el-descriptions-item label="FwMark">{{ detail.wg_config.fwmark ?? '-' }}</el-descriptions-item>
              <el-descriptions-item label="MTU">{{ detail.wg_config.mtu ?? '-' }}</el-descriptions-item>
            </el-descriptions>
            <!-- PostUp/PostDown 脚本 -->
            <el-row :gutter="14" v-if="detail.wg_config.post_up || detail.wg_config.post_down">
              <el-col :span="12" v-if="detail.wg_config.post_up">
                <el-card shadow="never" size="small" header="PostUp 脚本">
                  <pre class="script-pre">{{ detail.wg_config.post_up }}</pre>
                </el-card>
              </el-col>
              <el-col :span="12" v-if="detail.wg_config.post_down">
                <el-card shadow="never" size="small" header="PostDown 脚本">
                  <pre class="script-pre">{{ detail.wg_config.post_down }}</pre>
                </el-card>
              </el-col>
            </el-row>
          </div>
          <el-empty v-else description="尚未配置WireGuard参数" :image-size="60">
            <el-button type="primary" @click="openConfigEdit">立即配置</el-button>
          </el-empty>
        </template>

        <!-- Socks5 配置 -->
        <template v-else-if="detail.protocol === 'socks5'">
          <div class="config-header">
            <el-tag type="warning">Socks5 配置</el-tag>
            <el-button type="primary" size="small" :icon="Edit" style="margin-left:auto;" @click="openConfigEdit">
              {{ detail.socks5_config ? '修改配置' : '创建配置' }}
            </el-button>
          </div>
          <div v-if="detail.socks5_config">
            <el-descriptions :column="2" border size="small">
              <el-descriptions-item label="监听IP">{{ detail.socks5_config.listen_ip }}</el-descriptions-item>
              <el-descriptions-item label="监听端口">
                <el-tag type="primary" size="small">{{ detail.socks5_config.listen_port }}</el-tag>
              </el-descriptions-item>
              <el-descriptions-item label="需要认证">
                <el-tag :type="detail.socks5_config.auth_required?'warning':'info'" size="small">
                  {{ detail.socks5_config.auth_required?'是':'否' }}
                </el-tag>
              </el-descriptions-item>
              <el-descriptions-item label="用户名">{{ detail.socks5_config.username || '-' }}</el-descriptions-item>
              <el-descriptions-item label="UDP支持">
                <el-tag :type="detail.socks5_config.udp_enabled?'success':'info'" size="small">
                  {{ detail.socks5_config.udp_enabled?'启用':'禁用' }}
                </el-tag>
              </el-descriptions-item>
              <el-descriptions-item label="出口绑定IP">{{ detail.socks5_config.bind_address || '-' }}</el-descriptions-item>
              <el-descriptions-item label="最大连接数">{{ detail.socks5_config.max_connections }}</el-descriptions-item>
              <el-descriptions-item label="超时(秒)">{{ detail.socks5_config.timeout_seconds }}</el-descriptions-item>
            </el-descriptions>
          </div>
          <el-empty v-else description="尚未配置Socks5参数" :image-size="60">
            <el-button type="primary" @click="openConfigEdit">立即配置</el-button>
          </el-empty>
        </template>

        <!-- 节点下发预览（与 vpopmanager desired-tunnel netplan_tunnel 一致） -->
        <el-card v-if="detail.protocol !== 'socks5' && detail.tunnel_spec" shadow="never"
          class="ic" style="margin-top:14px;">
          <template #header>
            <div style="display:flex;align-items:center;gap:8px;">
              <span>节点下发预览</span>
              <el-tag size="small" type="info">接口名 {{ detail.node_ifname || detail.node_ifname_preview }}</el-tag>
              <el-tag size="small">{{ detail.tunnel_spec.kind }}</el-tag>
              <el-tag v-if="detail.is_synced" size="small" type="success">已下发</el-tag>
              <el-button size="small" type="warning" :icon="Promotion" style="margin-left:auto;"
                @click="openPush">下发到节点</el-button>
            </div>
          </template>
          <pre class="script-pre">{{ JSON.stringify(detail.tunnel_spec.spec, null, 2) }}</pre>
          <div class="push-note">该结构将写入节点 /api/interfacemanage/db/desired-tunnels（spec.netplan_tunnel 为唯一配置源），与 vpopmanager 完全一致。</div>
        </el-card>
      </el-tab-pane>

      <!-- ★ IP地址 ★ -->
      <el-tab-pane name="ips">
        <template #label>
          IP地址 <el-badge :value="detail.ip_list?.length" type="success"/>
        </template>
        <el-table :data="detail.ip_list" border stripe size="small" empty-text="暂无IP地址">
          <el-table-column label="IP地址" width="160">
            <template #default="{row}">
              <el-tag :type="row.is_primary?'success':'info'" size="small">{{ row.address }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="prefix_len" label="/前缀" width="65" align="center"/>
          <el-table-column label="网关" width="140">
            <template #default="{row}">{{ row.gateway || '-' }}</template>
          </el-table-column>
          <el-table-column label="掩码" width="140">
            <template #default="{row}">{{ row.netmask || '-' }}</template>
          </el-table-column>
          <el-table-column label="状态" width="90">
            <template #default="{row}">
              <el-tag :type="ipStatusMap[row.status]?.type" size="small">
                {{ ipStatusMap[row.status]?.label || row.status }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="主IP" width="65" align="center">
            <template #default="{row}">
              <el-icon v-if="row.is_primary" color="#67C23A"><Select/></el-icon>
              <span v-else style="color:#C0C4CC;">-</span>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      <!-- ★ 监控 ★ -->
      <el-tab-pane label="监控" name="monitor">
        <MonitoringPanel
          target-type="edge"
          :resource-id="detail.id"
          :resource-name="detail.name"
          :pop-point-id="detail.pop_point"
          :active="tab === 'monitor'"
        />
      </el-tab-pane>

      <!-- ★ 操作日志 ★ -->
      <el-tab-pane name="logs" label="操作日志">
        <el-timeline style="padding:8px 0 0;">
          <el-timeline-item
            v-for="log in detail.operation_logs"
            :key="log.id"
            :timestamp="fmt(log.created_at)"
            :type="logType(log.action_type)"
            placement="top"
          >
            <div class="log-row">
              <el-tag size="small" :type="logType(log.action_type)" style="margin-right:6px;">
                {{ log.action_type_label || log.action_type }}
              </el-tag>
              {{ log.description }}
              <el-text type="info" size="small" style="margin-left:8px;">{{ log.operator_name || '系统' }}</el-text>
              <div v-if="formatLogDiff(log.old_value, log.new_value)" class="log-diff">
                {{ formatLogDiff(log.old_value, log.new_value) }}
              </div>
            </div>
          </el-timeline-item>
        </el-timeline>
        <el-empty v-if="!detail.operation_logs?.length" description="暂无操作日志" :image-size="60"/>
      </el-tab-pane>
    </el-tabs>
  </div>

  <!-- ── 状态变更弹窗 ── -->
  <el-dialog v-model="statusDlg" title="变更加速节点状态" width="380px" destroy-on-close append-to-body>
    <el-form :model="statusForm" label-width="80px">
      <el-form-item label="新状态">
        <el-select v-model="statusForm.status" style="width:100%">
          <el-option v-for="s in statusOptions" :key="s.value" :label="s.label" :value="s.value"/>
        </el-select>
      </el-form-item>
      <el-form-item label="原因">
        <el-input v-model="statusForm.reason" type="textarea" :rows="2"/>
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="statusDlg=false">取消</el-button>
      <el-button type="primary" :loading="statusLoading" @click="doStatus">确认</el-button>
    </template>
  </el-dialog>

  <!-- ── 协议配置编辑弹窗 ── -->
  <el-dialog v-model="configDlg" :title="`${protoLabel[detail?.protocol]} 配置`" width="580px" destroy-on-close append-to-body>
    <!-- GRE -->
    <el-form v-if="detail?.protocol==='gre'" :model="configForm" label-width="110px">
      <el-row :gutter="16">
        <el-col :span="12">
          <el-form-item label="本端IP" required>
            <el-input v-model="configForm.local_ip" placeholder="如 203.0.113.1"/>
          </el-form-item>
        </el-col>
        <el-col :span="12">
          <el-form-item label="远端IP" required>
            <el-input v-model="configForm.remote_ip" placeholder="如 198.51.100.1"/>
          </el-form-item>
        </el-col>
        <el-col :span="12">
          <el-form-item label="隧道IP/CIDR">
            <el-input v-model="configForm.tunnel_ip" placeholder="如 10.10.0.1/30"/>
          </el-form-item>
        </el-col>
        <el-col :span="12">
          <el-form-item label="GRE Key">
            <el-input v-model="configForm.key" placeholder="可选"/>
          </el-form-item>
        </el-col>
        <el-col :span="12">
          <el-form-item label="TTL">
            <el-input-number v-model="configForm.ttl" :min="1" :max="255" controls-position="right"
              placeholder="留空不下发" style="width:100%"/>
          </el-form-item>
        </el-col>
        <el-col :span="12">
          <el-form-item label="MTU">
            <el-input-number v-model="configForm.mtu" :min="68" :max="65535" controls-position="right" style="width:100%"/>
          </el-form-item>
        </el-col>
        <el-col :span="12">
          <el-form-item label="PMTUD">
            <el-switch v-model="configForm.pmtud" active-text="启用" inactive-text="禁用"/>
          </el-form-item>
        </el-col>
        <el-col :span="12">
          <el-form-item label="Checksum">
            <el-switch v-model="configForm.checksum" active-text="启用" inactive-text="禁用"/>
          </el-form-item>
        </el-col>
      </el-row>
    </el-form>

    <!-- VXLAN -->
    <el-form v-else-if="detail?.protocol==='vxlan'" :model="configForm" label-width="110px">
      <el-row :gutter="16">
        <el-col :span="12">
          <el-form-item label="VNI" required>
            <el-input-number v-model="configForm.vni" :min="1" :max="16777215" style="width:100%"/>
          </el-form-item>
        </el-col>
        <el-col :span="12">
          <el-form-item label="UDP端口">
            <el-input-number v-model="configForm.port" :min="1" :max="65535" style="width:100%"/>
          </el-form-item>
        </el-col>
        <el-col :span="12">
          <el-form-item label="本端VTEP IP">
            <el-input v-model="configForm.local_ip" placeholder="如 203.0.113.1"/>
          </el-form-item>
        </el-col>
        <el-col :span="12">
          <el-form-item label="远端VTEP IP">
            <el-input v-model="configForm.remote_ip" placeholder="如 198.51.100.1"/>
          </el-form-item>
        </el-col>
        <el-col :span="12">
          <el-form-item label="隧道IP/CIDR">
            <el-input v-model="configForm.tunnel_ip" placeholder="如 10.20.0.1/30"/>
          </el-form-item>
        </el-col>
        <el-col :span="12">
          <el-form-item label="TTL">
            <el-input-number v-model="configForm.ttl" :min="1" :max="255" style="width:100%"/>
          </el-form-item>
        </el-col>
        <el-col :span="12">
          <el-form-item label="MTU">
            <el-input-number v-model="configForm.mtu" :min="68" :max="65535" controls-position="right" style="width:100%"/>
          </el-form-item>
        </el-col>
      </el-row>
    </el-form>

    <!-- WireGuard -->
    <el-form v-else-if="detail?.protocol==='wireguard'" :model="configForm" label-width="110px">
      <el-row :gutter="16">
        <el-col :span="12">
          <el-form-item label="监听端口">
            <el-input-number v-model="configForm.listen_port" :min="1" :max="65535" style="width:100%"/>
          </el-form-item>
        </el-col>
        <el-col :span="12">
          <el-form-item label="接口IP/CIDR">
            <el-input v-model="configForm.interface_ip" placeholder="如 10.100.0.1/24"/>
          </el-form-item>
        </el-col>
        <el-col :span="24">
          <el-form-item label="本端密钥对">
            <el-button :icon="Key" :loading="wgKeyLoading" @click="genWgKey">通过节点生成密钥对</el-button>
            <el-text type="info" size="small" style="margin-left:8px;">调用所属 POP 节点 WireGuard 工具</el-text>
          </el-form-item>
        </el-col>
        <el-col :span="24">
          <el-form-item label="私钥">
            <el-input v-model="configForm.private_key" type="password" show-password placeholder="WireGuard 私钥"/>
          </el-form-item>
        </el-col>
        <el-col :span="24">
          <el-form-item label="公钥">
            <el-input v-model="configForm.public_key" placeholder="WireGuard 公钥"/>
          </el-form-item>
        </el-col>
        <el-col :span="24">
          <el-form-item label="对端公钥">
            <el-input v-model="configForm.peer_public_key" placeholder="对端 WireGuard 公钥"/>
          </el-form-item>
        </el-col>
        <el-col :span="24">
          <el-form-item label="对端PSK">
            <el-input v-model="configForm.peer_preshared_key" type="password" show-password placeholder="可选，预共享密钥"/>
          </el-form-item>
        </el-col>
        <el-col :span="12">
          <el-form-item label="对端地址">
            <el-input v-model="configForm.peer_endpoint" placeholder="IP:Port 或纯 IP（自动补端口）"/>
          </el-form-item>
        </el-col>
        <el-col :span="12">
          <el-form-item label="Keepalive(s)">
            <el-input-number v-model="configForm.persistent_keepalive" :min="0" style="width:100%"/>
          </el-form-item>
        </el-col>
        <el-col :span="24">
          <el-form-item label="允许网段">
            <el-input v-model="configForm.peer_allowed_ips" placeholder="PtP 建议对端 /32；留空则从接口 IP 自动推断"/>
          </el-form-item>
        </el-col>
        <el-col :span="8">
          <el-form-item label="DNS">
            <el-input v-model="configForm.dns" placeholder="如 8.8.8.8"/>
          </el-form-item>
        </el-col>
        <el-col :span="8">
          <el-form-item label="FwMark">
            <el-input-number v-model="configForm.fwmark" :min="0" controls-position="right" style="width:100%"/>
          </el-form-item>
        </el-col>
        <el-col :span="8">
          <el-form-item label="MTU">
            <el-input-number v-model="configForm.mtu" :min="68" :max="65535" controls-position="right" style="width:100%"/>
          </el-form-item>
        </el-col>
        <el-col :span="24">
          <el-form-item label="PostUp">
            <el-input v-model="configForm.post_up" type="textarea" :rows="2" placeholder="PostUp 脚本"/>
          </el-form-item>
        </el-col>
        <el-col :span="24">
          <el-form-item label="PostDown">
            <el-input v-model="configForm.post_down" type="textarea" :rows="2" placeholder="PostDown 脚本"/>
          </el-form-item>
        </el-col>
      </el-row>
    </el-form>

    <!-- Socks5 -->
    <el-form v-else-if="detail?.protocol==='socks5'" :model="configForm" label-width="110px">
      <el-row :gutter="16">
        <el-col :span="12">
          <el-form-item label="监听IP">
            <el-input v-model="configForm.listen_ip" placeholder="0.0.0.0"/>
          </el-form-item>
        </el-col>
        <el-col :span="12">
          <el-form-item label="监听端口">
            <el-input-number v-model="configForm.listen_port" :min="1" :max="65535" style="width:100%"/>
          </el-form-item>
        </el-col>
        <el-col :span="12">
          <el-form-item label="需要认证">
            <el-switch v-model="configForm.auth_required"/>
          </el-form-item>
        </el-col>
        <el-col :span="12">
          <el-form-item label="UDP支持">
            <el-switch v-model="configForm.udp_enabled"/>
          </el-form-item>
        </el-col>
        <el-col :span="12">
          <el-form-item label="用户名">
            <el-input v-model="configForm.username"/>
          </el-form-item>
        </el-col>
        <el-col :span="12">
          <el-form-item label="密码">
            <el-input v-model="configForm.password" type="password" show-password/>
          </el-form-item>
        </el-col>
        <el-col :span="12">
          <el-form-item label="出口绑定IP">
            <el-input v-model="configForm.bind_address" placeholder="可选"/>
          </el-form-item>
        </el-col>
        <el-col :span="12">
          <el-form-item label="最大连接数">
            <el-input-number v-model="configForm.max_connections" :min="1" style="width:100%"/>
          </el-form-item>
        </el-col>
        <el-col :span="12">
          <el-form-item label="超时(秒)">
            <el-input-number v-model="configForm.timeout_seconds" :min="5" style="width:100%"/>
          </el-form-item>
        </el-col>
      </el-row>
    </el-form>

    <template #footer>
      <el-button @click="configDlg=false">取消</el-button>
      <el-button type="primary" :loading="configLoading" @click="saveConfig(false)">仅保存</el-button>
      <el-button v-if="detail?.protocol !== 'socks5'" type="warning" :loading="configLoading" @click="saveConfig(true)">
        保存并下发
      </el-button>
    </template>
  </el-dialog>

  <!-- ── 隧道下发弹窗（dry-run 预览 + 确认） ── -->
  <el-dialog v-model="pushDlg" title="隧道下发到节点" width="600px" destroy-on-close append-to-body>
    <el-alert type="info" :closable="false" show-icon style="margin-bottom:12px"
      title="先预览下发计划（dry-run），确认无误后再正式下发到节点并即时生效" />
    <div v-if="pushResult">
      <el-descriptions :column="1" border size="small">
        <el-descriptions-item label="边缘隧道">
          <template v-if="pushSummary">
            <el-tag v-if="pushSummary.would_push != null" size="small" type="primary">将下发 {{ pushSummary.would_push }}</el-tag>
            <el-tag v-if="pushSummary.pushed != null" size="small" type="success">已下发 {{ pushSummary.pushed }}</el-tag>
            <el-tag v-if="pushSummary.socks5_skipped" size="small" type="info" style="margin-left:4px">Socks5跳过 {{ pushSummary.socks5_skipped }}</el-tag>
            <el-tag v-if="pushSummary.skipped" size="small" type="info" style="margin-left:4px">忽略 {{ pushSummary.skipped }}</el-tag>
            <div v-if="pushSummary.note" class="push-note">{{ pushSummary.note }}</div>
            <div v-for="(item, i) in (pushSummary.plan || [])" :key="i" class="push-plan-item">
              {{ item.protocol }} {{ item.ifname }} {{ item.name || '' }} [{{ item.state }}]
            </div>
          </template>
        </el-descriptions-item>
      </el-descriptions>
      <div v-if="pushResult.errors && Object.keys(pushResult.errors).length" style="margin-top:8px">
        <el-alert v-for="(msg, k) in pushResult.errors" :key="k" :title="msg" type="error" :closable="false" style="margin-bottom:4px" />
      </div>
    </div>
    <template #footer>
      <el-button @click="pushDlg=false">关闭</el-button>
      <el-button type="primary" :loading="pushing" @click="doEdgePush(true)">预览计划</el-button>
      <el-button type="danger" :loading="pushing" :disabled="!pushPreviewed" @click="confirmEdgePush">确认下发</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Refresh, Select, Loading, Edit, Promotion, Key, ArrowLeft } from '@element-plus/icons-vue'
import { getEdgeDetail, changeEdgeStatus, updateEdgeProtocolConfig, pushEdgeToNode, genEdgeWgKey, getTunnelIPPools } from '@/api/resource'
import { confirmAndPush } from '@/composables/useConfirmPush'
import MonitoringPanel from '@/components/MonitoringPanel.vue'
import { formatLogDiff } from '@/utils/resourceLog'

const route = useRoute()
const router = useRouter()
const edgeId = computed(() => route.params.id)

function goBack() {
  router.push('/resource/edge')
}
function goEdit() {
  if (detail.value) router.push({ path: '/resource/edge', query: { edit: detail.value.id } })
}

const detail  = ref(null)
const loading = ref(false)
const tab     = ref('overview')

async function load() {
  if (!edgeId.value) return
  loading.value = true
  try { detail.value = await getEdgeDetail(edgeId.value) }
  catch (e) { ElMessage.error('加载失败：' + (e?.response?.data?.detail || e.message)) }
  finally { loading.value = false }
}
onMounted(load)

// ── 常量 ─────────────────────────────────────────────────────
const statusMap = {
  active:  { label:'运行中', type:'success' }, stopped: { label:'已停止', type:'info'    },
  pending: { label:'待配置', type:'warning' }, error:   { label:'故障',   type:'danger'  }
}
const statusOptions = [
  { value:'active', label:'运行中' }, { value:'stopped', label:'已停止' },
  { value:'pending', label:'待配置' }, { value:'error', label:'故障' }
]
const protoLabel = { gre:'GRE', vxlan:'VXLAN', wireguard:'WireGuard', socks5:'Socks5' }
const protoStyle = { gre:{ type:'' }, vxlan:{ type:'primary' }, wireguard:{ type:'success' }, socks5:{ type:'warning' } }
const ipStatusMap = {
  available: { label:'可用',   type:'success' }, assigned: { label:'已分配', type:'primary' },
  reserved:  { label:'预留',   type:'warning' }, blocked:  { label:'封禁',   type:'danger'  }
}

// ── 工具 ─────────────────────────────────────────────────────
function fmt(dt) {
  if (!dt) return '-'
  return new Date(dt).toLocaleString('zh-CN',{hour12:false}).replace(/\//g,'-').replace(',','')
}
function expCls(dt) {
  if (!dt) return ''
  const d = (new Date(dt) - Date.now()) / 86400000
  return d < 0 ? 'expired' : d < 30 ? 'expiring' : ''
}
function logType(a) {
  if (a === 'create') return 'success'
  if (['delete','ip_release'].includes(a)) return 'danger'
  if (['status_change'].includes(a)) return 'warning'
  if (['ip_assign','bw_alloc','config_change'].includes(a)) return 'primary'
  return ''
}

// ── 状态变更 ─────────────────────────────────────────────────
const statusDlg     = ref(false)
const statusLoading = ref(false)
const statusForm    = ref({ status:'active', reason:'' })
function openStatus() {
  statusForm.value = { status: detail.value?.status || 'active', reason:'' }
  statusDlg.value = true
}
async function doStatus() {
  statusLoading.value = true
  try {
    await changeEdgeStatus(detail.value.id, statusForm.value)
    ElMessage.success('状态已更新')
    statusDlg.value = false; load()
  } catch (e) { ElMessage.error('更新失败：' + (e?.response?.data?.detail || e.message)) }
  finally { statusLoading.value = false }
}

// ── 隧道下发 ─────────────────────────────────────────────────
const pushDlg = ref(false)
const pushing = ref(false)
const pushResult = ref(null)
const pushPreviewed = ref(false)
const pushSummary = computed(() => pushResult.value?.summary?.edge_tunnels || null)

function openPush() {
  pushResult.value = null
  pushPreviewed.value = false
  pushDlg.value = true
}

async function doEdgePush(dryRun) {
  pushing.value = true
  try {
    const res = await pushEdgeToNode(detail.value.id, dryRun)
    pushResult.value = res
    if (dryRun) {
      pushPreviewed.value = true
      ElMessage.success('已生成下发计划')
    } else {
      pushPreviewed.value = false
      if (res.ok) ElMessage.success('隧道已下发到节点')
      else ElMessage.warning('下发完成，部分失败')
      load()
    }
  } catch (e) {
    ElMessage.error('下发失败：' + (e?.response?.data?.detail || e.message))
  } finally { pushing.value = false }
}

async function confirmEdgePush() {
  await ElMessageBox.confirm('将把该隧道配置真正写入节点并即时生效，确定继续？', '确认下发', { type: 'warning' })
  doEdgePush(false)
}

// ── 协议配置 ─────────────────────────────────────────────────
const configDlg     = ref(false)
const configLoading = ref(false)
const configForm    = ref({})

function openConfigEdit() {
  const p = detail.value?.protocol
  let isNew = false
  if (p === 'gre') {
    const c = detail.value.gre_config || {}
    isNew = !detail.value.gre_config
    configForm.value = { local_ip: c.local_ip||'', remote_ip: c.remote_ip||'', tunnel_ip: c.tunnel_ip||'', key: c.key||'', ttl: c.ttl ?? null, mtu: c.mtu ?? 1400, pmtud: c.pmtud!==false, checksum: !!c.checksum }
  } else if (p === 'vxlan') {
    const c = detail.value.vxlan_config || {}
    isNew = !detail.value.vxlan_config
    configForm.value = {
      vni: c.vni||1, port: c.port||8472, local_ip: c.local_ip||'', remote_ip: c.remote_ip||'',
      tunnel_ip: c.tunnel_ip||'', ttl: c.ttl||255, mtu: c.mtu ?? 1400,
    }
  } else if (p === 'wireguard') {
    const c = detail.value.wg_config || {}
    isNew = !detail.value.wg_config
    configForm.value = {
      listen_port: c.listen_port||51820, interface_ip: c.interface_ip||'',
      private_key: '', public_key: c.public_key||'',
      peer_public_key: c.peer_public_key||'', peer_preshared_key: '', peer_endpoint: c.peer_endpoint||'',
      peer_allowed_ips: c.peer_allowed_ips||'', persistent_keepalive: c.persistent_keepalive||25,
      dns: c.dns||'', fwmark: c.fwmark||null, mtu: c.mtu ?? 1400,
      post_up: c.post_up||'', post_down: c.post_down||'',
    }
  } else if (p === 'socks5') {
    const c = detail.value.socks5_config || {}
    configForm.value = {
      listen_ip: c.listen_ip||'0.0.0.0', listen_port: c.listen_port||1080,
      auth_required: c.auth_required!==false, username: c.username||'', password: '',
      udp_enabled: c.udp_enabled!==false, bind_address: c.bind_address||'',
      max_connections: c.max_connections||1000, timeout_seconds: c.timeout_seconds||60,
    }
  }
  configDlg.value = true
  // 新建隧道配置时，自动从该 POP 的「隧道 IP 段」池取下一段可用地址回填 overlay IP
  if (isNew) prefillFromTunnelPool(p)
}

// 从边缘加速所属 POP 的隧道 IP 段（p2p 池）自动填充 overlay 隧道 IP。
// GRE/VXLAN → tunnel_ip（本端 /30）；WireGuard → interface_ip + peer_allowed_ips（对端 /32）。
async function prefillFromTunnelPool(proto) {
  const popId = detail.value?.pop_point
  if (!popId || !['gre', 'vxlan', 'wireguard'].includes(proto)) return
  try {
    const res = await getTunnelIPPools({ pop_point: popId, is_active: true, page_size: 100 })
    const pools = res?.results || []
    const pool = pools.find((x) => x.next_free)
    if (!pool || !pool.next_free) {
      if (pools.length > 0) {
        ElMessage.warning('该 POP 的隧道 IP 段已无可用 p2p 块，请到「资源管理 → 隧道IP段」补充地址段')
      }
      return
    }
    const nf = pool.next_free
    if (proto === 'gre' || proto === 'vxlan') {
      if (!configForm.value.tunnel_ip) configForm.value.tunnel_ip = nf.local_cidr
    } else if (proto === 'wireguard') {
      if (!configForm.value.interface_ip) configForm.value.interface_ip = nf.local_cidr
      if (!configForm.value.peer_allowed_ips) configForm.value.peer_allowed_ips = `${nf.peer}/32`
    }
    ElMessage.success(`已从隧道 IP 段「${pool.name}」自动填充：本端 ${nf.local_cidr}，对端 ${nf.peer_cidr}`)
  } catch (e) {
    // 自动填充失败不阻断手工配置
    console.warn('[prefillFromTunnelPool] failed:', e?.response?.data || e?.message)
  }
}

const wgKeyLoading = ref(false)
async function genWgKey() {
  const popId = detail.value?.pop_point
  if (!popId) { ElMessage.warning('该边缘加速未关联 POP 点，无法生成密钥'); return }
  wgKeyLoading.value = true
  try {
    const res = await genEdgeWgKey(popId)
    if (res?.private) configForm.value.private_key = res.private
    if (res?.public) configForm.value.public_key = res.public
    ElMessage.success('已生成 WireGuard 密钥对')
  } catch (e) {
    ElMessage.error('生成失败：' + (e?.response?.data?.detail || e.message))
  } finally { wgKeyLoading.value = false }
}

async function saveConfig(andPush = false) {
  const p = detail.value?.protocol
  if (['gre', 'vxlan', 'wireguard'].includes(p)) {
    const mtu = Number(configForm.value.mtu)
    if (!Number.isInteger(mtu) || mtu < 68 || mtu > 65535) {
      ElMessage.error('MTU 必须是 68 到 65535 之间的整数')
      return
    }
  }
  if (p === 'gre' && !configForm.value.tunnel_ip) {
    ElMessage.error('GRE 请填写隧道 IP/CIDR（如 10.0.0.1/30）')
    return
  }
  if (p === 'vxlan' && !configForm.value.tunnel_ip) {
    ElMessage.error('VXLAN 请填写隧道 IP/CIDR')
    return
  }
  if (p === 'wireguard') {
    if (!configForm.value.interface_ip) {
      ElMessage.error('WireGuard 请填写接口 IP/CIDR')
      return
    }
    if (!configForm.value.peer_public_key) {
      ElMessage.error('WireGuard 请填写对端公钥')
      return
    }
  }
  configLoading.value = true
  try {
    const payload = { ...configForm.value }
    if (p === 'wireguard') {
      const port = Number(payload.listen_port) || 51820
      if (payload.peer_endpoint) {
        const ep = String(payload.peer_endpoint).trim()
        const hasPort = ep.includes(']:') || (ep.split(':').length === 2 && /^\d+$/.test(ep.split(':').pop()))
        if (!hasPort) payload.peer_endpoint = `${ep}:${port}`
      }
      if (!payload.peer_allowed_ips) {
        const local = String(payload.interface_ip || '').split(',')[0].trim()
        const m = local.match(/^(\d+\.\d+\.\d+\.\d+)\/(\d+)$/)
        if (m && (Number(m[2]) === 30 || Number(m[2]) === 31)) {
          const parts = m[1].split('.').map(Number)
          const n = (parts[0] << 24) + (parts[1] << 16) + (parts[2] << 8) + parts[3]
          const block = Number(m[2]) === 31 ? 2 : 4
          const base = n & ~(block - 1)
          const numToIp = (v) => [(v >>> 24) & 255, (v >>> 16) & 255, (v >>> 8) & 255, v & 255].join('.')
          for (const h of (Number(m[2]) === 31 ? [base, base + 1] : [base + 1, base + 2])) {
            const cand = numToIp(h)
            if (cand !== m[1]) {
              payload.peer_allowed_ips = `${cand}/32`
              break
            }
          }
        }
      }
    }
    if (p === 'wireguard' && !payload.private_key) delete payload.private_key
    if (p === 'wireguard' && !payload.peer_preshared_key) delete payload.peer_preshared_key
    if (p === 'socks5' && !payload.password) delete payload.password
    if (payload.bind_address === '') delete payload.bind_address
    if (p === 'gre' && !payload.ttl) payload.ttl = null
    await updateEdgeProtocolConfig(detail.value.id, payload)
    if (!andPush || p === 'socks5') {
      ElMessage.success(andPush ? '配置已保存' : '配置已保存，已标记待下发')
      configDlg.value = false
      load()
      return
    }
    configDlg.value = false
    await confirmAndPush(
      () => pushEdgeToNode(detail.value.id, true),
      () => pushEdgeToNode(detail.value.id, false),
      { title: '下发边缘隧道到节点', pushOk: '隧道已下发到节点并生效' },
    )
    load()
  } catch (e) {
    ElMessage.error('保存失败：' + (e?.response?.data?.detail || JSON.stringify(e?.response?.data) || e.message))
  } finally { configLoading.value = false }
}
</script>

<style scoped>
/* 整页容器 */
.edge-detail-page { background:#fff; border-radius:8px; padding:16px 20px 24px; min-height:calc(100vh - 120px); }
.page-header      { margin-bottom:16px; padding-bottom:14px; border-bottom:1px solid #ebeef5; }
.dh-back          { margin-right:4px; }
/* 头部 */
.dh        { display:flex; align-items:center; gap:10px; width:100%; flex-wrap:wrap; }
.dh-left   { display:flex; align-items:center; gap:6px; flex:1; min-width:0; }
.dh-no     { font-size:12px; color:#909399; }
.dh-name   { font-size:16px; font-weight:700; color:#1a1a2e; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.dh-bw     { text-align:center; padding:4px 14px; background:#f0f9ff; border-radius:6px; }
.bw-num    { display:block; font-size:22px; font-weight:700; color:#409EFF; }
.bw-lbl    { display:block; font-size:11px; color:#909399; }
.dh-actions{ display:flex; gap:6px; align-items:center; }
.loading-tip { color:#606266; display:flex; align-items:center; gap:6px; }
.push-note { color:#909399; font-size:11px; margin-top:4px; }
.push-plan-item { font-size:11px; color:#606266; font-family:Menlo,Monaco,monospace; line-height:1.6; }
/* 卡片 */
.ic :deep(.el-card__header) { font-weight:600; font-size:13px; padding:10px 14px; }
.dtabs :deep(.el-tabs__header) { margin-bottom:12px; }
/* 协议配置 */
.config-header { display:flex; align-items:center; margin-bottom:14px; }
.config-body   { }
.script-pre { margin:0; font-size:12px; font-family:monospace; white-space:pre-wrap; word-break:break-all; color:#303133; background:#f5f7fa; padding:8px; border-radius:4px; }
/* 日志 */
.log-row   { display:flex; align-items:center; flex-wrap:wrap; font-size:13px; }
.log-diff  { width:100%; margin-top:4px; color:#909399; font-size:12px; }
/* 到期 */
.expired   { color:#F56C6C; font-weight:600; }
.expiring  { color:#E6A23C; }
</style>
