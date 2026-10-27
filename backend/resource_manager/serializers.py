from django.db.models import Sum
from rest_framework import serializers
from .models import (
    Region, Country, City, AvailabilityZone,
    Carrier, ResourceType, NetworkTypeOption,
    PopPoint,
    NetworkInterface, RouteTable, StaticRoute, PolicyRoute,
    IPBlock, IPAddress, TunnelIPPool,
    VPS, VPSNetworkInterface,
    EdgeAcceleration, GREConfig, VXLANConfig, WireguardConfig, Socks5Config,
    BandwidthAllocation,
    ResourceOperationLog,
)
from .ip_block_attrs import catalog_from_context, asn_nature_label


# ═══════════════════════════════════════════════════════════════
# § 1  地理位置
# ═══════════════════════════════════════════════════════════════

class RegionSerializer(serializers.ModelSerializer):
    code_label = serializers.CharField(source='get_code_display', read_only=True)

    class Meta:
        model = Region
        fields = ('id', 'code', 'code_label', 'name', 'name_en')


class CountrySerializer(serializers.ModelSerializer):
    region_name = serializers.CharField(source='region.name', read_only=True)

    class Meta:
        model = Country
        fields = ('id', 'region', 'region_name', 'name', 'name_en')


class CitySerializer(serializers.ModelSerializer):
    country_name = serializers.CharField(source='country.name', read_only=True)
    region_name = serializers.CharField(source='country.region.name', read_only=True)

    class Meta:
        model = City
        fields = (
            'id', 'country', 'country_name', 'region_name',
            'name', 'name_en', 'timezone', 'latitude', 'longitude',
        )


class CityBriefSerializer(serializers.ModelSerializer):
    country_name = serializers.CharField(source='country.name', read_only=True)
    region_name = serializers.CharField(source='country.region.name', read_only=True)

    class Meta:
        model = City
        fields = ('id', 'name', 'country_name', 'region_name', 'latitude', 'longitude')


class AvailabilityZoneSerializer(serializers.ModelSerializer):
    city_name = serializers.CharField(source='city.name', read_only=True)
    country_name = serializers.CharField(source='city.country.name', read_only=True)
    region_name = serializers.CharField(source='city.country.region.name', read_only=True)

    class Meta:
        model = AvailabilityZone
        fields = (
            'id', 'city', 'city_name', 'country_name', 'region_name',
            'name', 'name_en', 'code', 'remark', 'created_at', 'updated_at',
        )
        read_only_fields = ('id', 'created_at', 'updated_at')

    def validate(self, attrs):
        city = attrs.get('city') or getattr(self.instance, 'city', None)
        code = (attrs.get('code') if 'code' in attrs else getattr(self.instance, 'code', '')) or ''
        code = str(code).strip()
        if city and code:
            qs = AvailabilityZone.objects.filter(city=city, code=code)
            if self.instance:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise serializers.ValidationError({'code': '同一城市下可用区代码不能重复'})
        attrs['code'] = code
        return attrs


class NetworkTypeOptionSerializer(serializers.ModelSerializer):
    class Meta:
        model = NetworkTypeOption
        fields = ('id', 'code', 'name', 'sort_order', 'is_active', 'created_at', 'updated_at')
        read_only_fields = ('id', 'created_at', 'updated_at')

    def validate_code(self, value):
        value = (value or '').strip().lower()
        if not value:
            raise serializers.ValidationError('代码不能为空')
        return value

    def validate(self, attrs):
        attrs = super().validate(attrs)
        if self.instance and 'code' in attrs and attrs['code'] != self.instance.code:
            if IPBlock.objects.filter(network_type=self.instance.code).exists():
                raise serializers.ValidationError({'code': '已有地址块使用该代码，不能修改'})
        return attrs


class LocationFieldsMixin(serializers.Serializer):
    """资源列表统一的位置展示字段。"""

    region_id = serializers.SerializerMethodField()
    region_name = serializers.SerializerMethodField()
    country_name = serializers.SerializerMethodField()
    city_name = serializers.SerializerMethodField()
    zone_id = serializers.SerializerMethodField()
    zone_name = serializers.SerializerMethodField()
    location_pop_path = 'pop_point'

    def _location_pop(self, obj):
        if not self.location_pop_path:
            return obj
        value = obj
        for attribute in self.location_pop_path.split('.'):
            value = getattr(value, attribute, None)
            if value is None:
                return None
        return value

    def get_region_id(self, obj):
        pop = self._location_pop(obj)
        try:
            return pop.city.country.region_id
        except AttributeError:
            return None

    def get_region_name(self, obj):
        pop = self._location_pop(obj)
        try:
            return pop.city.country.region.name
        except AttributeError:
            return ''

    def get_country_name(self, obj):
        pop = self._location_pop(obj)
        try:
            return pop.city.country.name
        except AttributeError:
            return ''

    def get_city_name(self, obj):
        pop = self._location_pop(obj)
        try:
            return pop.city.name
        except AttributeError:
            return ''

    def get_zone_id(self, obj):
        pop = self._location_pop(obj)
        try:
            return pop.availability_zone_id
        except AttributeError:
            return None

    def get_zone_name(self, obj):
        pop = self._location_pop(obj)
        try:
            zone = pop.availability_zone
            return zone.name if zone else ''
        except AttributeError:
            return ''


# ═══════════════════════════════════════════════════════════════
# § 2  运营商
# ═══════════════════════════════════════════════════════════════

class CarrierSerializer(serializers.ModelSerializer):
    region_names = serializers.SerializerMethodField()

    class Meta:
        model = Carrier
        fields = ('id', 'name', 'code', 'regions', 'region_names', 'remark', 'created_at')
        read_only_fields = ('id', 'created_at')

    def get_region_names(self, obj):
        return [r.name for r in obj.regions.all()]


class CarrierBriefSerializer(serializers.ModelSerializer):
    class Meta:
        model = Carrier
        fields = ('id', 'code', 'name')


# ═══════════════════════════════════════════════════════════════
# § 3  网络接口
# ═══════════════════════════════════════════════════════════════

