from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0009_channel_role_drop_product_resource'),
    ]

    operations = [
        migrations.AddField(
            model_name='userprofile',
            name='last_seen_at',
            field=models.DateTimeField(
                blank=True,
                help_text='登录或用户操作后更新；超过空闲时限需重新登录',
                null=True,
                verbose_name='最近操作时间',
            ),
        ),
    ]
