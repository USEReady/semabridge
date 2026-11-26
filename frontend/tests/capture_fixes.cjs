const puppeteer = require('puppeteer');

/**
 * capture_fixes.cjs
 * Visual verification test script:
 * 1. Opens the drift page for proj-hr_t.
 * 2. Filters by Description Drift.
 * 3. Expands the VP Description Drift finding card.
 * 4. Takes a screenshot of the card showing clean text (markers stripped) and copy action.
 * 5. Opens the AI Impact Check modal.
 * 6. Takes a screenshot of the modal showing clean text and single consolidated empty-state message.
 */
(async () => {
  const browser = await puppeteer.launch({
    headless: 'new',
    args: ['--no-sandbox', '--disable-setuid-sandbox']
  });
  const page = await browser.newPage();
  await page.setViewport({ width: 1440, height: 950 });

  await page.goto('http://localhost:5180/drift?project=proj-hr_t', { waitUntil: 'networkidle2' });
  await new Promise((r) => setTimeout(r, 2500));

  // Click "Description Drift" pill in category summary
  await page.evaluate(() => {
    const buttons = Array.from(document.querySelectorAll('button'));
    const btn = buttons.find((b) => b.textContent && b.textContent.includes('Description Drift'));
    if (btn) btn.click();
  });
  await new Promise((r) => setTimeout(r, 1200));

  // Click "Expand all groups"
  await page.evaluate(() => {
    const all = Array.from(document.querySelectorAll('button, span, a'));
    const btn = all.find((el) => el.textContent && el.textContent.trim() === 'Expand all groups');
    if (btn) btn.click();
  });
  await new Promise((r) => setTimeout(r, 1200));

  // Click row with event id drift-d4fdd20e6870 (VP Description Drift)
  await page.evaluate(() => {
    const spans = Array.from(document.querySelectorAll('span'));
    const vpSpan = spans.find((s) => s.textContent && s.textContent.trim() === 'VP');
    if (vpSpan) {
      const clickable = vpSpan.closest('div');
      if (clickable) clickable.click();
    }
  });
  await new Promise((r) => setTimeout(r, 1500));

  // Scroll main container to center finding card
  await page.evaluate(() => {
    const scrollables = Array.from(document.querySelectorAll('*')).filter((el) => {
      const style = window.getComputedStyle(el);
      return (style.overflowY === 'auto' || style.overflowY === 'scroll') && el.scrollHeight > el.clientHeight;
    });
    for (const s of scrollables) {
      s.scrollTop += 500;
    }
  });
  await new Promise((r) => setTimeout(r, 1000));

  // Screenshot finding card
  await page.screenshot({
    path: 'C:/Users/manoj.s/.gemini/antigravity/brain/2534f7e2-8480-4878-886c-fca19a0d035e/drift_finding_card_clean.png',
    fullPage: false
  });
  console.log('Finding card screenshot saved: drift_finding_card_clean.png');

  // Click AI Impact Check button
  await page.evaluate(() => {
    const buttons = Array.from(document.querySelectorAll('button'));
    const aiBtn = buttons.find((b) => b.textContent && b.textContent.includes('AI Impact Check'));
    if (aiBtn) aiBtn.click();
  });
  await new Promise((r) => setTimeout(r, 6000));

  // Screenshot modal
  await page.screenshot({
    path: 'C:/Users/manoj.s/.gemini/antigravity/brain/2534f7e2-8480-4878-886c-fca19a0d035e/drift_ai_impact_modal_clean.png',
    fullPage: false
  });
  console.log('AI Impact modal screenshot saved: drift_ai_impact_modal_clean.png');

  await browser.close();
})();
const puppeteer = require('puppeteer');

/**
 * capture_fixes.cjs
 * Visual verification test script:
 * 1. Opens the drift page for proj-hr_t.
 * 2. Filters by Description Drift.
 * 3. Expands the VP Description Drift finding card.
 * 4. Takes a screenshot of the card showing clean text (markers stripped) and copy action.
 * 5. Opens the AI Impact Check modal.
 * 6. Takes a screenshot of the modal showing clean text and single consolidated empty-state message.
 */
(async () => {
  const browser = await puppeteer.launch({
    headless: 'new',
    args: ['--no-sandbox', '--disable-setuid-sandbox']
  });
  const page = await browser.newPage();
  await page.setViewport({ width: 1440, height: 950 });

  await page.goto('http://localhost:5180/drift?project=proj-hr_t', { waitUntil: 'networkidle2' });
  await new Promise((r) => setTimeout(r, 2500));

  // Click "Description Drift" pill in category summary
  await page.evaluate(() => {
    const buttons = Array.from(document.querySelectorAll('button'));
    const btn = buttons.find((b) => b.textContent && b.textContent.includes('Description Drift'));
    if (btn) btn.click();
  });
  await new Promise((r) => setTimeout(r, 1200));

  // Click "Expand all groups"
  await page.evaluate(() => {
    const all = Array.from(document.querySelectorAll('button, span, a'));
    const btn = all.find((el) => el.textContent && el.textContent.trim() === 'Expand all groups');
    if (btn) btn.click();
  });
  await new Promise((r) => setTimeout(r, 1200));

  // Click row with event id drift-d4fdd20e6870 (VP Description Drift)
  await page.evaluate(() => {
    const spans = Array.from(document.querySelectorAll('span'));
    const vpSpan = spans.find((s) => s.textContent && s.textContent.trim() === 'VP');
    if (vpSpan) {
      const clickable = vpSpan.closest('div');
      if (clickable) clickable.click();
    }
  });
  await new Promise((r) => setTimeout(r, 1500));

  // Scroll main container to center finding card
  await page.evaluate(() => {
    const scrollables = Array.from(document.querySelectorAll('*')).filter((el) => {
      const style = window.getComputedStyle(el);
      return (style.overflowY === 'auto' || style.overflowY === 'scroll') && el.scrollHeight > el.clientHeight;
    });
    for (const s of scrollables) {
      s.scrollTop += 500;
    }
  });
  await new Promise((r) => setTimeout(r, 1000));

  // Screenshot finding card
  await page.screenshot({
    path: 'C:/Users/manoj.s/.gemini/antigravity/brain/2534f7e2-8480-4878-886c-fca19a0d035e/drift_finding_card_clean.png',
    fullPage: false
  });
  console.log('Finding card screenshot saved: drift_finding_card_clean.png');

  // Click AI Impact Check button
  await page.evaluate(() => {
    const buttons = Array.from(document.querySelectorAll('button'));
    const aiBtn = buttons.find((b) => b.textContent && b.textContent.includes('AI Impact Check'));
    if (aiBtn) aiBtn.click();
  });
  await new Promise((r) => setTimeout(r, 6000));

  // Screenshot modal
  await page.screenshot({
    path: 'C:/Users/manoj.s/.gemini/antigravity/brain/2534f7e2-8480-4878-886c-fca19a0d035e/drift_ai_impact_modal_clean.png',
    fullPage: false
  });
  console.log('AI Impact modal screenshot saved: drift_ai_impact_modal_clean.png');

  await browser.close();
})();

