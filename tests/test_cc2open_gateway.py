import importlib.util
import pathlib
import sys
import tempfile
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "src" / "cc2open_gateway.py"


def load_gateway_module():
    spec = importlib.util.spec_from_file_location("cc2open_gateway_test", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class GatewayToolSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.gateway = load_gateway_module()
        cls.config = cls.gateway.Config(
            host="127.0.0.1",
            port=8787,
            openai_base_url="https://api.openai.com",
            openai_chat_path="/v1/chat/completions",
            openai_api_key="test-key",
            openai_model="gpt-5.4",
            reasoning_effort="xhigh",
            timeout_seconds=600,
            stream_ping_interval=5,
            stream_idle_timeout=15,
            post_finish_grace_timeout=5,
            debug=False,
            debug_pet="0",
        )

    def build_request(self, tools):
        return self.gateway.build_openai_request(
            self.config,
            {
                "model": "claude-sonnet-4-6",
                "max_tokens": 512,
                "messages": [{"role": "user", "content": "test"}],
                "tools": tools,
            },
        )

    def test_web_search_missing_schema_uses_fallback(self):
        request = self.build_request(
            [{"name": "web_search", "description": "Search the web"}]
        )

        schema = request["tools"][0]["function"]["parameters"]
        self.assertEqual(schema["type"], "object")
        self.assertEqual(schema["required"], ["query"])
        self.assertEqual(
            sorted(schema["properties"]),
            ["allowed_domains", "blocked_domains", "query"],
        )
        self.assertFalse(schema["additionalProperties"])

    def test_web_fetch_missing_schema_uses_fallback(self):
        request = self.build_request(
            [{"name": "WebFetch", "description": "Fetch a URL"}]
        )

        schema = request["tools"][0]["function"]["parameters"]
        self.assertEqual(schema["type"], "object")
        self.assertEqual(schema["required"], ["url", "prompt"])
        self.assertEqual(sorted(schema["properties"]), ["prompt", "url"])
        self.assertFalse(schema["additionalProperties"])

    def test_custom_object_schema_is_preserved(self):
        request = self.build_request(
            [
                {
                    "name": "custom_tool",
                    "description": "Custom tool",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string"},
                            "mode": {"type": "string"},
                        },
                        "required": ["path"],
                    },
                }
            ]
        )

        schema = request["tools"][0]["function"]["parameters"]
        self.assertEqual(
            schema,
            {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "mode": {"type": "string"},
                },
                "required": ["path"],
            },
        )

    def test_unknown_tool_without_schema_is_ignored(self):
        request = self.build_request(
            [{"name": "mystery_tool", "description": "Unknown tool"}]
        )

        self.assertNotIn("tools", request)

    def test_invalid_required_entries_are_removed(self):
        request = self.build_request(
            [
                {
                    "name": "custom_tool",
                    "description": "Custom tool",
                    "input_schema": {
                        "properties": {
                            "path": {"type": "string"},
                            "mode": {"type": "string"},
                        },
                        "required": ["path", "missing", 123],
                    },
                }
            ]
        )

        schema = request["tools"][0]["function"]["parameters"]
        self.assertEqual(schema["type"], "object")
        self.assertEqual(schema["required"], ["path"])


class GatewayRuntimeConfigTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.gateway = load_gateway_module()

    def make_config(self):
        return self.gateway.Config(
            host="127.0.0.1",
            port=8787,
            openai_base_url="https://airouter.service.itstudio.club/v1",
            openai_chat_path="/v1/chat/completions",
            openai_api_key="test-key-123456",
            openai_model="gpt-5.4",
            reasoning_effort="xhigh",
            timeout_seconds=600,
            stream_ping_interval=5,
            stream_idle_timeout=15,
            post_finish_grace_timeout=5,
            debug=False,
            debug_pet="0",
        )

    def test_runtime_store_updates_and_persists(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = pathlib.Path(temp_dir) / ".cc2open_state.json"
            store = self.gateway.RuntimeConfigStore(self.make_config(), state_path)

            updated = store.update(
                openai_base_url="https://geek.tm2.xin/v1/",
                openai_api_key="sk-new-abcdef",
            )

            self.assertEqual(updated.openai_base_url, "https://geek.tm2.xin/v1")
            self.assertEqual(updated.openai_api_key, "sk-new-abcdef")

            saved = self.gateway.json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["openai_base_url"], "https://geek.tm2.xin/v1")
            self.assertEqual(saved["openai_api_key"], "sk-new-abcdef")

    def test_parse_runtime_command_supports_url_and_apikey(self):
        self.assertEqual(
            self.gateway.parse_runtime_command("url https://geek.tm2.xin/v1"),
            ("url", "https://geek.tm2.xin/v1"),
        )
        self.assertEqual(
            self.gateway.parse_runtime_command("set apikey sk-abc"),
            ("apikey", "sk-abc"),
        )
        self.assertEqual(self.gateway.parse_runtime_command("show"), ("show", None))
        self.assertEqual(
            self.gateway.parse_runtime_command("something else"),
            ("unknown", "something else"),
        )


if __name__ == "__main__":
    unittest.main()
