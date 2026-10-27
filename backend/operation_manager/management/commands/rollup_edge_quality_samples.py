"""聚合 POP 上报的边缘接口品质样本并执行原始数据保留。"""
from __future__ import annotations

import json

from django.core.management.base import BaseCommand

from operation_manager.edge_quality_rollups import rebuild_edge_quality_rollups


class Command(BaseCommand):
    help = '重建边缘接口品质小时/日汇总，并清理超过保留期的原始样本'

    def add_arguments(self, parser):
        parser.add_argument(
            '--retention-days',
            type=int,
            default=30,
            help='原始样本保留天数（默认 30，最小 1）',
        )
        parser.add_argument(
            '--no-prune',
            action='store_true',
            help='仅重建汇总，不删除历史原始样本',
        )
        parser.add_argument(
            '--lookback-hours',
            type=int,
            default=48,
            help='每次重算最近多少小时的开放桶（默认 48）',
        )
        parser.add_argument(
            '--full',
            action='store_true',
            help='全量重建历史汇总；首次上线或数据修复时使用',
        )

    def handle(self, *_args, **options):
        result = rebuild_edge_quality_rollups(
            raw_retention_days=options['retention_days'],
            lookback_hours=options['lookback_hours'],
            prune_raw=not options['no_prune'],
            full_rebuild=options['full'],
        )
        self.stdout.write(json.dumps(result, ensure_ascii=False))
