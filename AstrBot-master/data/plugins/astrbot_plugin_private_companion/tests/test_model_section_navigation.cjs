const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const path = require("node:path");
const { test } = require("node:test");
const vm = require("node:vm");

function createPanel(page, installed) {
  const source = readFileSync(path.join(__dirname, "..", "pages", page, "app.js"), "utf8");
  const start = source.indexOf("function syncModelsSectionControls() {");
  const end = source.indexOf("function renderImageModelConfig()", start);
  assert.ok(start >= 0 && end > start);
  function element(dataset = {}) {
    return {
      dataset, hidden: false, tabIndex: 0, attributes: {}, listeners: {},
      classList: { toggle() {} },
      setAttribute(name, value) { this.attributes[name] = value; },
      addEventListener(name, listener) {
        (this.listeners[name] ||= []).push(listener);
      },
      click() { (this.listeners.click || []).forEach((listener) => listener()); },
      focus() { document.activeElement = this; },
    };
  }
  const tabs = ["providers", "tts", "image"].map((modelsSection) => element({ modelsSection }));
  const panes = ["providers", "tts", "image"].map((modelsPane) => element({ modelsPane }));
  const tablist = element();
  const panel = element();
  tablist.querySelectorAll = () => tabs;
  const document = {
    activeElement: tabs[0],
    querySelector: () => tablist,
    querySelectorAll: (selector) => selector === "[data-models-section]" ? tabs : panes,
    getElementById: () => panel,
  };
  const state = { modelsSection: "providers", lazyLoaded: {} };
  let available = installed;
  let statusLoads = 0;
  const context = vm.createContext({
    document, state,
    imageCompanionInstalled: () => available,
    renderProviders: () => context.syncModelsSectionControls(),
    loadImageApiStatus: async () => { statusLoads++; state.lazyLoaded.imageApiStatus = true; },
    loadTtsProviderConfigs: async () => { state.lazyLoaded.ttsProviderConfigs = true; },
    showToast: (message) => { throw new Error(message); },
  });
  vm.runInContext(source.slice(start, end), context);
  return {
    tabs, panes, state, panel,
    sync: () => context.syncModelsSectionControls(),
    detect: (value) => { available = value; },
    statusLoads: () => statusLoads,
    endKey: () => tablist.listeners.keydown[0]({ key: "End", preventDefault() {} }),
  };
}

for (const page of ["companion-panel", "陪伴面板"]) {
  test(`${page}: 扩展状态加载后点击生图能打开配置`, () => {
    const ui = createPanel(page, false);
    ui.sync();
    assert.equal(ui.tabs[2].hidden, true);
    ui.detect(true);
    ui.sync();
    assert.equal(ui.tabs[2].hidden, false);
    ui.tabs[2].click();
    assert.equal(ui.state.modelsSection, "image");
    assert.equal(ui.tabs[2].attributes["aria-selected"], "true");
    assert.equal(ui.panes[2].hidden, false);
    assert.equal(ui.panes[0].hidden, true);
    assert.equal(ui.statusLoads(), 1);
    assert.equal(ui.panel.dataset.modelsDirection, "next");
    ui.tabs[0].click();
    assert.equal(ui.panel.dataset.modelsDirection, "previous");
    ui.tabs[2].click();
    assert.equal(ui.statusLoads(), 1);
    assert.equal(ui.tabs[2].listeners.click.length, 1);
  });

  test(`${page}: 键盘导航读取最新可见标签`, () => {
    const ui = createPanel(page, false);
    ui.sync();
    ui.detect(true);
    ui.sync();
    ui.endKey();
    assert.equal(ui.state.modelsSection, "image");
    assert.equal(ui.panes[2].hidden, false);
  });

  test(`${page}: 扩展停用后不再进入隐藏的生图配置`, () => {
    const ui = createPanel(page, true);
    ui.sync();
    ui.tabs[2].click();
    ui.detect(false);
    ui.sync();
    ui.tabs[2].click();
    assert.equal(ui.state.modelsSection, "providers");
    assert.equal(ui.tabs[2].hidden, true);
    assert.equal(ui.panes[2].hidden, true);
    ui.tabs[1].click();
    assert.equal(ui.state.modelsSection, "tts");
    assert.equal(ui.panes[1].hidden, false);
  });
}
