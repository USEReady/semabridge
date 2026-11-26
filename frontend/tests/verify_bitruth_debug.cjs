const puppeteer = require('puppeteer');

(async () => {
  const browser = await puppeteer.launch({
    headless: 'new',
    args: ['--no-sandbox', '--disable-setuid-sandbox'],
  });

  const page = await browser.newPage();
  await page.setViewport({ width: 1440, height: 900 });

  page.on('console', (msg) => console.log('PAGE LOG:', msg.text()));
  page.on('pageerror', (err) => console.error('PAGE ERROR:', err.message));

  const url = 'http://localhost:5180/bi-truth?project=proj-hr_t';
  console.log(`Navigating to ${url}...`);
  await page.goto(url, { waitUntil: 'networkidle2' });
  await new Promise((r) => setTimeout(r, 3000));

  console.log('Current URL:', page.url());

  const html = await page.content();
  console.log('HTML snippet:', html.slice(0, 500));

  // Check if we are on a login page or main page
  const pageState = await page.evaluate(() => {
    return {
      title: document.title,
      bodyText: document.body.innerText.slice(0, 300),
      buttons: Array.from(document.querySelectorAll('button')).map((b) => b.innerText.trim()).filter(Boolean),
      selects: Array.from(document.querySelectorAll('select')).map((s) => ({
        label: s.getAttribute('aria-label') || s.name || s.id,
        options: Array.from(s.options).map((o) => o.text.trim()),
      })),
    };
  });

  console.log('Page State:', JSON.stringify(pageState, null, 2));

  const screenshotPath = 'C:/Users/manoj.s/.gemini/antigravity/brain/2534f7e2-8480-4878-886c-fca19a0d035e/bitruth_debug.png';
  await page.screenshot({ path: screenshotPath });
  console.log(`Debug screenshot saved to ${screenshotPath}`);

  await browser.close();
})();
