"""账单在线支付的状态机与入账服务。"""
from __future__ import annotations

import logging
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from django.db import transaction as db_transaction
from django.utils import timezone

from system_manager.models import PaymentChannel
from system_manager.payment_providers import PAYMENT_PROVIDER_SPECS, validate_provider_config
from system_manager import currency_services

from . import payment_gateways
from .models import (
    CustomerBilling,
    CustomerOperationLog,
    PaymentTransaction,
    PaymentWebhookEvent,
    record_operation_log,
)


logger = logging.getLogger(__name__)


class PaymentServiceError(ValueError):
    """支付业务校验失败。"""


OPEN_PAYMENT_STATUSES = frozenset({
    PaymentTransaction.Status.CREATED,
    PaymentTransaction.Status.PENDING,
    PaymentTransaction.Status.PROCESSING,
})
RECONCILE_STALE = timedelta(seconds=45)


ONLINE_METHODS = frozenset(PAYMENT_PROVIDER_SPECS)


def _billing_fx_rates(billing) -> dict | None:
    try:
        from service_manager.fx_snapshot import billing_fx_rates
    except ImportError:
        return None
    return billing_fx_rates(billing)


def available_channels(currency: str, *, billing=None) -> list[PaymentChannel]:
    """返回已启用且存在有效账单币种换算路径的线上渠道。"""
    rates = _billing_fx_rates(billing)
    channels = []
    for channel in PaymentChannel.objects.filter(enabled=True).order_by('sort_order', 'id'):
        try:
            validate_provider_config(channel.provider, channel.get_config(), enabled=True)
            payment_currency = channel_payment_currency(channel, currency)
            currency_services.conversion_rate(currency, payment_currency, rates)
        except ValueError:  # 密钥损坏/缺失或无汇率的渠道不得暴露给付款页面。
            continue
        channels.append(channel)
    return channels


def outstanding_amount(billing: CustomerBilling) -> Decimal:
    return billing.amount - billing.paid_amount


def validate_bill_payable(billing: CustomerBilling) -> Decimal:
    if billing.status == CustomerBilling.BillingStatus.CANCELLED:
        raise PaymentServiceError('账单已取消，无法支付')
    if billing.status in {
        CustomerBilling.BillingStatus.REFUNDING,
        CustomerBilling.BillingStatus.REFUNDED,
    }:
        raise PaymentServiceError('账单已退款或正在退款，无法支付')
    if billing.status == CustomerBilling.BillingStatus.PAID:
        raise PaymentServiceError('账单已支付')
    outstanding = outstanding_amount(billing)
    if outstanding <= 0:
        raise PaymentServiceError('账单暂无待付金额')
    return outstanding


def parse_payment_amount(raw: Any, billing: CustomerBilling) -> Decimal:
    if raw in (None, ''):
        return outstanding_amount(billing)
    try:
        amount = Decimal(str(raw)).quantize(Decimal('0.01'))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise PaymentServiceError('请输入有效的支付金额') from exc
    if amount <= 0:
        raise PaymentServiceError('支付金额必须大于 0')
    return amount


def channel_payment_currency(channel: PaymentChannel, billing_currency: str) -> str:
    billing_currency = str(billing_currency or '').upper()
    configured = str(channel.settlement_currency or '').strip().upper()
    if configured:
        return configured
    allowed = [
        str(item).strip().upper()
        for item in (channel.allowed_currencies or [])
        if str(item).strip()
    ]
    if not allowed or billing_currency in allowed:
        return billing_currency
    return allowed[0]


def quote_channel_amount(
    *,
    billing: CustomerBilling,
    channel: PaymentChannel,
    billing_amount: Any,
) -> dict[str, Any]:
    try:
        source_amount = Decimal(str(billing_amount)).quantize(Decimal('0.01'))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise PaymentServiceError('请输入有效的账单支付金额') from exc
    payment_currency = channel_payment_currency(channel, billing.currency)
    rates = _billing_fx_rates(billing)
    try:
        exchange_rate = currency_services.conversion_rate(
            billing.currency,
            payment_currency,
            rates,
        )
        payment_amount = currency_services.convert_strict(
            source_amount,
            billing.currency,
            payment_currency,
            rates,
        )
    except ValueError as exc:
        raise PaymentServiceError(str(exc)) from exc
    if source_amount > 0 and payment_amount <= 0:
        raise PaymentServiceError('汇率换算后的支付金额必须大于 0')
    return {
        'billing_amount': source_amount,
        'billing_currency': billing.currency.upper(),
        'payment_amount': payment_amount,
        'payment_currency': payment_currency,
        'exchange_rate': exchange_rate,
    }


