import test from 'node:test';
import assert from 'node:assert/strict';
import { chromium } from 'playwright';

const BASE_URL = process.env.FRONTEND_URL || 'http://localhost:5180';
const BACKEND_URL = process.env.BACKEND_URL || 'http://127.0.0.1:8002';

async function canLaunchBrowser() {
  try {
    const b = await chromium.launch({ channel: 'chrome', headless: true }).catch(() => chromium.launch({ headless: true }));
    await b.close();
    return true;
  } catch {
    return false;
  }
}

async function getBrowser() {
  try {
    return await chromium.launch({ channel: 'chrome', headless: true });
  } catch {
    return await chromium.launch({ headless: true });
  }
}

async function isFrontendRunning() {
  try {
    const res = await fetch(`${BASE_URL}/projects/new`, { signal: AbortSignal.timeout(2000) });
    return res.ok;
  } catch {
    return false;
  }
}

async function getRunningDesktopInstances() {
  try {
    const res = await fetch(`${BACKEND_URL}/api/sources/pbi-desktop/instances`, { signal: AbortSignal.timeout(3000) });
    if (!res.ok) return [];
    const data = await res.json();
    return Array.isArray(data.instances) ? data.instances : [];
  } catch {
    return [];
  }
}

function setupErrorTracking(page) {
  const consoleErrors = [];
  const pageErrors = [];

  page.on('console', (msg) => {
    if (msg.type() === 'error') {
      const text = msg.text();
      // Exclude normal test network responses and background alerts websocket connection retries
      if (
        !text.includes('Failed to load resource') &&
        !text.includes('400') &&
        !text.includes('403') &&
        !text.includes('404') &&
        !text.includes('422') &&
        !text.includes('WebSocket connection to') &&
        !text.includes('/ws/alerts')
      ) {
        consoleErrors.push(text);
      }
    }
  });

  page.on('pageerror', (err) => {
    pageErrors.push(err.message);
  });

  async function assertNoErrors(stage = '') {
    const bodyText = await page.innerText('body').catch(() => '');
    const hasErrorBoundary =
      bodyText.includes('Something went wrong') ||
      bodyText.includes('An unexpected error occurred') ||
      bodyText.includes('Application Error');

    assert.equal(hasErrorBoundary, false, `[${stage}] React ErrorBoundary detected on page`);
    assert.equal(pageErrors.length, 0, `[${stage}] Page errors: ${JSON.stringify(pageErrors)}`);
    assert.equal(consoleErrors.length, 0, `[${stage}] Console errors: ${JSON.stringify(consoleErrors)}`);
  }

  const clickContinue = async () => {
    const btn = page.locator('button:has-text("CONTINUE"), button:has-text("Continue"), button:has-text("Next")').last();
    await btn.waitFor({ timeout: 8000 });
    await btn.click();
    await page.waitForTimeout(1500);
  };

  return { assertNoErrors, clickContinue };
}

// ─────────────────────────────────────────────────────────────────────────────
// E2E Flow Tests (One per flow)
// ─────────────────────────────────────────────────────────────────────────────

test('E2E Flow: Desktop create + dry run', async (t) => {
  const [browserOk, frontendOk] = await Promise.all([canLaunchBrowser(), isFrontendRunning()]);
  if (!browserOk) {
    t.skip('Skipped: Chrome / Chromium browser binary not available');
    return;
  }
  if (!frontendOk) {
    t.skip(`Skipped: Frontend server not running at ${BASE_URL}`);
    return;
  }

  const instances = await getRunningDesktopInstances();
  if (instances.length === 0) {
    t.skip('Skipped: No active Power BI Desktop engine found on machine');
    return;
  }

  const browser = await getBrowser();
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();
  const { assertNoErrors, clickContinue } = setupErrorTracking(page);

  try {
    // 1. Init
    await page.goto(`${BASE_URL}/projects/new`, { waitUntil: 'networkidle' });
    await assertNoErrors('Create Project Init');

    // 2. Select Power BI Desktop Source Card
    const pbiCard = page.locator('button:has-text("Power BI Desktop (open file)")');
    await pbiCard.waitFor({ timeout: 8000 });
    await pbiCard.click();
    await page.waitForTimeout(400);

    // Enter project name
    const nameInput = page.locator('input[placeholder*="Sales Analytics Sync" i]');
    if (await nameInput.count() > 0) {
      await nameInput.fill(`Competitive Marketing Desktop Project ${Date.now()}`);
    }

    // Select Snowflake as target and OSI intermediate format
    const sfBtns = page.locator('button:has-text("Snowflake")');
    if (await sfBtns.count() > 1) {
      await sfBtns.nth(1).click();
    }
    const osiBtn = page.locator('button:has-text("OSI (Open Semantic Interchange)")');
    if (await osiBtn.isVisible()) {
      await osiBtn.click();
    }
    await assertNoErrors('Source Selected (Desktop)');

    // 3. Continue to Desktop Step
    await clickContinue();
    await assertNoErrors('Desktop Step List');

    // 4. Select instance card
    const instanceCard = page.locator('div:has-text(".pbix")').last();
    await instanceCard.waitFor({ timeout: 8000 });
    await instanceCard.click();
    await page.waitForTimeout(2000);
    await assertNoErrors('Desktop Step Inspect Summary');

    // 5. Test Refresh button
    const refreshBtn = await page.$('button:has-text("Refresh")');
    if (refreshBtn) {
      await refreshBtn.click();
      await page.waitForTimeout(1000);
      await assertNoErrors('Desktop Step Refresh');
    }

    // 6. Continue to Ontology Review
    await clickContinue();
    await assertNoErrors('Ontology Review Step');

    // 7. Continue to Mapping Options
    await clickContinue();
    await assertNoErrors('Mapping Options Step');

    // 8. Run Dry Run
    const runCheckBtn = await page.$('button:has-text("Run check"), button:has-text("Run Dry Run"), button:has-text("Dry Run")');
    if (runCheckBtn) {
      await runCheckBtn.click();
      await page.waitForTimeout(6000);
      await assertNoErrors('Dry Run Execution');
    }
  } finally {
    await browser.close();
  }
});