class NetworkInterfaceSerializer(serializers.ModelSerializer):
    interface_type_label = serializers.CharField(source='get_interface_type_display', read_only=True)
    status_label = serializers.CharField(source='get_status_display', read_only=True)

    class Meta:
        model = NetworkInterface
        fields = (
            'id', 'pop_point', 'ifname',
            'interface_type', 'interface_type_label',
            'mac_address', 'mtu', 'speed_mbps',
            'status', 'status_label',
            'is_management', 'description', 'vlan_id', 'parent_interface',
            'netplan_config',
            'is_synced', 'last_synced_at',
            'created_at', 'updated_at',
        )
        read_only_fields = ('id', 'created_at', 'updated_at', 'last_synced_at')


# ═══════════════════════════════════════════════════════════════
# § 4  路由管理
# ═══════════════════════════════════════════════════════════════

class StaticRouteSerializer(serializers.ModelSerializer):
    class Meta:
        model = StaticRoute
        fields = (
            'id', 'route_table', 'destination', 'gateway',
            'interface', 'metric', 'is_active', 'description',
            'is_synced', 'last_synced_at', 'created_at',
        )
        read_only_fields = ('id', 'created_at', 'last_synced_at')


class RouteTableSerializer(serializers.ModelSerializer):
    static_routes = StaticRouteSerializer(many=True, read_only=True)
    owner_type_label = serializers.CharField(source='get_owner_type_display', read_only=True)

    class Meta:
        model = RouteTable
        fields = (
            'id', 'owner_type', 'owner_type_label',
            'pop_point', 'vps',
            'table_id', 'name', 'description',
            'static_routes', 'is_synced', 'last_synced_at', 'created_at',
        )
        read_only_fields = ('id', 'created_at', 'last_synced_at')


class RouteTableBriefSerializer(serializers.ModelSerializer):
    class Meta:
        model = RouteTable
        fields = ('id', 'table_id', 'name')


class PolicyRouteSerializer(serializers.ModelSerializer):
    action_label = serializers.CharField(source='get_action_display', read_only=True)

    class Meta:
        model = PolicyRoute
        fields = (
            'id', 'pop_point', 'vps', 'priority',
            'src_prefix', 'dst_prefix', 'fwmark',
            'lookup_table', 'action', 'action_label',
            'is_active', 'description',
            'is_synced', 'last_synced_at', 'created_at',
        )
        read_only_fields = ('id', 'created_at', 'last_synced_at')


# ═══════════════════════════════════════════════════════════════
# § 5  IP 管理
# ═══════════════════════════════════════════════════════════════

class IPAddressSerializer(LocationFieldsMixin, serializers.ModelSerializer):
    status_label = serializers.CharField(source='get_status_display', read_only=True)
    # 分配目标详情（只读，方便前端直接展示，无需二次请求）
    assigned_vps_info  = serializers.SerializerMethodField()
    assigned_edge_info = serializers.SerializerMethodField()
    # IP块 CIDR（方便列表页直接展示）
    block_cidr = serializers.CharField(source='ip_block.cidr', read_only=True)
    block_gateway = serializers.IPAddressField(source='ip_block.gateway', read_only=True, allow_null=True)
    pop_point = serializers.IntegerField(source='ip_block.pop_point_id', read_only=True)
    pop_name = serializers.CharField(source='ip_block.pop_point.name', read_only=True)
    ip_delivery_mode = serializers.CharField(source='ip_block.pop_point.ip_delivery_mode', read_only=True)
    ip_delivery_mode_label = serializers.CharField(
        source='ip_block.pop_point.get_ip_delivery_mode_display', read_only=True,
    )
    effective_gateway = serializers.SerializerMethodField()
    effective_prefix_len = serializers.SerializerMethodField()
    netmask = serializers.SerializerMethodField()
    network_type = serializers.CharField(source='ip_block.network_type', read_only=True)
    network_type_label = serializers.SerializerMethodField()
    asn_nature = serializers.CharField(source='ip_block.asn_nature', read_only=True)
    asn_nature_label = serializers.SerializerMethodField()
    location_pop_path = 'ip_block.pop_point'

    class Meta:
        model = IPAddress
        fields = (
            'id', 'ip_block', 'block_cidr', 'block_gateway', 'pop_point', 'pop_name',
            'ip_delivery_mode', 'ip_delivery_mode_label',
            'region_id', 'region_name', 'country_name', 'city_name',
            'zone_id', 'zone_name',
            'address', 'prefix_len', 'effective_prefix_len', 'gateway', 'effective_gateway', 'netmask',
            'status', 'status_label',
            'assigned_vps', 'assigned_vps_info',
            'assigned_edge', 'assigned_edge_info',
            'product',
            'network_type', 'network_type_label',
            'asn_nature', 'asn_nature_label',
            'is_primary', 'description', 'subnet_label', 'interface_code',
            'assigned_at', 'node_route_ref',
            'is_synced', 'last_synced_at', 'created_at',
        )
        read_only_fields = ('id', 'created_at', 'block_cidr', 'block_gateway', 'last_synced_at',
                            'assigned_vps_info', 'assigned_edge_info', 'node_route_ref',
                            'effective_gateway', 'effective_prefix_len', 'netmask',
                            'ip_delivery_mode', 'ip_delivery_mode_label',
                            'network_type', 'network_type_label', 'asn_nature', 'asn_nature_label')

    def get_network_type_label(self, obj):
        cat = catalog_from_context(self.context)
        return cat.network_type_label(getattr(obj.ip_block, 'network_type', '') or '')

    def get_asn_nature_label(self, obj):
        return asn_nature_label(getattr(obj.ip_block, 'asn_nature', '') or '')

    def get_effective_gateway(self, obj):
        return obj.effective_gateway

    def get_effective_prefix_len(self, obj):
        return obj.effective_prefix_len

    def get_netmask(self, obj):
        return obj.netmask

    def get_assigned_vps_info(self, obj):
        if not obj.assigned_vps_id:
            return None
        vps = obj.assigned_vps
        customer = getattr(vps, 'customer', None)
        return {
            'id':          vps.pk,
            'vps_no':      vps.vps_no,
            'name':        vps.name,
            'status':      vps.status,
            'customer_id': vps.customer_id,
            'customer_name': customer.name if customer else '',
            'pop_point': vps.pop_point_id,
        }

    def get_assigned_edge_info(self, obj):
        if not obj.assigned_edge_id:
            return None
        edge = obj.assigned_edge
        customer = getattr(edge, 'customer', None)
        return {
            'id':          edge.pk,
            'edge_no':     edge.edge_no,
            'name':        edge.name,
            'protocol':    edge.protocol,
            'status':      edge.status,
            'customer_id': edge.customer_id,
            'customer_name': customer.name if customer else '',
            'pop_point': edge.pop_point_id,
            'node_ifname': edge.node_ifname or '',
        }