def _assert_channel_payable(channel: PaymentChannel, currency: str, *, billing=None) -> None:
    if not channel.enabled:
        raise PaymentServiceError('该支付渠道未启用')
    try:
        validate_provider_config(channel.provider, channel.get_config(), enabled=True)
        currency_services.conversion_rate(
            currency,
            channel_payment_currency(channel, currency),
            _billing_fx_rates(billing),
        )
    except ValueError as exc:
        raise PaymentServiceError(f'支付渠道配置不完整：{exc}') from exc


def create_payment_transaction(
    *,
    billing: CustomerBilling,
    channel: PaymentChannel,
    operator=None,
    amount: Any = None,
    return_url: str = '',
) -> PaymentTransaction:
    """创建本地交易记录；网关下单在事务提交后进行，避免持锁网络 IO。"""
    with db_transaction.atomic():
        locked_billing = CustomerBilling.objects.select_for_update().select_related(
            'service_order',
        ).get(pk=billing.pk)
        _assert_channel_payable(channel, locked_billing.currency, billing=locked_billing)
        outstanding = validate_bill_payable(locked_billing)
        pay_amount = parse_payment_amount(amount, locked_billing)
        if pay_amount > outstanding:
            raise PaymentServiceError(
                f'支付金额不能超过待付金额 {locked_billing.currency}{outstanding:.2f}',
            )
        quote = quote_channel_amount(
            billing=locked_billing,
            channel=channel,
            billing_amount=pay_amount,
        )
        payment = PaymentTransaction.objects.create(
            billing=locked_billing,
            channel=channel,
            provider=channel.provider,
            amount=quote['payment_amount'],
            currency=quote['payment_currency'],
            billing_amount=quote['billing_amount'],
            billing_currency=quote['billing_currency'],
            exchange_rate=quote['exchange_rate'],
            initiated_by=operator,
            return_url=return_url[:1024],
            expires_at=timezone.now() + timedelta(minutes=30),
        )
    return payment


def _checkout_snapshot(checkout: dict[str, Any]) -> dict[str, Any]:
    """仅持久化排错所需元信息，避免持久化 URL token / client secret / 表单签名。"""
    safe = {
        'mode': checkout.get('mode', ''),
        'provider_order_id': checkout.get('provider_order_id', ''),
        'response': checkout.get('response') or {},
    }
    if checkout.get('mode') == 'qr':
        safe['has_qr_code'] = bool(checkout.get('qr_code_text'))
    if checkout.get('mode') == 'sdk':
        safe['sdk'] = checkout.get('sdk', '')
    fields = checkout.get('form_fields') if isinstance(checkout.get('form_fields'), dict) else {}
    if fields.get('txnTime'):
        safe['txnTime'] = str(fields['txnTime'])
    return safe


def issue_gateway_checkout(
    *,
    payment: PaymentTransaction,
    callback_url: str,
    gateway_return_url: str,
) -> tuple[PaymentTransaction, dict[str, Any]]:
    """对已创建的本地交易调用网关下单。"""
    if not payment.channel:
        raise PaymentServiceError('支付渠道已删除')
    try:
        checkout = payment_gateways.create_checkout(
            payment, payment.channel, callback_url, gateway_return_url,
        )
    except payment_gateways.PaymentGatewayError as exc:
        PaymentTransaction.objects.filter(pk=payment.pk).update(
            status=PaymentTransaction.Status.FAILED,
            failure_reason=str(exc),
            provider_response={'stage': 'create_checkout', 'error': str(exc)},
        )
        payment.refresh_from_db()
        raise PaymentServiceError(str(exc)) from exc

    payment.provider_order_id = str(checkout.get('provider_order_id') or '') or None
    payment.status = PaymentTransaction.Status.PENDING
    payment.checkout_payload = _checkout_snapshot(checkout)
    payment.provider_response = dict(checkout.get('response') or {})
    payment.save(update_fields=[
        'provider_order_id', 'status', 'checkout_payload', 'provider_response', 'updated_at',
    ])
    checkout = {
        **checkout,
        'transaction_id': payment.id,
        'transaction_no': payment.transaction_no,
        'expires_at': payment.expires_at.isoformat() if payment.expires_at else None,
    }
    return payment, checkout


