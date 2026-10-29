import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const source = readFileSync(
  new URL("../src/components/shared/PersonaForm.vue", import.meta.url),
  "utf8",
);

function createForm(dialogs) {
  const requests = [];
  const errors = [];
  const context = {
    formValid: { value: true },
    saving: { value: false },
    personaForm: {
      persona_id: "测试人格",
      begin_dialogs: [...dialogs],
      tools: null,
      skills: null,
    },
    props: { editingPersona: {} },
    tm: (key) => key,
    emit: (event, message) => {
      if (event === "error") errors.push(message);
    },
    personaApi: {
      update: async (id, payload) => {
        requests.push(payload);
        return { data: { status: "ok" } };
      },
    },
    window: { dispatchEvent() {} },
    CustomEvent: class {},
    closeDialog() {},
  };
  vm.createContext(context);
  for (const [start, end] of [
    ["function getDialogRules(index)", "\nwatch("],
    ["async function savePersona()", "\nasync function deletePersona()"],
  ]) {
    const offset = source.indexOf(start);
    assert.ok(offset >= 0);
    const limit = source.indexOf(end, offset);
    assert.ok(limit > offset);
    vm.runInContext(source.slice(offset, limit), context);
  }
  return { context, requests, errors };
}

test("助手示例允许留空表达不接话", () => {
  const { context } = createForm([]);
  assert.ok(context.getDialogRules(1).every((rule) => rule("") === true));
});

test("用户示例仍拒绝空内容和纯空白", () => {
  const { context } = createForm([]);
  for (const value of ["", "  \n "]) {
    assert.ok(context.getDialogRules(0).some((rule) => rule(value) !== true));
  }
});

test("普通非空问答仍然有效", () => {
  const { context } = createForm([]);
  for (const index of [0, 1]) {
    assert.ok(context.getDialogRules(index).every((rule) => rule("你好") === true));
  }
});

test("保存时保留空助手回复及原始问答顺序", async () => {
  const dialogs = ["哦哦", "", "晚柠你在吗", "在呀"];
  const { context, requests, errors } = createForm(dialogs);
  await context.savePersona();
  assert.deepEqual(errors, []);
  assert.equal(requests.length, 1);
  assert.deepEqual(Array.from(requests[0].begin_dialogs), dialogs);
});

test("提交前仍拦截空用户消息", async () => {
  const { context, requests, errors } = createForm(["  ", "你好"]);
  await context.savePersona();
  assert.equal(requests.length, 0);
  assert.equal(errors.length, 1);
});
