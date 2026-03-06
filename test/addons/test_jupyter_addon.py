"""Tests für JupyterAddOn — ExecutionResult, Client (gemockt), Lifecycle, Tool-Interface."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from addons.jupyter import JupyterAddOn, ExecutionResult
from addons.jupyter.addon import JupyterClient, _parse_execute_response


# =============================================================================
# ExecutionResult
# =============================================================================


def test_execution_result_success():
    r = ExecutionResult(stdout="hello")
    assert r.success is True


def test_execution_result_error():
    r = ExecutionResult(error="NameError: x")
    assert r.success is False


def test_as_text_stdout():
    r = ExecutionResult(stdout="42\n")
    assert "42" in r.as_text()


def test_as_text_stderr():
    r = ExecutionResult(stderr="warning\n")
    assert "stderr" in r.as_text()


def test_as_text_error():
    r = ExecutionResult(error="NameError: x")
    assert "NameError" in r.as_text()


def test_as_text_empty():
    r = ExecutionResult()
    assert r.as_text() == "(kein Output)"


def test_as_text_image_output():
    r = ExecutionResult(outputs=[{"type": "image", "format": "png"}])
    assert "Bild" in r.as_text()


def test_as_text_rich_output():
    r = ExecutionResult(outputs=[{"type": "text", "content": "DataFrame..."}])
    assert "DataFrame" in r.as_text()


# =============================================================================
# _parse_execute_response
# =============================================================================


def test_parse_stdout():
    data = {"outputs": [{"output_type": "stream", "name": "stdout", "text": "hello\n"}]}
    r = _parse_execute_response(data)
    assert "hello" in r.stdout


def test_parse_stderr():
    data = {"outputs": [{"output_type": "stream", "name": "stderr", "text": "warn\n"}]}
    r = _parse_execute_response(data)
    assert "warn" in r.stderr


def test_parse_execute_result():
    data = {"outputs": [{"output_type": "execute_result", "data": {"text/plain": "42"}}]}
    r = _parse_execute_response(data)
    assert r.outputs[0]["content"] == "42"


def test_parse_error():
    data = {"outputs": [{"output_type": "error", "ename": "NameError", "evalue": "x", "traceback": []}]}
    r = _parse_execute_response(data)
    assert r.success is False
    assert "NameError" in r.error


def test_parse_image_png():
    data = {"outputs": [{"output_type": "display_data", "data": {"image/png": "base64..."}}]}
    r = _parse_execute_response(data)
    assert r.outputs[0]["type"] == "image"


def test_parse_execution_count():
    data = {"outputs": [], "execution_count": 5}
    r = _parse_execute_response(data)
    assert r.execution_count == 5


# =============================================================================
# JupyterClient — gemockt
# =============================================================================


@pytest.mark.asyncio
async def test_client_execute_success():
    client = JupyterClient(url="http://localhost:8888", token="tok")
    client._kernel_id = "kernel-1"

    mock_response = MagicMock()
    mock_response.json.return_value = {
        "outputs": [{"output_type": "stream", "name": "stdout", "text": "hello\n"}],
        "execution_count": 1,
    }
    mock_response.raise_for_status = MagicMock()

    import httpx
    client._client = AsyncMock(spec=httpx.AsyncClient)
    client._client.post = AsyncMock(return_value=mock_response)

    result = await client.execute("print('hello')")
    assert result.stdout == "hello\n"


@pytest.mark.asyncio
async def test_client_execute_no_kernel():
    client = JupyterClient(url="http://localhost:8888", token="tok")
    result = await client.execute("x = 1")
    assert result.success is False
    assert "Kernel" in result.error


# =============================================================================
# JupyterAddOn — Lifecycle
# =============================================================================


@pytest.mark.asyncio
async def test_on_attach_creates_client():
    addon = JupyterAddOn(url="http://localhost:8888", token="tok")
    heinzel = MagicMock()
    heinzel.addons.get = MagicMock(return_value=None)
    await addon.on_attach(heinzel)
    assert addon._client is not None
    await addon.on_detach(heinzel)


@pytest.mark.asyncio
async def test_on_detach_stops_client():
    addon = JupyterAddOn()
    mock_client = MagicMock()
    mock_client.stop = AsyncMock()
    addon._client = mock_client

    heinzel = MagicMock()
    await addon.on_detach(heinzel)
    mock_client.stop.assert_called_once()
    assert addon._client is None


@pytest.mark.asyncio
async def test_execute_without_init():
    addon = JupyterAddOn()
    result = await addon.execute("x = 1")
    assert result.success is False


# =============================================================================
# JupyterAddOn — execute via gemocktem Client
# =============================================================================


@pytest.mark.asyncio
async def test_execute_starts_kernel_if_needed():
    addon = JupyterAddOn(kernel="python3")
    mock_client = MagicMock()
    mock_client.kernel_id = None
    mock_client.start = AsyncMock()
    mock_client.execute = AsyncMock(return_value=ExecutionResult(stdout="ok"))
    addon._client = mock_client

    result = await addon.execute("print('ok')")
    mock_client.start.assert_called_once_with("python3")
    assert result.stdout == "ok"


@pytest.mark.asyncio
async def test_restart_kernel():
    addon = JupyterAddOn(kernel="python3")
    mock_client = MagicMock()
    mock_client.stop = AsyncMock()
    mock_client.start = AsyncMock()
    addon._client = mock_client

    ok = await addon.restart_kernel()
    assert ok is True
    mock_client.stop.assert_called_once()
    mock_client.start.assert_called_once_with("python3")


# =============================================================================
# Tool-Interface
# =============================================================================


@pytest.mark.asyncio
async def test_tool_execute_returns_text():
    addon = JupyterAddOn()
    mock_client = MagicMock()
    mock_client.kernel_id = "k1"
    mock_client.execute = AsyncMock(return_value=ExecutionResult(stdout="42\n"))
    addon._client = mock_client

    result = await addon._tool_execute({"code": "print(42)"})
    assert "42" in result


@pytest.mark.asyncio
async def test_tool_execute_no_code():
    addon = JupyterAddOn()
    result = await addon._tool_execute({})
    assert "Kein Code" in result


@pytest.mark.asyncio
async def test_on_attach_registers_tool():
    addon = JupyterAddOn()
    router = MagicMock()
    router.register_local_handler = AsyncMock()
    heinzel = MagicMock()
    heinzel.addons.get = MagicMock(return_value=router)

    await addon.on_attach(heinzel)
    router.register_local_handler.assert_called_once()
    call_args = router.register_local_handler.call_args
    # address ist erstes positional oder keyword arg
    address = call_args.args[0] if call_args.args else call_args.kwargs.get("address", "")
    assert address == "local:jupyter:execute_code"
    addon._client = None  # verhindere stop()-Aufruf auf echtem Client
    await addon.on_detach(heinzel)
