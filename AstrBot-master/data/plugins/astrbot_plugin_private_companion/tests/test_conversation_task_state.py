import unittest

from astrbot_plugin_private_companion.conversation_task_state import ConversationTaskStateStore


class ConversationTaskStateTests(unittest.TestCase):
    def test_task_lifecycle_keeps_model_status_and_unfinished_task(self):
        store = ConversationTaskStateStore(max_tasks=2)
        task = store.start_task(
            session_key="private:u1",
            task_text="帮我分析这个项目并给出修改方案",
            provider_id="openai/gpt-6-sol",
            fallback_provider_id="openai/gpt-6-mini",
            estimated_seconds=90,
        )
        self.assertEqual("running", task.status)
        self.assertEqual("openai/gpt-6-sol", task.provider_id)
        self.assertEqual("帮我分析这个项目并给出修改方案", task.unfinished_task)

        progress = store.mark_progress(task.task_id, "正在读取项目文件")
        self.assertEqual("progress", progress.status)
        self.assertEqual("正在读取项目文件", progress.progress_note)

        failed = store.fail_task(task.task_id, "Request timed out")
        self.assertEqual("failed", failed.status)
        self.assertEqual("Request timed out", failed.error)
        summary = store.summary_for_session("private:u1")
        self.assertEqual("failed", summary["status"])
        self.assertEqual("openai/gpt-6-sol", summary["provider_id"])
        self.assertIn("分析这个项目", summary["unfinished_task"])

    def test_completed_task_keeps_result_and_bounded_store(self):
        store = ConversationTaskStateStore(max_tasks=2)
        first = store.start_task(session_key="s", task_text="一", provider_id="gpt")
        second = store.start_task(session_key="s", task_text="二", provider_id="gpt")
        third = store.start_task(session_key="s", task_text="三", provider_id="gpt")
        self.assertIsNone(store.get(first.task_id))
        completed = store.complete_task(second.task_id, "最终结果")
        self.assertEqual("complete", completed.status)
        self.assertEqual("最终结果", completed.result_preview)
        self.assertEqual("running", store.get(third.task_id).status)

    def test_timeout_is_distinct_from_failure(self):
        store = ConversationTaskStateStore()
        task = store.start_task(session_key="s", task_text="长任务", provider_id="gpt")
        timeout = store.timeout_task(task.task_id, "超过预计等待时间")
        self.assertEqual("timeout", timeout.status)
        self.assertEqual("长任务", timeout.unfinished_task)


if __name__ == "__main__":
    unittest.main()
