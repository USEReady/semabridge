import { chromium } from 'playwright';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const SCREENSHOT_DIR = path.resolve(__dirname, '..', 'screenshots');

if (!fs.existsSync(SCREENSHOT_DIR)) {
  fs.mkdirSync(SCREENSHOT_DIR, { recursive: true });
}

const errorsFound = [];
const consoleErrors = [];
const pageErrors = [];
const walkResults = [];

function recordResult(screen, ok, details = '') {
  walkResults.push({ screen, ok: ok ? 'OK' : 'FAIL', details });
  console.log(`[WALK] ${screen}: ${ok ? '✅ OK' : '❌ FAIL'} ${details}`);
}

async function captureScreen(page, name) {
  const filePath = path.join(SCREENSHOT_DIR, `${name}.png`);
  await page.screenshot({ path: filePath, fullPage: true });
  console.log(`📸 Screenshot saved: ${filePath}`);
}

async function checkErrors(page, screenName) {
  // Check for React error boundary text
  const bodyText = await page.innerText('body').catch(() => '');
  const hasErrorBoundary =
    bodyText.includes('Something went wrong') ||
    bodyText.includes('An unexpected error occurred') ||
    bodyText.includes('Application Error');

  if (hasErrorBoundary) {
    const errObj = {
      screen: screenName,
      type: 'ErrorBoundary',
      url: page.url(),
      details: 'Error boundary detected in DOM',
    };
    errorsFound.push(errObj);
    recordResult(screenName, false, 'ErrorBoundary detected');
    return false;
  }
  return true;
}

