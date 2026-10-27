"""
通知事件元数据与消息模板渲染。

该模块不负责投递；它只把各业务模块提供的结构化变更上下文转换为可配置的
标题、正文和前端可展示的模板变量说明。这样业务侧不再需要拼接特定 IM
平台的固定文案，通知规则也可以安全地复用同一套变量。
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from django.utils import timezone


_MAX_DISPLAY_LENGTH = 1600
_SNAPSHOT_DISPLAY_LENGTH = 480
_SENSITIVE_KEY_PATTERN = re.compile(
    r'(?:password|passwd|secret|token|api[_-]?key|private[_-]?key|credential|'
    r'webhook|authorization)',
    re.IGNORECASE,
)


COMMON_VARIABLES = [
    {'key': 'event_label', 'label': '事件名称', 'description': '当前通知事件的中文名称'},
    {'key': 'module', 'label': '业务模块', 'description': '发起变更的模块名称'},
    {'key': 'action', 'label': '动作代码', 'description': '用于动作筛选的稳定代码，如 create、update'},
    {'key': 'action_label', 'label': '动作名称', 'description': '动作的中文名称，如新建、更新、状态变更'},
    {'key': 'customer_name', 'label': '客户名称', 'description': '关联客户名称'},
    {'key': 'customer_no', 'label': '客户编号', 'description': '关联客户编号'},
    {'key': 'customer_email', 'label': '客户邮箱', 'description': '客户联系邮箱，用于邮件通知'},
    {'key': 'target_type', 'label': '对象类型', 'description': '被操作对象类型'},
    {'key': 'target_name', 'label': '对象名称', 'description': '被操作对象名称或编号'},
    {'key': 'target_id', 'label': '对象 ID', 'description': '被操作对象的系统 ID'},
    {'key': 'operator_name', 'label': '操作人', 'description': '执行操作的用户；系统任务显示为“系统”'},
    {'key': 'pop_name', 'label': 'POP 点', 'description': '关联的 POP 节点名称'},
    {'key': 'order_no', 'label': '工单编号', 'description': '关联服务工单编号'},
    {'key': 'instance_no', 'label': '服务实例编号', 'description': '关联服务实例编号'},
    {'key': 'vps_no', 'label': 'VPS 编号', 'description': '关联 VPS 编号'},
    {'key': 'edge_no', 'label': '边缘加速编号', 'description': '关联边缘加速编号'},
    {'key': 'contract_no', 'label': '合同编号', 'description': '关联合同编号'},
    {'key': 'bill_no', 'label': '账单编号', 'description': '关联账单编号'},
    {'key': 'billing_status', 'label': '账单状态', 'description': '账单当前状态'},
    {'key': 'payment_status', 'label': '支付状态', 'description': '在线支付交易当前状态'},
    {'key': 'contact_name', 'label': '联系人', 'description': '咨询或客户需求的联系人'},
    {'key': 'company_name', 'label': '公司/组织', 'description': '咨询或客户需求所属组织'},
    {'key': 'contact', 'label': '联系方式', 'description': '电话、邮箱等联系方式'},
    {'key': 'preferred_contact', 'label': '联系偏好', 'description': '客户偏好的联系渠道'},
    {'key': 'inquiry_type', 'label': '需求类型', 'description': '官网咨询或测试申请类型'},
    {'key': 'inquiry_status', 'label': '需求状态', 'description': '官网咨询或测试申请当前状态'},
    {'key': 'order_type', 'label': '工单类型', 'description': '新开、续费、变更等工单类型'},
    {'key': 'business_type', 'label': '业务类型', 'description': 'VPS、边缘加速、IP、带宽等业务类型'},
    {'key': 'order_status', 'label': '工单状态', 'description': '当前工单处理状态'},
    {'key': 'lifecycle_stage', 'label': '生命周期阶段', 'description': '下单、需求确认、测试、使用、变更或终止'},
    {'key': 'handler_name', 'label': '处理人', 'description': '当前负责处理的人员'},
    {'key': 'approver_name', 'label': '审批人', 'description': '审批该工单或变更的人员'},
    {'key': 'quoted_price', 'label': '报价', 'description': '工单报价或服务价格'},
    {'key': 'requirements', 'label': '需求描述', 'description': '客户提交的服务需求说明'},
    {'key': 'requested_vcpu', 'label': '需求 vCPU', 'description': '客户申请的 vCPU 数量'},
    {'key': 'requested_ram_mb', 'label': '需求内存(MB)', 'description': '客户申请的内存大小'},
    {'key': 'requested_disk_gb', 'label': '需求硬盘(GB)', 'description': '客户申请的硬盘大小'},
    {'key': 'requested_bandwidth_mbps', 'label': '需求带宽(Mbps)', 'description': '客户申请的带宽'},
    {'key': 'requested_ip_count', 'label': '需求 IP 数', 'description': '客户申请的 IP 数量'},
    {'key': 'service_type', 'label': '服务类型', 'description': '服务实例的类型'},
    {'key': 'service_status', 'label': '服务状态', 'description': '服务实例当前状态'},
    {'key': 'monthly_fee', 'label': '月费', 'description': '服务实例当前月费'},
    {'key': 'expire_time', 'label': '到期时间', 'description': '服务实例到期时间'},
    {'key': 'grace_days', 'label': '宽限天数', 'description': '到期后到回收的宽限天数'},
    {'key': 'suspend_at', 'label': '计划停机时间', 'description': '到期引擎计划暂停电源的时间'},
    {'key': 'recycle_at', 'label': '计划回收时间', 'description': '到期引擎计划释放 IP 并回收的时间'},
    {'key': 'days_left', 'label': '剩余天数', 'description': '距离到期日的剩余天数，已到期为负数'},
    {'key': 'is_trial', 'label': '测试服务', 'description': '服务是否处于测试期'},
    {'key': 'change_no', 'label': '变更单号', 'description': '关联服务变更单编号'},
    {'key': 'change_type', 'label': '变更类型', 'description': '服务变更类型'},
    {'key': 'change_status', 'label': '变更状态', 'description': '服务变更单当前状态'},
    {'key': 'change_reason', 'label': '变更原因', 'description': '客户或内部提交的变更原因'},
    {'key': 'price_delta', 'label': '价格差额', 'description': '变更引起的价格差额'},
    {'key': 'amount', 'label': '金额', 'description': '账单或收款金额'},
    {'key': 'spec_summary', 'label': '规格摘要', 'description': '工单或资源规格一句话摘要'},
    {'key': 'detail', 'label': '操作说明', 'description': '业务模块记录的操作说明'},
    {'key': 'before_summary', 'label': '触发前原始情况', 'description': '变更发生前的字段快照，已脱敏并转为可读文本'},
    {'key': 'change_summary', 'label': '变更摘要', 'description': '由变更前后数据自动归纳的字段差异'},
    {'key': 'after_summary', 'label': '变更后情况', 'description': '变更完成后的字段快照，已脱敏并转为可读文本'},
    {'key': 'old_value', 'label': '变更前数据', 'description': '已脱敏的变更前数据'},
    {'key': 'new_value', 'label': '变更后数据', 'description': '已脱敏的变更后数据'},
    {'key': 'occurred_at', 'label': '发生时间', 'description': '变更记录创建时间'},
    {'key': 'title', 'label': '原始标题', 'description': '旧调用方提供的标题，兼容历史模板'},
    {'key': 'content', 'label': '原始正文', 'description': '旧调用方提供的正文，兼容历史模板'},
]


EVENT_GUIDANCE = (
    '默认正文按事件主题挑选字段：主题/动作、操作人或触发来源、时间，'
    '以及该业务相关的对象与说明。仅在确有字段差异时附带变更明细，'
    '不再套用同一套「变更前后快照」模板。'
)

# 各事件正文额外字段（标题、人员、时间由渲染器统一加）
EVENT_BODY_FIELDS: dict[str, list[tuple[str, str]]] = {
    'resource_change': [('对象类型', 'target_type'), ('对象', 'target_name'), ('POP', 'pop_name'), ('客户', 'customer_name')],
    'vps_change': [('客户', 'customer_name'), ('VPS', 'vps_no'), ('POP', 'pop_name')],
    'edge_acceleration_change': [('客户', 'customer_name'), ('边缘加速', 'edge_no'), ('POP', 'pop_name')],
    'pop_change': [('POP', 'pop_name'), ('对象', 'target_name')],
    'ip_change': [('对象', 'target_name'), ('客户', 'customer_name'), ('POP', 'pop_name')],
    'bandwidth_change': [('对象', 'target_name'), ('客户', 'customer_name'), ('POP', 'pop_name'), ('带宽', 'requested_bandwidth_mbps')],
    'product_publish': [('产品', 'target_name')],
    'service_change': [
        ('客户', 'customer_name'), ('工单', 'order_no'), ('实例', 'instance_no'),
        ('变更单', 'change_no'), ('变更类型', 'change_type'), ('VPS', 'vps_no'),
        ('边缘加速', 'edge_no'), ('申请人', 'requested_by'),
    ],
    'service_order': [
        ('客户', 'customer_name'), ('工单', 'order_no'), ('工单类型', 'order_type'),
        ('业务', 'business_type'), ('报价', 'quoted_price'), ('规格', 'spec_summary'),
        ('POP', 'pop_name'), ('处理人', 'handler_name'),
    ],
    'service_inquiry': [
        ('需求类型', 'inquiry_type'), ('编号', 'target_name'), ('联系人', 'contact_name'),
        ('公司', 'company_name'), ('联系方式', 'contact'), ('偏好', 'preferred_contact'),
        ('需求', 'requirements'),
    ],
    'service_ticket': [
        ('客户', 'customer_name'), ('工单', 'ticket_no'), ('主题', 'ticket_subject'),
        ('分类', 'ticket_category'), ('优先级', 'ticket_priority'),
        ('状态', 'ticket_status'), ('处理人', 'handler_name'),
    ],
    'service_selfservice': [
        ('客户', 'customer_name'), ('任务', 'job_no'), ('类型', 'job_type'),
        ('VPS', 'vps_no'), ('实例', 'instance_no'), ('状态', 'job_status'),
    ],
    'service_lifecycle': [
        ('客户', 'customer_name'), ('工单', 'order_no'), ('实例', 'instance_no'),
        ('阶段', 'action_label'),
    ],
    'service_provision': [
        ('客户', 'customer_name'), ('工单', 'order_no'), ('实例', 'instance_no'),
        ('业务', 'business_type'), ('规格', 'spec_summary'),
    ],
    'service_expiry': [
        ('客户', 'customer_name'), ('实例', 'instance_no'), ('工单', 'order_no'),
        ('到期时间', 'expire_time'), ('剩余天数', 'days_left'),
        ('是否测试', 'is_trial'), ('办理入口', 'portal_hint'),
        ('宽限天数', 'grace_days'), ('计划停机', 'suspend_at'), ('计划回收', 'recycle_at'),
    ],
    'customer_change': [('客户', 'customer_name'), ('客户编号', 'customer_no'), ('对象', 'target_name')],
    'customer_contact': [('客户', 'customer_name'), ('对象', 'target_name')],
    'customer_contract': [('客户', 'customer_name'), ('合同', 'contract_no')],
    'billing_change': [('客户', 'customer_name'), ('账单', 'bill_no'), ('账单状态', 'billing_status'), ('金额', 'amount')],
    'billing_payment': [('客户', 'customer_name'), ('账单', 'bill_no'), ('支付状态', 'payment_status'), ('金额', 'amount')],
    'billing_overdue': [('客户', 'customer_name'), ('账单', 'bill_no'), ('金额', 'amount')],
    'supplier_sync': [('供应商', 'target_name')],
    'system_setting_change': [('对象类型', 'target_type'), ('配置项', 'target_name')],
    'system_alert': [('对象类型', 'target_type'), ('对象', 'target_name'), ('POP', 'pop_name')],
    'custom': [('标题', 'title')],
}

SYSTEM_ACTOR_NAMES = {'系统', '系统品质监控', '系统自动', '系统同步', '到期引擎'}


def recommended_body_template(event: str) -> str:
    lines = [
        '【{event_label}】{action_label}',
        '时间：{occurred_at}',
        '操作人：{operator_name}',
    ]
    for label, key in EVENT_BODY_FIELDS.get(event, (('对象', 'target_name'),)):
        lines.append(f'{label}：{{{key}}}')
    lines.append('说明：{detail}')
    return '\n'.join(lines)


_FIELD_LABELS = {
    'action': '操作动作',
    'amount': '金额',
    'assigned_at': '分配时间',
    'assigned_edge': '关联边缘加速',
    'assigned_vps': '关联 VPS',
    'auto_generated': '系统生成',
    'bandwidth_mbps': '带宽(Mbps)',
    'bill_month': '账单月份',
    'bill_no': '账单编号',
    'billing_start_date': '计费起始日',
    'change_reason': '变更原因',
    'contract': '合同',
    'customer': '客户',
    'customer_id': '客户 ID',
    'disk_gb': '硬盘(GB)',
    'due_date': '到期日',
    'edge': '边缘加速',
    'exchange_rate': '汇率',
    'is_trial': '测试服务',
    'ip_count': 'IP 数量',
    'mgmt_ip': '管理 IP',
    'monthly_fee': '月费',
    'name': '名称',
    'paid_amount': '已支付金额',
    'payment_method': '支付方式',
    'payment_status': '支付状态',
    'pop_point': 'POP 节点',
    'price_delta': '价格差额',
    'product': '产品',
    'protocol': '协议',
    'ram_mb': '内存(MB)',
    'service_type': '服务类型',
    'source_vmid': '源虚机 ID',
    'status': '状态',
    'target_type': '对象类型',
    'vcpu': 'vCPU',
    'vps': 'VPS',
}


def _actions(*values: tuple[str, str]) -> list[dict[str, str]]:
    return [{'value': value, 'label': label} for value, label in values]


def _event(
    label: str,
    description: str,
    title_template: str,
    *actions: tuple[str, str],
) -> dict[str, Any]:
    return {
        'label': label,
        'description': description,
        'default_title_template': title_template,
        'recommended_body_template': '',
        'format_guidance': EVENT_GUIDANCE,
        'variables': COMMON_VARIABLES,
        'actions': _actions(*actions),
    }


# value 必须与 NotificationEvent 中的值保持一致。放在独立模块可避免 models 与
# serializers / services 之间形成循环导入。
EVENT_CATALOG: dict[str, dict[str, Any]] = {
    'resource_change': _event(
        '资源异动',
        '覆盖全部资源写操作：VPS、边缘加速、POP、IP、带宽，以及网络/资源池等通用资源。'
        '订阅本事件即会收到下列细分事件，无需再逐条勾选。',
        '【资源异动】{action_label} · {target_name}',
        ('create', '新建'), ('update', '更新'), ('delete', '删除'),
        ('status_change', '状态变更'), ('deploy', '部署'), ('restart', '重启'),
        ('config_change', '配置变更'), ('import', '批量导入'),
        ('ip_assign', 'IP 分配'), ('ip_release', 'IP 释放'),
        ('bw_alloc', '带宽分配'), ('other', '其他'),
    ),
    'vps_change': _event(
        'VPS 变更', 'VPS 的规格、状态、带宽、同步、门户电源及节点操作。',
        '【VPS 变更】{action_label} · {vps_no}',
        ('create', '新建'), ('update', '更新'), ('delete', '删除'),
        ('status_change', '状态变更'), ('deploy', '部署'), ('restart', '重启'),
        ('config_change', '配置变更'), ('import', '节点同步'),
        ('other', '其他'),
    ),
    'edge_acceleration_change': _event(
        '边缘加速变更', 'GRE、VXLAN、WireGuard、Socks5 等边缘加速资源的变更，含门户协议配置与下发。',
        '【边缘加速】{action_label} · {edge_no}',
        ('create', '新建'), ('update', '更新'), ('delete', '删除'),
        ('status_change', '状态变更'), ('deploy', '部署'),
        ('config_change', '协议配置变更'), ('import', '节点同步'),
        ('other', '其他'),
    ),
    'pop_change': _event(
        'POP 节点变更', 'POP 节点配置、状态、资源统计及下发状态的变更。',
        '【POP 节点】{action_label} · {pop_name}',
        ('create', '新建'), ('update', '更新'), ('delete', '删除'),
        ('status_change', '状态变更'), ('deploy', '配置下发'),
    ),
    'ip_change': _event(
        'IP 资源变更', 'IP 地址块、IP 地址分配和释放等变更。',
        '【IP 资源】{action_label} · {target_name}',
        ('create', '新建'), ('update', '更新'), ('delete', '删除'),
        ('ip_assign', 'IP 分配'), ('ip_release', 'IP 释放'), ('import', '同步导入'),
    ),
    'bandwidth_change': _event(
        '带宽变更', 'POP、VPS 或边缘加速的带宽分配与调整。',
        '【带宽变更】{action_label} · {target_name}',
        ('bw_alloc', '带宽分配'), ('update', '更新'), ('deploy', '下发到节点'),
    ),
    'product_publish': _event(
        '产品发布/上下线', '产品发布、下线和销售状态切换。',
        '【产品发布】{action_label} · {target_name}',
        ('publish', '发布'), ('online', '上线'), ('offline', '下线'),
        ('archive', '归档'), ('update', '更新'),
    ),
    'service_change': _event(
        '服务变更', '服务实例的开通、暂停、恢复、终止和配置调整。',
        '【服务变更】{action_label} · {instance_no}',
        ('activate', '激活'), ('suspend', '暂停'), ('resume', '恢复'),
        ('terminate', '终止'), ('change', '服务变更'), ('update', '更新'),
        ('delete', '删除'), ('assign_ip', '分配 IP'), ('release_ip', '释放 IP'),
        ('convert_billing', '测试转正计费'), ('approve', '审批通过'),
        ('reject', '审批驳回'), ('cancel', '取消'), ('execute', '执行完成'),
        ('rollback', '回滚完成'),
        ('status_change', '状态变更'), ('restart', '重启'),
        ('config_change', '配置变更'), ('deploy', '配置下发'),
    ),
    'service_order': _event(
        '服务工单', '客户服务工单的新建、提交、审批、指派，以及门户下单、变更和续费。',
        '【服务工单】{action_label} · {order_no}',
        ('create_order', '创建工单'), ('submit_order', '提交工单'),
        ('approve_order', '审批通过'), ('reject_order', '审批驳回'),
        ('assign_handler', '指派处理人'), ('update', '更新'), ('delete', '删除'),
        ('change', '变更申请'), ('renew', '续费'),
    ),
    'service_inquiry': _event(
        '官网咨询/测试申请', '官网咨询、需求提交、测试申请、指派和回复。',
        '【客户需求】{action_label} · {target_name}',
        ('create', '提交需求'), ('assign', '指派处理人'), ('reply', '回复需求'),
        ('close', '关闭需求'),
    ),
    'service_ticket': _event(
        '客户技术工单', '与服务开通工单分离的技术/滥用/财务/接入工单及 SLA。',
        '【技术工单】{action_label} · {ticket_no}',
        ('create', '提交工单'), ('assign', '指派处理人'), ('reply', '回复工单'),
        ('customer_reply', '客户回复'), ('resolve', '解决工单'), ('close', '关闭工单'),
        ('escalate', '工单升级'), ('sla_response', '响应超时'), ('sla_resolve', '解决超时'),
    ),
    'service_selfservice': _event(
        '客户自助重装/快照', '门户重装系统、创建快照和恢复快照（骨架，不调用节点）。',
        '【客户自助】{action_label} · {job_no}',
        ('reinstall', '提交重装'), ('snapshot_create', '提交快照'),
        ('snapshot_restore', '提交恢复'), ('complete', '任务完成'),
        ('fail', '任务失败'), ('cancel', '取消任务'),
    ),
    'service_lifecycle': _event(
        '服务生命周期', '服务从下单、需求确认、测试、使用、变更到终止的阶段推进。',
        '【服务生命周期】{action_label} · {order_no}',
        ('order', '下单阶段'), ('stage_change', '阶段变更'), ('requirement', '需求确认'),
        ('test', '测试阶段'), ('usage', '正式使用'), ('termination', '终止阶段'),
        ('change', '变更阶段'),
    ),
    'service_provision': _event(
        '服务开通', '自动或人工开通服务、资源下发和开通失败。',
        '【服务开通】{action_label} · {order_no}',
        ('provision', '开通资源'), ('success', '开通成功'), ('failed', '开通失败'),
    ),
    'service_expiry': _event(
        '服务到期', '实例到期催缴、到期通知、到期停机和宽限后回收。',
        '【服务到期】{action_label} · {instance_no}',
        ('remind', '催缴提醒'), ('expired', '已到期'),
        ('suspend', '到期停机'), ('recycle', '宽限回收'),
        ('renew', '一键续费'),
    ),
    'customer_change': _event(
        '客户变更', '客户资料、归属、状态和账户信息变更。',
        '【客户变更】{action_label} · {customer_name}',
        ('create', '新建'), ('update', '更新'), ('delete', '删除'),
        ('status_change', '状态变更'), ('import', '批量导入'),
    ),
    'customer_contact': _event(
        '客户联系记录', '客户跟进、联系记录和客服沟通记录。',
        '【客户跟进】{action_label} · {customer_name}',
        ('contact', '新增跟进'), ('create', '新建'), ('update', '更新'),
    ),
    'customer_contract': _event(
        '客户合同变更', '合同新建、签订、更新、到期或删除。',
        '【客户合同】{action_label} · {contract_no}',
        ('create', '新建'), ('update', '更新'), ('delete', '删除'),
        ('contract_sign', '合同签订'),
    ),
    'billing_change': _event(
        '客户账单变更', '账单创建、金额调整、状态变更和作废。',
        '【客户账单】{action_label} · {bill_no}',
        ('create', '新建'), ('update', '更新'), ('delete', '删除'),
        ('status_change', '状态变更'), ('other', '其他'),
    ),
    'billing_payment': _event(
        '客户收款', '账单收款、线上支付成功和支付状态变化。',
        '【客户收款】{action_label} · {bill_no}',
        ('payment', '收款'), ('succeeded', '支付成功'), ('failed', '支付失败'),
        ('cancelled', '支付取消'), ('pending', '支付处理中'), ('expired', '支付过期'),
    ),
    'billing_overdue': _event(
        '账单逾期', '账单逾期提醒或催收处理。',
        '【账单逾期】{bill_no}',
        ('overdue', '账单逾期'),
    ),
    'supplier_sync': _event(
        '供应商资源同步', '供应商资源拉取、同步和异常处理。',
        '【供应商同步】{action_label} · {target_name}',
        ('test', '连接测试'), ('pull_resources', '拉取资源'),
        ('pull_products', '拉取产品'), ('pull_pops', '拉取 POP'),
        ('map', '映射同步'), ('failed', '同步失败'),
    ),
    'system_setting_change': _event(
        '系统参数变更', '系统参数、业务开关或平台配置调整，不包含敏感参数明文。',
        '【系统参数】{action_label} · {target_name}',
        ('create', '新建'), ('update', '更新'),
    ),
    'system_alert': _event(
        '系统告警', '系统运行、同步、外部接口或任务执行告警。',
        '【系统告警】{target_name}',
        ('alert', '告警'), ('recovered', '恢复'),
    ),
    'custom': _event(
        '自定义/测试', '手动发送或测试通知。',
        '【ISP Manager】{title}',
        ('custom', '自定义'),
    ),
}

for _event_key, _meta in EVENT_CATALOG.items():
    _meta['recommended_body_template'] = recommended_body_template(_event_key)


# 父事件订阅后自动覆盖细分事件。用户只勾选「资源异动」时，VPS/边缘/POP/IP/带宽
# 写操作仍会命中该规则，避免业务已记账却因事件代码拆细而漏推 IM。
EVENT_CHILDREN: dict[str, tuple[str, ...]] = {
    'resource_change': (
        'vps_change',
        'edge_acceleration_change',
        'pop_change',
        'ip_change',
        'bandwidth_change',
    ),
}
EVENT_PARENTS: dict[str, tuple[str, ...]] = {}
for _parent, _children in EVENT_CHILDREN.items():
    for _child in _children:
        EVENT_PARENTS.setdefault(_child, ())
        EVENT_PARENTS[_child] = EVENT_PARENTS[_child] + (_parent,)


def parent_events_of(event: str) -> tuple[str, ...]:
    return EVENT_PARENTS.get(event, ())


def event_is_subscribed(subscribed: list[str] | tuple[str, ...], fired: str) -> bool:
    """规则是否订阅了本次触发事件，或该事件的父级（如资源异动覆盖 VPS 变更）。"""
    subscribed_set = {str(item).strip() for item in (subscribed or []) if str(item).strip()}
    if fired in subscribed_set:
        return True
    return any(parent in subscribed_set for parent in parent_events_of(fired))


class _SafeFormatDict(dict):
    """缺失变量渲染为空，避免某个可选字段导致整条通知失败。"""

    def __missing__(self, key):
        return ''


def _is_sensitive_key(key: Any) -> bool:
    return bool(_SENSITIVE_KEY_PATTERN.search(str(key)))


def _sanitize(value: Any, *, key: str = '') -> Any:
    if _is_sensitive_key(key):
        return '***'
    if isinstance(value, dict):
        return {str(k): _sanitize(v, key=str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_sanitize(item) for item in value]
    return value


def _format_value(value: Any) -> str:
    if value is None:
        return ''
    if isinstance(value, bool):
        return '是' if value else '否'
    if isinstance(value, (datetime, date)):
        value = timezone.localtime(value) if isinstance(value, datetime) and timezone.is_aware(value) else value
        return value.strftime('%Y-%m-%d %H:%M:%S') if isinstance(value, datetime) else value.isoformat()
    if isinstance(value, (dict, list, tuple, set)):
        rendered = json.dumps(_sanitize(value), ensure_ascii=False, default=str, separators=(',', ': '))
    elif isinstance(value, Decimal):
        rendered = format(value, 'f')
    else:
        rendered = str(value)
    return rendered[:_MAX_DISPLAY_LENGTH] + ('…' if len(rendered) > _MAX_DISPLAY_LENGTH else '')


def _truncate(value: str, limit: int = _SNAPSHOT_DISPLAY_LENGTH) -> str:
    return value[:limit] + ('…' if len(value) > limit else '')


def _field_label(key: Any) -> str:
    key = str(key)
    return _FIELD_LABELS.get(key, key.replace('_', ' '))


def _readable_snapshot(value: Any, *, empty_label: str) -> str:
    """把审计 JSON 快照改为业务人员可读的逐字段文本。"""
    value = _sanitize(value)
    if not value:
        return empty_label
    if not isinstance(value, dict):
        return _truncate(_format_value(value))
    ignored = {'created_at', 'updated_at', 'id'}
    lines = []
    for key in sorted(value):
        if key in ignored:
            continue
        rendered = _format_value(value.get(key)) or '—'
        lines.append(f'• {_field_label(key)}：{rendered}')
    return _truncate('\n'.join(lines) or empty_label)


def _change_summary(old_value: Any, new_value: Any) -> str:
    old = _sanitize(old_value or {})
    new = _sanitize(new_value or {})
    if not isinstance(old, dict) or not isinstance(new, dict):
        return ''
    ignored = {'updated_at', 'created_at', 'id'}
    changes = []
    for key in sorted(set(old) | set(new)):
        if key in ignored or old.get(key) == new.get(key):
            continue
        changes.append(
            f'• {_field_label(key)}：'
            f'{_format_value(old.get(key)) or "—"} → {_format_value(new.get(key)) or "—"}'
        )
    return _truncate('\n'.join(changes), _SNAPSHOT_DISPLAY_LENGTH)


def build_context(
    event: str,
    context: dict[str, Any] | None = None,
    *,
    title: str = '',
    content: str = '',
) -> dict[str, str]:
    """标准化业务事件上下文，并将可选复杂值转换成模板可安全使用的文本。"""
    meta = EVENT_CATALOG.get(event, {})
    raw = dict(context or {})
    occurred_at = raw.get('occurred_at') or raw.get('created_at') or timezone.now()
    old_value = _sanitize(raw.get('old_value'))
    new_value = _sanitize(raw.get('new_value'))
    action = raw.get('action', '')
    before_summary = raw.get('before_summary')
    after_summary = raw.get('after_summary')
    change_summary = raw.get('change_summary') or _change_summary(old_value, new_value)
    if not before_summary:
        before_summary = _readable_snapshot(
            old_value,
            empty_label='无历史记录（本次为新建或未提供变更前快照）'
            if action == 'create' else '未记录变更前快照',
        )
    if not change_summary:
        change_summary = ''
    if not after_summary:
        after_summary = _readable_snapshot(
            new_value,
            empty_label='对象已删除或未提供变更后快照'
            if action == 'delete' else '未记录变更后快照',
        )

    resolved: dict[str, Any] = {
        'event': event,
        'event_label': meta.get('label', event),
        'module': raw.get('module', ''),
        'action': action,
        'action_label': raw.get('action_label') or action,
        'customer_name': raw.get('customer_name') or raw.get('customer', ''),
        'customer_no': raw.get('customer_no', ''),
        'target_type': raw.get('target_type', ''),
        'target_name': raw.get('target_name') or raw.get('target', ''),
        'target_id': raw.get('target_id', ''),
        'operator_name': raw.get('operator_name') or raw.get('operator', '系统'),
        'pop_name': raw.get('pop_name', ''),
        'order_no': raw.get('order_no', ''),
        'instance_no': raw.get('instance_no', ''),
        'vps_no': raw.get('vps_no', ''),
        'edge_no': raw.get('edge_no', ''),
        'contract_no': raw.get('contract_no', ''),
        'bill_no': raw.get('bill_no', ''),
        'detail': raw.get('detail') or content,
        'old_value': old_value,
        'new_value': new_value,
        'before_summary': before_summary,
        'change_summary': change_summary,
        'after_summary': after_summary,
        'occurred_at': occurred_at,
        'title': title,
        'content': content,
    }
    # 允许业务模块提供额外变量，例如 inquiry_no、protocol、quoted_price。
    for key, value in raw.items():
        if key not in resolved:
            resolved[str(key)] = _sanitize(value, key=str(key))
    return {key: _format_value(value) for key, value in resolved.items()}


def validate_template(template: str, *, field_label: str = '模板') -> None:
    """提前校验花括号格式；变量缺失本身是允许的。"""
    if not template:
        return
    try:
        template.format_map(_SafeFormatDict())
    except (ValueError, AttributeError, IndexError) as exc:
        raise ValueError(f'{field_label}格式无效：{exc}') from exc


def render_template(template: str, context: dict[str, str]) -> str:
    if not template:
        return ''
    try:
        return template.format_map(_SafeFormatDict(context)).strip()
    except (ValueError, AttributeError, IndexError):
        # 所有模板均经 serializer 校验；这里兜底保证旧数据不会阻断业务主流程。
        return template.strip()


def _usable_snapshot(text: str) -> str:
    value = (text or '').strip()
    if not value:
        return ''
    if value.startswith(('无历史记录', '未记录', '对象已删除', '未提供')):
        return ''
    return value


def _usable_change_summary(text: str) -> str:
    value = _usable_snapshot(text)
    if not value:
        return ''
    if value.startswith('• 已执行：'):
        return ''
    if '→' in value or value.startswith('•'):
        return value
    return ''


def _actor_line(context: dict[str, str], event: str) -> str:
    name = (context.get('operator_name') or '').strip() or '系统'
    if event in {'system_alert', 'supplier_sync', 'service_expiry'} or name in SYSTEM_ACTOR_NAMES:
        return f'触发：{name}'
    return f'操作人：{name}'


def _default_body(context: dict[str, str], mode: str) -> str:
    """按事件主题渲染正文，避免所有通知套同一套变更前后模板。"""
    event = context.get('event', '')
    event_label = (context.get('event_label') or '内部通知').strip()
    action_label = (context.get('action_label') or '').strip()
    title = f'【{event_label}】{action_label}'.strip()
    lines = [title, _actor_line(context, event)]
    occurred = (context.get('occurred_at') or '').strip()
    if occurred:
        lines.append(f'时间：{occurred}')

    field_keys: set[str] = set()
    extra: list[str] = []
    for label, key in EVENT_BODY_FIELDS.get(event, (('对象类型', 'target_type'), ('对象', 'target_name'))):
        field_keys.add(key)
        value = (context.get(key) or '').strip()
        if value and value != action_label:
            extra.append(f'{label}：{_truncate(value, 200)}')

    person = (context.get('operator_name') or '').strip()
    for label, key in (('处理人', 'handler_name'), ('审批人', 'approver_name'), ('申请人', 'requested_by')):
        if key in field_keys:
            continue
        value = (context.get(key) or '').strip()
        if value and value != person:
            extra.append(f'{label}：{value}')

    detail = (context.get('detail') or '').strip()
    requirements = (context.get('requirements') or '').strip()
    if detail and 'detail' not in field_keys and detail != requirements:
        extra.append(f'说明：{_truncate(detail, 280 if mode == "compact" else 480)}')

    change = _usable_change_summary(context.get('change_summary', ''))
    before = _usable_snapshot(context.get('before_summary', ''))
    after = _usable_snapshot(context.get('after_summary', ''))

    if mode == 'compact':
        compact_lines = lines + extra[:8]
        if change:
            compact_lines.append(f'变更：{_truncate(change.replace(chr(10), "；"), 200)}')
        return '\n'.join(compact_lines)[:_MAX_DISPLAY_LENGTH]

    lines.extend(extra)
    if change:
        lines.append('')
        lines.append('变更明细：')
        lines.append(change)
    elif before or after:
        if before:
            lines.extend(['', '变更前：', before])
        if after:
            lines.extend(['', '变更后：', after])
    return '\n'.join(lines)[:_MAX_DISPLAY_LENGTH]


def render_rule_message(
    event: str,
    *,
    title: str = '',
    content: str = '',
    context: dict[str, Any] | None = None,
    title_template: str = '',
    content_template: str = '',
    format_mode: str = 'detailed',
) -> tuple[str, str, dict[str, str]]:
    """按某条规则的模板渲染消息，返回 ``(title, body, normalized_context)``。"""
    normalized = build_context(event, context, title=title, content=content)
    meta = EVENT_CATALOG.get(event, {})
    rendered_title = render_template(
        title_template or title or meta.get('default_title_template', '【{event_label}】{target_name}'),
        normalized,
    )
    if content_template:
        rendered_body = render_template(content_template, normalized)
    elif content:
        rendered_body = render_template(content, normalized)
    else:
        rendered_body = _default_body(normalized, format_mode)
    return rendered_title[:256], rendered_body[:_MAX_DISPLAY_LENGTH], normalized


def event_catalog_payload() -> list[dict[str, Any]]:
    """提供给前端的事件、动作和模板变量元数据。"""
    payload = []
    for value, meta in EVENT_CATALOG.items():
        sample_context = build_context(
            value,
            {
                'module': '示例模块',
                'action': 'update',
                'action_label': '更新',
                'customer_name': '示例客户',
                'customer_no': 'CUS20260001',
                'target_type': '示例对象',
                'target_name': '示例资源',
                'target_id': 1001,
                'operator_name': '管理员',
                'pop_name': 'Shanghai-01',
                'order_no': 'SO20260001',
                'instance_no': 'SI20260001',
                'vps_no': 'VPS20260001',
                'edge_no': 'EDGE20260001',
                'contract_no': 'CT20260001',
                'bill_no': 'BILL20260001',
                'detail': '这是用于预览通知格式的示例说明。',
                'old_value': {'status': 'pending', 'bandwidth_mbps': 100},
                'new_value': {'status': 'active', 'bandwidth_mbps': 200},
            },
        )
        payload.append({
            'value': value,
            'label': meta['label'],
            'description': meta['description'],
            'default_title_template': meta['default_title_template'],
            'recommended_body_template': meta.get('recommended_body_template') or recommended_body_template(value),
            'format_guidance': meta['format_guidance'],
            'variables': meta['variables'],
            'actions': meta['actions'],
            'covers': [
                {'value': child, 'label': EVENT_CATALOG[child]['label']}
                for child in EVENT_CHILDREN.get(value, ())
                if child in EVENT_CATALOG
            ],
            'sample_context': sample_context,
        })
    return payload
