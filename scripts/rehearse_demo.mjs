import fs from 'node:fs/promises';
import path from 'node:path';
import { createRequire } from 'node:module';
const require = createRequire(path.resolve('apps/web/package.json'));
const { chromium } = require('@playwright/test');
const browser = await chromium.launch({channel:'chrome',headless:true});
const page = await browser.newPage({viewport:{width:1280,height:900}});
const base = process.env.QD_DEMO_URL ?? 'http://localhost:3000';
await page.goto(base);
await page.getByRole('button',{name:/97% Scan Waste/}).waitFor();
console.log(JSON.stringify(await page.locator('button,textarea,a').evaluateAll(elements => elements.filter(el => el.getBoundingClientRect().height).map(el=>({tag:el.tagName,role:el.getAttribute('role'),text:el.textContent?.trim().slice(0,90),id:el.id}))),null,2));
for (const name of [/97% Scan Waste/,/Sort Spill/,/Unindexed MongoDB/,/False Positive/]) {
 await page.getByRole('button',{name}).click();
 await page.getByRole('group',{name:'Execution plan'}).waitFor();
}
await page.goto(base+'/analyses/new');
await page.getByRole('textbox',{name:'Paste EXPLAIN Output'}).fill('{"Plan":{"Node Type":"Seq Scan","Relation Name":"orders","Filter":"customer_id = 1"}}');
await page.getByRole('button',{name:'Inspect plan'}).click();
await page.getByText(/Suggested \(Unverified/).waitFor();
console.log('Rehearsal passed');
await browser.close();
