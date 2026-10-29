const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const path = require("node:path");
const { test } = require("node:test");
const vm = require("node:vm");

const keys = ["enable_hub_group_batching", "enable_hub_context_compaction", "enable_hub_persona_summary", "enable_hub_decision_reuse"];
for (const page of ["companion-panel", "陪伴面板"]) {
  const source = readFileSync(path.join(__dirname, "..", "pages", page, "app.js"), "utf8");
  function readConstant(name, array = false) {
    const start = source.indexOf(`const ${name} = `);
    assert.ok(start >= 0);
    const end = source.indexOf(array ? "\n];" : "\n};", start) + 3;
    return vm.runInNewContext(`${source.slice(start, end)}\n${name}`, { advancedCycleSettingKeys: [] });
  }
  test(`${page}: 四个中枢优化开关独立显示并提供数值参数`, () => {
    const meta = readConstant("featureMeta");
    const groups = readConstant("featureGroups", true);
    const settings = readConstant("featureSettingGroups");
    const types = readConstant("featureSettingTypes");
    const stages = readConstant("featureStageKeySets");
    for (const key of keys) assert.ok(meta[key]?.[0]);
    const group = groups.find((group) => group.title === "中枢Token优化");
    assert.ok(group);
    assert.deepEqual(Array.from(group.keys), keys);
    assert.ok(settings.enable_hub_group_batching.includes("hub_group_batch_seconds"));
    assert.ok(settings.enable_hub_context_compaction.includes("hub_recent_message_limit"));
    assert.equal(types.hub_group_batch_seconds.type, "number");
    assert.equal(types.hub_recent_message_limit.type, "number");
    for (const key of keys) assert.ok(stages.before.has(key));
    assert.ok(group.note.includes("全局"));
    const impactStart = source.indexOf("function featureImpactLines(key) {");
    const impactEnd = source.indexOf("\nfunction configLabel(", impactStart);
    const impact = vm.runInNewContext(`${source.slice(impactStart, impactEnd)}\nfeatureImpactLines`, { featureGroupForKey: () => "中枢Token优化" });
    for (const key of keys) {
      const rows = impact(key);
      assert.ok(rows.find(([label, value]) => label === "场景" && value.includes("中枢")));
      assert.ok(rows.find(([label, value]) => label === "配置范围" && value.includes("全局")));
    }
  });
  test(`${page}: 保存载荷保留四项独立开关的真假值`, () => {
    const start = source.indexOf("function collectFeatureSwitchPayload() {");
    const end = source.indexOf("\nasync function ", start);
    const context = {
      state: { featureDraft: Object.fromEntries(keys.map((key, index) => [key, index % 2 === 0])), overview: { settings: {} } },
      document: { querySelector: () => null }, visibleConfigKey: () => true, toBool: Boolean,
    };
    const payload = vm.runInNewContext(`${source.slice(start, end)}\ncollectFeatureSwitchPayload()`, context);
    for (let i = 0; i < keys.length; i++) assert.equal(payload.features[keys[i]], i % 2 === 0);
  });
}