class IPAddressAssignSerializer(serializers.Serializer):
    """IP 分配请求体"""
    target_type = serializers.ChoiceField(choices=['vps', 'edge'])
    target_id   = serializers.IntegerField()
    is_primary  = serializers.BooleanField(default=False)
    assigned_at = serializers.DateTimeField(required=False, allow_null=True, default=None)
    push_to_node = serializers.BooleanField(default=True)
    create_route = serializers.BooleanField(default=True, help_text='边缘加速分配时联动 host 路由')
    route = serializers.DictField(required=False, allow_null=True)
    interface_code = serializers.CharField(required=False, allow_blank=True, default='')


class IPBulkCreateSerializer(serializers.Serializer):
    """单条 / 不连续列表 / 起止 / CIDR，可带本段掩码与网关。"""
    mode = serializers.ChoiceField(
        choices=['single', 'range', 'list', 'cidr'], default='single',
    )
    address = serializers.IPAddressField(required=False)
    start = serializers.IPAddressField(required=False)
    end = serializers.IPAddressField(required=False)
    addresses = serializers.ListField(child=serializers.IPAddressField(), required=False)
    cidr = serializers.CharField(required=False, allow_blank=True, default='')
    prefix_len = serializers.IntegerField(required=False, allow_null=True, min_value=0, max_value=128)
    gateway = serializers.IPAddressField(required=False, allow_null=True)
    include_network = serializers.BooleanField(default=False)
    include_broadcast = serializers.BooleanField(default=False)
    ip_block = serializers.IntegerField(required=True)
    state = serializers.ChoiceField(
        choices=['available', 'reserved'], default='available',
    )
    subnet_label = serializers.CharField(required=False, allow_blank=True, default='')
    push_to_node = serializers.BooleanField(default=True)
    update_block_gateway = serializers.BooleanField(default=True)


class IPBlockSerializer(LocationFieldsMixin, serializers.ModelSerializer):
    ip_version_label = serializers.CharField(source='get_ip_version_display', read_only=True)
    ip_attribute_label = serializers.CharField(source='get_ip_attribute_display', read_only=True)
    network_type_label = serializers.SerializerMethodField()
    asn_nature_label = serializers.CharField(source='get_asn_nature_display', read_only=True)
    ip_stats = serializers.SerializerMethodField()
    pop_name = serializers.CharField(source='pop_point.name', read_only=True)
    prefix_len = serializers.IntegerField(read_only=True)
    netmask = serializers.CharField(read_only=True)
    ip_delivery_mode = serializers.CharField(source='pop_point.ip_delivery_mode', read_only=True)
    ip_delivery_mode_label = serializers.CharField(
        source='pop_point.get_ip_delivery_mode_display', read_only=True,
    )
    member_ids = serializers.SerializerMethodField()
    merged_count = serializers.SerializerMethodField()

    class Meta:
        model = IPBlock
        fields = (
            'id', 'pop_point', 'pop_name',
            'region_id', 'region_name', 'country_name', 'city_name',
            'zone_id', 'zone_name',
            'cidr', 'gateway', 'prefix_len', 'netmask',
            'ip_delivery_mode', 'ip_delivery_mode_label',
            'ip_version', 'ip_version_label',
            'carrier', 'ip_attribute', 'ip_attribute_label',
            'network_type', 'network_type_label',
            'asn_nature', 'asn_nature_label',
            'description', 'is_active', 'ip_stats',
            'member_ids', 'merged_count',
            'is_synced', 'last_synced_at', 'created_at',
        )
        read_only_fields = ('id', 'created_at', 'last_synced_at',
                            'member_ids', 'merged_count',
                            'prefix_len', 'netmask',
                            'ip_delivery_mode', 'ip_delivery_mode_label',
                            'network_type_label', 'asn_nature_label')
        extra_kwargs = {
            'gateway': {'allow_blank': True, 'allow_null': True, 'required': False},
        }

    def get_network_type_label(self, obj):
        return catalog_from_context(self.context).network_type_label(obj.network_type or '')

    def validate_network_type(self, value):
        from .models import NetworkType
        value = (value or '').strip() or 'idc'
        builtin = value in dict(NetworkType.choices)
        qs = NetworkTypeOption.objects.filter(code=value)
        if not qs.exists() and not builtin:
            raise serializers.ValidationError('网络类型不存在，请先在目录中新增')
        if qs.exists() and not qs.filter(is_active=True).exists():
            if not self.instance or self.instance.network_type != value:
                raise serializers.ValidationError('该网络类型已停用')
        return value

    def get_member_ids(self, obj):
        ids = getattr(obj, 'member_ids', None)
        if ids:
            return list(ids)
        return [obj.id]

    def get_merged_count(self, obj):
        return int(getattr(obj, 'merged_count', 1) or 1)

    def validate(self, attrs):
        from .ip_scope import exclude_tunnel_blocks
        pop = attrs.get('pop_point') or getattr(self.instance, 'pop_point', None)
        cidr = attrs.get('cidr') or getattr(self.instance, 'cidr', None)
        if pop and cidr:
            qs = exclude_tunnel_blocks(IPBlock.objects.filter(pop_point=pop, cidr=cidr))
            if self.instance:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise serializers.ValidationError({
                    'cidr': '该 POP 已存在相同 CIDR 的业务地址块，请在原条目下管理 IP',
                })
        gateway = attrs.get('gateway')
        if gateway == '':
            attrs['gateway'] = None
            gateway = None
        if gateway and cidr:
            from .ip_net import gateway_in_cidr
            if not gateway_in_cidr(str(gateway), cidr):
                raise serializers.ValidationError({'gateway': '网关必须属于该 CIDR 网段'})
        return attrs

    def get_ip_stats(self, obj):
        if hasattr(obj, 'ann_ip_total'):
            return {
                'capacity': self._capacity(obj),
                'tracked': obj.ann_ip_total,
                'total': obj.ann_ip_total,
                'available': obj.ann_ip_available,
                'assigned': obj.ann_ip_assigned,
                'reserved': obj.ann_ip_reserved,
                'blocked': obj.ann_ip_blocked,
                'system': obj.ann_ip_system,
            }
        qs = obj.ip_addresses
        # capacity：按 CIDR 计算的可用地址容量（IPv4 去掉网络/广播）；
        # tracked：库内实际登记的 IP 行数（同步/手工生成）。两者口径不同，分列展示避免误解。
        capacity = self._capacity(obj)
        return {
            'capacity': capacity,
            'tracked': qs.count(),
            'total': qs.count(),  # 兼容旧字段：等于 tracked
            'available': qs.filter(status='available').count(),
            'assigned': qs.filter(status='assigned').count(),
            'reserved': qs.filter(status='reserved').count(),
            'blocked': qs.filter(status='blocked').count(),
            'system': qs.filter(status='system').count(),
        }

    @staticmethod
    def _capacity(obj):
        try:
            import ipaddress
            net = ipaddress.ip_network(obj.cidr, strict=False)
            if net.version == 4 and net.prefixlen <= 30:
                return max(0, net.num_addresses - 2)
            return net.num_addresses
        except (ValueError, TypeError):
            return None


