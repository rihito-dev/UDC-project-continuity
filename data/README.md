# Data dictionary

## `udc_projects.csv`

UDC公式受賞ページを起点に作成した受賞記録と、公開URLの確認結果です。

主な列:

| column | meaning |
|---|---|
| `year` | 受賞年度 |
| `award` | 賞名 |
| `category` | 部門 |
| `project_name` | 作品名 |
| `team` | チーム名・代表者名（公式ページにある場合） |
| `source_url` | 受賞記録の公式出典 |
| `primary_url` | 公式情報から確認できた公開URL |
| `github_url` | 公式情報から確認できたGitHubリポジトリ |
| `url_status` | URLの観測状態 |
| `http_status` | 確認時のHTTP status |
| `page_title_now` | 確認時のページタイトル |
| `evidence_snippet` | 確認時に得た短い本文断片 |
| `wayback_first` | Wayback Machineで確認できた最古日 |
| `wayback_last` | Wayback Machineで確認できた最新日 |
| `wayback_count` | CDX検索で得たHTMLスナップショット件数 |
| `github_last_commit` | GitHubが確認できた場合の最終コミット日時 |
| `checked_at` | 現在状態を確認した日時 |
| `notes` | 判定理由・取得上の注意 |

`cause_of_death` と `cause_resolved` は、現行調査では原因推定を行わないため空欄です。

## Status definitions

- `reachable_related`: HTTP 200かつ作品との関連を本文・タイトルから確認
- `unreachable`: 404/410、または5xxが再確認でも継続
- `reachable_unrelated`: HTTP 200だが作品との関連を確認できない
- `redirected_domain`: 別ドメインへリダイレクト
- `unknown`: URL未掲載、robots制限、ログイン要求、その他判定不能

この分類は**URLの状態**です。プロジェクトや活動そのものの存続・成功・失敗を意味しません。

## `udc_url_candidates.csv`

受賞ページにURLがない案件について、UDC公式アーカイブや公式一次選考結果、公式ページからリンクされた公開候補者シートから再探索した記録です。

採用された候補だけでなく、候補なし・保留も残し、探索過程を追跡できるようにしています。

## Reuse note

このリポジトリのスクリプト・説明文と、CSV内に記録された第三者の名称・URL・ページ由来情報は同一の権利状態とは限りません。元情報の利用条件は各出典を確認してください。


## Privacy and minimization

公開版では、調査目的に不要な個人名・代表者名・チーム名と、第三者ページ本文の抜粋を保存しません。該当列が残っている場合も公開CSVでは空欄です。URL状態はプロジェクト自体の存続・成功・失敗を意味しません。
