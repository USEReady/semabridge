const puppeteer = require('puppeteer');

(async () => {
  const browser = await puppeteer.launch({
    headless: 'new',
    args: ['--no-sandbox', '--disable-setuid-sandbox'],
  });

  const page = await browser.newPage();
  await page.setViewport({ width: 1440, height: 900 });

  await page.goto('http://localhost:5180/bi-truth', { waitUntil: 'networkidle2' });
  await new Promise((r) => setTimeout(r, 2000));

  const select = await page.$('select[aria-label="Filter by warehouse"]');
  if (select) {
    // Focus or click
    await select.focus();
  }

  const outPath = 'C:/Users/manoj.s/.gemini/antigravity/brain/2534f7e2-8480-4878-886c-fca19a0d035e/bitruth_warehouse_fixed.png';
  await page.screenshot({ path: outPath });
  console.log('Saved screenshot to:', outPath);
  await browser.close();
})();