class IPBlockDetailSerializer(IPBlockSerializer):
    ip_addresses = serializers.SerializerMethodField()

    class Meta(IPBlockSerializer.Meta):
        fields = IPBlockSerializer.Meta.fields + ('ip_addresses',)

    def get_ip_addresses(self, obj):
        from .ip_scope import sibling_business_block_ids
        ids = getattr(obj, 'member_ids', None) or sibling_business_block_ids(obj)
        qs = (
            IPAddress.objects
            .filter(ip_block_id__in=ids)
            .select_related(
                'ip_block__pop_point__city__country__region',
                'assigned_vps__customer',
                'assigned_edge__customer',
            )
            .order_by('address')
        )
        return IPAddressSerializer(qs, many=True).data


# ═══════════════════════════════════════════════════════════════
# § 6  VPS
# ═══════════════════════════════════════════════════════════════

class VPSNetworkInterfaceSerializer(serializers.ModelSerializer):
    class Meta:
        model = VPSNetworkInterface
        fields = ('id', 'vps', 'ifname', 'mac_address', 'mtu', 'is_primary', 'description', 'bandwidth_mbps')
        read_only_fields = ('id',)


class VPSListSerializer(LocationFieldsMixin, serializers.ModelSerializer):
    status_label = serializers.CharField(source='get_status_display', read_only=True)
    os_type_label = serializers.CharField(source='get_os_type_display', read_only=True)
    pop_name = serializers.CharField(source='pop_point.name', read_only=True)
    pop_city = serializers.CharField(source='pop_point.city.name', read_only=True)
    customer_name = serializers.CharField(source='customer.name', read_only=True)
    product_name = serializers.CharField(source='product.name', read_only=True)
    product_billing = serializers.SerializerMethodField()
    ip_list = serializers.SerializerMethodField()

    class Meta:
        model = VPS
        fields = (
            'id', 'vps_no', 'name', 'status', 'status_label',
            'pop_point', 'pop_name', 'pop_city',
            'region_id', 'region_name', 'country_name', 'city_name',
            'zone_id', 'zone_name',
            'vcpu', 'ram_mb', 'disk_gb', 'nic_count',
            'os_type', 'os_type_label', 'os_version',
            'mgmt_ip', 'mgmt_port', 'bandwidth_mbps',
            'customer', 'customer_name',
            'product', 'product_name', 'product_billing',
            'start_time', 'expire_time',
            'is_synced', 'source_node', 'source_vmid',
            'source_ref', 'source_host_id', 'last_synced_at',
            'ip_list', 'created_at', 'updated_at',
        )
        read_only_fields = ('id', 'vps_no', 'created_at', 'updated_at',
                            'is_synced', 'source_node', 'source_vmid',
                            'source_ref', 'source_host_id', 'last_synced_at')

    def get_ip_list(self, obj):
        prefetched = getattr(obj, '_prefetched_objects_cache', {}).get('ip_addresses')
        ips = prefetched if prefetched is not None else obj.ip_addresses.all()
        return [
            {
                'id': ip.id,
                'address': str(ip.address) if ip.address else None,
                'prefix_len': ip.effective_prefix_len,
                'gateway': ip.effective_gateway,
                'netmask': ip.netmask,
                'is_primary': ip.is_primary,
                'status': ip.status,
            }
            for ip in ips
        ]

    def get_product_billing(self, obj):
        if not obj.product_id:
            return None
        try:
            from product_manager import services as product_services
            return product_services.compute_resource_billing(obj, 'vps')
        except Exception:
            return None


class VPSDetailSerializer(VPSListSerializer):
    vps_interfaces = VPSNetworkInterfaceSerializer(many=True, read_only=True)
    route_tables = RouteTableBriefSerializer(many=True, read_only=True)
    operation_logs = serializers.SerializerMethodField()

    class Meta(VPSListSerializer.Meta):
        fields = VPSListSerializer.Meta.fields + (
            'mgmt_username', 'mgmt_password', 'kernel_version',
            'contract', 'remark',
            'vps_interfaces', 'route_tables', 'operation_logs',
        )

    def get_operation_logs(self, obj):
        logs = obj.operation_logs.select_related('operator').order_by('-created_at')[:20]
        return ResourceOperationLogSerializer(logs, many=True).data


