import request from './request'

export const getCustomers = (params) => request.get('/customer/customers/', { params })
export const getCustomer = (id) => request.get(`/customer/customers/${id}/`)
export const createCustomer = (data) => request.post('/customer/customers/', data)
export const updateCustomer = (id, data) => request.patch(`/customer/customers/${id}/`, data)
export const deleteCustomer = (id) => request.delete(`/customer/customers/${id}/`)
export const getCustomerLogs = (id) => request.get(`/customer/customers/${id}/logs/`)
export const getCustomerWalletLedger = (id, params) =>
  request.get(`/customer/customers/${id}/wallet-ledger/`, { params })
export const adjustCustomerBalance = (id, data) =>
  request.post(`/customer/customers/${id}/adjust-balance/`, data)
export const getCustomerStatistics = () => request.get('/customer/customers/statistics/')
export const getCustomerPortalAccount = (id) =>
  request.get(`/customer/customers/${id}/portal-account/`)
export const createCustomerPortalAccount = (id, data) =>
  request.post(`/customer/customers/${id}/portal-account/`, data)

export const getContactRecords = (params) => request.get('/customer/contact-records/', { params })
export const createContactRecord = (data) => request.post('/customer/contact-records/', data)
export const updateContactRecord = (id, data) => request.patch(`/customer/contact-records/${id}/`, data)
export const deleteContactRecord = (id) => request.delete(`/customer/contact-records/${id}/`)

export const getContracts = (params) => request.get('/customer/contracts/', { params })
export const createContract = (data) => request.post('/customer/contracts/', data)
export const updateContract = (id, data) => request.patch(`/customer/contracts/${id}/`, data)
export const deleteContract = (id) => request.delete(`/customer/contracts/${id}/`)
export const signContract = (id) => request.post(`/customer/contracts/${id}/sign/`)

export const getBillings = (params) => request.get('/customer/billings/', { params })
export const createBilling = (data) => request.post('/customer/billings/', data)
export const updateBilling = (id, data) => request.patch(`/customer/billings/${id}/`, data)
export const deleteBilling = (id) => request.delete(`/customer/billings/${id}/`)
export const payBilling = (id, data) => request.post(`/customer/billings/${id}/pay/`, data)
export const previewBilling = (data) => request.post('/customer/billings/preview/', data)
export const generateBilling = (data) => request.post('/customer/billings/generate/', data)
export const getBillingOnlinePaymentMethods = (id) =>
  request.get(`/customer/billings/${id}/online-payment-methods/`)
export const getBillingPaymentTransactions = (id, params) =>
  request.get(`/customer/billings/${id}/payment-transactions/`, { params })
export const startBillingOnlinePayment = (id, data) =>
  request.post(`/customer/billings/${id}/start-online-payment/`, data)
export const requestBillingRefund = (id, data = {}) =>
  request.post(`/customer/billings/${id}/request-refund/`, data)
export const completeBillingRefund = (id) =>
  request.post(`/customer/billings/${id}/complete-refund/`)
export const getPaymentTransaction = (id) => request.get(`/customer/payment-transactions/${id}/`)
export const reconcilePaymentTransaction = (id) =>
  request.post(`/customer/payment-transactions/${id}/reconcile/`)

export const getOperationLogs = (params) => request.get('/customer/operation-logs/', { params })

export const getChannelCommissions = (params) =>
  request.get('/customer/channel-commissions/', { params })
export const getChannelCommissionSummary = (params) =>
  request.get('/customer/channel-commissions/summary/', { params })
export const createChannelSettlement = (data) =>
  request.post('/customer/channel-commissions/create-settlement/', data)
export const voidChannelCommission = (id, data) =>
  request.post(`/customer/channel-commissions/${id}/void/`, data)

export const getChannelSettlements = (params) =>
  request.get('/customer/channel-settlements/', { params })
export const getChannelSettlementCommissions = (id, params) =>
  request.get(`/customer/channel-settlements/${id}/commissions/`, { params })
export const confirmChannelSettlement = (id) =>
  request.post(`/customer/channel-settlements/${id}/confirm/`)
export const markChannelSettlementPaid = (id, data) =>
  request.post(`/customer/channel-settlements/${id}/mark-paid/`, data)
export const cancelChannelSettlement = (id, data) =>
  request.post(`/customer/channel-settlements/${id}/cancel/`, data)

function normalizePayoutProfile(profile) {
  if (!profile) return null
  return {
    ...profile,
    payment_method: profile.account_type === 'bank' ? 'bank_transfer' : profile.account_type,
    account_name: profile.entity_name,
    account_number_masked: profile.masked_account,
  }
}

function payoutProfilePayload(channelId, data) {
  const accountType = data.payment_method === 'bank_transfer' ? 'bank' : data.payment_method
  const payload = {
    channel: channelId,
    entity_name: data.account_name,
    account_type: accountType,
    bank_name: data.bank_name || '',
    bank_branch: data.bank_branch || '',
    recipient_name: data.recipient_name || data.account_name,
    tax_id: data.tax_id || '',
    is_active: data.is_active ?? true,
  }
  if (data.account_number) payload.account = data.account_number
  return payload
}

export async function getChannelPayoutProfile(channelId) {
  const data = await request.get('/customer/channel-payout-profiles/', {
    params: { channel: channelId, page_size: 1 },
  })
  return normalizePayoutProfile(data?.results?.[0] || data?.[0] || null)
}

export async function updateChannelPayoutProfile(channelId, data) {
  const current = await getChannelPayoutProfile(channelId)
  const payload = payoutProfilePayload(channelId, data)
  const saved = current?.id
    ? await request.patch(`/customer/channel-payout-profiles/${current.id}/`, payload)
    : await request.post('/customer/channel-payout-profiles/', payload)
  return normalizePayoutProfile(saved)
}

export const getChannelFinanceLedgers = (params = {}) => {
  const normalized = { ...params }
  if (normalized.channel) normalized['account__channel'] = normalized.channel
  if (normalized.currency) normalized['account__currency'] = normalized.currency
  if (normalized.event_type) normalized.entry_type = normalized.event_type
  delete normalized.channel
  delete normalized.currency
  delete normalized.event_type
  return request.get('/customer/channel-finance-ledgers/', { params: normalized })
}
export const getChannelFinanceLedgerSummary = (params) =>
  request.get('/customer/channel-finance-ledgers/summary/', { params })
export const adjustChannelFinanceLedger = (data) =>
  request.post('/customer/channel-finance-ledgers/adjustment/', data)

export const getChannelSettlementDetail = (id) =>
  request.get(`/customer/channel-settlements/${id}/`)
