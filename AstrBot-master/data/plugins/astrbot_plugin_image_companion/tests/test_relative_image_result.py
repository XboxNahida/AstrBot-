"""站内缓存结果的相对地址和下载预算回归测试。"""
import asyncio
import unittest

from astrbot_plugin_image_companion.image_runtime import (
    ProactiveMessageMixin,
    _EXTERNAL_IMAGE_DOWNLOAD_TIMEOUT_OVERRIDE,
)


class RelativeImageResultTests(unittest.TestCase):
    def runtime(self):
        runtime = ProactiveMessageMixin.__new__(ProactiveMessageMixin)
        runtime.external_image_api_base_url = 'https://www.heiheiai.shop/v1'
        runtime.external_image_api_platform = 'openai'
        runtime.external_image_api_timeout_seconds = 300
        return runtime

    def test_root_relative_url_preserves_signed_query(self):
        runtime = self.runtime()
        received = []

        async def download(url, **kwargs):
            received.append(url)
            return 'saved.png', 'ok'

        runtime._download_external_image_url = download
        outcome = asyncio.run(runtime._materialize_external_image_value(
            '/api/v1/gen/cached/generated/test.png?sign=a%2Bb', session_key='test'))
        self.assertEqual(received, ['https://www.heiheiai.shop/api/v1/gen/cached/generated/test.png?sign=a%2Bb'])
        self.assertTrue(outcome.generation_completed)
        self.assertEqual(outcome.image_path, 'saved.png')

    def test_absolute_url_is_unchanged(self):
        runtime = self.runtime()
        received = []

        async def download(url, **kwargs):
            received.append(url)
            return 'saved.png', 'ok'

        runtime._download_external_image_url = download
        url = 'https://cdn.example.org/image.png?x=a%2Fb'
        asyncio.run(runtime._materialize_external_image_value(url, session_key='test'))
        self.assertEqual(received, [url])

    def test_base64_is_not_treated_as_relative_url(self):
        runtime = self.runtime()
        received = []

        async def materialize(value, **kwargs):
            received.append(value)
            return 'saved.png', 'ok'

        runtime._materialize_external_image_base64 = materialize
        asyncio.run(runtime._materialize_external_image_value('data:image/png;base64,abcd', session_key='test'))
        self.assertEqual(received, ['abcd'])

    def test_site_cache_gets_longer_bounded_attempt(self):
        runtime = self.runtime()
        timeouts = []

        async def once(url, **kwargs):
            timeouts.append(_EXTERNAL_IMAGE_DOWNLOAD_TIMEOUT_OVERRIDE.get())
            return 'saved.png', 'ok'

        runtime._download_external_image_url_once = once
        asyncio.run(runtime._download_external_image_url(
            'https://www.heiheiai.shop/api/v1/gen/cached/generated/test.png', session_key='test'))
        self.assertGreater(timeouts[0], 75)
        self.assertLessEqual(timeouts[0], 180)

    def test_other_hosts_keep_existing_budget(self):
        runtime = self.runtime()
        timeouts = []

        async def once(url, **kwargs):
            timeouts.append(_EXTERNAL_IMAGE_DOWNLOAD_TIMEOUT_OVERRIDE.get())
            return 'saved.png', 'ok'

        runtime._download_external_image_url_once = once
        asyncio.run(runtime._download_external_image_url(
            'https://cdn.example.org/api/v1/gen/cached/test.png', session_key='test'))
        self.assertLessEqual(timeouts[0], 35)


if __name__ == '__main__':
    unittest.main()
