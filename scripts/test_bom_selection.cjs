// Exercise the actual dropdown implementation without a browser dependency.
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const html = fs.readFileSync(require('node:path').join(__dirname, '../ui/index.html'), 'utf8');
const source = html.slice(html.indexOf('function renderCandidates('), html.indexOf('function renderFiles('));
function selected(items, previous = {}) {
  const controls = Object.fromEntries(['xy', 'bom'].map(role => [role, {
    value: previous[role] || '', options: [],
    replaceChildren(option) { this.options = [option]; this.value = ''; },
    add(option) { this.options.push(option); }
  }]));
  const context = { $: role => controls[role], Option: function(text, value) { Object.assign(this, {text, value}); } };
  vm.createContext(context);
  vm.runInContext('let candidates;\n' + source, context);
  context.renderCandidates(items);
  return {xy: controls.xy.value, bom: controls.bom.value};
}
const xy = {name: 'xy.csv', value: 'xy.csv', source: 'BOM folder / xy.csv', roles: ['xy', 'bom']};
const bom = {name: 'module.xlsx', value: 'module.xlsx', source: 'module.xlsx', roles: ['bom']};
assert.deepEqual(selected([xy, bom]), {xy: 'xy.csv', bom: 'module.xlsx'});
assert.deepEqual(selected([xy]), {xy: 'xy.csv', bom: ''});
assert.deepEqual(selected([bom]), {xy: '', bom: 'module.xlsx'});
const alternate = {...bom, name: 'alternate.xlsx', value: 'alternate.xlsx', source: 'alternate.xlsx'};
assert.equal(selected([xy, bom, alternate], {bom: alternate.value}).bom, alternate.value);
const named = {...bom, name: 'board_BOM.xlsx', value: 'board_BOM.xlsx', source: 'board_BOM.xlsx'};
assert.equal(selected([xy, bom, named]).bom, named.value);
const combined = {...xy, name: 'combined_BOM.csv', value: 'combined_BOM.csv'};
assert.equal(selected([combined]).bom, combined.value);
assert.equal(selected([combined, bom]).bom, bom.value);
console.log('PASS: 7 BOM/XY dropdown selection scenarios');
