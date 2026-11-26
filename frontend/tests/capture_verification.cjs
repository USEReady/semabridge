const puppeteer = require('puppeteer');

(async () => {
  const browser = await puppeteer.launch({
    headless: 'new',
    args: ['--no-sandbox', '--disable-setuid-sandbox']
  });
  const page = await browser.newPage();
  await page.setViewport({ width: 1440, height: 950 });

  // 1. Navigate to proj-hr drift page
  console.log('Navigating to http://127.0.0.1:5173/drift?project=proj-hr...');
  await page.goto('http://127.0.0.1:5173/drift?project=proj-hr', { waitUntil: 'networkidle2' });

  // Wait for React lazy component and data to finish loading
  console.log('Waiting for warehouse selector...');
  await page.waitForSelector('select[aria-label="Filter by warehouse"]', { timeout: 30000 });
  await new Promise((r) => setTimeout(r, 3000));

  // ── Step 1: Click "All" status filter tab ──────────────────────────────
  console.log('Selecting "All" tab...');
  await page.evaluate(() => {
    const buttons = Array.from(document.querySelectorAll('button'));
    const allTab = buttons.find((b) => b.textContent && b.textContent.includes('All (') && !b.textContent.includes('All warehouses'));
    if (allTab) allTab.click();
  });
  await new Promise((r) => setTimeout(r, 2000));

  // Capture Screenshot 1: Status filter on "all"
  await page.screenshot({
    path: 'C:/Users/manoj.s/.gemini/antigravity/brain/2534f7e2-8480-4878-886c-fca19a0d035e/drift_status_all_consistent.png',
    fullPage: false
  });
  console.log('Saved drift_status_all_consistent.png');

  // ── Step 2: Click "Pending" status filter tab ──────────────────────────
  console.log('Selecting "Pending" tab...');
  await page.evaluate(() => {
    const buttons = Array.from(document.querySelectorAll('button'));
    const pendTab = buttons.find((b) => b.textContent && b.textContent.includes('Pending ('));
    if (pendTab) pendTab.click();
  });
  await new Promise((r) => setTimeout(r, 2000));

  // Capture Screenshot 2: Status filter on "pending"
  await page.screenshot({
    path: 'C:/Users/manoj.s/.gemini/antigravity/brain/2534f7e2-8480-4878-886c-fca19a0d035e/drift_status_pending_consistent.png',
    fullPage: false
  });
  console.log('Saved drift_status_pending_consistent.png');

  // ── Step 3: Warehouse dropdown selector ───────────────────────────────
  console.log('Inspecting warehouse selector options...');
  const optionsText = await page.evaluate(() => {
    const sel = document.querySelector('select[aria-label="Filter by warehouse"]');
    if (!sel) return [];
    return Array.from(sel.options).map((o) => `${o.value}: ${o.textContent}`);
  });
  console.log('Warehouse filter options:', optionsText);

  // Capture Screenshot 3: Warehouse filter section with open select
  await page.evaluate(() => {
    const sel = document.querySelector('select[aria-label="Filter by warehouse"]');
    if (sel) {
      sel.size = 4;
      sel.style.height = 'auto';
    }
  });
  await new Promise((r) => setTimeout(r, 500));
  await page.screenshot({
    path: 'C:/Users/manoj.s/.gemini/antigravity/brain/2534f7e2-8480-4878-886c-fca19a0d035e/drift_warehouse_filter_open.png',
    fullPage: false
  });
  console.log('Saved drift_warehouse_filter_open.png with open select');

  await browser.close();
  console.log('Verification screenshots complete.');
})();