def start_online_payment(
    *,
    billing: CustomerBilling,
    channel: PaymentChannel,
    callback_url: str,
    gateway_return_url: str,
    frontend_return_url: str,
    operator=None,
    amount: Any = None,
) -> tuple[PaymentTransaction, dict[str, Any]]:
    """创建本地交易并调用服务商下单的便捷入口。"""
    payment = create_payment_transaction(
        billing=billing, channel=channel, operator=operator,
        amount=amount, return_url=frontend_return_url,
    )
    return issue_gateway_checkout(
        payment=payment,
        callback_url=callback_url,
        gateway_return_url=gateway_return_url,
    )


def _find_transaction(channel: PaymentChannel, result: payment_gateways.GatewayResult) -> PaymentTransaction:
    base = PaymentTransaction.objects.select_related(
        'billing', 'billing__customer', 'channel', 'initiated_by',
    ).filter(channel=channel)
    transaction = None
    if result.transaction_no:
        transaction = base.filter(transaction_no=result.transaction_no).first()
    if transaction is None and result.provider_order_id:
        transaction = base.filter(provider_order_id=result.provider_order_id).first()
    if transaction is None and result.provider_payment_id:
        transaction = base.filter(provider_payment_id=result.provider_payment_id).first()
    if transaction is None:
        raise PaymentServiceError('未找到对应的支付交易')
    return transaction


def _validate_result_identity(
    payment: PaymentTransaction,
    result: payment_gateways.GatewayResult,
) -> None:
    if result.transaction_no and result.transaction_no != payment.transaction_no:
        raise PaymentServiceError('服务商回调交易号与本地交易不匹配')
    if (
        result.provider_order_id
        and payment.provider_order_id
        and result.provider_order_id != payment.provider_order_id
    ):
        raise PaymentServiceError('服务商订单号与本地交易不匹配')


def _validate_settlement_amount(
    payment: PaymentTransaction,
    result: payment_gateways.GatewayResult,
) -> None:
    if result.paid_amount is None or not result.paid_currency:
        raise PaymentServiceError('服务商成功回调缺少金额或币种')
    if result.paid_currency.upper() != payment.currency.upper():
        raise PaymentServiceError('服务商回调币种与账单不一致')
    if abs(result.paid_amount - payment.amount) > Decimal('0.009'):
        raise PaymentServiceError('服务商回调金额与本地支付交易不一致')


def _billing_settlement_amount(payment: PaymentTransaction) -> Decimal:
    if payment.billing_amount > 0:
        return payment.billing_amount
    # 兼容迁移前创建的同币种交易。
    if not payment.billing_currency or payment.billing_currency == payment.currency:
        return payment.amount
    raise PaymentServiceError('支付交易缺少账单币种入账金额')


def _set_transaction_status(
    payment: PaymentTransaction,
    result: payment_gateways.GatewayResult,
    *,
    callback: bool,
) -> None:
    if callback:
        payment.callback_count += 1
        payment.last_callback_at = timezone.now()
    if result.provider_order_id:
        payment.provider_order_id = result.provider_order_id
    if result.provider_payment_id:
        payment.provider_payment_id = result.provider_payment_id
    if result.response:
        payment.provider_response = result.response
    if result.failed:
        payment.status = PaymentTransaction.Status.FAILED
        payment.failure_reason = result.failure_reason or '支付失败'
    elif result.cancelled:
        payment.status = PaymentTransaction.Status.CANCELLED
    elif result.processing:
        payment.status = PaymentTransaction.Status.PENDING