test('E2E Flow: Desktop edit project', async (t) => {
  const [browserOk, frontendOk] = await Promise.all([canLaunchBrowser(), isFrontendRunning()]);
  if (!browserOk) {
    t.skip('Skipped: Chrome / Chromium browser binary not available');
    return;
  }
  if (!frontendOk) {
    t.skip(`Skipped: Frontend server not running at ${BASE_URL}`);
    return;
  }

  const instances = await getRunningDesktopInstances();
  if (instances.length === 0) {
    t.skip('Skipped: No active Power BI Desktop engine found on machine');
    return;
  }

  const browser = await getBrowser();
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();
  const { assertNoErrors } = setupErrorTracking(page);

  try {
    await page.goto(`${BASE_URL}/projects/proj-competitive-marketing-desktop-project/edit`, { waitUntil: 'networkidle' });
    await page.waitForTimeout(1500);
    await assertNoErrors('Edit Project Page');
  } finally {
    await browser.close();
  }
});

test('E2E Flow: Launcher URL pre-selection (open and closed file)', async (t) => {
  const [browserOk, frontendOk] = await Promise.all([canLaunchBrowser(), isFrontendRunning()]);
  if (!browserOk) {
    t.skip('Skipped: Chrome / Chromium browser binary not available');
    return;
  }
  if (!frontendOk) {
    t.skip(`Skipped: Frontend server not running at ${BASE_URL}`);
    return;
  }

  const browser = await getBrowser();
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();
  const { assertNoErrors } = setupErrorTracking(page);

  try {
    // 1. Closed file notice (runs everywhere)
    await page.goto(
      `${BASE_URL}/projects/new?source=pbi_desktop&server=localhost:99999&database=nonexistent-db`,
      { waitUntil: 'networkidle' }
    );
    await page.waitForTimeout(2000);
    await assertNoErrors('Launcher Closed File Notice');

    // 2. Open file pre-selection (if Desktop running)
    const instances = await getRunningDesktopInstances();
    if (instances.length > 0) {
      const inst = instances[0];
      const openLauncherUrl = `${BASE_URL}/projects/new?source=pbi_desktop&server=${encodeURIComponent(inst.server)}&database=${encodeURIComponent(inst.database)}`;
      await page.goto(openLauncherUrl, { waitUntil: 'networkidle' });
      await page.waitForTimeout(2000);
      await assertNoErrors('Launcher Open File Preselected');
    }
  } finally {
    await browser.close();
  }
});

test('E2E Flow: Desktop empty state (no Desktop running)', async (t) => {
  const [browserOk, frontendOk] = await Promise.all([canLaunchBrowser(), isFrontendRunning()]);
  if (!browserOk) {
    t.skip('Skipped: Chrome / Chromium browser binary not available');
    return;
  }
  if (!frontendOk) {
    t.skip(`Skipped: Frontend server not running at ${BASE_URL}`);
    return;
  }

  const browser = await getBrowser();
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();
  const { assertNoErrors } = setupErrorTracking(page);

  try {
    // Intercept instances API to simulate 0 running instances
    await page.route('**/api/sources/pbi-desktop/instances', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ instances: [], total: 0, enabled: true }),
      });
    });

    await page.goto(`${BASE_URL}/projects/new?source=pbi_desktop`, { waitUntil: 'networkidle' });
    await page.waitForTimeout(1500);

    const emptyNotice = page.locator('text=Open your .pbix in Power BI Desktop, then Refresh');
    assert.equal(await emptyNotice.isVisible(), true, 'Empty state instructions should be visible');
    await assertNoErrors('Desktop Step Empty State');
  } finally {
    await browser.close();
  }
});

