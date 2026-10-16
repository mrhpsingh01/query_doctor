import fs from 'node:fs/promises';
import path from 'node:path';
import { createRequire } from 'node:module';
const require = createRequire(path.resolve('apps/web/package.json'));
const { chromium } = require('@playwright/test');
const base = process.env.QD_DEMO_URL ?? 'http://localhost:3100';
const output = path.resolve('.test-artifacts/demo-frames');
await fs.mkdir(output,{recursive:true});
const browser = await chromium.launch({channel:'chrome',headless:true});
const page = await browser.newPage({viewport:{width:1280,height:900},colorScheme:'light'});
const frames=[];
async function frame(label, duration) {
  await page.evaluate(text=>{
    let subtitle=document.getElementById('demo-subtitle');
    if(!subtitle){subtitle=document.createElement('div');subtitle.id='demo-subtitle';subtitle.style.cssText='position:fixed;bottom:0;left:0;right:0;z-index:999998;background:rgba(12,35,43,.94);color:white;text-align:center;padding:14px;font:18px system-ui;pointer-events:none';document.body.appendChild(subtitle);}
    subtitle.textContent=text;
  },label);
  await page.waitForTimeout(400);
  const filename=`frame-${String(frames.length).padStart(2,'0')}.png`;
  await page.screenshot({path:path.join(output,filename)});
  frames.push({file:filename,duration});
}
async function click(locator) {
  await locator.scrollIntoViewIfNeeded();
  const box=await locator.boundingBox();
  if(box) await page.mouse.move(box.x+box.width/2,box.y+box.height/2,{steps:12});
  await locator.click();
}
async function cursor() {
  await page.evaluate(()=>{
    const element=document.createElement('div');element.id='demo-cursor';element.textContent='↖';element.style.cssText='position:fixed;z-index:999999;pointer-events:none;font:28px system-ui;color:#087f9b;text-shadow:1px 1px white;left:16px;top:16px';document.body.appendChild(element);
    document.addEventListener('mousemove',event=>{element.style.left=event.clientX+'px';element.style.top=event.clientY+'px';});
  });
}
try {
  await page.goto(base);await cursor();
  await page.getByRole('button',{name:/97% Scan Waste/}).waitFor();
  await frame('QueryDoctor: inspect a plan, then measure whether a fix works.',5000);
  await click(page.getByRole('button',{name:/97% Scan Waste/}));
  await frame('1. Open a recorded case. No database setup or API call.',5000);
  await page.getByRole('heading',{name:/97% Scan Waste/}).scrollIntoViewIfNeeded();
  await frame('Real pipeline runs, HypoPG cost changes, row counts and SHA-256.',5000);
  const graph=page.getByRole('group',{name:'Execution plan'});
  await graph.scrollIntoViewIfNeeded();
  await click(graph.getByText('Seq Scan',{exact:true}));
  await frame('2. Select the red bottleneck to inspect its execution evidence.',5000);
  await page.getByText('Full recorded evidence',{exact:true}).scrollIntoViewIfNeeded();
  await click(page.getByText('Full recorded evidence',{exact:true}));
  await frame('3. Open the complete evidence. Download it to review every run.',5000);
  await click(page.getByText('Full recorded evidence',{exact:true}));
  for (const [title,label] of [[/Sort Spill/,'An ordered index eliminates the sort that spilled to disk.'],[/Unindexed MongoDB/,'MongoDB lookup: verify the index on the foreign collection.'],[/False Positive/,'The false positive: planner approval does not guarantee a benchmark pass.']]) {
    await click(page.getByRole('button',{name:title}));
    await page.getByRole('heading',{name:title}).scrollIntoViewIfNeeded();
    await frame(label,5000);
  }
  await page.goto(base+'/analyses/new');await cursor();
  await page.getByRole('textbox',{name:'Paste EXPLAIN Output'}).fill('{"Plan":{"Node Type":"Seq Scan","Relation Name":"orders","Filter":"customer_id = 42","Actual Total Time":8,"Actual Loops":1,"Actual Rows":100,"Rows Removed by Filter":249900}}');
  await frame('Paste your own PostgreSQL JSON/text or MongoDB executionStats.',5000);
  await click(page.getByRole('button',{name:'Inspect plan'}));
  await page.getByText(/Suggested \(Unverified/).scrollIntoViewIfNeeded();
  await frame('Pasted evidence stays unverified until you connect and measure.',5000);
  await page.goto(base+'/workload');await cursor();
  await page.getByRole('region',{name:'Ranked Fix Queue'}).waitFor();
  await frame('The fix queue ranks consolidated indexes by estimated time saved.',5000);
  await page.goto(base);await cursor();
  await frame('Standalone showcase now. Connect a database when you need validation.',5000);
  await fs.writeFile(path.join(output,'manifest.json'),JSON.stringify(frames,null,2));
  console.log(`Captured ${frames.length} frames for a ${frames.reduce((s,f)=>s+f.duration,0)/1000}s demo.`);
} finally {await browser.close();}