class VPSWriteSerializer(serializers.ModelSerializer):
    # 管理 IP 允许留空（同步虚机常无管理 IP）：空字符串归一化为 None，非空才校验 IP 格式
    mgmt_ip = serializers.CharField(required=False, allow_blank=True, allow_null=True, max_length=64)

    class Meta:
        model = VPS
        fields = (
            'name', 'status', 'pop_point',
            'vcpu', 'ram_mb', 'disk_gb', 'nic_count',
            'mgmt_ip', 'mgmt_username', 'mgmt_password', 'mgmt_port',
            'os_type', 'os_version', 'kernel_version',
            'bandwidth_mbps',
            'customer', 'contract', 'product',
            'start_time', 'expire_time', 'remark',
        )

    def validate_bandwidth_mbps(self, value):
        if value <= 0:
            raise serializers.ValidationError('带宽必须大于 0')
        return value

    def validate_mgmt_ip(self, value):
        if value in (None, ''):
            return None
        import ipaddress
        try:
            ipaddress.ip_address(value)
        except ValueError:
            raise serializers.ValidationError('请输入合法的 IP 地址')
        return value


# ═══════════════════════════════════════════════════════════════
# § 7  边缘加速协议配置
# ═══════════════════════════════════════════════════════════════

class TunnelMtuValidationMixin:
    def validate_mtu(self, value):
        if value is None:
            return 1400
        if not 68 <= int(value) <= 65535:
            raise serializers.ValidationError('MTU 必须在 68 到 65535 之间')
        return int(value)


class GREConfigSerializer(TunnelMtuValidationMixin, serializers.ModelSerializer):
    class Meta:
        model = GREConfig
        fields = (
            'id', 'edge', 'local_ip', 'remote_ip', 'tunnel_ip',
            'key', 'ttl', 'mtu', 'pmtud', 'checksum', 'extra_options',
        )
        read_only_fields = ('id',)
        extra_kwargs = {
            'remote_ip': {'required': False, 'allow_null': True},
        }

    def validate_remote_ip(self, value):
        return value or None


class VXLANConfigSerializer(TunnelMtuValidationMixin, serializers.ModelSerializer):
    class Meta:
        model = VXLANConfig
        fields = (
            'id', 'edge', 'vni', 'local_ip', 'remote_ip', 'tunnel_ip',
            'port', 'ttl', 'mtu', 'extra_options',
        )
        read_only_fields = ('id',)


class WireguardConfigSerializer(TunnelMtuValidationMixin, serializers.ModelSerializer):
    class Meta:
        model = WireguardConfig
        fields = (
            'id', 'edge', 'listen_port', 'interface_ip',
            'private_key', 'public_key', 'fwmark', 'mtu',
            'peer_public_key', 'peer_endpoint', 'peer_allowed_ips',
            'peer_preshared_key', 'persistent_keepalive', 'dns',
            'pre_up', 'post_up', 'pre_down', 'post_down',
            'extra_options',
        )
        read_only_fields = ('id',)
        extra_kwargs = {
            'private_key': {'write_only': True},
            'peer_preshared_key': {'write_only': True},
        }


class Socks5ConfigSerializer(serializers.ModelSerializer):
    class Meta:
        model = Socks5Config
        fields = (
            'id', 'edge', 'listen_ip', 'listen_port',
            'auth_required', 'username', 'password',
            'udp_enabled', 'bind_address',
            'max_connections', 'timeout_seconds', 'extra_options',
        )
        read_only_fields = ('id',)
        extra_kwargs = {
            'password': {'write_only': True},
        }


# ═══════════════════════════════════════════════════════════════
# § 7  边缘加速
# ═══════════════════════════════════════════════════════════════

class EdgeAccelerationListSerializer(LocationFieldsMixin, serializers.ModelSerializer):
    protocol_label = serializers.CharField(source='get_protocol_display', read_only=True)
    status_label = serializers.CharField(source='get_status_display', read_only=True)
    pop_name = serializers.CharField(source='pop_point.name', read_only=True)
    customer_name = serializers.CharField(source='customer.name', read_only=True)
    product_name = serializers.CharField(source='product.name', read_only=True)
    product_billing = serializers.SerializerMethodField()
    ip_list = serializers.SerializerMethodField()

    class Meta:
        model = EdgeAcceleration
        fields = (
            'id', 'edge_no', 'name',
            'protocol', 'protocol_label',
            'status', 'status_label',
            'pop_point', 'pop_name',
            'region_id', 'region_name', 'country_name', 'city_name',
            'zone_id', 'zone_name',
            'bandwidth_mbps',
            'customer', 'customer_name',
            'product', 'product_name', 'product_billing',
            'start_time', 'expire_time',
            'node_pushed', 'is_synced', 'node_ifname', 'last_synced_at',
            'ip_list', 'created_at', 'updated_at',
        )
        read_only_fields = ('id', 'edge_no', 'created_at', 'updated_at',
                            'node_pushed', 'is_synced', 'node_ifname', 'last_synced_at')

    def get_ip_list(self, obj):
        prefetched = getattr(obj, '_prefetched_objects_cache', {}).get('ip_addresses')
        ips = prefetched if prefetched is not None else obj.ip_addresses.all()
        return [
            {
                'id': ip.id,
                'address': str(ip.address) if ip.address else None,
                'prefix_len': ip.effective_prefix_len,
                'gateway': ip.effective_gateway,
                'netmask': ip.netmask,
                'is_primary': ip.is_primary,
                'status': ip.status,
            }
            for ip in ips
        ]

    def get_product_billing(self, obj):
        if not obj.product_id:
            return None
        try:
            from product_manager import services as product_services
            return product_services.compute_resource_billing(obj, 'edge')
        except Exception:
            return None


