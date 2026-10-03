"""転送モジュール: `tdrive.lifecycle` に移った（F0-1）。既存の import（tdrive_toon.lifecycle）を保つため、同じモジュールオブジェクトを返す。

モジュールの状態（登録した後片付け・キャッシュなど）を 1 つだけにするため、コピーでなく sys.modules を差し替える。
"""

import sys

from tdrive import lifecycle as _module

sys.modules[__name__] = _module
