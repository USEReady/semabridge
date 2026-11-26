const puppeteer = require('puppeteer');

(async () => {
  const browser = await puppeteer.launch({ headless: 'new', args: ['--no-sandbox', '--disable-setuid-sandbox'] });
  const page = await browser.newPage();
  await page.setViewport({ width: 1440, height: 900 });

  await page.goto('http://localhost:5180/bi-truth', { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.waitForSelector('button[role="option"]', { timeout: 15000 });
  await new Promise(r => setTimeout(r, 2000));

  // Click proj-hr_t ("Human Resources S...")
  await page.evaluate(() => {
    const opts = Array.from(document.querySelectorAll('button[role="option"]'));
    const target = opts.find(o => o.textContent.includes('Human Resources S') || o.textContent.includes('proj-hr_t'));
    if (target) target.click();
  });

  await new Promise(r => setTimeout(r, 3000));

  // Click the case "What is the total AVG Tenure Days?"
  await page.evaluate(() => {
    const caseItems = Array.from(document.querySelectorAll('*')).filter(el => 
      el.textContent && el.textContent.includes('What is the total AVG Tenure Days?') && el.getAttribute('role') === 'button'
    );
    if (caseItems.length > 0) {
      caseItems[0].click();
      return;
    }
    const headers = Array.from(document.querySelectorAll('div, p, span, button')).filter(el =>
      el.textContent && el.textContent.trim() === 'What is the total AVG Tenure Days?'
    );
    if (headers.length > 0) headers[0].click();
  });

  await new Promise(r => setTimeout(r, 2500));

  const outPath = 'C:/Users/manoj.s/.gemini/antigravity/brain/2534f7e2-8480-4878-886c-fca19a0d035e/bitruth_live_run_verified.png';
  await page.screenshot({ path: outPath });
  await browser.close();
  console.log('LIVE_RUN_SCREENSHOT_DONE:', outPath);
})();