class EdgeAccelerationDetailSerializer(EdgeAccelerationListSerializer):
    gre_config = GREConfigSerializer(read_only=True)
    vxlan_config = VXLANConfigSerializer(read_only=True)
    wg_config = WireguardConfigSerializer(read_only=True)
    socks5_config = Socks5ConfigSerializer(read_only=True)
    route_table = RouteTableBriefSerializer(read_only=True)
    operation_logs = serializers.SerializerMethodField()
    tunnel_spec = serializers.SerializerMethodField()
    node_ifname_preview = serializers.SerializerMethodField()
    route_assign_hints = serializers.SerializerMethodField()

    class Meta(EdgeAccelerationListSerializer.Meta):
        fields = EdgeAccelerationListSerializer.Meta.fields + (
            'route_table', 'contract', 'remark',
            'gre_config', 'vxlan_config', 'wg_config', 'socks5_config',
            'tunnel_spec', 'node_ifname_preview', 'route_assign_hints',
            'operation_logs',
        )

    def get_operation_logs(self, obj):
        logs = obj.operation_logs.select_related('operator').order_by('-created_at')[:20]
        return ResourceOperationLogSerializer(logs, many=True).data

    def get_tunnel_spec(self, obj):
        """下发到节点的 desired-tunnel spec 预览（与 vpopmanager netplan_tunnel 一致）。"""
        from .deploy_service import _build_tunnel_spec
        try:
            built = _build_tunnel_spec(obj)
        except Exception:  # noqa: BLE001
            return None
        if not built:
            return None
        kind, spec = built
        return {'kind': kind, 'spec': spec}

    def get_route_assign_hints(self, obj):
        from .ip_ops import edge_route_hints
        return edge_route_hints(obj)

    def get_node_ifname_preview(self, obj):
        from .deploy_service import _edge_ifname
        if obj.protocol == EdgeAcceleration.Protocol.SOCKS5:
            return None
        try:
            return _edge_ifname(obj)
        except Exception:  # noqa: BLE001
            return None


class EdgeAccelerationWriteSerializer(serializers.ModelSerializer):
    class Meta:
        model = EdgeAcceleration
        fields = (
            'name', 'protocol', 'status', 'pop_point',
            'bandwidth_mbps', 'route_table',
            'customer', 'contract', 'product',
            'start_time', 'expire_time', 'remark',
        )


# ═══════════════════════════════════════════════════════════════
# § 3  POP 点（依赖前面的 Serializer，放在后面）
# ═══════════════════════════════════════════════════════════════

class PopPointListSerializer(LocationFieldsMixin, serializers.ModelSerializer):
    status_label        = serializers.CharField(source='get_status_display', read_only=True)
    pop_type_label      = serializers.CharField(source='get_pop_type_display', read_only=True)
    network_type_label  = serializers.CharField(source='get_network_type_display', read_only=True)
    asn_nature_label    = serializers.CharField(source='get_asn_nature_display', read_only=True)
    ip_attribute_label  = serializers.CharField(source='get_ip_attribute_display', read_only=True)
    config_mode_label   = serializers.CharField(source='get_config_mode_display', read_only=True)
    ip_delivery_mode_label = serializers.CharField(source='get_ip_delivery_mode_display', read_only=True)
    deploy_status_label = serializers.CharField(source='get_deploy_status_display', read_only=True)
    city_info           = CityBriefSerializer(source='city', read_only=True)
    availability_zone_name = serializers.CharField(
        source='availability_zone.name', read_only=True, allow_null=True, default='',
    )
    carriers_info       = CarrierBriefSerializer(source='carriers', many=True, read_only=True)
    supplier_name       = serializers.CharField(source='supplier.name', read_only=True, default=None)
    pop_name            = serializers.SerializerMethodField()
    location_pop_path   = ''

    # ── 带宽字段（全部自动计算，不再手动维护 used_bandwidth_mbps）──
    vps_bandwidth_mbps      = serializers.SerializerMethodField()
    edge_bandwidth_mbps     = serializers.SerializerMethodField()
    used_bandwidth_mbps     = serializers.SerializerMethodField()
    available_bandwidth_mbps = serializers.SerializerMethodField()
    bw_usage_pct            = serializers.SerializerMethodField()

    vps_count  = serializers.SerializerMethodField()
    edge_count = serializers.SerializerMethodField()

    class Meta:
        model = PopPoint
        fields = (
            'id', 'code', 'name', 'pop_name',
            'region_id', 'region_name', 'country_name', 'city_name',
            'zone_id', 'zone_name',
            'pop_type', 'pop_type_label',
            'status', 'status_label',
            'city', 'city_info', 'availability_zone', 'availability_zone_name',
            'network_type', 'network_type_label',
            'asn_nature', 'asn_nature_label',
            'ip_attribute', 'ip_attribute_label',
            'carriers', 'carriers_info',
            'supplier', 'supplier_name', 'supplier_external_id',
            'config_mode', 'config_mode_label', 'allow_oversell',
            'ip_delivery_mode', 'ip_delivery_mode_label',
            'mgmt_ip', 'ssh_port', 'total_bandwidth_mbps',
            'cpu_model', 'cpu_cores', 'ram_gb', 'disk_gb', 'disk_info',
            'os_type', 'os_version',
            'deploy_status', 'deploy_status_label', 'agent_version',
            'last_deploy_at', 'last_sync_at',
            'config_dirty', 'config_dirty_at', 'last_push_at',
            'vps_bandwidth_mbps', 'edge_bandwidth_mbps',
            'used_bandwidth_mbps', 'available_bandwidth_mbps', 'bw_usage_pct',
            'vps_count', 'edge_count',
            'created_at', 'updated_at',
        )
        read_only_fields = ('id', 'code', 'created_at', 'updated_at',
                            'deploy_status', 'agent_version', 'last_deploy_at', 'last_sync_at',
                            'config_dirty', 'config_dirty_at', 'last_push_at')

    def get_pop_name(self, obj):
        return obj.name

    # ── 内部辅助：优先读 queryset 注解，避免额外 SQL ──────────────
    def _ann(self, obj, key):
        """从 queryset 注解中读取，若不存在则返回 None"""
        return getattr(obj, key, None)

    def get_vps_bandwidth_mbps(self, obj):
        v = self._ann(obj, 'ann_vps_bw')
        if v is not None:
            return v
        return obj.vps_list.aggregate(t=Sum('bandwidth_mbps'))['t'] or 0

    def get_edge_bandwidth_mbps(self, obj):
        v = self._ann(obj, 'ann_edge_bw')
        if v is not None:
            return v
        return obj.edge_accelerations.aggregate(t=Sum('bandwidth_mbps'))['t'] or 0

    def get_used_bandwidth_mbps(self, obj):
        return self.get_vps_bandwidth_mbps(obj) + self.get_edge_bandwidth_mbps(obj)

    def get_available_bandwidth_mbps(self, obj):
        return max(0, obj.total_bandwidth_mbps - self.get_used_bandwidth_mbps(obj))

    def get_bw_usage_pct(self, obj):
        if not obj.total_bandwidth_mbps:
            return 0
        return round(self.get_used_bandwidth_mbps(obj) / obj.total_bandwidth_mbps * 100, 1)

    def get_vps_count(self, obj):
        v = self._ann(obj, 'ann_vps_count')
        return v if v is not None else obj.vps_list.count()

    def get_edge_count(self, obj):
        v = self._ann(obj, 'ann_edge_count')
        return v if v is not None else obj.edge_accelerations.count()