def apply_gateway_result(
    payment: PaymentTransaction,
    result: payment_gateways.GatewayResult,
    *,
    callback: bool = False,
    request=None,
) -> PaymentTransaction:
    """基于经过验签/服务商查询的结果原子更新交易和账单，支持重复回调。"""
    with db_transaction.atomic():
        locked = PaymentTransaction.objects.select_for_update().select_related(
            'billing', 'billing__customer', 'channel', 'initiated_by',
        ).get(pk=payment.pk)
        _validate_result_identity(locked, result)

        if locked.status in {
            PaymentTransaction.Status.REFUNDING,
            PaymentTransaction.Status.REFUNDED,
            PaymentTransaction.Status.PENDING_MANUAL,
        }:
            if callback:
                locked.callback_count += 1
                locked.last_callback_at = timezone.now()
                locked.save(update_fields=['callback_count', 'last_callback_at', 'updated_at'])
            return locked

        if locked.status == PaymentTransaction.Status.SUCCEEDED:
            if callback:
                locked.callback_count += 1
                locked.last_callback_at = timezone.now()
            update_fields = ['callback_count', 'last_callback_at', 'updated_at']
            if result.failed:
                # 已开通业务不得因乱序 reversal 回调自动回滚；保留到账记录并明确标记人工处理。
                locked.failure_reason = (
                    f'已到账后收到异常事件：{result.failure_reason or "支付被撤销"}；'
                    '请人工核对退款与业务状态'
                )
                if result.response:
                    locked.provider_response = result.response
                update_fields.extend(['failure_reason', 'provider_response'])
            if callback or result.failed:
                locked.save(update_fields=update_fields)
            return locked

        if result.succeeded:
            _validate_settlement_amount(locked, result)
            billing = CustomerBilling.objects.select_for_update().get(pk=locked.billing_id)
            outstanding = outstanding_amount(billing)
            billing_settlement = _billing_settlement_amount(locked)
            if billing_settlement > outstanding:
                locked.status = PaymentTransaction.Status.CANCELLED
                locked.failure_reason = '账单已通过其他方式支付，拒绝重复入账'
                if callback:
                    locked.callback_count += 1
                    locked.last_callback_at = timezone.now()
                locked.save(update_fields=[
                    'status', 'failure_reason', 'callback_count', 'last_callback_at', 'updated_at',
                ])
                return locked

            old_paid = billing.paid_amount
            old_status = billing.status
            billing.paid_amount += billing_settlement
            billing.payment_method = locked.provider
            billing.paid_at = timezone.now()
            if billing.paid_amount >= billing.amount:
                billing.status = CustomerBilling.BillingStatus.PAID
            billing.save(update_fields=[
                'paid_amount', 'payment_method', 'paid_at', 'status', 'updated_at',
            ])

            commission = None
            if (
                old_status != CustomerBilling.BillingStatus.PAID
                and billing.status == CustomerBilling.BillingStatus.PAID
            ):
                from .channel_services import ensure_paid_billing_commission
                from .wallet import credit_paid_recharge

                credit_paid_recharge(billing, operator=locked.initiated_by)
                commission = ensure_paid_billing_commission(
                    billing,
                    operator=locked.initiated_by,
                    request=request,
                )

            locked.status = PaymentTransaction.Status.SUCCEEDED
            locked.paid_at = timezone.now()
            locked.failure_reason = ''
            if callback:
                locked.callback_count += 1
                locked.last_callback_at = timezone.now()
            if result.provider_order_id:
                locked.provider_order_id = result.provider_order_id
            if result.provider_payment_id:
                locked.provider_payment_id = result.provider_payment_id
            if result.response:
                locked.provider_response = result.response
            locked.save()

            record_operation_log(
                operator=locked.initiated_by,
                action_type=CustomerOperationLog.ActionType.PAYMENT,
                description=(
                    f'在线支付 {locked.currency}{locked.amount:,.2f}'
                    + (
                        f'（折合 {billing.currency}{billing_settlement:,.2f}）'
                        if locked.currency != billing.currency else ''
                    )
                    + f'（{locked.channel.name if locked.channel else locked.provider}），'
                    f'账单 [{billing.bill_no}]，交易 [{locked.transaction_no}]'
                ),
                customer=billing.customer,
                target_model=CustomerOperationLog.TargetModel.BILLING,
                target_id=billing.pk,
                old_value={'paid_amount': str(old_paid), 'status': old_status},
                new_value={
                    'paid_amount': str(billing.paid_amount),
                    'status': billing.status,
                    'payment_status': locked.status,
                    'transaction_no': locked.transaction_no,
                    'provider_payment_id': locked.provider_payment_id,
                    'payment_amount': str(locked.amount),
                    'payment_currency': locked.currency,
                    'billing_amount': str(billing_settlement),
                    'exchange_rate': str(locked.exchange_rate),
                    'channel_commission_id': commission.pk if commission else None,
                },
                request=request,
            )
            if (
                old_status != CustomerBilling.BillingStatus.PAID
                and billing.status == CustomerBilling.BillingStatus.PAID
                and billing.service_order_id
            ):
                from service_manager.payment_fulfillment import fulfill_paid_billing_safely
                db_transaction.on_commit(
                    lambda billing_id=billing.pk: fulfill_paid_billing_safely(billing_id),
                )
            return locked

        previous_status = locked.status
        _set_transaction_status(locked, result, callback=callback)
        locked.save()
        if locked.status != previous_status:
            record_operation_log(
                operator=locked.initiated_by,
                action_type=CustomerOperationLog.ActionType.PAYMENT,
                description=(
                    f'在线支付交易 [{locked.transaction_no}] 状态：'
                    f'{previous_status} → {locked.get_status_display()}'
                ),
                customer=locked.billing.customer,
                target_model=CustomerOperationLog.TargetModel.BILLING,
                target_id=locked.billing_id,
                old_value={
                    'payment_status': previous_status,
                    'transaction_no': locked.transaction_no,
                },
                new_value={
                    'payment_status': locked.status,
                    'transaction_no': locked.transaction_no,
                    'provider': locked.provider,
                    'failure_reason': locked.failure_reason,
                },
                request=request,
            )
        return locked