async function run() {
  console.log('Starting Playwright walk through all SemaBridge screens...');
  const browser = await chromium.launch({ channel: 'chrome' });
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();

  page.on('console', (msg) => {
    const text = msg.text();
    if (msg.type() === 'error') {
      // Exclude normal backend 4xx test responses or expected polling logs
      if (!text.includes('Failed to load resource') && !text.includes('422') && !text.includes('400')) {
        consoleErrors.push({ text, url: page.url() });
        console.log(`[CONSOLE ERROR @ ${page.url()}]:`, text);
      }
    }
  });

  page.on('pageerror', (err) => {
    pageErrors.push({ message: err.message, stack: err.stack, url: page.url() });
    console.log(`[PAGE ERROR @ ${page.url()}]:`, err.message);
  });

  const clickContinue = async (label = '') => {
    console.log(`Clicking CONTINUE for ${label}...`);
    const btn = page.locator('button:has-text("CONTINUE"), button:has-text("Continue"), button:has-text("Next")').last();
    await btn.waitFor({ timeout: 8000 });
    await btn.click();
    await page.waitForTimeout(1500);
  };

  try {
    // ════════════════════════════════════════════════════════════════════════
    // FLOW 1: Power BI Desktop Source Flow (Flag ON)
    // ════════════════════════════════════════════════════════════════════════
    console.log('\n--- Starting Flow 1: Power BI Desktop Flow ---');

    // 1. Create project page
    await page.goto('http://localhost:5180/projects/new', { waitUntil: 'networkidle' });
    await captureScreen(page, '01_create_project_init');
    let ok = await checkErrors(page, 'Create Project Page (Init)');
    if (ok) recordResult('01 Create Project Init', true);

    // 2. Select Power BI Desktop Source Card
    const pbiCard = page.locator('button:has-text("Power BI Desktop (open file)")');
    await pbiCard.waitFor({ timeout: 8000 });
    await pbiCard.click();
    await page.waitForTimeout(500);

    // Enter project name
    const nameInput = await page.$('input[placeholder*="Sales Analytics Sync" i]');
    if (nameInput) {
      await nameInput.fill('Competitive Marketing Desktop Project');
    }

    // Select Snowflake as target connector
    const sfBtns = page.locator('button:has-text("Snowflake")');
    await sfBtns.nth(1).click();
    await page.waitForTimeout(300);

    // Select OSI intermediate format
    const osiBtn = page.locator('button:has-text("OSI (Open Semantic Interchange)")');
    await osiBtn.click();
    await page.waitForTimeout(300);

    await captureScreen(page, '02_source_selected');
    ok = await checkErrors(page, 'Source Selected (Desktop)');
    if (ok) recordResult('02 Source Card Selected', true);

    // 3. Click Continue to go to Desktop Step
    await clickContinue('to Desktop Step');

    await captureScreen(page, '03_desktop_step_list');
    ok = await checkErrors(page, 'Desktop Step (Instance List)');
    if (ok) recordResult('03 Desktop Step List', true);

    // 4. Select instance card
    console.log('Selecting instance card...');
    const instanceCard = page.locator('div:has-text("Untitled.pbix")').last();
    await instanceCard.waitFor({ timeout: 8000 });
    await instanceCard.click();
    await page.waitForTimeout(2500); // Allow inspect endpoint to return and render summary

    await captureScreen(page, '04_desktop_step_selected_inspect');
    ok = await checkErrors(page, 'Desktop Step (Inspect Summary)');
    if (ok) recordResult('04 Desktop Step Inspect Summary', true);

    // 5. Test Refresh button on Desktop step
    const refreshBtn = await page.$('button:has-text("Refresh")');
    if (refreshBtn) {
      await refreshBtn.click();
      await page.waitForTimeout(1000);
      await captureScreen(page, '05_desktop_step_refreshed');
      ok = await checkErrors(page, 'Desktop Step (Refresh)');
      if (ok) recordResult('05 Desktop Step Refresh', true);
    }

    // 6. Click Continue -> Step 4: Ontology Review
    await clickContinue('to Ontology Review');

    await captureScreen(page, '06_ontology_review_step');
    ok = await checkErrors(page, 'Step 4: Ontology Review');
    if (ok) recordResult('06 Ontology Review Step', true);

    // 7. Click Continue -> Step 5: Mapping Options
    await clickContinue('to Mapping Options');

    await captureScreen(page, '07_mapping_options_step');
    ok = await checkErrors(page, 'Step 5: Mapping Options');
    if (ok) recordResult('07 Mapping Options Step', true);

    // 8. Trigger Dry Run / Run check
    const runCheckBtn = await page.$('button:has-text("Run check"), button:has-text("Run Dry Run"), button:has-text("Dry Run")');
    if (runCheckBtn) {
      console.log('Clicking Dry Run / Run check button...');
      await runCheckBtn.click();
      await page.waitForTimeout(6000);
      await captureScreen(page, '08_dry_run_completed');
      ok = await checkErrors(page, 'Dry Run Execution');
      if (ok) recordResult('08 Dry Run Completed', true);
    }

    // 9. Finish step / Created Project View
    const createdProjectId = 'proj-competitive-marketing-desktop-project';
    await page.goto(`http://localhost:5180/projects/${createdProjectId}`, { waitUntil: 'networkidle' });
    await page.waitForTimeout(1500);
    await captureScreen(page, '09_project_created_view');
    ok = await checkErrors(page, 'Project Detail View');
    if (ok) recordResult('09 Project Created View', true);

    // 10. Run report / runs screen on project detail
    await page.goto(`http://localhost:5180/runs`, { waitUntil: 'networkidle' });
    await page.waitForTimeout(1500);
    await captureScreen(page, '10_run_report_view');
    ok = await checkErrors(page, 'Run Report View');
    if (ok) recordResult('10 Run Report View', true);

    // ════════════════════════════════════════════════════════════════════════
    // FLOW 2: Edit Project View
    // ════════════════════════════════════════════════════════════════════════
    console.log('\n--- Starting Flow 2: Edit Project ---');
    await page.goto(`http://localhost:5180/projects/${createdProjectId}/edit`, { waitUntil: 'networkidle' });
    await page.waitForTimeout(1500);
    await captureScreen(page, '11_edit_project_page');
    ok = await checkErrors(page, 'Edit Project Page');
    if (ok) recordResult('11 Edit Project Page', true);

    // ════════════════════════════════════════════════════════════════════════
    // FLOW 3: Schedule Modal
    // ════════════════════════════════════════════════════════════════════════
    console.log('\n--- Starting Flow 3: Schedule Modal ---');
    await page.goto('http://localhost:5180/runs', { waitUntil: 'networkidle' });
    await page.waitForTimeout(1000);

    const schedulesTab = page.locator('button:has-text("Schedules")');
    if (await schedulesTab.count() > 0) {
      await schedulesTab.click();
      await page.waitForTimeout(500);

      const addScheduleBtn = page.locator('button:has-text("Add Schedule"), button:has-text("Schedule")').first();
      if (await addScheduleBtn.count() > 0) {
        await addScheduleBtn.click();
        await page.waitForTimeout(1000);
        await captureScreen(page, '12_schedule_modal_warning');
        ok = await checkErrors(page, 'Schedule Modal');
        if (ok) recordResult('12 Schedule Modal Warning', true);

        const closeBtn = page.locator('button[aria-label="Close"], button:has-text("Cancel")').first();
        if (await closeBtn.count() > 0) await closeBtn.click();
      }
    }

    // ════════════════════════════════════════════════════════════════════════
    // FLOW 4: External Tools Launcher Pre-Selection
    // ════════════════════════════════════════════════════════════════════════
    console.log('\n--- Starting Flow 4: Launcher Pre-Selection ---');

    // 4A: With currently open file (port 56888 and db 3f81a039-98ba-4ff6-bfe3-82c5c5bb69db)
    const openLauncherUrl = 'http://localhost:5180/projects/new?source=pbi_desktop&server=localhost:56888&database=3f81a039-98ba-4ff6-bfe3-82c5c5bb69db';
    await page.goto(openLauncherUrl, { waitUntil: 'networkidle' });
    await page.waitForTimeout(2000);
    await captureScreen(page, '13_launcher_open_file_preselected');
    ok = await checkErrors(page, 'Launcher Pre-Selection (Open File)');
    if (ok) recordResult('13 Launcher Open Pre-selected', true);

    // 4B: With closed file
    const closedLauncherUrl = 'http://localhost:5180/projects/new?source=pbi_desktop&server=localhost:99999&database=nonexistent-db';
    await page.goto(closedLauncherUrl, { waitUntil: 'networkidle' });
    await page.waitForTimeout(2000);
    await captureScreen(page, '14_launcher_closed_file_notice');
    ok = await checkErrors(page, 'Launcher Pre-Selection (Closed File Notice)');
    if (ok) recordResult('14 Launcher Closed File Notice', true);

    // ════════════════════════════════════════════════════════════════════════
    // FLOW 5: Empty & Error States on Desktop Step
    // ════════════════════════════════════════════════════════════════════════
    console.log('\n--- Starting Flow 5: Empty & Error States ---');

    // 5A: Empty state (mock 0 instances)
    await page.route('**/api/sources/pbi-desktop/instances', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ instances: [], total: 0, enabled: true }),
      });
    });

    await page.goto('http://localhost:5180/projects/new?source=pbi_desktop', { waitUntil: 'networkidle' });
    await page.waitForTimeout(1500);
    await captureScreen(page, '15_desktop_empty_state');
    ok = await checkErrors(page, 'Desktop Step (Empty State)');
    if (ok) recordResult('15 Desktop Empty State', true);

    // 5B: Error state (mock 500 error)
    await page.unroute('**/api/sources/pbi-desktop/instances');
    await page.route('**/api/sources/pbi-desktop/instances', async (route) => {
      await route.fulfill({
        status: 500,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'Analysis Services engine discovery encountered a fatal error' }),
      });
    });

    await page.goto('http://localhost:5180/projects/new?source=pbi_desktop', { waitUntil: 'networkidle' });
    await page.waitForTimeout(1500);
    await captureScreen(page, '16_desktop_error_state');
    ok = await checkErrors(page, 'Desktop Step (Error State)');
    if (ok) recordResult('16 Desktop Error State', true);

    await page.unroute('**/api/sources/pbi-desktop/instances');

    // ════════════════════════════════════════════════════════════════════════
    // FLOW 6: Flag OFF Walk
    // ════════════════════════════════════════════════════════════════════════
    console.log('\n--- Starting Flow 6: Flag OFF Walk ---');
    await page.route('**/api/sources/pbi-desktop/status', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ enabled: false }),
      });
    });

    await page.goto('http://localhost:5180/projects/new', { waitUntil: 'networkidle' });
    await page.waitForTimeout(1500);
    const pbiCardOff = await page.$('text=Power BI Desktop (open file)');
    const isHidden = !pbiCardOff;
    await captureScreen(page, '17_flag_off_source_hidden');
    ok = await checkErrors(page, 'Flag OFF Source Screen');
    if (ok && isHidden) recordResult('17 Flag OFF Source Card Hidden', true);
    else recordResult('17 Flag OFF Source Card Hidden', false, 'Card was still visible');

    await page.unroute('**/api/sources/pbi-desktop/status');

    // ════════════════════════════════════════════════════════════════════════
    // FLOW 7: PBIX Project Flow
    // ════════════════════════════════════════════════════════════════════════
    console.log('\n--- Starting Flow 7: PBIX Project Flow ---');
    await page.goto('http://localhost:5180/projects/new', { waitUntil: 'networkidle' });
    await page.waitForTimeout(1000);
    const pbixCard = await page.waitForSelector('text=Local PBIX File', { timeout: 5000 });
    await pbixCard.click();
    await page.waitForTimeout(500);
    const pbixName = await page.$('input[placeholder*="Sales Analytics Sync" i]');
    if (pbixName) await pbixName.fill('PBIX Parity Flow');
    const sfTarget1 = page.locator('button:has-text("Snowflake")').nth(1);
    await sfTarget1.click();
    await page.locator('button:has-text("OSI (Open Semantic Interchange)")').click();
    await captureScreen(page, '18_pbix_flow_step1');
    ok = await checkErrors(page, 'PBIX Step 1');
    if (ok) recordResult('18 PBIX Flow Step 1', true);

    await clickContinue('PBIX to Step 2');
    await captureScreen(page, '19_pbix_flow_step2');
    ok = await checkErrors(page, 'PBIX Step 2');
    if (ok) recordResult('19 PBIX Flow Step 2', true);

    // ════════════════════════════════════════════════════════════════════════
    // FLOW 8: Fabric Project Flow
    // ════════════════════════════════════════════════════════════════════════
    console.log('\n--- Starting Flow 8: Fabric Project Flow ---');
    await page.goto('http://localhost:5180/projects/new', { waitUntil: 'networkidle' });
    await page.waitForTimeout(1000);
    const fabricCard = await page.waitForSelector('text=Microsoft Fabric', { timeout: 5000 });
    await fabricCard.click();
    await page.waitForTimeout(500);
    const fabricName = await page.$('input[placeholder*="Sales Analytics Sync" i]');
    if (fabricName) await fabricName.fill('Fabric Parity Flow');
    const sfTarget2 = page.locator('button:has-text("Snowflake")').nth(1);
    await sfTarget2.click();
    await page.locator('button:has-text("OSI (Open Semantic Interchange)")').click();
    await captureScreen(page, '20_fabric_flow_step1');
    ok = await checkErrors(page, 'Fabric Step 1');
    if (ok) recordResult('20 Fabric Flow Step 1', true);

    await clickContinue('Fabric to Step 2');
    await captureScreen(page, '21_fabric_flow_step2');
    ok = await checkErrors(page, 'Fabric Step 2');
    if (ok) recordResult('21 Fabric Flow Step 2', true);

  } catch (err) {
    console.error('Walk test failed with exception:', err);
  } finally {
    await browser.close();
  }

  // Summary Report
  console.log('\n════════════════════════════════════════════════════════════════════════');
  console.log('                          WALK RUN SUMMARY                              ');
  console.log('════════════════════════════════════════════════════════════════════════');
  console.table(walkResults);
  console.log(`\nTotal Screens Checked: ${walkResults.length}`);
  console.log(`Total ErrorBoundaries caught: ${errorsFound.length}`);
  console.log(`Total Console Errors logged: ${consoleErrors.length}`);
  console.log(`Total Page Errors logged: ${pageErrors.length}`);

  fs.writeFileSync(
    path.join(SCREENSHOT_DIR, 'walk_report.json'),
    JSON.stringify({ walkResults, errorsFound, consoleErrors, pageErrors }, null, 2),
    'utf-8'
  );
}

run();