class PopPointDetailSerializer(PopPointListSerializer):
    interfaces = NetworkInterfaceSerializer(many=True, read_only=True)
    ip_blocks = serializers.SerializerMethodField()
    vps_list = VPSListSerializer(many=True, read_only=True)
    edge_accelerations = EdgeAccelerationListSerializer(many=True, read_only=True)
    route_tables = RouteTableSerializer(many=True, read_only=True)
    policy_routes = PolicyRouteSerializer(many=True, read_only=True)
    operation_logs = serializers.SerializerMethodField()
    # 硬件资源已分配汇总（用于产品规划/库存测算）
    allocated_vcpu = serializers.IntegerField(read_only=True)
    allocated_ram_gb = serializers.IntegerField(read_only=True)
    allocated_disk_gb = serializers.IntegerField(read_only=True)
    has_mgmt_password = serializers.SerializerMethodField()
    has_api_password = serializers.SerializerMethodField()
    has_mgmt_token = serializers.SerializerMethodField()

    class Meta(PopPointListSerializer.Meta):
        fields = PopPointListSerializer.Meta.fields + (
            'mgmt_username', 'mgmt_token', 'mgmt_url',
            'api_base_url', 'api_username', 'api_verify_ssl',
            'has_mgmt_password', 'has_api_password', 'has_mgmt_token',
            'snmp_community', 'snmp_version',
            'cpu_model', 'cpu_cores', 'ram_gb', 'disk_gb', 'disk_info',
            'allocated_vcpu', 'allocated_ram_gb', 'allocated_disk_gb',
            'os_type', 'os_version',
            'remark',
            'interfaces', 'ip_blocks',
            'vps_list', 'edge_accelerations',
            'route_tables', 'policy_routes',
            'operation_logs',
        )

    def get_ip_blocks(self, obj):
        from .ip_scope import annotate_ip_block_usage, exclude_tunnel_blocks, group_ip_blocks
        qs = annotate_ip_block_usage(
            exclude_tunnel_blocks(obj.ip_blocks.select_related(
                'pop_point__city__country__region', 'carrier',
            ))
        )
        return IPBlockSerializer(group_ip_blocks(list(qs)), many=True, context=self.context).data

    def get_operation_logs(self, obj):
        logs = obj.operation_logs.select_related('operator').order_by('-created_at')[:30]
        return ResourceOperationLogSerializer(logs, many=True).data

    def get_has_mgmt_password(self, obj):
        return bool((obj.mgmt_password or '').strip())

    def get_has_api_password(self, obj):
        return bool((obj.api_password or '').strip())

    def get_has_mgmt_token(self, obj):
        return bool((obj.mgmt_token or '').strip())


class PopPointWriteSerializer(serializers.ModelSerializer):
    """
    POP 点写入序列化器。
    注意：used_bandwidth_mbps 已从可写字段移除，
    系统将自动从下挂 VPS + 边缘加速的带宽汇总计算。
    """
    availability_zone = serializers.PrimaryKeyRelatedField(
        queryset=AvailabilityZone.objects.all(),
        required=False, allow_null=True,
    )

    class Meta:
        model = PopPoint
        fields = (
            'name', 'pop_type', 'status', 'city', 'availability_zone',
            'network_type', 'asn_nature', 'ip_attribute', 'carriers',
            'supplier', 'supplier_external_id', 'config_mode', 'allow_oversell',
            'ip_delivery_mode',
            'mgmt_ip', 'ssh_port', 'mgmt_username', 'mgmt_password', 'mgmt_token',
            'mgmt_url', 'api_base_url', 'api_username', 'api_password',
            'api_verify_ssl',
            'snmp_community', 'snmp_version',
            'cpu_model', 'cpu_cores', 'ram_gb', 'disk_gb', 'disk_info',
            'os_type', 'os_version',
            'total_bandwidth_mbps',   # 仅总带宽可由管理员设置
            'remark',
        )
        extra_kwargs = {
            'mgmt_password': {'write_only': True},
            'api_password': {'write_only': True},
        }

    # 编辑时留空表示沿用已保存的凭据，避免每次改节点都把密码和 Token 清空。
    _KEEP_IF_BLANK = ('mgmt_password', 'api_password', 'mgmt_token')

    def validate(self, attrs):
        city = attrs.get('city') or getattr(self.instance, 'city', None)
        zone = attrs.get('availability_zone')
        if 'availability_zone' not in attrs and self.instance:
            zone = self.instance.availability_zone
        if zone and city and zone.city_id != city.pk:
            raise serializers.ValidationError({'availability_zone': '可用区必须属于所选城市'})
        return attrs

    def update(self, instance, validated_data):
        for field in self._KEEP_IF_BLANK:
            if field in validated_data and not str(validated_data.get(field) or '').strip():
                validated_data.pop(field)
        return super().update(instance, validated_data)


# ═══════════════════════════════════════════════════════════════
# § 3b  POP 点带宽明细
# ═══════════════════════════════════════════════════════════════

class VPSBandwidthItemSerializer(serializers.ModelSerializer):
    """用于带宽明细中展示每台VPS的带宽占用"""
    status_label   = serializers.CharField(source='get_status_display', read_only=True)
    customer_name  = serializers.CharField(source='customer.name', read_only=True, default=None)

    class Meta:
        model = VPS
        fields = ('id', 'vps_no', 'name', 'status', 'status_label',
                  'bandwidth_mbps', 'customer', 'customer_name',
                  'mgmt_ip', 'expire_time')


