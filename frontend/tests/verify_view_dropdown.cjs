const puppeteer = require('puppeteer');

(async () => {
  const browser = await puppeteer.launch({
    headless: 'new',
    args: ['--no-sandbox', '--disable-setuid-sandbox'],
  });

  const page = await browser.newPage();
  await page.setViewport({ width: 1440, height: 900 });

  const url = 'http://localhost:5180/bi-truth?project=proj-employee_hiring';
  console.log(`Navigating to ${url}...`);
  await page.goto(url, { waitUntil: 'networkidle2' });
  await new Promise((r) => setTimeout(r, 2500));

  const viewSelectorInfo = await page.evaluate(() => {
    const viewSelector = document.querySelector('select[aria-label="Select deployed semantic view"]');
    const selectedProjectEl = document.querySelector('button[aria-selected="true"]');
    const questionItems = Array.from(document.querySelectorAll('div[role="listitem"], button')).filter(b => b.textContent.includes('What is'));
    const liveVal = document.querySelector('div')?.innerText;

    return {
      selectedProjectText: selectedProjectEl ? selectedProjectEl.textContent.trim().replace(/\s+/g, ' ') : null,
      viewSelectorValue: viewSelector ? viewSelector.value : null,
      viewOptions: viewSelector ? Array.from(viewSelector.options).map((o) => o.text.trim()) : [],
      firstFewQuestionsCount: questionItems.length,
    };
  });

  console.log('View Selector Info:', JSON.stringify(viewSelectorInfo, null, 2));

  // Capture screenshot of the full workbench
  const screenshotPath = 'C:/Users/manoj.s/.gemini/antigravity/brain/2534f7e2-8480-4878-886c-fca19a0d035e/bitruth_view_dropdown_verified.png';
  await page.screenshot({ path: screenshotPath });
  console.log(`Saved screenshot to: ${screenshotPath}`);

  await browser.close();
})();
