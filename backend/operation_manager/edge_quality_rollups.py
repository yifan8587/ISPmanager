"""中心边缘品质样本的小时/日汇总与原始数据保留。"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone as datetime_timezone
from typing import Iterator

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .models import EdgeInterfaceQualityRollup, EdgeInterfaceQualitySample


@dataclass
class _Aggregate:
    sample_count: int = 0
    ok_samples: int = 0
    latency_values: list[float] = field(default_factory=list)
    loss_values: list[float] = field(default_factory=list)
    jitter_values: list[float] = field(default_factory=list)
    customer_id: int | None = None
    last_sampled_at: datetime | None = None

    def add(self, sample: EdgeInterfaceQualitySample) -> None:
        self.sample_count += 1
        self.ok_samples += int(bool(sample.ok))
        if sample.latency_ms is not None:
            self.latency_values.append(float(sample.latency_ms))
        if sample.packet_loss_pct is not None:
            self.loss_values.append(float(sample.packet_loss_pct))
        if sample.jitter_ms is not None:
            self.jitter_values.append(float(sample.jitter_ms))
        self.customer_id = sample.customer_id
        if self.last_sampled_at is None or sample.sampled_at > self.last_sampled_at:
            self.last_sampled_at = sample.sampled_at

    @staticmethod
    def _average(values: list[float]) -> float | None:
        return round(sum(values) / len(values), 4) if values else None

    def defaults(self) -> dict:
        return {
            'customer_id': self.customer_id,
            'sample_count': self.sample_count,
            'ok_samples': self.ok_samples,
            'latency_avg_ms': self._average(self.latency_values),
            'latency_max_ms': round(max(self.latency_values), 4) if self.latency_values else None,
            'packet_loss_avg_pct': self._average(self.loss_values),
            'jitter_avg_ms': self._average(self.jitter_values),
            'availability_pct': round(
                100 * self.ok_samples / self.sample_count,
                4,
            ) if self.sample_count else None,
            'last_sampled_at': self.last_sampled_at,
        }


def _bucket_start(sampled_at: datetime, bucket_kind: str) -> datetime:
    """统一按 UTC 对齐聚合桶，避免节点/数据库时区变化造成重复汇总。"""
    if timezone.is_naive(sampled_at):
        sampled_at = timezone.make_aware(sampled_at, datetime_timezone.utc)
    value = sampled_at.astimezone(datetime_timezone.utc)
    if bucket_kind == EdgeInterfaceQualityRollup.BucketKind.DAY:
        return value.replace(hour=0, minute=0, second=0, microsecond=0)
    return value.replace(minute=0, second=0, microsecond=0)


def _aggregates_for_bucket(samples, bucket_kind: str) -> Iterator[tuple[tuple, _Aggregate]]:
    """按 POP、Edge、UTC 桶流式遍历，避免长期运行时一次加载所有原始样本。"""
    samples = (
        samples
        .order_by('pop_id', 'edge_id', 'sampled_at', 'id')
        .only(
            'pop_id', 'edge_id', 'customer_id', 'sampled_at', 'latency_ms',
            'packet_loss_pct', 'jitter_ms', 'ok',
        )
    )
    previous_key = None
    aggregate = None
    for sample in samples.iterator(chunk_size=2000):
        bucket_ts = _bucket_start(sample.sampled_at, bucket_kind)
        key = (sample.pop_id, sample.edge_id, bucket_ts)
        if previous_key is not None and key != previous_key:
            yield previous_key, aggregate
            aggregate = None
        if aggregate is None:
            aggregate = _Aggregate()
        aggregate.add(sample)
        previous_key = key
    if previous_key is not None and aggregate is not None:
        yield previous_key, aggregate


def rebuild_edge_quality_rollups(
    *,
    raw_retention_days: int = 30,
    lookback_hours: int = 48,
    prune_raw: bool = True,
    full_rebuild: bool = False,
    now: datetime | None = None,
) -> dict[str, int]:
    """
    幂等重建小时和日品质汇总，并在确认写入后清理超过保留期的原始样本。

    该函数适合由每小时一次的管理命令调用。正常运行只重算最近的开放桶，并在删除即将
    过期的原始样本前补齐其汇总，避免每小时扫描完整 30 天原始表。首次上线可传
    ``full_rebuild=True`` 建立全量历史汇总。
    """
    now = now or timezone.now()
    raw_retention_days = max(1, int(raw_retention_days))
    lookback_hours = max(1, int(lookback_hours))
    summary = {'hour_rollups': 0, 'day_rollups': 0, 'raw_pruned': 0}
    retention_cutoff = now - timedelta(days=raw_retention_days)
    if full_rebuild:
        samples = EdgeInterfaceQualitySample.objects.all()
    else:
        # 最近开放桶会持续更新；即将清理的旧桶需在删除前最后一次汇总。
        samples = EdgeInterfaceQualitySample.objects.filter(
            Q(sampled_at__gte=now - timedelta(hours=lookback_hours))
            | Q(sampled_at__lt=retention_cutoff),
        )

    with transaction.atomic():
        for bucket_kind, summary_key in (
            (EdgeInterfaceQualityRollup.BucketKind.HOUR, 'hour_rollups'),
            (EdgeInterfaceQualityRollup.BucketKind.DAY, 'day_rollups'),
        ):
            for (pop_id, edge_id, bucket_ts), aggregate in _aggregates_for_bucket(
                samples,
                bucket_kind,
            ):
                EdgeInterfaceQualityRollup.objects.update_or_create(
                    pop_id=pop_id,
                    edge_id=edge_id,
                    bucket_kind=bucket_kind,
                    bucket_ts=bucket_ts,
                    defaults=aggregate.defaults(),
                )
                summary[summary_key] += 1

        if prune_raw:
            deleted, _ = EdgeInterfaceQualitySample.objects.filter(
                sampled_at__lt=retention_cutoff,
            ).delete()
            summary['raw_pruned'] = deleted

    return summary
