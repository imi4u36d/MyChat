"""Bridge to the companion WeChat export tool.

The project deliberately does not vendor WeChat decryption. Set WECHAT_TOOL
to a directory that exposes ``chats``, ``config`` and ``image_decode``.
``wechat_send`` is optional and only needed for reply automation.
"""

from __future__ import annotations

import sys

from .settings import WECHAT_TOOL

TOOL = str(WECHAT_TOOL)
if TOOL not in sys.path:
    sys.path.insert(0, TOOL)

try:
    import chats  # type: ignore  # noqa: F401,E402
    import config  # type: ignore  # noqa: F401,E402
    import image_decode  # type: ignore  # noqa: F401,E402
except ModuleNotFoundError as exc:  # pragma: no cover - environment error
    raise SystemExit(
        f"找不到微信导出工具模块（{exc.name}）。\n"
        f"请把 WECHAT_TOOL 指向包含 chats.py / config.py / image_decode.py 的目录。\n"
        f"当前 WECHAT_TOOL={TOOL}"
    ) from exc

try:
    import wechat_send  # type: ignore  # noqa: E402
except ImportError:  # optional feature
    wechat_send = None
