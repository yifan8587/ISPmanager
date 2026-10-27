from django.db.models.signals import post_save
from django.dispatch import receiver


@receiver(post_save, sender='customer_manager.CustomerBilling')
def create_supplier_bills_on_paid(sender, instance, **kwargs):
    from customer_manager.models import CustomerBilling
    from .settlement import ensure_paid_supplier_bills

    if getattr(instance, 'status', None) != CustomerBilling.BillingStatus.PAID:
        return
    ensure_paid_supplier_bills(instance)
