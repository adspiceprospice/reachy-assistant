"""Pytest configuration for path setup."""

import os
import sys
import types
import importlib.util
import importlib.machinery
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_PATH = PROJECT_ROOT / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))


# Make tests reproducible by ignoring machine-specific profile/tool env config.
# Without this, importing config during test collection can pick up a developer's
# local .env and fail before tests run.
os.environ["REACHY_MINI_SKIP_DOTENV"] = "1"
os.environ.pop("REACHY_MINI_CUSTOM_PROFILE", None)
os.environ.pop("REACHY_MINI_EXTERNAL_PROFILES_DIRECTORY", None)
os.environ.pop("REACHY_MINI_EXTERNAL_TOOLS_DIRECTORY", None)


def _install_fastrtc_stub_if_needed() -> None:
    """Provide the tiny fastrtc surface used by unit tests when unavailable."""
    try:
        import fastrtc  # type: ignore

        required = ("AdditionalOutputs", "AsyncStreamHandler", "audio_to_int16", "audio_to_float32", "wait_for_item")
        if all(hasattr(fastrtc, name) for name in required):
            return
    except Exception:
        pass

    fastrtc_stub = types.ModuleType("fastrtc")
    fastrtc_stub.__spec__ = importlib.machinery.ModuleSpec("fastrtc", None)

    class _AdditionalOutputs:
        def __init__(self, *args) -> None:
            self.args = args

    class _AsyncStreamHandler:
        def __init__(self, **kwargs) -> None:
            for key, value in kwargs.items():
                setattr(self, key, value)

    async def _wait_for_item(queue):
        return await queue.get()

    fastrtc_stub.AdditionalOutputs = _AdditionalOutputs
    fastrtc_stub.AsyncStreamHandler = _AsyncStreamHandler
    fastrtc_stub.audio_to_int16 = lambda value: value
    fastrtc_stub.audio_to_float32 = lambda value: value
    fastrtc_stub.wait_for_item = _wait_for_item
    sys.modules["fastrtc"] = fastrtc_stub


def _install_optional_runtime_stubs() -> None:
    """Stub optional robot/UI dependencies that are not needed for unit tests."""
    if importlib.util.find_spec("cv2") is None:
        cv2_stub = types.ModuleType("cv2")
        cv2_stub.__spec__ = importlib.machinery.ModuleSpec("cv2", None)
        cv2_stub.COLOR_BGR2RGB = 0
        cv2_stub.cvtColor = lambda image, _code: image
        sys.modules["cv2"] = cv2_stub

    if importlib.util.find_spec("gradio") is None:
        gradio_stub = types.ModuleType("gradio")
        gradio_stub.__spec__ = importlib.machinery.ModuleSpec("gradio", None)

        class _Image:
            def __init__(self, value=None) -> None:
                self.value = value

        gradio_stub.Image = _Image
        sys.modules["gradio"] = gradio_stub

    if importlib.util.find_spec("openai") is None:
        openai_stub = types.ModuleType("openai")
        openai_stub.__spec__ = importlib.machinery.ModuleSpec("openai", None)
        openai_stub.AsyncOpenAI = object
        sys.modules["openai"] = openai_stub

    if importlib.util.find_spec("websockets") is None:
        websockets_stub = types.ModuleType("websockets")
        websocket_exceptions_stub = types.ModuleType("websockets.exceptions")
        websockets_stub.__spec__ = importlib.machinery.ModuleSpec("websockets", None)
        websocket_exceptions_stub.__spec__ = importlib.machinery.ModuleSpec("websockets.exceptions", None)

        class _ConnectionClosedError(Exception):
            pass

        websocket_exceptions_stub.ConnectionClosedError = _ConnectionClosedError
        sys.modules["websockets"] = websockets_stub
        sys.modules["websockets.exceptions"] = websocket_exceptions_stub

    if importlib.util.find_spec("reachy_mini") is None:
        reachy_mini_stub = types.ModuleType("reachy_mini")
        reachy_mini_stub.__spec__ = importlib.machinery.ModuleSpec("reachy_mini", None)
        reachy_mini_stub.ReachyMini = object
        media_module = types.ModuleType("reachy_mini.media")
        media_manager_module = types.ModuleType("reachy_mini.media.media_manager")
        media_module.__spec__ = importlib.machinery.ModuleSpec("reachy_mini.media", None)
        media_manager_module.__spec__ = importlib.machinery.ModuleSpec("reachy_mini.media.media_manager", None)

        class _MediaBackend:
            GSTREAMER = "gstreamer"
            DEFAULT = "default"
            DEFAULT_NO_VIDEO = "default_no_video"

        media_manager_module.MediaBackend = _MediaBackend
        sys.modules["reachy_mini"] = reachy_mini_stub
        sys.modules["reachy_mini.media"] = media_module
        sys.modules["reachy_mini.media.media_manager"] = media_manager_module


_install_fastrtc_stub_if_needed()
_install_optional_runtime_stubs()
