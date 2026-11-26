const puppeteer = require('puppeteer');

(async () => {
  const browser = await puppeteer.launch({ headless: 'new', args: ['--no-sandbox'] });
  const page = await browser.newPage();
  await page.setViewport({ width: 1440, height: 900 });
  await page.goto('http://localhost:5180/bi-truth?project_id=proj-hr_t', { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.waitForSelector('span', { timeout: 15000 });
  await new Promise(r => setTimeout(r, 6000));
  
  // Find case with "What is the total AVG Tenure Days?"
  const buttons = await page.$$('[role="button"]');
  for (const b of buttons) {
    const text = await page.evaluate(el => el.innerText, b);
    if (text.includes('What is the total AVG Tenure Days?')) {
      await b.click();
      await new Promise(r => setTimeout(r, 1000));
      break;
    }
  }

  await page.screenshot({ path: 'C:/Users/manoj.s/.gemini/antigravity/brain/2534f7e2-8480-4878-886c-fca19a0d035e/bitruth_live_pass_verified.png' });
  await browser.close();
  console.log('PASS_SCREENSHOT_DONE');
})();
