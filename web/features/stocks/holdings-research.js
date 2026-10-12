/* Use the authenticated dashboard holdings; no duplicate portfolio storage. */
export function collectResearchHoldings(accounts = []) {
  const securities = new Map();
  for (const account of accounts) {
    if (account.type !== 'brokerage') continue;
    for (const holding of account.holdings || []) {
      if (!(Number(holding.quantity) > 0) || !holding.exchange || !holding.symbol) continue;
      const exchange = holding.exchange.toUpperCase(), symbol = holding.symbol.toUpperCase();
      const id = `${exchange}:${symbol}`;
      if (!securities.has(id)) securities.set(id, {id, exchange, symbol,
        assetClass: holding.asset_class, currency: holding.currency, accounts: []});
      securities.get(id).accounts.push({id: account.id, name: account.name, quantity: holding.quantity});
    }
  }
  return [...securities.values()].sort((left, right) => left.id.localeCompare(right.id));
}

export function createHoldingsResearchPanel({esc}) {
  return function holdingsResearchPanel(accounts, selected = '') {
    const holdings = collectResearchHoldings(accounts);
    return `<section class="panel holdings-research"><h2>Research your holdings</h2>
      <p class="section-note">Current holdings across your brokerage accounts, grouped by exchange and ticker. Select a security to see its prices and financial results.</p>
      ${holdings.length ? `<ul class="research-holdings-list">${holdings.map(holding => `<li>
        <a href="#stocks/${encodeURIComponent(holding.id)}" ${selected === holding.id ? 'aria-current="page"' : ''}>
          <strong>${esc(holding.symbol)}</strong><span>${esc(holding.exchange)} · ${esc(holding.assetClass || 'Unknown class')} · ${esc(holding.currency || '')}</span>
          <small>${holding.accounts.map(account => esc(account.name)).join(' · ')}</small>
        </a></li>`).join('')}</ul>` : '<p>No current brokerage holdings. You can still search for securities above.</p>'}
    </section>`;
  };
}
