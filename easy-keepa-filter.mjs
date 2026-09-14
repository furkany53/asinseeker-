import { chromium } from 'playwright';

const EASY_URL = process.env.EASY_URL || 'https://app.easycentral.com';
const KEEPA_BASE = 'https://keepa.com/#!product/5-';
const CHROME_PROFILE_PATH = process.env.CHROME_PROFILE_PATH || 'C:\\Users\\onury\\AppData\\Local\\Google\\Chrome\\User Data\\Default';

const criteria = {
  minSalesLast12Months: Number(process.env.MIN_SALES_LAST_12_MONTHS || 1),
  requireYearTab: process.env.REQUIRE_YEAR_TAB !== 'false',
};

const safeText = async (locator) => {
  try {
    return (await locator.textContent())?.trim() || '';
  } catch {
    return '';
  }
};

async function clickIfVisible(page, selector, label) {
  try {
    const target = page.locator(selector).first();
    if (await target.isVisible({ timeout: 1500 }).catch(() => false)) {
      await target.click({ timeout: 5000 });
      return true;
    }
  } catch {
    // ignore
  }
  return false;
}

async function findAsinsFromEasyPage(page) {
  const rows = page.locator('tr, [data-row], .product-row, .table-row, li');
  const count = await rows.count().catch(() => 0);
  const asins = new Set();

  for (let i = 0; i < count; i++) {
    const row = rows.nth(i);
    const rowText = await safeText(row);
    const match = rowText.match(/\b[A-Z0-9]{10}\b/);
    if (match) {
      asins.add(match[0]);
    }
  }

  if (asins.size === 0) {
    const textMatches = await page.locator('body').textContent();
    const found = [...new Set((textMatches || '').match(/\b[A-Z0-9]{10}\b/g) || [])];
    found.forEach((asin) => asins.add(asin));
  }

  return [...asins];
}

async function openKeepaWithAsin(page, asin) {
  const productUrl = `${KEEPA_BASE}${asin}`;
  await page.goto(productUrl, { waitUntil: 'domcontentloaded', timeout: 20000 });

  const yearTab = page.locator('button:has-text("1Y"), [role="button"]:has-text("1Y"), button:has-text("Year"), [aria-label*="Year"], text=/1Y/i').first();
  if (criteria.requireYearTab) {
    const yearClicked = await yearTab.isVisible({ timeout: 5000 }).catch(() => false);
    if (yearClicked) {
      await yearTab.click({ timeout: 5000 });
    }
  }

  await page.waitForTimeout(2000);

  const graphText = await page.locator('body').textContent();
  const hasSalesSignal = /sales|sell|units|1Y|12M|year/i.test(graphText || '');

  return { productUrl, hasSalesSignal, graphText: (graphText || '').slice(0, 4000) };
}

async function markAsApprovedInEasy(page, asin) {
  // Try generic row selection methods first.
  const rowSelectors = [
    `text=${asin}`,
    `xpath=//*[contains(text(), '${asin}')]`,
    `tr:has-text("${asin}")`,
    `div:has-text("${asin}")`,
  ];

  let matched = false;
  for (const selector of rowSelectors) {
    try {
      const el = page.locator(selector).first();
      if (await el.isVisible({ timeout: 2000 }).catch(() => false)) {
        await el.click({ timeout: 5000 });
        matched = true;
        break;
      }
    } catch {
      // ignore
    }
  }

  if (!matched) {
    console.log(`Could not locate row for ${asin}; leaving it for manual review.`);
    return false;
  }

  const tickSelectors = [
    'button:has-text("Yükle")',
    'button:has-text("Upload")',
    'button:has-text("Onay")',
    'input[type="checkbox"]',
    '[aria-label*="check" i]',
    '[title*="check" i]',
  ];

  for (const selector of tickSelectors) {
    await clickIfVisible(page, selector, 'approval action');
  }

  return true;
}

async function main() {
  const context = await chromium.launchPersistentContext(CHROME_PROFILE_PATH, {
    headless: false,
    channel: 'chrome',
    ignoreDefaultArgs: ['--enable-automation'],
  });

  const page = context.pages()[0] || await context.newPage();

  console.log('Using Chrome profile:', CHROME_PROFILE_PATH);
  console.log('Opening EasyCentral...');
  await page.goto(EASY_URL, { waitUntil: 'domcontentloaded', timeout: 25000 });
  await page.waitForTimeout(3000);

  const asins = await findAsinsFromEasyPage(page);
  console.log('ASIN count:', asins.length);

  const approved = [];

  for (const asin of asins) {
    console.log(`Checking ${asin} in Keepa...`);
    const result = await openKeepaWithAsin(page, asin);
    const qualifies = result.hasSalesSignal && criteria.minSalesLast12Months <= 1;

    if (qualifies) {
      approved.push({ asin, url: result.productUrl });
      console.log('QUALIFIED:', asin);
    } else {
      console.log('REJECTED:', asin);
    }
  }

  console.log('Approved products:', approved.length);

  for (const item of approved) {
    console.log('Returning to EasyCentral...');
    await page.goto(EASY_URL, { waitUntil: 'domcontentloaded', timeout: 25000 });
    await page.waitForTimeout(2500);
    await markAsApprovedInEasy(page, item.asin);
    await page.waitForTimeout(1500);
  }

  console.log('Automation complete.');
  console.log('Approved items:', approved.map((x) => x.asin).join(', '));

  // Keep browser open for manual inspection and final upload.
  // await context.close();
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
