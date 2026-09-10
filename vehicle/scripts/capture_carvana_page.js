// Paste this function into Chrome DevTools on the selected public vehicle page.
// It reads the DOM only: no requests, purchases, clipboard access, or file writes.
// Then run copy(JSON.stringify(captureCarvanaPage({vin: 'EXPECTED_VIN', listing_id: 'ID'}), null, 2)).
function captureCarvanaPage(expected) {
  const expectedVin = expected.vin, expectedListingId = expected.listing_id;
  const fields = ['vehicleId', 'vin', 'saleStatus', 'purchaseType', 'inventoryType'];
  const contexts = [];
  const scripts = Array.from(document.scripts, s => s.textContent || '');
  const chunks = [];
  for (const text of scripts) {
    const match = text.match(/^self\.__next_f\.push\((\[[\s\S]*\])\);?\s*$/);
    if (!match) continue;
    try {
      const value = JSON.parse(match[1]);
      if (value[0] === 1 && typeof value[1] === 'string') chunks.push(value[1]);
    } catch (_) { /* An unreadable stream will yield no validated vehicle. */ }
  }
  function visit(value, path) {
    if (!value || typeof value !== 'object') return;
    const details = value.forVehicleContext?.vehicleDetails;
    if (details && typeof details === 'object' && !Array.isArray(details)) {
      contexts.push({source_path: path + '.forVehicleContext.vehicleDetails',
        forVehicleContext: {vehicleDetails: Object.fromEntries(
          fields.filter(key => Object.hasOwn(details, key)).map(key => [key, details[key]]))}});
    }
    for (const [key, child] of Object.entries(value)) {
      if (!/cookie|token|session|user|account|auth|customer|financing/i.test(key)) {
        visit(child, path + '.' + key);
      }
    }
  }
  // A JSON record can cross script boundaries; concatenate before splitting lines.
  for (const line of chunks.join('').split('\n')) {
    const colon = line.indexOf(':');
    if (colon < 0) continue;
    try { visit(JSON.parse(line.slice(colon + 1)), line.slice(0, colon)); }
    catch (_) { /* Other React record types are not JSON vehicle records. */ }
  }
  const hero = document.querySelector('[data-testid="hero-container"]');
  const badge = document.querySelector('[data-testid="hero-badge"]');
  const button = document.querySelector('[data-testid="get-started-button"]');
  return {
    format: 'carvana-public-detail-projection-v1',
    expected: {retailer: 'carvana', vin: expectedVin, listing_id: String(expectedListingId)},
    requested_url: 'https://www.carvana.com/vehicle/' + expectedListingId,
    final_url: location.href,
    checked_at: new Date().toISOString(),
    access_outcome: contexts.length ? 'ok' : 'unknown',
    contexts,
    hero_text: hero?.innerText ?? null,
    hero_badge: badge?.innerText ?? null,
    purchase_button: button?.innerText ?? null,
    note: 'Selected public DOM/RSC fields; not original HTML. No HTTP status measured.'
  };
}
