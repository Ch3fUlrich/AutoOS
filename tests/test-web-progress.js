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

// A retired id (catalog "tombstone") has to behave in the page exactly as it
// does in the terminal: never pre-ticked, never pulled in as a dependency, never
// asked for an answer, and still shown — the row is where a reader learns that
// the id went away and what replaced it. These cases run the shipped functions,
// so a page that merely stops drawing the row cannot pass them either.
const tombstones = [
  { id: 'live', provider: 'winget', profiles: ['everyday'], name: 'Live App', description: 'installs' },
  { id: 'old-dep', provider: 'custom', profiles: [], name: 'Old Dependency', description: 'was needed' },
  {
    id: 'retired', provider: 'custom', profiles: ['everyday'], name: 'Retired App',
    description: 'the id stays known', tombstone: true, note: 'wired by live now',
    replaced_by: ['live'],
    requires: ['old-dep'], prompt: 'demo_url', homepage: 'https://example.invalid/retired'
  },
  { id: 'needer', provider: 'custom', profiles: ['everyday'], name: 'Needer', description: 'names a tombstone', requires: ['retired'] }
];
context.BY_ID = new Map(tombstones.map(c => [c.id, c]));
context.STATE = { components: tombstones };
for (const name of ['platformChip', 'installedChip', 'iconDomain', 'componentIcon', 'itemHtml',
                    'retiredChip', 'replacedByText', 'itemDescription']) {
  const source = html.match(new RegExp('function ' + name + '\\([^]*?\\n\\}'));
  assert(source, name + ' exists');
  vm.runInContext(source[0], context);
}
for (const decl of html.match(/^const (PLATFORM_NAME|ALL_PLATFORMS) = .*/gm) || []) {
  vm.runInContext(decl, context);
}

assert.equal(context.canInstall(tombstones[2]), false, 'a retired id must not be installable');
assert.deepEqual(context.profileClosureDirect('everyday'), ['live'],
  'a profile must not pre-tick a retired id, nor a component whose only requirement is one');
assert.deepEqual(Array.from(context.closure(['retired'])), [],
  'a retired id must not enter a plan, nor drag in what it used to require');
assert.deepEqual(Array.from(context.closure(['needer'])), [],
  'a retired id must not be pulled in as somebody else\'s dependency');

const card = context.itemHtml(tombstones[2]);
assert(card.includes('disabled aria-disabled="true"'), 'the retired card must not be tickable');
assert(card.includes('chip-retired'), 'the retired card must say so');
assert(card.includes('(retired)'), 'the retired row reads (retired), like --list and the menu');
assert(card.includes('wired by live now'), 'the retire note is what explains the row');
// replaced_by is shown here and never planned: expanding a replayed selection is
// the installer's job (--only / --from-state run it), and a page that expanded it
// too would tick successors the reader never asked for.
assert(card.includes('replaced by live'), 'the retired card names the ids that took the work');
assert(!card.includes('data-configure-field="cfg_demo_url"'), 'the page must not offer to configure a prompt it will never ask');
assert(!card.includes('quick-install'), 'the page must not offer a one-click install of nothing');
assert(!context.itemHtml(tombstones[0]).includes('chip-retired'), 'an ordinary card must not read as retired');
console.log('Retired ids stay visible in the page, unselectable, and pull in nothing.');