class EdgeBandwidthItemSerializer(serializers.ModelSerializer):
    """用于带宽明细中展示每个边缘加速节点的带宽占用"""
    status_label   = serializers.CharField(source='get_status_display', read_only=True)
    protocol_label = serializers.CharField(source='get_protocol_display', read_only=True)
    customer_name  = serializers.CharField(source='customer.name', read_only=True, default=None)

    class Meta:
        model = EdgeAcceleration
        fields = ('id', 'edge_no', 'name', 'protocol', 'protocol_label',
                  'status', 'status_label', 'bandwidth_mbps',
                  'customer', 'customer_name', 'expire_time')


# ═══════════════════════════════════════════════════════════════
# § 8  带宽分配
# ═══════════════════════════════════════════════════════════════

class BandwidthAllocationSerializer(LocationFieldsMixin, serializers.ModelSerializer):
    target_type_label = serializers.CharField(source='get_target_type_display', read_only=True)
    target_name = serializers.SerializerMethodField()
    product_name = serializers.CharField(source='product.name', read_only=True)
    pop_name = serializers.CharField(source='pop_point.name', read_only=True)

    class Meta:
        model = BandwidthAllocation
        fields = (
            'id', 'pop_point', 'pop_name',
            'region_id', 'region_name', 'country_name', 'city_name',
            'zone_id', 'zone_name',
            'target_type', 'target_type_label',
            'vps', 'edge', 'target_name',
            'allocated_mbps', 'burst_mbps', 'is_active',
            'product', 'product_name',
            'is_synced', 'source_interface_code', 'last_synced_at',
            'created_by', 'created_at', 'updated_at',
        )
        read_only_fields = ('id', 'created_at', 'updated_at',
                            'is_synced', 'source_interface_code', 'last_synced_at')

    def get_target_name(self, obj):
        if obj.vps:
            return str(obj.vps)
        if obj.edge:
            return str(obj.edge)
        if obj.source_interface_code:
            return f'隧道 {obj.source_interface_code}'
        return '—'

    def validate(self, attrs):
        target_type = attrs.get('target_type')
        vps = attrs.get('vps')
        edge = attrs.get('edge')
        if target_type == 'vps' and not vps:
            raise serializers.ValidationError('目标类型为 VPS 时，vps 字段必填')
        if target_type == 'edge' and not edge:
            raise serializers.ValidationError('目标类型为边缘加速时，edge 字段必填')
        return attrs


# ═══════════════════════════════════════════════════════════════
# § 9  操作日志
# ═══════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════
# § 隧道 IP 地址段（p2p 池）
# ═══════════════════════════════════════════════════════════════

class TunnelIPPoolSerializer(LocationFieldsMixin, serializers.ModelSerializer):
    pop_point_name = serializers.CharField(source='pop_point.name', read_only=True)
    pop_name = serializers.CharField(source='pop_point.name', read_only=True)
    customer_name = serializers.CharField(source='customer.name', read_only=True, default=None)
    total_blocks = serializers.SerializerMethodField()
    used_blocks = serializers.SerializerMethodField()
    free_blocks = serializers.SerializerMethodField()
    next_free = serializers.SerializerMethodField()

    class Meta:
        model = TunnelIPPool
        fields = (
            'id', 'pop_point', 'pop_point_name', 'pop_name',
            'region_id', 'region_name', 'country_name', 'city_name',
            'zone_id', 'zone_name',
            'name', 'cidr', 'point_prefixlen',
            'customer', 'customer_name',
            'is_active', 'remark',
            'is_synced', 'source_ref', 'last_synced_at',
            'total_blocks', 'used_blocks', 'free_blocks', 'next_free',
            'created_at', 'updated_at',
        )
        read_only_fields = (
            'id', 'is_synced', 'source_ref', 'last_synced_at',
            'created_at', 'updated_at',
        )

    def _usage(self, obj):
        cached = getattr(obj, '_usage_cache', None)
        if cached is None:
            from . import tunnel_ip
            cached = tunnel_ip.usage(obj)
            obj._usage_cache = cached
        return cached

    def get_total_blocks(self, obj):
        return self._usage(obj)['total_blocks']

    def get_used_blocks(self, obj):
        return self._usage(obj)['used_blocks']

    def get_free_blocks(self, obj):
        return self._usage(obj)['free_blocks']

    def get_next_free(self, obj):
        return self._usage(obj)['next_free']

    def validate(self, attrs):
        from django.core.exceptions import ValidationError as DjangoValidationError
        # 组装临时实例触发 model.clean() 的 CIDR / 前缀校验与归一化
        merged = {
            'name': '', 'cidr': '0.0.0.0/0', 'point_prefixlen': 30,
        }
        if self.instance:
            for f in ('name', 'cidr', 'point_prefixlen'):
                merged[f] = getattr(self.instance, f)
        merged.update({k: v for k, v in attrs.items()
                       if k in ('name', 'cidr', 'point_prefixlen')})
        instance = TunnelIPPool(**merged)
        try:
            instance.clean()
        except DjangoValidationError as exc:
            raise serializers.ValidationError(
                exc.message_dict if hasattr(exc, 'message_dict') else exc.messages
            )
        if 'cidr' in attrs:
            attrs['cidr'] = instance.cidr  # 归一化后的网络地址
        return attrs


class ResourceOperationLogSerializer(serializers.ModelSerializer):
    action_type_label = serializers.CharField(source='get_action_type_display', read_only=True)
    target_model_label = serializers.CharField(source='get_target_model_display', read_only=True)
    operator_name = serializers.SerializerMethodField()

    class Meta:
        model = ResourceOperationLog
        fields = (
            'id', 'operator', 'operator_name',
            'action_type', 'action_type_label',
            'target_model', 'target_model_label',
            'target_id', 'target_repr',
            'pop_point', 'vps', 'edge',
            'description', 'old_value', 'new_value',
            'ip_address', 'created_at',
        )
        read_only_fields = fields

    def get_operator_name(self, obj):
        if not obj.operator:
            return '系统'
        return obj.operator.get_full_name() or obj.operator.username


# ═══════════════════════════════════════════════════════════════
# § 辅助 — 状态变更请求体
# ═══════════════════════════════════════════════════════════════

class StatusChangeSerializer(serializers.Serializer):
    status = serializers.CharField(max_length=20)
    reason = serializers.CharField(max_length=512, required=False, default='')
