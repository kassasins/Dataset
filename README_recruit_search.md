# GitHub 採用候補者リサーチボット (`recruit_search.py`)

GitHub Search API を使い、**公開プロフィールと公開リポジトリ情報のみ**を収集して
採用候補者一覧を作成するスクリプトです。外部依存なし（Python 3 標準ライブラリのみ）。

## セットアップ

1. （推奨）[GitHub でパーソナルアクセサントークン (PAT) を作成](https://github.com/settings/tokens)
   （権限なしの public-only トークンで十分です）
2. 環境変数を設定:
   ```bash
   export GITHUB_TOKEN="ghp_xxxxxxxx"
   ```

> **トークンなしでも実行可能**（匿名モード）。ただし Search API 10 req/min・
> Read API 60 req/h に制限されるため、大量取得や定期実行には PAT の設定を推奨します。

## 使い方

```bash
# クエリ文字列を直接指定
python3 recruit_search.py --query "location:Japan language:TypeScript"

# 条件を分けて指定
python3 recruit_search.py --location Japan --language TypeScript --min-followers 10

# 件数・出力先・並び順の指定
python3 recruit_search.py -q "location:Osaka language:Python" --per-page 50 --sort stars -o osaka_py_devs

# リポジトリ情報をスキップして高速化
python3 recruit_search.py -q "location:Japan language:Go" --no-repos
```

実行すると `candidates.csv`（Excel で開きやすい UTF-8 BOM 付き）と
`candidates.json` に一覧が出力されます。

## 取得項目（すべて公開情報のみ）

| 列 | 内容 |
|---|---|
| username / login | 表示名・ログイン名 |
| public_email | **プロフィールに本人が公開している**メールのみ |
| bio / company / location | 自己紹介・会社・所在地 |
| followers / public_repos | フォロワー数・公開リポジトリ数 |
| profile_url | プロフィール URL |
| top_repos | スター数上位のリポジトリ（名前・言語・★数・説明） |

## レート制限への対応

- Search API（認証済み）: 30 リクエスト/分 → ページ間で自動待機
- Core API: ヘッダ `X-RateLimit-Remaining` / `Retry-After` を監視し自動で待機
- 取得上限: Search API の仕様に従い最大 1,000 件（`--max-pages` × `--per-page`）

## 注意事項（必ずお読みください）

- **非公開メールアドレスの収集は不可能であり、行わないでください。**
  GitHub API は本人が公開設定したメールしか返しません。コミットの git author
  メールから連絡先を「割り出す」ような手法は、GitHub の利用規約および
  プラットフォームポリシーに違反する可能性があります。本スクリプトもそのような
  処理は一切行いません。
- アウトリーチ（スカウトメール等）を送る場合は、日本の個人情報保護法・
  特定電子メール法（オプトイン規制）、各国の類似法制（GDPR 等）を遵守してください。
  原則として、公開プロフィールや LinkedIn など本人が連絡先公開を意図した経路へ、
  個別かつ誠実に連絡するのが適切です。
- 収集データは社内の個人情報管理規程に従って保管・破棄してください。
