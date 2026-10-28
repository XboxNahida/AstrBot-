const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

function panel(page = 'companion-panel') {
  const source = readFileSync(path.join(__dirname, '..', 'pages', page, 'app.js'), 'utf8');
  const start = source.indexOf('function renderScopedMemberMemorySelectors()');
  const end = source.indexOf('function worldbookStat(', start);
  assert.ok(start >= 0 && end > start, '群友记忆页面逻辑应存在');
  const nodes = Object.fromEntries(['Bot', 'Platform', 'Group', 'Member'].map(key => [`#scopedMemory${key}`, {
    value: '', dataset: {}, innerHTML: '', addEventListener() {},
  }]));
  nodes['#scopedMemberMemoryEditor'] = { dataset: {}, innerHTML: '', querySelectorAll: () => [] };
  let persona = '小七';
  const requests = [];
  const state = { scopedMemberMemoryScopes: [], scopedMemberMemories: [] };
  const context = vm.createContext({
    state, URLSearchParams, JSON, Date, Set,
    $: selector => nodes[selector],
    escapeHtml: value => String(value).replaceAll('<', '&lt;'),
    selectedPagePersonaId: () => persona,
    fetchJson: url => new Promise(resolve => requests.push({url, resolve})),
    showToast() {},
  });
  vm.runInContext(source.slice(start, end), context);
  function select(bot, group = 'g1') {
    for (const [key, value] of [['Bot', bot], ['Platform', 'qq'], ['Group', group], ['Member', 'qq:10001']]) {
      nodes[`#scopedMemory${key}`].value = value;
      nodes[`#scopedMemory${key}`].dataset.value = value;
    }
  }
  return { context, nodes, state, requests, select, persona: value => { persona = value; } };
}

for (const page of ['companion-panel', '陪伴面板']) {
  test(`${page}: 同群号也必须按机器人和平台过滤`, () => {
    const ui = panel(page);
    ui.state.scopedMemberMemoryScopes = ['a', 'b'].map(bot => ({bot_instance_id: bot, platform: 'qq', group_id: 'g1'}));
    ui.state.scopedMemberMemories = [
      {bot_instance_id: 'a', platform: 'qq', group_id: 'g1', person_id: 'qq:10001', name: '甲'},
      {bot_instance_id: 'b', platform: 'qq', group_id: 'g1', person_id: 'qq:10002', name: '乙'},
    ];
    ui.select('a');
    ui.context.renderScopedMemberMemorySelectors();
    assert.ok(!ui.nodes['#scopedMemoryMember'].innerHTML.includes('乙'));
    assert.ok(ui.nodes['#scopedMemoryMember'].innerHTML.includes('甲'));
  });

  test(`${page}: 旧作用域和旧人格请求不能覆盖新页面`, async () => {
    const ui = panel(page);
    ui.select('a');
    const first = ui.context.loadScopedGroupMemberMemories();
    ui.select('b');
    const second = ui.context.loadScopedGroupMemberMemories();
    ui.requests[1].resolve({members: [{name: '新'}]});
    await second;
    ui.requests[0].resolve({members: [{name: '旧'}]});
    await first;
    assert.equal(ui.state.scopedMemberMemories[0].name, '新');
    ui.select('a');
    const third = ui.context.loadScopedGroupMemberMemories();
    ui.persona('晚柠');
    ui.requests[2].resolve({members: [{name: '旧人格'}]});
    await third;
    assert.notEqual(ui.state.scopedMemberMemories[0]?.name, '旧人格');
  });

  test(`${page}: 保存不丢失记忆ID和证据元数据，完整作用域不符则拒绝`, () => {
    const ui = panel(page);
    ui.select('a');
    const memory = {id: 'm1', source_event_id: 'event1', created_at: 10, confidence: 'confirmed', content: '茶'};
    ui.state.scopedMemberMemories = [{bot_instance_id: 'a', platform: 'qq', group_id: 'g1', person_id: 'qq:10001', important_memories: [memory]}];
    const editor = ui.nodes['#scopedMemberMemoryEditor'];
    editor.dataset.scopeKey = JSON.stringify(['小七', 'a', 'qq', 'g1', 'qq:10001']);
    editor.querySelectorAll = () => [{dataset: {scopedMemoryIndex: '0'}, querySelector: selector => ({value: selector.includes('title') ? '偏好' : '乌龙茶', checked: true})}];
    const payload = ui.context.scopedMemberMemoryEditPayload();
    assert.equal(payload.important_memories[0].id, 'm1');
    assert.equal(payload.important_memories[0].source_event_id, 'event1');
    assert.equal(payload.important_memories[0].content, '乌龙茶');
    ui.select('b');
    assert.throws(() => ui.context.scopedMemberMemoryEditPayload());
  });

  test(`${page}: 群聊页提供可见的记忆入口`, () => {
    const html = readFileSync(path.join(__dirname, '..', 'pages', page, 'index.html'), 'utf8');
    const group = html.indexOf('id="panel-group"');
    const memory = html.indexOf('id="scopedMemberMemoryRefresh"');
    const next = html.indexOf('<section class="panel"', group + 1);
    assert.ok(memory > group && (next < 0 || memory < next));
  });
}
