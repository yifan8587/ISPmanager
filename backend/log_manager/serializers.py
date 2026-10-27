from rest_framework import serializers

from .models import AuditLog
from .services import ACTION_TYPE_LABELS, MODULE_LABELS, action_type_label


class AuditLogSerializer(serializers.ModelSerializer):
    module_label = serializers.SerializerMethodField()
    action_type_label = serializers.SerializerMethodField()
    source_label = serializers.CharField(source='get_source_display', read_only=True)

    class Meta:
        model = AuditLog
        fields = (
            'id',
            'operator',
            'operator_name',
            'module',
            'module_label',
            'action_type',
            'action_type_label',
            'target_model',
            'target_id',
            'target_repr',
            'description',
            'detail',
            'ip_address',
            'request_path',
            'request_method',
            'source',
            'source_label',
            'source_ref',
            'created_at',
        )
        read_only_fields = fields

    def get_module_label(self, obj):
        return MODULE_LABELS.get(obj.module) or obj.get_module_display()

    def get_action_type_label(self, obj):
        return action_type_label(obj.action_type)


def serialize_meta(*, module_counts: dict[str, int] | None = None) -> dict:
    counts = module_counts or {}
    return {
        'modules': [
            {
                'value': value,
                'label': label,
                'count': counts.get(value, 0),
            }
            for value, label in AuditLog.Module.choices
        ],
        'action_types': [
            {'value': value, 'label': label}
            for value, label in ACTION_TYPE_LABELS.items()
        ],
        'sources': [
            {'value': value, 'label': label}
            for value, label in AuditLog.Source.choices
        ],
    }
