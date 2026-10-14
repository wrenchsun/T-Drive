"""create_tool が例外を投げるテスト用の外部ツール。"""


def create_tool():
    raise RuntimeError("create_tool failed on purpose")
