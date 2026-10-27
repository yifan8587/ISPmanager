#!/usr/bin/env bash
# ════════════════════════════════════════════════════════════════
# manage-service.sh — 后端服务与部署便捷管理
# 用法：
#   bash manage-service.sh status        查看后端 + POP 品质 + 到期/维护/日终 timer + nginx
#   bash manage-service.sh restart       重启后端与 POP 品质监控
#   bash manage-service.sh reload        平滑重载后端（HUP）
#   bash manage-service.sh stop|start    停止/启动后端
#   bash manage-service.sh logs          跟踪后端日志
#   bash manage-service.sh quality-logs  跟踪 POP 品质监控日志
#   bash manage-service.sh expiry-logs   跟踪实例到期引擎日志
#   bash manage-service.sh maint-logs    跟踪 15 分钟维护任务日志
#   bash manage-service.sh daily-logs    跟踪日终出账 / SLA 日志
#   bash manage-service.sh migrate       执行数据库迁移
#   bash manage-service.sh collectstatic 重新收集静态资源
#   sudo bash manage-service.sh update   检查 + 调优 + 构建 + 迁移 + 重启
#   sudo bash manage-service.sh update-package [--payload DIR] [--root /opt/app]
#   bash manage-service.sh preflight     安装/升级前检查
#   sudo bash manage-service.sh tune     按机器规格调优
#   sudo bash manage-service.sh ssl      申请/刷新 HTTPS
#   bash manage-service.sh verify        部署自检
# ════════════════════════════════════════════════════════════════
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"
load_env

ACTION="${1:-status}"
SVC="ispmanager.service"
QUALITY_SVC="ispmanager-pop-quality.service"
EXPIRY_TIMER="ispmanager-instance-expiry.timer"
EXPIRY_SVC="ispmanager-instance-expiry.service"
MAINT_TIMER="ispmanager-maintenance.timer"
MAINT_SVC="ispmanager-maintenance.service"
DAILY_TIMER="ispmanager-daily-jobs.timer"
DAILY_SVC="ispmanager-daily-jobs.service"
RUNTIME_ENV_FILE="/etc/ispmanager.env"

run_manage() {
    (
        cd "$BACKEND_DIR" || exit 1
        if [[ -f "$RUNTIME_ENV_FILE" ]]; then
            set -a
            # shellcheck disable=SC1090
            source_env_file "$RUNTIME_ENV_FILE"
            set +a
        fi
        "$VENV_DIR/bin/python" manage.py "$@"
    )
}

case "$ACTION" in
    status)
        systemctl status "$SVC" --no-pager || true
        echo; systemctl status "$QUALITY_SVC" --no-pager || true
        echo; systemctl status "$EXPIRY_TIMER" --no-pager || true
        echo; systemctl status "$MAINT_TIMER" --no-pager || true
        echo; systemctl status "$DAILY_TIMER" --no-pager || true
        echo; systemctl status nginx --no-pager | head -n 6 || true
        ;;
    start|stop|restart)
        require_root
        systemctl "$ACTION" "$SVC" "$QUALITY_SVC"
        ok "已 $ACTION $SVC 与 $QUALITY_SVC"
        ;;
    reload)
        require_root
        systemctl reload "$SVC"
        systemctl restart "$QUALITY_SVC"
        ok "已重载 $SVC，并重启 $QUALITY_SVC"
        ;;
    logs)
        journalctl -u "$SVC" -f
        ;;
    quality-logs)
        journalctl -u "$QUALITY_SVC" -f
        ;;
    expiry-logs)
        journalctl -u "$EXPIRY_SVC" -f
        ;;
    maint-logs)
        journalctl -u "$MAINT_SVC" -f
        ;;
    daily-logs)
        journalctl -u "$DAILY_SVC" -f
        ;;
    migrate)
        run_manage migrate --noinput
        ;;
    collectstatic)
        run_manage collectstatic --noinput
        ;;
    preflight|check)
        bash "$SCRIPT_DIR/preflight.sh"
        ;;
    tune)
        require_root
        bash "$SCRIPT_DIR/tune-system.sh"
        ;;
    update)
        require_root
        export FORCE_UPGRADE=1
        PREFLIGHT_SOURCED=1
        # shellcheck disable=SC1091
        source "$SCRIPT_DIR/preflight.sh" || die "更新前检查未通过"
        unset PREFLIGHT_SOURCED
        backup_existing_install
        bash "$SCRIPT_DIR/tune-system.sh"
        load_env
        step "一键更新：构建 → 完整升级（依赖/迁移/静态）→ 重启"
        sudo -u "${SUDO_USER:-root}" bash "$SCRIPT_DIR/build.sh" || bash "$SCRIPT_DIR/build.sh"
        bash "$SCRIPT_DIR/apply-upgrade.sh"

        step "重载 nginx 并重启后端"
        chmod -R a+rX "$ADMIN_FRONTEND_DIR/dist" \
            "$DJANGO_STATIC_ROOT" "$DJANGO_MEDIA_ROOT" 2>/dev/null || true
        if [[ -d "$PORTAL_FRONTEND_DIR/dist" ]]; then
            chmod -R a+rX "$PORTAL_FRONTEND_DIR/dist" 2>/dev/null || true
        fi
        ensure_nginx_traverse "$ADMIN_FRONTEND_DIR/dist" \
            "$DJANGO_STATIC_ROOT" "$DJANGO_MEDIA_ROOT" || true
        [[ -d "$PORTAL_FRONTEND_DIR/dist" ]] && ensure_nginx_traverse "$PORTAL_FRONTEND_DIR/dist" || true
        render_nginx_conf "$NGINX_AVAIL" || warn "nginx 模板渲染失败"
        render_portal_api_nginx || warn "门户 API 站点渲染失败"
        enable_nginx_site || true
        systemctl restart "$SVC" || warn "重启 $SVC 失败"
        systemctl restart "$QUALITY_SVC" || warn "重启 $QUALITY_SVC 失败"
        systemctl enable "$EXPIRY_TIMER" "$MAINT_TIMER" "$DAILY_TIMER" >/dev/null 2>&1 || true
        systemctl start "$EXPIRY_TIMER" "$MAINT_TIMER" "$DAILY_TIMER" >/dev/null 2>&1 || true
        reload_nginx || warn "nginx reload 失败，请执行 nginx -t"

        echo
        if systemctl is-active --quiet "$SVC"; then
            ok "════════════════════════════════════════════════════"
            ok " 更新完成（后端已重启）"
            ok "════════════════════════════════════════════════════"
            systemctl is-active --quiet "$SVC" && echo -e "  $SVC: active"
            systemctl is-active --quiet nginx && echo -e "  nginx: active"
        else
            err "更新流程已走完，但 $SVC 未处于 active，请：journalctl -u $SVC -n 50"
            exit 1
        fi
        ;;
    update-package)
        require_root
        shift
        bash "$SCRIPT_DIR/update-from-package.sh" "$@"
        ;;
    ssl)
        require_root
        bash "$SCRIPT_DIR/setup-ssl.sh"
        ;;
    nat|publish-check)
        bash "$SCRIPT_DIR/check-nat-publish.sh"
        ;;
    verify)
        bash "$SCRIPT_DIR/verify-install.sh"
        ;;
    *)
        die "未知操作：$ACTION（可用 status|start|stop|restart|reload|logs|quality-logs|expiry-logs|maint-logs|daily-logs|migrate|collectstatic|update|update-package|preflight|tune|ssl|nat|verify）"
        ;;
esac
