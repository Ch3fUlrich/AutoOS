// Exercise actual UI functions without a browser or a test framework.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const path = require('node:path');
const html = fs.readFileSync(path.join(__dirname, '..', 'web', 'index.html'), 'utf8');
const elements = new Map();
function element(id) {
  if (!elements.has(id)) elements.set(id, {
    style: {}, attrs: {}, textContent: '', classes: new Set(),
    setAttribute(k, v) { this.attrs[k] = v; },
    removeAttribute(k) { delete this.attrs[k]; },
    classList: {toggle(k, enabled) { element(id).classes[enabled ? 'add' : 'delete'](k); }}
  });
  return elements.get(id);
}
const context = vm.createContext({el: element, esc: s => String(s).replaceAll('<', '&lt;')});
for (const name of ['renderProgress', 'installedChip']) {
  const source = html.match(new RegExp('function ' + name + '\\([^]*?\\n\\}'));
  assert(source, name + ' exists');
  vm.runInContext(source[0], context);
}
context.renderProgress({running:true,done:0,total:3,current:{name:'Example',status:'running',phase:'downloading',percent:25,elapsedSeconds:2}});
assert.equal(element('prog').style.width, '0%');
assert.equal(element('currentProg').style.width, '25%');
context.renderProgress({running:true,done:1,total:3,current:{name:'Example',status:'running',phase:'installing',percent:null,elapsedSeconds:5}});
assert.equal(element('prog').style.width, '33%');
assert(element('currentBar').classes.has('indeterminate'));
assert.equal(element('currentBar').attrs['aria-valuenow'], undefined);
context.renderProgress({running:false,done:1,total:3,current:{name:'Example',status:'complete',phase:'failed',percent:null}});
assert.equal(element('prog').style.width, '33%', 'interrupted run must not jump to 100%');
assert(!element('currentBar').classes.has('indeterminate'));
assert(context.installedChip({installed:true}).includes('✓ Installed'));
assert(!context.installedChip({installed:false,installedStatus:'unknown'}).includes('✓'));
assert(context.installedChip({installed:false,provider:'manual'}).includes('Vendor setup'));
console.log('Web progress and installed-status regression checks passed.');

// Test the actual eligibility and closure functions: a disabled checkbox alone
// would still let Select all, profiles, or restored selections queue an app.
const apps = [
  {id:'available',provider:'winget',profiles:['everyday']},
  {id:'vendor',provider:'manual',profiles:['everyday']},
  {id:'dependent',provider:'custom',requires:['vendor'],profiles:['everyday']},
  {id:'missing',provider:'custom',requires:['unknown'],profiles:['everyday']}
];
context.BY_ID = new Map(apps.map(app => [app.id, app]));
context.STATE = {components:apps};
for (const name of ['canInstall','closure','profileClosureDirect']) {
  vm.runInContext(html.match(new RegExp('function ' + name + '\\([^]*?\\n\\}'))[0],context);
}
assert.deepEqual(Array.from(context.profileClosureDirect('everyday')),['available']);
assert.deepEqual(Array.from(context.closure(apps.map(app => app.id))),['available']);
assert.equal(context.canInstall(apps[1]),false);
assert.equal(context.canInstall(apps[2]),false);
assert(html.includes(' disabled aria-disabled="true"'));
assert(html.includes('.item.unavailable'));
console.log('Unavailable applications cannot enter profile or bulk selections.');
