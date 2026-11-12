#!/usr/bin/env python3
"""
採用候補者リサーチ用ボット (recruit_search.py)

GitHub Search API を使った、公開情報のみに基づく技術者検索ツール。

使い方:
    # 環境変数でトークンを指定（推奨）
    export GITHUB_TOKEN="ghp_..."        # 個人アクセサントークン（必須）

    # 検索クエリを直接指定
    python recruit_search.py --query "location:Japan language:TypeScript"

    # 条件を分けて指定（上記と同じクエリになる）
    python recruit_search.py --location Japan --language TypeScript --min-followers 10

    # 出力先・件数を指定
    python recruit_search.py --query "location:Japan language:Rust" --per-page 50 -o rust_devs

取得する情報は GitHub の「公開プロフィール」および「公開リポジトリ情報」のみです:
    - ユーザー名 / ログイン名 / 公開メールアドレス / 自己紹介 / 会社名 / ロケーション
    - リポジトリ名・説明・言語・スター数
    - フォロワー数 / 公開リポジトリ数

プライバシー上の注意:
    - メールアドレスはユーザー自身がプロフィールに公開しているものだけを収集します。
      GitHub API は非公開メールを返さないため、このツールで裏技的に取得することはできませんし、
      そのような試みも禁止されています。
    - 収集した候補者へ連絡する際は、各国の個人情報保護法制（日本の個人情報保護法など）
      および GitHub の利用規約・スパム規制を遵守してください。
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

API_BASE = "https://api.github.com"
DEFAULT_PER_PAGE = 30
MAX_PAGES = 10  # Search API は結果の先頭1000件までしか取得できないため


# ---------------------------------------------------------------------------
# GitHub API クライアント（標準ライブラリのみ使用）
# ---------------------------------------------------------------------------
@dataclass
class GitHubClient:
    token: str
    request_count: int = 0

    def _request(self, url: str) -> dict[str, Any]:
        """GET リクエスト送信。レート制限を尊重して必要に応じて待機する。"""
        req = Request(
            url,
            headers={
                # トークン未指定時は匿名リクエスト（Read API 60 req/h、Search 10 req/min）
                **({"Authorization": f"Bearer {self.token}"} if self.token else {}),
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "recruit-search-bot",
            },
        )
        while True:
            try:
                with urlopen(req, timeout=30) as resp:
                    self.request_count += 1
                    remaining = resp.headers.get("X-RateLimit-Remaining")
                    reset = resp.headers.get("X-RateLimit-Reset")
                    body = json.loads(resp.read().decode("utf-8"))
                    # Search API: 認証済みで 30 req/min。残りが尽きたらリセットまで待機
                    if remaining is not None and int(remaining) == 0 and reset:
                        wait = max(int(reset) - int(time.time()) + 2, 2)
                        print(f"  [レート制限] {wait} 秒待機...", file=sys.stderr)
                        time.sleep(wait)
                    return body
            except HTTPError as e:
                if e.code in (403, 429):  # レート制限 / 二次制限
                    retry_after = e.headers.get("Retry-After")
                    reset = e.headers.get("X-RateLimit-Reset")
                    if retry_after:
                        wait = int(retry_after) + 1
                    elif reset:
                        wait = max(int(reset) - int(time.time()) + 2, 5)
                    else:
                        wait = 60
                    print(f"  [レート制限 {e.code}] {wait} 秒待機...", file=sys.stderr)
                    time.sleep(wait)
                    continue
                if e.code == 401:
                    raise SystemExit("エラー: トークンが無効です (401)。GITHUB_TOKEN を確認してください。")
                if e.code == 422:
                    raise SystemExit(f"エラー: クエリが不正です (422): {url}")
                raise SystemExit(f"エラー: HTTP {e.code} ({url})")
            except URLError as e:
                raise SystemExit(f"エラー: ネットワーク接続失敗: {e.reason}")

    def search_users(self, query: str, per_page: int, max_pages: int) -> list[dict]:
        items: list[dict] = []
        for page in range(1, max_pages + 1):
            qs = urlencode({"q": query, "per_page": per_page, "page": page})
            data = self._request(f"{API_BASE}/search/users?{qs}")
            batch = data.get("items", [])
            total = data.get("total_count", 0)
            items.extend(batch)
            print(f"  ユーザー検索 ページ{page}: {len(batch)} 件取得（合計 {total} 件中）",
                  file=sys.stderr)
            if len(items) >= total or not batch:
                break
            time.sleep(2.5)  # Search API: 約30req/min → 安全マージン
        return items

    def get_user(self, login: str) -> Optional[dict]:
        try:
            return self._request(f"{API_BASE}/users/{login}")
        except SystemExit:
            return None

    def list_repos(self, login: str, sort: str, per_page: int = 5) -> list[dict]:
        qs = urlencode({"sort": sort, "per_page": per_page})
        try:
            return self._request(f"{API_BASE}/users/{login}/repos?{qs}") or []
        except SystemExit:
            return []


# ---------------------------------------------------------------------------
# 結果モデル
# ---------------------------------------------------------------------------
@dataclass
class Candidate:
    login: str
    name: str = ""
    email: str = ""          # プロフィールに公開されているメールのみ
    bio: str = ""
    company: str = ""
    location: str = ""
    html_url: str = ""
    followers: int = 0
    public_repos: int = 0
    top_repos: list[dict] = field(default_factory=list)

    def repos_summary(self, limit: int = 3) -> str:
        parts = []
        for r in self.top_repos[:limit]:
            desc = (r.get("description") or "").replace("\n", " ")[:80]
            lang = r.get("language") or "-"
            parts.append(f"{r['full_name']} [{lang}, ★{r.get('stargazers_count', 0)}]"
                         + (f": {desc}" if desc else ""))
        return " | ".join(parts)


def build_query(args: argparse.Namespace) -> str:
    if args.query:
        return args.query
    parts = []
    if args.location:
        parts.append(f"location:{args.location}")
    if args.language:
        parts.append(f"language:{args.language}")
    if args.min_repos:
        parts.append(f"public_repos:>={args.min_repos}")
    if args.min_followers:
        parts.append(f"followers:>={args.min_followers}")
    if not parts:
        parts.append("location:Japan language:TypeScript")  # 例示デフォルト
    return " ".join(parts)


def collect(client: GitHubClient, query: str, args: argparse.Namespace) -> list[Candidate]:
    print(f"検索クエリ: {query!r}", file=sys.stderr)
    raw = client.search_users(query=query, per_page=args.per_page, max_pages=args.max_pages)
    candidates: list[Candidate] = []
    for i, u in enumerate(raw, 1):
        login = u.get("login", "")
        if not login:
            continue
        print(f"[{i}/{len(raw)}] @{login} を取得中...", file=sys.stderr)
        detail = client.get_user(login) or {}
        cand = Candidate(
            login=login,
            name=detail.get("name") or "",
            email=detail.get("email") or "",  # 公開プロフィールのメールのみ
            bio=detail.get("bio") or "",
            company=detail.get("company") or "",
            location=detail.get("location") or "",
            html_url=detail.get("html_url") or f"https://github.com/{login}",
            followers=detail.get("followers", u.get("followers", 0)),
            public_repos=detail.get("public_repos", 0),
        )
        if args.with_repos:
            cand.top_repos = client.list_repos(login, sort="stars", per_page=args.repos_per_user)
        candidates.append(cand)
        time.sleep(0.5)  # Core API への過剰アクセス防止
    return candidates


def write_outputs(candidates: list[Candidate], args: argparse.Namespace) -> None:
    base = args.output
    csv_path = base + ".csv"
    json_path = base + ".json"

    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["username", "login", "public_email", "bio", "company",
                    "location", "followers", "public_repos", "profile_url", "top_repos"])
        for c in candidates:
            w.writerow([c.name, c.login, c.email, c.bio, c.company,
                        c.location, c.followers, c.public_repos, c.html_url,
                        c.repos_summary(args.repos_per_user)])

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump([{
            "name": c.name, "login": c.login, "public_email": c.email,
            "bio": c.bio, "company": c.company, "location": c.location,
            "followers": c.followers, "public_repos": c.public_repos,
            "profile_url": c.html_url,
            "top_repos": [{"full_name": r["full_name"], "description": r.get("description"),
                           "language": r.get("language"),
                           "stars": r.get("stargazers_count", 0)} for r in c.top_repos],
        } for c in candidates], f, ensure_ascii=False, indent=2)

    emails = sum(1 for c in candidates if "@" in c.email)
    print(f"\n完了: {len(candidates)} 件の候補者を取得しました。")
    print(f"  CSV : {csv_path}")
    print(f"  JSON: {json_path}")
    print(f"  ※ プロフィールに公開メールを持つ候補: {emails} 名"
          "（非公開メールの取得は不可能であり対象外です）")


def main() -> None:
    p = argparse.ArgumentParser(description="GitHub 公開プロフィール検索ボット（採用リサーチ用）")
    p.add_argument("--query", "-q", help="Search API クエリ文字列（例: 'location:Japan language:TypeScript'）")
    p.add_argument("--location", help="location 条件（例: Japan）")
    p.add_argument("--language", help="language 条件（例: TypeScript）")
    p.add_argument("--min-repos", type=int, default=0, help="公開リポジトリ数の下限")
    p.add_argument("--min-followers", type=int, default=0, help="フォロワー数の下限")
    p.add_argument("--sort", default="followers", choices=["followers", "stars", "repositories", "updated"],
                   help="検索結果の並び順")
    p.add_argument("--order", default="desc", choices=["desc", "asc"])
    p.add_argument("--per-page", type=int, default=DEFAULT_PER_PAGE, choices=range(1, 101), metavar="1-100")
    p.add_argument("--max-pages", type=int, default=1, help=f"取得ページ数の上限（最大 {MAX_PAGES}）")
    p.add_argument("--with-repos", action="store_true", default=True,
                   help="各候補者の上位リポジトリも取得（既定: ON）")
    p.add_argument("--no-repos", dest="with_repos", action="store_false",
                   help="リポジトリ情報をスキップ（高速化）")
    p.add_argument("--repos-per-user", type=int, default=3, help="1人あたり取得するリポジトリ数")
    p.add_argument("--output", "-o", default="candidates", help="出力ファイル名のベース（拡張子除く）")
    p.add_argument("--token", default=os.environ.get("GITHUB_TOKEN", ""),
                   help="GitHub パーソナルアクセサントークン（既定: 環境変数 GITHUB_TOKEN）")
    args = p.parse_args()

    if not args.token:
        # トークンなしでも匿名で実行可能（Search 10 req/min, Read 60 req/h の制限あり）
        print(
            "ヒント: GITHUB_TOKEN が未設定のため匿名モードで実行します"
            "（Search 10 req/min・Read 60 req/h に制限されます）。"
            "大量取得は export GITHUB_TOKEN=\"ghp_...\" を推奨します。",
            file=sys.stderr,
        )
    args.max_pages = min(args.max_pages, MAX_PAGES)

    client = GitHubClient(token=args.token)
    query = build_query(args)
    q = f"{query} sort:{args.sort} order:{args.order}" if args.sort != "followers" else query
    candidates = collect(client, q, args)
    if not candidates:
        print("該当する候補者がいませんでした。クエリ条件を見直してください。")
        return
    write_outputs(candidates, args)


if __name__ == "__main__":
    main()