def process_webhook(
    *,
    channel: PaymentChannel,
    raw_body: bytes = b'',
    form_data: Any = None,
    headers: Any = None,
    request=None,
) -> PaymentTransaction | None:
    """验签服务商异步通知并应用状态；无签名或验签失败绝不更新账单。"""
    if form_data is not None:
        result = payment_gateways.process_form_webhook(channel, form_data)
    else:
        result = payment_gateways.process_webhook(channel, raw_body, headers or {})

    webhook_event = None
    if result.webhook_event_id:
        webhook_event, created = PaymentWebhookEvent.objects.get_or_create(
            provider=channel.provider,
            event_id=result.webhook_event_id,
            defaults={
                'event_type': result.webhook_event_type,
                'resource_id': result.provider_payment_id or result.provider_order_id,
                'transmission_id': result.webhook_transmission_id,
                'channel': channel,
                'payload': result.webhook_payload,
            },
        )
        if not created and webhook_event.status in {
            PaymentWebhookEvent.ProcessingStatus.PROCESSED,
            PaymentWebhookEvent.ProcessingStatus.IGNORED,
        }:
            return webhook_event.payment
        if not created:
            webhook_event.status = PaymentWebhookEvent.ProcessingStatus.RECEIVED
            webhook_event.error_message = ''
            webhook_event.save(update_fields=['status', 'error_message', 'updated_at'])

    try:
        # 已验签但与账单支付无关的事件正常确认，避免服务商无意义重试。
        if not any((
            result.transaction_no,
            result.provider_order_id,
            result.provider_payment_id,
        )):
            if webhook_event:
                webhook_event.status = PaymentWebhookEvent.ProcessingStatus.IGNORED
                webhook_event.processed_at = timezone.now()
                webhook_event.save(update_fields=['status', 'processed_at', 'updated_at'])
            return None

        payment = _find_transaction(channel, result)
        if (
            channel.provider == 'paypal'
            and result.webhook_event_type == 'CHECKOUT.ORDER.APPROVED'
        ):
            result = payment_gateways.capture_paypal_order(
                payment,
                channel,
                result.provider_order_id or payment.provider_order_id or '',
            )
        settled = apply_gateway_result(payment, result, callback=True, request=request)
        if webhook_event:
            webhook_event.payment = settled
            webhook_event.status = PaymentWebhookEvent.ProcessingStatus.PROCESSED
            webhook_event.processed_at = timezone.now()
            webhook_event.save(update_fields=[
                'payment', 'status', 'processed_at', 'updated_at',
            ])
        return settled
    except Exception as exc:
        if webhook_event:
            webhook_event.status = PaymentWebhookEvent.ProcessingStatus.FAILED
            webhook_event.error_message = str(exc)[:4000]
            webhook_event.processed_at = timezone.now()
            webhook_event.save(update_fields=[
                'status', 'error_message', 'processed_at', 'updated_at',
            ])
        raise


