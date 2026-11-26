const puppeteer = require('puppeteer');

(async () => {
  const browser = await puppeteer.launch({ headless: 'new', args: ['--no-sandbox'] });
  const page = await browser.newPage();
  await page.setViewport({ width: 1440, height: 900 });

  await page.goto('http://127.0.0.1:5173/drift?project=proj-human-res', { waitUntil: 'networkidle2' });
  await new Promise((r) => setTimeout(r, 2000));

  // Click Expand all groups
  await page.evaluate(() => {
    const buttons = Array.from(document.querySelectorAll('button'));
    const expandBtn = buttons.find((b) => b.textContent && b.textContent.includes('Expand all groups'));
    if (expandBtn) expandBtn.click();
  });
  await new Promise((r) => setTimeout(r, 1000));

  await page.screenshot({ path: 'C:/Users/manoj.s/.gemini/antigravity/brain/2534f7e2-8480-4878-886c-fca19a0d035e/drift_all_groups_expanded.png' });
  await browser.close();
})();
