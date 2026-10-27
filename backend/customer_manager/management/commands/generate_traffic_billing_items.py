from django.core.management.base import BaseCommand

from customer_manager.traffic_billing import process_traffic_billing_cycle


class Command(BaseCommand):
    help = '归档已关闭窗口的完成报告，并生成独立流量超额账单明细'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='只列出将归档 / 出账的报告，不写库',
        )
        parser.add_argument(
            '--report-id',
            type=int,
            default=None,
            help='只处理指定报告（完成报告会先归档，出账不限制必须已归档）',
        )
        parser.add_argument(
            '--bill-month',
            default=None,
            help='按报告结束时间过滤账期，格式 YYYY-MM',
        )
        parser.add_argument(
            '--no-archive',
            action='store_true',
            help='不自动归档，只补扫已归档报告',
        )
        parser.add_argument(
            '--archive-only',
            action='store_true',
            help='只归档，不出账',
        )
        parser.add_argument(
            '--include-open-month',
            action='store_true',
            help='连本月已结束窗口的完成报告一并归档（默认只归档上一自然月及更早）',
        )

    def handle(self, *args, **options):
        if options['archive_only']:
            from customer_manager.traffic_billing import archive_closed_traffic_reports
            stats = archive_closed_traffic_reports(
                dry_run=options['dry_run'],
                report_id=options['report_id'],
                include_open_month=options['include_open_month'],
            )
            prefix = '计划归档' if options['dry_run'] else '已归档'
            self.stdout.write(self.style.SUCCESS(
                f'{prefix}：扫描 {stats["scanned"]}，归档 {stats["archived"]}',
            ))
            for item in stats.get('planned') or []:
                self.stdout.write(f'  - {item}')
            return

        stats = process_traffic_billing_cycle(
            dry_run=options['dry_run'],
            report_id=options['report_id'],
            bill_month=options['bill_month'],
            archive=not options['no_archive'],
            include_open_month=options['include_open_month'],
        )
        prefix = '计划' if options['dry_run'] else '已处理'
        self.stdout.write(self.style.SUCCESS(
            f'{prefix}：归档 {stats["archived"]}，扫描 {stats["scanned"]}，'
            f'出账 {stats["billed"]}，跳过 {stats["skipped"]}，错误 {stats["errors"]}',
        ))
        for item in stats.get('planned') or []:
            self.stdout.write(f'  - {item}')
