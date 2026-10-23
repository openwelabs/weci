"""向后兼容别名包。

``weci_sdk`` 是 WeCi Plugin SDK 的正式公开包名。此 ``sdk`` 包仅为兼容历史代码
与已有插件而保留，等同于::

    from weci_sdk import *

新插件请使用 ``from weci_sdk import ...``。
"""

from weci_sdk import *  # noqa: F401,F403
from weci_sdk import __version__  # noqa: F401
