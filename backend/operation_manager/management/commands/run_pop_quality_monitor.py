"""作为受管守护进程运行 POP 品质探测。"""
from __future__ import annotations

import signal
import time
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import close_old_connections
from django.utils import timezone

from operation_manager.pop_quality import (
    discover_pop_quality_monitors,
    probe_tools_status,
    purge_old_probes,
    run_due_probes,
)


class Command(BaseCommand):
    help = (
        '持续探测 POP 管理 IP/管理 URL 并写入品质采样；'
        '应由 systemd 或 supervisor 作为独立守护进程运行。'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--once',
            action='store_true',
            help='仅执行一轮到期探测后退出（用于联调或 cron）。',
        )
        parser.add_argument(
            '--poll-seconds',
            type=int,
            default=5,
            help='守护进程轮询到期任务的间隔，默认 5 秒。',
        )
        parser.add_argument(
            '--batch-size',
            type=int,
            default=20,
            help='单轮最多执行多少个到期 POP 探测，默认 20。',
        )
        parser.add_argument(
            '--retention-days',
            type=int,
            default=90,
            help='品质采样保留天数，默认 90 天。',
        )
        parser.add_argument(
            '--no-discover',
            action='store_true',
            help='不自动为带管理 IP 或管理 URL 的 POP 创建监控配置。',
        )

    def handle(self, *args, **options):
        self._stopping = False
        self._install_signal_handlers()
        poll_seconds = max(1, min(int(options['poll_seconds']), 300))
        batch_size = max(1, min(int(options['batch_size']), 200))
        retention_days = max(1, int(options['retention_days']))
        auto_discover = not options['no_discover']

        tools = probe_tools_status()
        missing = [name for name, available in tools.items() if not available]
        if missing:
            self.stderr.write(self.style.WARNING(
                f'未发现命令：{", ".join(missing)}；对应探测会写入错误采样，'
                '请安装 iputils-ping / curl 后重启守护进程。',
            ))

        if options['once']:
            result = self._run_cycle(
                auto_discover=auto_discover,
                batch_size=batch_size,
                retention_days=retention_days,
                cleanup=True,
            )
            self.stdout.write(self.style.SUCCESS(
                'POP 品质监控单轮完成：'
                f'新增配置 {result["discovered"]}，采样 {result["probed"]}，'
                f'清理 {result["purged"]}',
            ))
            return

        self.stdout.write(self.style.SUCCESS(
            f'POP 品质监控守护进程已启动（轮询 {poll_seconds}s，'
            f'批次 {batch_size}，采样保留 {retention_days} 天）。',
        ))
        next_cleanup_at = timezone.now()
        while not self._stopping:
            now = timezone.now()
            result = self._run_cycle(
                auto_discover=auto_discover,
                batch_size=batch_size,
                retention_days=retention_days,
                cleanup=now >= next_cleanup_at,
            )
            if result['purged'] or result['probed'] or result['discovered']:
                self.stdout.write(
                    f'[{now:%Y-%m-%d %H:%M:%S}] '
                    f'新增配置={result["discovered"]} '
                    f'采样={result["probed"]} 清理={result["purged"]}',
                )
            if now >= next_cleanup_at:
                next_cleanup_at = now + timedelta(hours=24)
            self._sleep_until_next_cycle(poll_seconds)

        self.stdout.write(self.style.WARNING('POP 品质监控守护进程已停止。'))

    def _run_cycle(
        self,
        *,
        auto_discover: bool,
        batch_size: int,
        retention_days: int,
        cleanup: bool,
    ) -> dict[str, int]:
        close_old_connections()
        try:
            discover_result = (
                discover_pop_quality_monitors() if auto_discover else {'created': 0}
            )
            probes = run_due_probes(batch_size=batch_size)
            purged = purge_old_probes(retention_days=retention_days) if cleanup else 0
            return {
                'discovered': discover_result['created'],
                'probed': len(probes),
                'purged': purged,
            }
        finally:
            close_old_connections()

    def _install_signal_handlers(self):
        def request_stop(_signum, _frame):
            self._stopping = True

        signal.signal(signal.SIGINT, request_stop)
        signal.signal(signal.SIGTERM, request_stop)

    def _sleep_until_next_cycle(self, seconds: int):
        remaining = seconds
        while remaining > 0 and not self._stopping:
            delay = min(1, remaining)
            time.sleep(delay)
            remaining -= delay