def process_payment_return(
    *,
    payment: PaymentTransaction,
    data: Any,
    request=None,
) -> PaymentTransaction:
    """处理浏览器同步回跳；只接受服务商验签或服务端查单的结果。"""
    if not payment.channel:
        raise PaymentServiceError('支付渠道已删除，无法确认支付状态')
    result = payment_gateways.process_return(payment, payment.channel, data)
    return apply_gateway_result(payment, result, callback=False, request=request)


def _payment_expired(payment: PaymentTransaction, *, now=None) -> bool:
    now = now or timezone.now()
    return bool(payment.expires_at and payment.expires_at < now)


def _mark_reconcile(payment: PaymentTransaction, status: str, error: str = '') -> None:
    PaymentTransaction.objects.filter(pk=payment.pk).update(
        last_reconciled_at=timezone.now(),
        last_reconcile_status=status,
        last_reconcile_error=(error or '')[:256],
        updated_at=timezone.now(),
    )
    payment.refresh_from_db()


def _expire_local_payment(payment: PaymentTransaction) -> PaymentTransaction:
    previous_status = payment.status
    payment.status = PaymentTransaction.Status.EXPIRED
    payment.failure_reason = payment.failure_reason or '支付交易已过期'
    payment.last_reconciled_at = timezone.now()
    payment.last_reconcile_status = 'expired'
    payment.last_reconcile_error = ''
    payment.save(update_fields=[
        'status', 'failure_reason',
        'last_reconciled_at', 'last_reconcile_status', 'last_reconcile_error',
        'updated_at',
    ])
    record_operation_log(
        operator=payment.initiated_by,
        action_type=CustomerOperationLog.ActionType.PAYMENT,
        description=f'在线支付交易 [{payment.transaction_no}] 已过期',
        customer=payment.billing.customer,
        target_model=CustomerOperationLog.TargetModel.BILLING,
        target_id=payment.billing_id,
        old_value={
            'payment_status': previous_status,
            'transaction_no': payment.transaction_no,
        },
        new_value={
            'payment_status': payment.status,
            'transaction_no': payment.transaction_no,
            'provider': payment.provider,
        },
    )
    return payment


