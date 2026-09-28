"""ツール本体の更新（docs/13 §3）。Maya 非依存（git の呼び出しと、版・CHANGELOG の解析）。

D-Drive の更新ウィンドウ（GitCliTagLister / GitTagListParser / UpdateCheckLogic / ChangelogRangeReader）と同じ考え方:
- 配布はリリースタグ vX.Y.Z だけ。main の途中のコミットは配布しない
- 今の版との差を PATCH / MINOR / MAJOR で示し、MAJOR は移行ガイドへ誘導する
- 「前の版に戻す」は 1 段の入れ替え（戻すともう一度押すと戻す前に戻る）
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import REPO_ROOT

Version = tuple[int, int, int]
_VERSION_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")
_TAG_REF_RE = re.compile(r"refs/tags/(v\d+\.\d+\.\d+)$")
_CHANGELOG_HEAD_RE = re.compile(r"^## \[(\d+\.\d+\.\d+)\]", re.M)
STATE_FILE = "tdrive-update.json"  # .git の中（ツール本体の導入ごと。リポジトリの履歴には入らない）
AUTO_CHECK_INTERVAL = 24 * 60 * 60  # 起動時の確認は 1 日 1 回
MAJOR_MIGRATION_DOC = "docs/migrations/v{major}.md"


# ---------------------------------------------------------------- 版
def parse_version(text: str) -> Version | None:
    m = _VERSION_RE.match(text.strip())
    return (int(m[1]), int(m[2]), int(m[3])) if m else None


def fmt(v: Version) -> str:
    return f"{v[0]}.{v[1]}.{v[2]}"


def bump_kind(current: Version, target: Version) -> str:
    """current → target の更新の大きさ: major / minor / patch / same / older。"""
    if target == current:
        return "same"
    if target < current:
        return "older"
    if target[0] != current[0]:
        return "major"
    if target[1] != current[1]:
        return "minor"
    return "patch"


def parse_ls_remote(text: str) -> list[Version]:
    """`git ls-remote --tags` の出力からリリースタグ（vX.Y.Z）だけを新しい順に。"""
    found = set()
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2 and (m := _TAG_REF_RE.search(parts[1])):
            v = parse_version(m[1])
            if v:
                found.add(v)
    return sorted(found, reverse=True)


# ---------------------------------------------------------------- CHANGELOG
def changelog_sections(text: str) -> list[tuple[Version, str]]:
    """`## [X.Y.Z] …` の節を（版, 本文）で。[Unreleased] は含めない。"""
    heads = list(_CHANGELOG_HEAD_RE.finditer(text))
    out = []
    for i, m in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        nxt = text.find("\n## ", m.end())
        if nxt != -1 and nxt < end:
            end = nxt
        v = parse_version(m[1])
        if v:
            out.append((v, text[m.start():end].strip()))
    return out


def changelog_between(text: str, current: Version, target: Version) -> list[tuple[Version, str]]:
    """current より新しく target 以下の節（新しい順）。更新ウィンドウの「変更点」。"""
    return sorted(((v, body) for v, body in changelog_sections(text) if current < v <= target), reverse=True)


def compat_notes(body: str) -> str:
    """節の `### 互換性` の本文。"""
    m = re.search(r"^### 互換性\s*$(.*?)(?=^### |\Z)", body, re.M | re.S)
    return m[1].strip() if m else ""


# ---------------------------------------------------------------- git
class GitError(RuntimeError):
    pass


@dataclass
class Git:
    repo: Path = REPO_ROOT
    timeout: float = 30.0

    def run(self, *args: str, timeout: float | None = None) -> str:
        env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="never", GIT_LFS_SKIP_SMUDGE="1")
        flags = 0x08000000 if sys.platform == "win32" else 0  # CREATE_NO_WINDOW: コンソールを出さない
        try:
            r = subprocess.run(
                ["git", "-C", str(self.repo), *args], capture_output=True, text=True, encoding="utf-8",
                errors="replace", env=env, timeout=timeout or self.timeout, creationflags=flags,
            )
        except FileNotFoundError as exc:
            raise GitError("git が見つかりません（Git for Windows を入れてください）") from exc
        except subprocess.TimeoutExpired as exc:
            raise GitError(f"git {args[0]} が時間切れ（ネットワーク・GitHub の権限を確認）") from exc
        if r.returncode != 0:
            raise GitError(f"git {' '.join(args)}: {r.stderr.strip() or r.stdout.strip()}")
        return r.stdout

    def is_repo(self) -> bool:
        try:
            return self.run("rev-parse", "--is-inside-work-tree").strip() == "true"
        except GitError:
            return False

    def remote_versions(self) -> list[Version]:
        return parse_ls_remote(self.run("ls-remote", "--tags", "origin"))

    def fetch_tags(self) -> None:
        self.run("fetch", "--tags", "--force", "origin", timeout=120)

    def current_tag(self) -> str | None:
        try:
            tag = self.run("describe", "--tags", "--exact-match", "HEAD").strip()
        except GitError:
            return None
        return tag if parse_version(tag) else None

    def branch(self) -> str | None:
        try:
            return self.run("symbolic-ref", "--quiet", "--short", "HEAD").strip() or None
        except GitError:
            return None  # detached（タグに固定）

    def local_changes(self) -> list[str]:
        """追跡しているファイルの変更（未追跡は数えない。__pycache__ など）。"""
        return [line[3:] for line in self.run("status", "--porcelain", "--untracked-files=no").splitlines() if line.strip()]

    def show(self, ref: str, path: str) -> str:
        return self.run("show", f"{ref}:{path}")

    def changed_files(self, a: str, b: str) -> list[str]:
        return [x for x in self.run("diff", "--name-only", a, b).splitlines() if x.strip()]

    def checkout(self, ref: str) -> None:
        self.run("-c", "advice.detachedHead=false", "checkout", "--quiet", ref, timeout=120)

    def git_dir(self) -> Path:
        d = Path(self.run("rev-parse", "--git-dir").strip())
        return d if d.is_absolute() else self.repo / d


# ---------------------------------------------------------------- 導入の種類と状態
def install_kind(git: Git) -> str:
    """release: リリースタグに固定・変更なし / dev: ブランチ上や変更あり（更新は git で）/ none: git 管理外。"""
    if not git.is_repo():
        return "none"
    if git.branch() is None and git.current_tag() and not git.local_changes():
        return "release"
    return "dev"


def read_state(git: Git) -> dict[str, Any]:
    try:
        return json.loads((git.git_dir() / STATE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError, GitError):
        return {}


def write_state(git: Git, **values: Any) -> dict[str, Any]:
    st = read_state(git)
    st.update(values)
    (git.git_dir() / STATE_FILE).write_text(json.dumps(st, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return st


def should_auto_check(state: dict[str, Any], now: float | None = None) -> bool:
    if not state.get("autoCheck", True):
        return False
    return (now or time.time()) - float(state.get("lastCheck", 0)) >= AUTO_CHECK_INTERVAL


# ---------------------------------------------------------------- 更新の実行
RESTART_FILES = ("maya/scripts/userSetup.py",)  # これが変わる更新は Maya の再起動が必要


def update_to(git: Git, tag: str) -> dict[str, Any]:
    """tag（vX.Y.Z）に更新する。前の版を記録し、再起動が必要かを返す。"""
    if install_kind(git) != "release":
        raise GitError("開発用の導入（ブランチ上・変更あり）は更新ウィンドウでは更新しません。git で更新してください")
    changes = git.local_changes()
    if changes:
        raise GitError("ツール本体に変更があります: " + ", ".join(changes[:5]))
    before = git.current_tag() or git.run("rev-parse", "HEAD").strip()
    git.fetch_tags()
    changed = git.changed_files(before, tag)
    git.checkout(tag)
    write_state(git, previous=before)
    return {"from": before, "to": tag, "restart": any(f in changed for f in RESTART_FILES), "changed": changed}


def rollback(git: Git) -> dict[str, Any]:
    """記録した前の版に戻す（1 段の入れ替え）。"""
    prev = read_state(git).get("previous")
    if not prev:
        raise GitError("戻せる前の版の記録がありません")
    return update_to(git, prev)


def update_mod_version(text: str, version: str) -> str:
    """TDriveToon.mod の 1 行目（+ MAYAVERSION:… TDriveToon <版> <場所>）の版を書き換える。"""
    return re.sub(r"^(\+ .*?TDriveToon )\S+( )", lambda m: f"{m[1]}{version}{m[2]}", text, count=1, flags=re.M)


def mod_path(user_app_dir: str | Path | None = None) -> Path:
    """TDriveToon.mod の場所。Maya からは cmds.internalVar(userAppDir=True)（ドキュメントの移動に追従）を渡す。"""
    base = Path(user_app_dir) if user_app_dir else Path(os.path.expanduser("~")) / "Documents" / "maya"
    return base / "modules" / "TDriveToon.mod"


def installed_version(repo: Path = REPO_ROOT) -> str:
    try:
        return (repo / "VERSION").read_text(encoding="utf-8").strip()
    except OSError:
        return "0.0.0"
