const puppeteer = require('puppeteer');

(async () => {
  const browser = await puppeteer.launch({
    headless: 'new',
    args: ['--no-sandbox', '--disable-setuid-sandbox'],
  });

  const page = await browser.newPage();
  await page.setViewport({ width: 1440, height: 900 });

  const errors400 = [];
  page.on('response', (res) => {
    if (res.status() === 400) {
      errors400.push(`${res.request().method()} ${res.url()} -> 400 Bad Request`);
    }
  });

  const url = 'http://localhost:5180/bi-truth';
  console.log(`Navigating to ${url}...`);
  await page.goto(url, { waitUntil: 'networkidle2' });
  await new Promise((r) => setTimeout(r, 2000));

  // 1. Check sidebar elements
  const sidebarInfo = await page.evaluate(() => {
    const projButtons = Array.from(document.querySelectorAll('button[role="option"]'));
    const sectionHeaders = Array.from(document.querySelectorAll('button[aria-expanded]'));
    const viewSelector = document.querySelector('select[aria-label="Select deployed semantic view"]');
    const warehouseSelect = document.querySelector('select[aria-label="Filter by warehouse"]');

    return {
      projectCount: projButtons.length,
      projectNames: projButtons.slice(0, 10).map((b) => b.textContent.trim().replace(/\s+/g, ' ')),
      sectionHeaders: sectionHeaders.map((b) => b.textContent.trim().replace(/\s+/g, ' ')),
      hasViewSelector: Boolean(viewSelector),
      viewOptions: viewSelector ? Array.from(viewSelector.options).map((o) => o.text.trim()) : [],
      hasWarehouseFilter: Boolean(warehouseSelect),
      warehouseOptions: warehouseSelect ? Array.from(warehouseSelect.options).map((o) => o.text.trim()) : [],
    };
  });

  console.log('Sidebar Info:', JSON.stringify(sidebarInfo, null, 2));

  // 2. Switch projects 10 times
  console.log('\n--- Switching projects 10 times ---');
  for (let i = 0; i < 10; i++) {
    const clicked = await page.evaluate((index) => {
      const projButtons = Array.from(document.querySelectorAll('button[role="option"]'));
      if (projButtons.length === 0) return null;
      const target = projButtons[index % projButtons.length];
      target.click();
      return target.textContent.trim().replace(/\s+/g, ' ');
    }, i);

    console.log(`Switch ${i + 1}: clicked project -> ${clicked}`);
    await new Promise((r) => setTimeout(r, 600));
  }

  await new Promise((r) => setTimeout(r, 1500));

  console.log('\n--- 400 Errors Recorded during Project Switching ---');
  if (errors400.length === 0) {
    console.log('SUCCESS: Exactly ZERO 400 errors occurred across all project transitions!');
  } else {
    console.error(`FAILURE: Encountered ${errors400.length} 400 errors:`, errors400);
  }

  // 3. Capture screenshot
  const screenshotPath = 'C:/Users/manoj.s/.gemini/antigravity/brain/2534f7e2-8480-4878-886c-fca19a0d035e/bitruth_suite_ui_verified.png';
  await page.screenshot({ path: screenshotPath });
  console.log(`\nScreenshot captured at: ${screenshotPath}`);

  await browser.close();

  if (errors400.length > 0) {
    process.exit(1);
  }
})();
