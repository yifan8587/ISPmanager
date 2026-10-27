from django.db import migrations, models


DEFAULT_TEMPLATES = [
    {
        'code': 'passwd',
        'name': '修改系统密码',
        'category': '系统',
        'description': '启动 passwd 交互改密（请在终端内按提示输入新密码）',
        'interactive': True,
        'fields': [
            {'key': 'username', 'label': '用户名', 'default': 'root', 'required': True},
        ],
        'command': 'passwd {username}',
        'sort_order': 10,
    },
    {
        'code': 'whoami',
        'name': '当前用户',
        'category': '系统',
        'description': 'whoami && id',
        'fields': [],
        'command': 'whoami && id',
        'sort_order': 20,
    },
    {
        'code': 'uname',
        'name': '系统信息',
        'category': '系统',
        'description': 'uname -a',
        'fields': [],
        'command': 'uname -a',
        'sort_order': 30,
    },
    {
        'code': 'uptime',
        'name': '运行时间',
        'category': '系统',
        'description': 'uptime',
        'fields': [],
        'command': 'uptime',
        'sort_order': 40,
    },
    {
        'code': 'df',
        'name': '磁盘使用',
        'category': '系统',
        'description': 'df -hT',
        'fields': [],
        'command': 'df -hT',
        'sort_order': 50,
    },
    {
        'code': 'nload',
        'name': 'nload 流量监控',
        'category': '网络',
        'description': '实时网卡流量（需已安装 nload）',
        'fields': [
            {'key': 'iface', 'label': '网卡', 'default': 'eth0', 'required': True},
        ],
        'command': 'nload {iface}',
        'sort_order': 110,
    },
    {
        'code': 'iftop',
        'name': 'iftop 连接监控',
        'category': '网络',
        'description': '按连接查看流量（需已安装 iftop）',
        'fields': [
            {'key': 'iface', 'label': '网卡', 'default': 'eth0', 'required': True},
        ],
        'command': 'iftop -i {iface}',
        'sort_order': 120,
    },
    {
        'code': 'iperf3_server',
        'name': 'iperf3 服务端',
        'category': '网络',
        'description': '在本机启动 iperf3 监听',
        'fields': [
            {'key': 'port', 'label': '端口', 'default': '5201', 'required': True},
        ],
        'command': 'iperf3 -s -p {port}',
        'sort_order': 130,
    },
    {
        'code': 'iperf3_client',
        'name': 'iperf3 客户端测速',
        'category': '网络',
        'description': '向远端 iperf3 服务端发起测试',
        'fields': [
            {'key': 'server', 'label': '服务端 IP', 'default': '', 'required': True},
            {'key': 'port', 'label': '端口', 'default': '5201', 'required': True},
            {'key': 'duration', 'label': '时长(秒)', 'default': '30', 'required': True},
            {'key': 'parallel', 'label': '并行流', 'default': '1', 'required': False},
        ],
        'command': 'iperf3 -c {server} -p {port} -t {duration} -P {parallel}',
        'sort_order': 140,
    },
    {
        'code': 'ping',
        'name': 'Ping 探测',
        'category': '网络',
        'description': 'ping 指定地址 4 次',
        'fields': [
            {'key': 'host', 'label': '目标', 'default': '8.8.8.8', 'required': True},
        ],
        'command': 'ping -c 4 {host}',
        'sort_order': 150,
    },
    {
        'code': 'ss_listen',
        'name': '监听端口',
        'category': '网络',
        'description': 'ss -tulpn',
        'fields': [],
        'command': 'ss -tulpn',
        'sort_order': 160,
    },
    {
        'code': 'install_tools',
        'name': '安装常用工具',
        'category': '维护',
        'description': 'Debian/Ubuntu 下安装 iperf3 nload iftop（需 root）',
        'fields': [],
        'command': (
            'export DEBIAN_FRONTEND=noninteractive && apt-get update -qq && '
            'apt-get install -y iperf3 nload iftop'
        ),
        'sort_order': 210,
    },
]


def seed_default_templates(apps, schema_editor):
    WebSSHCommandTemplate = apps.get_model('operation_manager', 'WebSSHCommandTemplate')
    for item in DEFAULT_TEMPLATES:
        WebSSHCommandTemplate.objects.update_or_create(
            code=item['code'],
            defaults={
                **item,
                'is_system': True,
                'is_active': True,
            },
        )


class Migration(migrations.Migration):

    dependencies = [
        ('operation_manager', '0006_monitoring_host_scope_and_counts'),
    ]

    operations = [
        migrations.CreateModel(
            name='WebSSHCommandTemplate',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('code', models.CharField(max_length=64, unique=True, verbose_name='编码')),
                ('name', models.CharField(max_length=128, verbose_name='名称')),
                ('category', models.CharField(default='系统', max_length=32, verbose_name='分类')),
                ('description', models.TextField(blank=True, verbose_name='说明')),
                ('command', models.TextField(verbose_name='命令模板')),
                ('fields', models.JSONField(blank=True, default=list, help_text='[{key, label, default, required}]', verbose_name='参数字段')),
                ('interactive', models.BooleanField(default=False, help_text='如 passwd，需在终端内继续输入', verbose_name='交互命令')),
                ('is_system', models.BooleanField(default=False, verbose_name='系统内置')),
                ('is_active', models.BooleanField(default=True, verbose_name='启用')),
                ('sort_order', models.IntegerField(default=0, verbose_name='排序')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='创建时间')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='更新时间')),
            ],
            options={
                'verbose_name': 'WebSSH 命令模板',
                'verbose_name_plural': 'WebSSH 命令模板',
                'ordering': ['sort_order', 'id'],
            },
        ),
        migrations.RunPython(seed_default_templates, migrations.RunPython.noop),
    ]
