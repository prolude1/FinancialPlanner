/** Owns brokerage chart UI state and delegates valuation-series rendering. */
export function createBrokerageChartController({ getData, getDashboardPages, documentRef = globalThis.document }) {
  const state = {};
  let attachedDocument = null;

  function handleAction(button) {
    const action = button?.dataset?.action;
    if (!['chart-period', 'chart-metric', 'chart-projection'].includes(action)) return false;
    const root = button.closest('.brokerage-chart');
    if (!root) return false;
    const accountId = root.dataset.account;
    const rows = getData()?.portfolio_history?.[accountId] || [];
    const chartState = state[accountId];
    if (!chartState) return true;

    if (action === 'chart-period') {
      chartState.period = button.dataset.value;
      [chartState.start, chartState.end] = getDashboardPages().historyRange(rows, chartState.period);
    } else if (action === 'chart-metric') {
      chartState.metric = button.dataset.value;
    } else {
      chartState.showProjections = !chartState.showProjections;
    }
    getDashboardPages().drawBrokerageChart(accountId);
    return true;
  }

  function handlePointerMove(event) {
    if (!event.target.matches?.('[data-action="chart-hover"]')) return false;
    getDashboardPages().chartHover(event);
    return true;
  }

  function handleWheel(event) {
    const root = event.target.closest?.('.brokerage-chart');
    if (!root) return false;
    event.preventDefault();
    const accountId = root.dataset.account;
    const rows = getData()?.portfolio_history?.[accountId] || [];
    const chartState = state[accountId];
    const span = chartState ? chartState.end - chartState.start + 1 : 0;
    if (rows.length < 2 || !chartState) return true;

    const factor = event.deltaY < 0 ? 0.8 : 1.25;
    const next = Math.min(rows.length, Math.max(2, Math.round(span * factor)));
    const svg = root.querySelector('svg');
    if (!svg) return true;
    const rect = svg.getBoundingClientRect();
    const focus = Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width));
    const center = chartState.start + Math.round(focus * (span - 1));
    chartState.start = Math.max(0, Math.min(rows.length - next, center - Math.round(focus * (next - 1))));
    chartState.end = chartState.start + next - 1;
    chartState.period = 'custom';
    getDashboardPages().drawBrokerageChart(accountId);
    return true;
  }

  function attach(target = documentRef) {
    if (!target || attachedDocument === target) return;
    attachedDocument = target;
    target.addEventListener('pointermove', handlePointerMove);
    target.addEventListener('wheel', handleWheel, { passive: false });
  }

  function drawAll(target = documentRef) {
    target?.querySelectorAll('.brokerage-chart').forEach(chart => {
      getDashboardPages().drawBrokerageChart(chart.dataset.account);
    });
  }

  return { state, attach, drawAll, handleAction, handlePointerMove, handleWheel };
}