test('E2E Flow: Desktop error state (discovery failed)', async (t) => {
  const [browserOk, frontendOk] = await Promise.all([canLaunchBrowser(), isFrontendRunning()]);
  if (!browserOk) {
    t.skip('Skipped: Chrome / Chromium browser binary not available');
    return;
  }
  if (!frontendOk) {
    t.skip(`Skipped: Frontend server not running at ${BASE_URL}`);
    return;
  }

  const browser = await getBrowser();
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();
  const { assertNoErrors } = setupErrorTracking(page);

  try {
    // Intercept instances API to simulate a 500 error
    await page.route('**/api/sources/pbi-desktop/instances', async (route) => {
      await route.fulfill({
        status: 500,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'Analysis Services engine discovery encountered a fatal error' }),
      });
    });

    await page.goto(`${BASE_URL}/projects/new?source=pbi_desktop`, { waitUntil: 'networkidle' });
    await page.waitForTimeout(1500);

    const retryBtn = page.locator('button:has-text("Retry")');
    assert.equal(await retryBtn.isVisible(), true, 'Retry button should be visible in error state');
    await assertNoErrors('Desktop Step Error State');
  } finally {
    await browser.close();
  }
});

test('E2E Flow: Feature flag off (Power BI Desktop source hidden)', async (t) => {
  const [browserOk, frontendOk] = await Promise.all([canLaunchBrowser(), isFrontendRunning()]);
  if (!browserOk) {
    t.skip('Skipped: Chrome / Chromium browser binary not available');
    return;
  }
  if (!frontendOk) {
    t.skip(`Skipped: Frontend server not running at ${BASE_URL}`);
    return;
  }

  const browser = await getBrowser();
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();
  const { assertNoErrors } = setupErrorTracking(page);

  try {
    // Intercept status API to report enabled: false
    await page.route('**/api/sources/pbi-desktop/status', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ enabled: false }),
      });
    });

    await page.goto(`${BASE_URL}/projects/new`, { waitUntil: 'networkidle' });
    await page.waitForTimeout(1500);

    const pbiCardOff = await page.$('text=Power BI Desktop (open file)');
    assert.equal(!pbiCardOff, true, 'Power BI Desktop source card must be hidden when flag is off');
    await assertNoErrors('Flag OFF Source Screen');
  } finally {
    await browser.close();
  }
});

test('E2E Flow: PBIX source flow unchanged', async (t) => {
  const [browserOk, frontendOk] = await Promise.all([canLaunchBrowser(), isFrontendRunning()]);
  if (!browserOk) {
    t.skip('Skipped: Chrome / Chromium browser binary not available');
    return;
  }
  if (!frontendOk) {
    t.skip(`Skipped: Frontend server not running at ${BASE_URL}`);
    return;
  }

  const browser = await getBrowser();
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();
  const { assertNoErrors, clickContinue } = setupErrorTracking(page);

  try {
    await page.goto(`${BASE_URL}/projects/new`, { waitUntil: 'networkidle' });
    await page.waitForTimeout(1000);

    const pbixCard = await page.waitForSelector('text=Local PBIX File', { timeout: 8000 });
    await pbixCard.click();
    await page.waitForTimeout(300);

    const pbixName = page.locator('input[placeholder*="Sales Analytics Sync" i]');
    if (await pbixName.count() > 0) {
      await pbixName.fill('PBIX Parity Flow');
    }

    const sfTarget = page.locator('button:has-text("Snowflake")').nth(1);
    await sfTarget.click();
    await page.locator('button:has-text("OSI (Open Semantic Interchange)")').click();
    await assertNoErrors('PBIX Step 1');

    await clickContinue();
    await assertNoErrors('PBIX Step 2');
  } finally {
    await browser.close();
  }
});

test('E2E Flow: Fabric source flow unchanged', async (t) => {
  const [browserOk, frontendOk] = await Promise.all([canLaunchBrowser(), isFrontendRunning()]);
  if (!browserOk) {
    t.skip('Skipped: Chrome / Chromium browser binary not available');
    return;
  }
  if (!frontendOk) {
    t.skip(`Skipped: Frontend server not running at ${BASE_URL}`);
    return;
  }

  const browser = await getBrowser();
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();
  const { assertNoErrors, clickContinue } = setupErrorTracking(page);

  try {
    await page.goto(`${BASE_URL}/projects/new`, { waitUntil: 'networkidle' });
    await page.waitForTimeout(1000);

    const fabricCard = await page.waitForSelector('text=Microsoft Fabric', { timeout: 8000 });
    await fabricCard.click();
    await page.waitForTimeout(300);

    const fabricName = page.locator('input[placeholder*="Sales Analytics Sync" i]');
    if (await fabricName.count() > 0) {
      await fabricName.fill('Fabric Parity Flow');
    }

    const sfTarget = page.locator('button:has-text("Snowflake")').nth(1);
    await sfTarget.click();
    await page.locator('button:has-text("OSI (Open Semantic Interchange)")').click();
    await assertNoErrors('Fabric Step 1');

    await clickContinue();
    await assertNoErrors('Fabric Step 2');
  } finally {
    await browser.close();
  }
});