def reconcile_payment_transaction(
    payment: PaymentTransaction,
    *,
    force: bool = False,
    expire_if_unpaid: bool = True,
    request=None,
) -> dict[str, Any]:
    """向服务商查单后入账或过期。查单失败时不把已付款交易标成过期。"""
    payment = (
        PaymentTransaction.objects.select_related('billing', 'channel', 'initiated_by')
        .get(pk=payment.pk)
    )
    if payment.status not in OPEN_PAYMENT_STATUSES:
        return {
            'transaction_no': payment.transaction_no,
            'action': 'skipped',
            'reason': 'already_final',
            'status': payment.status,
        }

    expired = _payment_expired(payment)
    if (
        not force
        and not expired
        and payment.last_reconciled_at
        and timezone.now() - payment.last_reconciled_at < RECONCILE_STALE
    ):
        return {
            'transaction_no': payment.transaction_no,
            'action': 'skipped',
            'reason': 'recently_reconciled',
            'status': payment.status,
        }

    if not payment.channel_id:
        if expire_if_unpaid and expired:
            _expire_local_payment(payment)
            return {
                'transaction_no': payment.transaction_no,
                'action': 'expired',
                'reason': 'channel_missing',
                'status': payment.status,
            }
        _mark_reconcile(payment, 'error', '支付渠道已删除，无法查单')
        return {
            'transaction_no': payment.transaction_no,
            'action': 'error',
            'reason': 'channel_missing',
            'status': payment.status,
        }

    try:
        result = payment_gateways.query_payment(payment, payment.channel)
    except Exception as exc:  # noqa: BLE001 — 查单失败不得把已付款交易标过期
        logger.warning('支付对账查单失败 %s: %s', payment.transaction_no, exc)
        _mark_reconcile(payment, 'error', str(exc))
        return {
            'transaction_no': payment.transaction_no,
            'action': 'error',
            'reason': str(exc),
            'status': payment.status,
        }

    if result.succeeded or result.failed or result.cancelled:
        try:
            locked = apply_gateway_result(payment, result, callback=False, request=request)
        except PaymentServiceError as exc:
            logger.warning('支付对账入账失败 %s: %s', payment.transaction_no, exc)
            _mark_reconcile(payment, 'error', str(exc))
            payment.refresh_from_db()
            return {
                'transaction_no': payment.transaction_no,
                'action': 'error',
                'reason': str(exc),
                'status': payment.status,
            }
        if locked.status == PaymentTransaction.Status.SUCCEEDED:
            outcome = 'paid'
        elif locked.status == PaymentTransaction.Status.EXPIRED:
            outcome = 'expired'
        elif locked.status in {
            PaymentTransaction.Status.FAILED,
            PaymentTransaction.Status.CANCELLED,
        }:
            outcome = 'failed'
        else:
            outcome = 'pending'
        _mark_reconcile(locked, outcome)
        return {
            'transaction_no': locked.transaction_no,
            'action': outcome,
            'reason': result.failure_reason or outcome,
            'status': locked.status,
        }

    if expire_if_unpaid and expired:
        _expire_local_payment(payment)
        return {
            'transaction_no': payment.transaction_no,
            'action': 'expired',
            'reason': 'unpaid_expired',
            'status': payment.status,
        }

    _mark_reconcile(payment, 'pending')
    return {
        'transaction_no': payment.transaction_no,
        'action': 'pending',
        'reason': 'still_open',
        'status': payment.status,
    }


def reconcile_open_transactions(*, force: bool = False, only_expired: bool = False) -> dict[str, int]:
    """扫描未终态交易：先查单入账，仍未付且已到期才标过期。"""
    now = timezone.now()
    qs = (
        PaymentTransaction.objects.select_related('billing', 'channel', 'initiated_by')
        .filter(status__in=OPEN_PAYMENT_STATUSES)
        .order_by('id')
    )
    if only_expired:
        qs = qs.filter(expires_at__lt=now)
    stats = {'scanned': 0, 'paid': 0, 'failed': 0, 'expired': 0, 'pending': 0, 'error': 0, 'skipped': 0}
    ids = list(qs.values_list('pk', flat=True))
    for pk in ids:
        stats['scanned'] += 1
        payment = PaymentTransaction.objects.get(pk=pk)
        try:
            result = reconcile_payment_transaction(
                payment, force=force, expire_if_unpaid=True,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception('支付对账扫描异常 %s: %s', pk, exc)
            stats['error'] += 1
            continue
        action = result.get('action') or 'skipped'
        stats[action] = stats.get(action, 0) + 1
    return stats


def mark_expired_transactions() -> int:
    """兼容旧命令：对账后关闭仍未付款的过期交易。"""
    stats = reconcile_open_transactions(only_expired=False)
    return int(stats.get('expired') or 0)


def transaction_queryset_for_customer(customer=None):
    qs = PaymentTransaction.objects.select_related('billing', 'channel', 'initiated_by')
    if customer is not None:
        qs = qs.filter(billing__customer=customer)
    return qs
