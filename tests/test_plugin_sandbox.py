"""Tests for PluginSandbox — subprocess-based plugin execution isolation."""

from selfheal.plugins.sandbox import PluginSandbox


class TestPluginSandboxMissingFile:
    def test_missing_file(self, tmp_path):
        sandbox = PluginSandbox()
        result = sandbox.execute(tmp_path / "nonexistent.py")
        assert result["success"] is False
        assert "not found" in result["error"]


class TestPluginSandboxNormalExecution:
    def test_execute_normal_plugin(self, tmp_path):
        plugin_file = tmp_path / "good_plugin.py"
        plugin_file.write_text("""def run(**kwargs):
    return {"answer": 42, "msg": "ok"}
""")
        sandbox = PluginSandbox()
        result = sandbox.execute(plugin_file, func_name="run", args={})
        assert result["success"] is True
        assert result["result"] == {"answer": 42, "msg": "ok"}

    def test_execute_with_args(self, tmp_path):
        plugin_file = tmp_path / "echo.py"
        plugin_file.write_text("""def run(x=0, name=""):
    return {"x": x, "name": name}
""")
        sandbox = PluginSandbox()
        result = sandbox.execute(plugin_file, args={"x": 7, "name": "cc"})
        assert result["success"] is True
        assert result["result"] == {"x": 7, "name": "cc"}


class TestPluginSandboxIntegrity:
    def test_integrity_fail(self, tmp_path):
        plugin_file = tmp_path / "plugin.py"
        plugin_file.write_text('def run(): return {"ok": True}')
        sandbox = PluginSandbox()
        result = sandbox.execute(plugin_file, expected_checksum="deadbeef" * 8)
        assert result["success"] is False
        assert result["error"] == "Integrity check failed"

    def test_integrity_pass(self, tmp_path):
        plugin_file = tmp_path / "plugin.py"
        plugin_file.write_text('def run(): return {"ok": True}')
        actual = PluginSandbox._compute_sha256(plugin_file)
        sandbox = PluginSandbox()
        result = sandbox.execute(plugin_file, expected_checksum=actual)
        assert result["success"] is True


class TestPluginSandboxTimeout:
    def test_timeout(self, tmp_path):
        plugin_file = tmp_path / "slow.py"
        plugin_file.write_text("""
def run():
    import time
    time.sleep(99)
    return {}
""")
        sandbox = PluginSandbox(timeout=1)
        result = sandbox.execute(plugin_file)
        assert result["success"] is False
        assert "timed out" in result["error"].lower()


class TestPluginSandboxComputeSha256:
    def test_deterministic(self, tmp_path):
        plugin_file = tmp_path / "test.py"
        plugin_file.write_text("print(1)\n")
        h1 = PluginSandbox._compute_sha256(plugin_file)
        h2 = PluginSandbox._compute_sha256(plugin_file)
        assert h1 == h2
        assert len(h1) == 64
        assert all(c in "0123456789abcdef" for c in h1)

    def test_different_files_different_hash(self, tmp_path):
        f1 = tmp_path / "a.py"
        f1.write_text("x = 1\n")
        f2 = tmp_path / "b.py"
        f2.write_text("x = 2\n")
        assert PluginSandbox._compute_sha256(f1) != PluginSandbox._compute_sha256(f2)


class TestPluginSandboxSubprocessError:
    def test_non_zero_exit_code(self, tmp_path):
        plugin_file = tmp_path / "exit_bad.py"
        plugin_file.write_text("""
def run():
    import sys
    sys.exit(1)
""")
        sandbox = PluginSandbox(timeout=5)
        result = sandbox.execute(plugin_file)
        assert result["success"] is False

    def test_plugin_with_empty_args(self, tmp_path):
        plugin_file = tmp_path / "noargs.py"
        plugin_file.write_text("""
def run():
    return {"ok": True}
""")
        sandbox = PluginSandbox()
        result = sandbox.execute(plugin_file)
        assert result["success"] is True
        assert result["result"] == {"ok": True}


class TestPluginSandboxErrorHandling:
    def test_plugin_raises_exception(self, tmp_path):
        plugin_file = tmp_path / "broken.py"
        plugin_file.write_text("""
def run():
    raise RuntimeError("oops")
""")
        sandbox = PluginSandbox()
        result = sandbox.execute(plugin_file)
        assert result["success"] is False
        assert "oops" in result.get("error", "")

    def test_plugin_missing_function(self, tmp_path):
        plugin_file = tmp_path / "nofunc.py"
        plugin_file.write_text("x = 1\n")
        sandbox = PluginSandbox()
        result = sandbox.execute(plugin_file, func_name="nonexistent_func")
        assert result["success"] is False
