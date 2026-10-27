"""
system_manager/currency_services.py
币种与信誉额汇率换算。

约定（可在「系统设置 → 账单设置」中调整）：
  - 默认结算币种 default_currency（单选），后续账单/钱包/财务汇总都折算到该币种
  - 库内仍以人民币(CNY)为中间基准保存汇率：1 单位币种 = ? CNY
  - 界面按当前结算币种展示「1 XXX = ? 结算币种」

所有换算先把金额折算到 CNY，再折算到目标币种。
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from django.utils import timezone

CNY = 'CNY'
USD = 'USD'
EUR = 'EUR'
GBP = 'GBP'
HKD = 'HKD'
JPY = 'JPY'
AUD = 'AUD'
SGD = 'SGD'
CREDIT = 'CREDIT'

CURRENCY_CHOICES = [
    {'value': CNY, 'label': '人民币', 'symbol': '¥'},
    {'value': USD, 'label': '美元', 'symbol': '$'},
    {'value': EUR, 'label': '欧元', 'symbol': '€'},
    {'value': GBP, 'label': '英镑', 'symbol': '£'},
    {'value': HKD, 'label': '港币', 'symbol': 'HK$'},
    {'value': JPY, 'label': '日元', 'symbol': '¥'},
    {'value': AUD, 'label': '澳元', 'symbol': 'A$'},
    {'value': SGD, 'label': '新加坡元', 'symbol': 'S$'},
    {'value': CREDIT, 'label': '信誉额', 'symbol': '积分'},
]

_SYMBOL = {item['value']: item['symbol'] for item in CURRENCY_CHOICES}
_ZERO_DECIMAL_CURRENCIES = frozenset({JPY})
CURRENCY_CODES = tuple(item['value'] for item in CURRENCY_CHOICES)

# 库内仍按「1 单位外币 = ? CNY」保存，便于兼容历史配置。
RATE_SETTING_KEYS = {
    USD: 'exchange_rate_usd_cny',
    EUR: 'exchange_rate_eur_cny',
    GBP: 'exchange_rate_gbp_cny',
    HKD: 'exchange_rate_hkd_cny',
    JPY: 'exchange_rate_jpy_cny',
    AUD: 'exchange_rate_aud_cny',
    SGD: 'exchange_rate_sgd_cny',
    CREDIT: 'credit_rate_cny',
}


def _dec(v, default='0') -> Decimal:
    try:
        return Decimal(str(v))
    except Exception:  # noqa: BLE001
        return Decimal(default)


def get_rates() -> dict:
    """读取当前汇率配置（以 1 单位币种折算为多少 CNY 表示）。"""
    from .services import get_setting
    usd = _dec(get_setting('exchange_rate_usd_cny', '7.20'), '7.20')
    eur = _dec(get_setting('exchange_rate_eur_cny', '0'), '0')
    gbp = _dec(get_setting('exchange_rate_gbp_cny', '0'), '0')
    hkd = _dec(get_setting('exchange_rate_hkd_cny', '0'), '0')
    jpy = _dec(get_setting('exchange_rate_jpy_cny', '0'), '0')
    aud = _dec(get_setting('exchange_rate_aud_cny', '0'), '0')
    sgd = _dec(get_setting('exchange_rate_sgd_cny', '0'), '0')
    credit = _dec(get_setting('credit_rate_cny', '1.00'), '1.00')
    default_currency = str(get_setting('default_currency', CNY) or CNY).upper()
    if default_currency not in _SYMBOL:
        default_currency = CNY
    return {
        'default_currency': default_currency,
        'to_cny': {
            CNY: Decimal('1'),
            USD: usd,
            EUR: eur,
            GBP: gbp,
            HKD: hkd,
            JPY: jpy,
            AUD: aud,
            SGD: sgd,
            CREDIT: credit,
        },
    }


def currency_symbol(currency: str) -> str:
    return _SYMBOL.get(currency, currency)


def convert(amount, from_currency: str, to_currency: str, rates: dict | None = None) -> Decimal:
    """把 amount 从 from_currency 换算到 to_currency，保留 2 位小数。"""
    amount = _dec(amount)
    if amount == 0:
        return Decimal('0.00')
    from_currency = (from_currency or CNY).upper()
    to_currency = (to_currency or CNY).upper()
    if from_currency == to_currency:
        return amount.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    table = (rates or get_rates())['to_cny']
    rate_from = table.get(from_currency, Decimal('1'))
    rate_to = table.get(to_currency, Decimal('1'))
    cny = amount * rate_from
    result = cny / rate_to if rate_to else cny
    return result.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def conversion_rate(
    from_currency: str,
    to_currency: str,
    rates: dict | None = None,
) -> Decimal:
    """返回 1 单位源币种可兑换的目标币种数量；不允许未知或无效汇率。"""
    from_currency = (from_currency or CNY).upper()
    to_currency = (to_currency or CNY).upper()
    table = (rates or get_rates())['to_cny']
    if from_currency not in table:
        raise ValueError(f'尚未配置 {from_currency} 汇率')
    if to_currency not in table:
        raise ValueError(f'尚未配置 {to_currency} 汇率')
    rate_from = table[from_currency]
    rate_to = table[to_currency]
    if rate_from <= 0 or rate_to <= 0:
        raise ValueError(f'{from_currency}/{to_currency} 汇率必须大于 0')
    return (rate_from / rate_to).quantize(
        Decimal('0.00000001'),
        rounding=ROUND_HALF_UP,
    )


def convert_strict(
    amount,
    from_currency: str,
    to_currency: str,
    rates: dict | None = None,
) -> Decimal:
    rate = conversion_rate(from_currency, to_currency, rates)
    precision = (
        Decimal('1')
        if str(to_currency).upper() in _ZERO_DECIMAL_CURRENCIES
        else Decimal('0.01')
    )
    return (_dec(amount) * rate).quantize(precision, rounding=ROUND_HALF_UP)


def default_currency() -> str:
    from .services import get_setting
    value = str(get_setting('default_currency', CNY) or CNY).upper()
    return value if value in _SYMBOL else CNY


def settlement_currency() -> str:
    """账单、钱包、财务汇总使用的默认结算币种。"""
    return default_currency()


def rates_to_settlement(settlement: str | None = None, rates: dict | None = None) -> dict[str, Decimal]:
    """返回 1 单位各币种可兑换的结算币种数量。"""
    table = (rates or get_rates())
    settlement = (settlement or table.get('default_currency') or CNY).upper()
    to_cny = table['to_cny']
    rate_to = to_cny.get(settlement) or Decimal('0')
    result = {}
    for code in CURRENCY_CODES:
        rate_from = to_cny.get(code) or Decimal('0')
        if code == settlement:
            result[code] = Decimal('1')
        elif rate_from > 0 and rate_to > 0:
            result[code] = (rate_from / rate_to).quantize(
                Decimal('0.00000001'), rounding=ROUND_HALF_UP,
            )
        else:
            result[code] = Decimal('0')
    return result


def pivot_rates_from_settlement(settlement: str, displayed: dict) -> dict[str, Decimal]:
    """把「1 外币 = ? 结算币种」还原成库内 CNY 基准汇率。"""
    settlement = (settlement or CNY).upper()
    shown = {str(key).upper(): _dec(value, '0') for key, value in (displayed or {}).items()}
    to_cny = {CNY: Decimal('1')}
    if settlement == CNY:
        for code in RATE_SETTING_KEYS:
            amount = shown.get(code, Decimal('0'))
            if amount > 0:
                to_cny[code] = amount
        return to_cny
    cny_per_settlement = Decimal('0')
    cny_shown = shown.get(CNY, Decimal('0'))
    if cny_shown > 0:
        cny_per_settlement = (Decimal('1') / cny_shown).quantize(
            Decimal('0.00000001'), rounding=ROUND_HALF_UP,
        )
    elif shown.get(settlement, Decimal('0')) > 0 and settlement != CNY:
        cny_per_settlement = shown[settlement]
    if cny_per_settlement > 0 and settlement != CNY:
        to_cny[settlement] = cny_per_settlement
    for code in RATE_SETTING_KEYS:
        if code == settlement:
            continue
        amount = shown.get(code, Decimal('0'))
        if amount > 0 and cny_per_settlement > 0:
            to_cny[code] = (amount * cny_per_settlement).quantize(
                Decimal('0.00000001'), rounding=ROUND_HALF_UP,
            )
    return to_cny


def apply_settlement_rates(settlement: str, displayed: dict, user=None) -> dict[str, str]:
    """按界面输入的结算币种汇率写回 CNY 基准配置。"""
    from .services import set_setting
    to_cny = pivot_rates_from_settlement(settlement, displayed)
    written = {}
    for code, key in RATE_SETTING_KEYS.items():
        value = to_cny.get(code, Decimal('0'))
        set_setting(key, format(value, 'f'), user=user)
        written[key] = format(value, 'f')
    return written


def currency_payload() -> dict:
    rates = get_rates()
    settlement = (rates.get('default_currency') or CNY).upper()
    to_settlement = rates_to_settlement(settlement, rates)
    return {
        'choices': CURRENCY_CHOICES,
        'default_currency': settlement,
        'settlement_currency': settlement,
        'rates_to_cny': {key: str(value) for key, value in rates['to_cny'].items()},
        'rates_to_settlement': {key: str(value) for key, value in to_settlement.items()},
        'rate_fields': [
            {
                'code': code,
                'label': next(item['label'] for item in CURRENCY_CHOICES if item['value'] == code),
                'setting_key': RATE_SETTING_KEYS.get(code, ''),
                'is_settlement': code == settlement,
            }
            for code in CURRENCY_CODES
        ],
    }


def convert_to_settlement(amount, from_currency: str, rates: dict | None = None) -> Decimal:
    table = rates or get_rates()
    return convert(amount, from_currency, table.get('default_currency') or CNY, table)


def sum_in_settlement(rows, amount_fields, *, currency_key: str = 'currency') -> dict:
    """把多币种金额列表折算到默认结算币种后求和。"""
    table = get_rates()
    settlement = (table.get('default_currency') or CNY).upper()
    totals = {field: Decimal('0.00') for field in amount_fields}
    for row in rows or []:
        source = str((row or {}).get(currency_key) or settlement).upper()
        for field in amount_fields:
            totals[field] += convert((row or {}).get(field) or 0, source, settlement, table)
    return {
        'currency': settlement,
        **{
            field: str(amount.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))
            for field, amount in totals.items()
        },
    }


def snapshot_rates(*, quote_currency: str = '') -> dict:
    """拍摄当前汇率表，供工单锁定。"""
    rates = get_rates()
    quote = (quote_currency or rates['default_currency'] or CNY).upper()
    return {
        'quote_currency': quote,
        'default_currency': (rates['default_currency'] or CNY).upper(),
        'to_cny': {key: str(value) for key, value in rates['to_cny'].items()},
        'locked_at': timezone.now().isoformat(),
    }


def rates_from_snapshot(snapshot: dict | None) -> dict | None:
    """把工单快照还原成 ``get_rates()`` 同结构；无效则返回 None（回退实时汇率）。"""
    if not snapshot or not isinstance(snapshot, dict):
        return None
    raw = snapshot.get('to_cny') or {}
    if not raw:
        return None
    to_cny = {}
    for key, value in raw.items():
        amount = _dec(value, '0')
        if amount > 0:
            to_cny[str(key).upper()] = amount
    if CNY not in to_cny:
        to_cny[CNY] = Decimal('1')
    if not to_cny:
        return None
    return {
        'default_currency': str(snapshot.get('default_currency') or CNY).upper(),
        'to_cny': to_cny,
    }
