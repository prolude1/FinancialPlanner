/** Refreshes one visible brokerage chart without reloading dashboard state. */
export const BROKERAGE_HISTORY_REFRESH_MS = 30_000;

export function dashboardRefreshAction({ revisionChanged, brokerageOpen, chartRefreshDue, force = false }) {
  if (revisionChanged) return 'dashboard';
  if (brokerageOpen && (force || chartRefreshDue)) return 'brokerage-history';
  return 'none';
}

export function createBrokerageHistoryRefresh({
  api,
  getData,
  getRoute,
  isAuthenticated,
  onUpdate = () => {},
  now = () => Date.now(),
  intervalMs = BROKERAGE_HISTORY_REFRESH_MS,
}) {
  const lastSuccessAt = new Map();
  const statuses = new Map();
  const requests = new Map();
  let generation = 0;

  function accountIsCurrent(accountId) {
    const data = getData();
    return isAuthenticated()
      && getRoute()?.page === 'accounts'
      && getRoute()?.id === accountId
      && data?.accounts?.some(account => account.id === accountId && account.type === 'brokerage');
  }

  function setStatus(accountId, status) {
    statuses.set(accountId, status);
    onUpdate(accountId, status);
  }

  function invalidate() {
    generation++;
    for (const accountId of requests.keys()) setStatus(accountId, { loading: false, error: '', success: false });
  }

  function noteDashboardLoaded(snapshot = getData()) {
    const receivedAt = now();
    for (const account of snapshot?.accounts || []) {
      if (account.type === 'brokerage') lastSuccessAt.set(account.id, receivedAt);
    }
    statuses.clear();
  }

  function isDue(accountId) {
    return !lastSuccessAt.has(accountId) || now() - lastSuccessAt.get(accountId) >= intervalMs;
  }

  async function refresh(accountId, { force = false } = {}) {
    if (!accountId || !accountIsCurrent(accountId) || requests.has(accountId)) return false;
    if (!force && !isDue(accountId)) return false;

    const request = { generation, token: Symbol(accountId) };
    requests.set(accountId, request);
    setStatus(accountId, { loading: true, error: '', success: false });
    const isFresh = () => generation === request.generation
      && requests.get(accountId) === request
      && accountIsCurrent(accountId);

    try {
      const payload = await api(`/accounts/${encodeURIComponent(accountId)}/brokerage-history`);
      if (!isFresh()) return false;
      if (payload?.account_id !== accountId
        || !Array.isArray(payload.portfolio_history)
        || !Array.isArray(payload.portfolio_deposits)
        || typeof payload.as_of !== 'string') {
        throw new Error('The brokerage history response is incomplete.');
      }
      const data = getData();
      data.portfolio_history ||= {};
      data.portfolio_deposits ||= {};
      data.portfolio_history_as_of ||= {};
      data.portfolio_history[accountId] = payload.portfolio_history;
      data.portfolio_deposits[accountId] = payload.portfolio_deposits;
      data.portfolio_history_as_of[accountId] = payload.as_of;
      lastSuccessAt.set(accountId, now());
      setStatus(accountId, { loading: false, error: '', success: true });
      return true;
    } catch (error) {
      if (!isFresh()) return false;
      setStatus(accountId, { loading: false, error: error?.message || 'Unable to refresh brokerage history.', success: false });
      throw error;
    } finally {
      if (requests.get(accountId) === request) requests.delete(accountId);
    }
  }

  function refreshIfDue(accountId, options) {
    return refresh(accountId, options);
  }

  return { refresh, refreshIfDue, isDue, invalidate, noteDashboardLoaded, status: accountId => statuses.get(accountId) || { loading: false, error: '', success: false }, lastSuccess: accountId => lastSuccessAt.get(accountId) };
}
