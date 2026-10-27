from django.urls import path
from rest_framework.routers import DefaultRouter
from .views import (
    CustomerViewSet,
    ContactRecordViewSet,
    ContractViewSet,
    CustomerBillingViewSet,
    CustomerOperationLogViewSet,
    ChannelCommissionViewSet,
    ChannelFinanceLedgerViewSet,
    ChannelPayoutProfileViewSet,
    ChannelSettlementViewSet,
    PaymentReturnView,
    PaymentTransactionViewSet,
    PaymentWebhookView,
)

router = DefaultRouter()
router.register(r'customers', CustomerViewSet, basename='customer')
router.register(r'contact-records', ContactRecordViewSet, basename='contact-record')
router.register(r'contracts', ContractViewSet, basename='contract')
router.register(r'billings', CustomerBillingViewSet, basename='billing')
router.register(r'payment-transactions', PaymentTransactionViewSet, basename='payment-transaction')
router.register(r'operation-logs', CustomerOperationLogViewSet, basename='operation-log')
router.register(r'channel-commissions', ChannelCommissionViewSet, basename='channel-commission')
router.register(r'channel-settlements', ChannelSettlementViewSet, basename='channel-settlement')
router.register(r'channel-payout-profiles', ChannelPayoutProfileViewSet, basename='channel-payout-profile')
router.register(r'channel-finance-ledgers', ChannelFinanceLedgerViewSet, basename='channel-finance-ledger')

urlpatterns = [
    path(
        'payments/webhooks/<int:channel_id>/',
        PaymentWebhookView.as_view(),
        name='payment-webhook',
    ),
    path(
        'payments/return/<str:transaction_no>/',
        PaymentReturnView.as_view(),
        name='payment-return',
    ),
] + router.urls
