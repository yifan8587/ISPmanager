"""运维 WebSSH REST API。"""

from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import HasPermission, IsSystemAccount
from accounts.role_permissions import Perm
from .ssh_resolver import resolve_ssh_target


class WebSSHResolveView(APIView):
    """解析 SSH 默认连接信息（含密码，仅已登录运维用户）。"""

    permission_classes = [
        IsAuthenticated,
        IsSystemAccount,
        HasPermission.with_perm(Perm.OPERATION_MANAGE),
    ]

    def get(self, request):
        target_type = request.query_params.get('target_type', '').strip()
        target_id = request.query_params.get('target_id')
        if not target_type or target_id is None:
            return Response({'detail': '请提供 target_type 与 target_id'}, status=400)
        try:
            tid = int(target_id)
        except (TypeError, ValueError):
            return Response({'detail': 'target_id 无效'}, status=400)

        ctx = resolve_ssh_target(target_type, tid)
        if ctx is None:
            return Response({'detail': '目标不存在或类型不支持'}, status=404)
        return Response(ctx)
