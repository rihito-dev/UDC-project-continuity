# UDC Project Continuity Study

[![check](https://github.com/rihito-dev/UDC-project-continuity/actions/workflows/check.yml/badge.svg)](https://github.com/rihito-dev/UDC-project-continuity/actions/workflows/check.yml)

Urban Data Challenge（UDC）の受賞プロジェクトについて、**受賞後も公開情報から追跡できるか**を調べる小規模な再現可能調査です。

2014–2025年のUDC公式受賞ページを起点に、公式ページで確認できる公開URL・GitHub・Wayback Machineなどをたどり、現在の公開状態を記録しています。

> この調査はプロジェクトの「成功／失敗」を評価するものではありません。  
> `unknown` は「終了した」という意味ではなく、公開情報だけでは確認できなかったことを表します。

## Snapshot

2026-08-31 に取得・確認したスナップショットでは、**317件の受賞記録**を収録しています。

| 指標 | 件数 |
|---|---:|
| 受賞記録 | 317 |
| 明示的な公開URLあり | 68 |
| GitHub URLあり | 5 |
| `reachable_related` | 27 |
| `unreachable` | 13 |
| `reachable_unrelated` | 12 |
| `redirected_domain` | 3 |
| `unknown` | 262 |

317件は「ユニークなプロジェクト数」ではなく、受賞ページから抽出した**受賞記録数**です。同じ作品が複数の賞として掲載されている場合は複数行になることがあります。

2026-09-23 補正: 1件のページタイトルが文字化け（UTF-8をMacRomanとして誤読）していたため、再取得せずにタイトルを復元し、同じ判定ルールで `reachable_unrelated` → `reachable_related` に更新しました。原因となったデコード処理も修正済みです。

## Research question

**公開された受賞プロジェクトは、その後どの程度、公開情報だけで追跡可能なのか？**

単純な生存率を出すのではなく、以下を分離して記録します。

- 公式受賞ページに公開URLが明示されていたか
- 現在そのURLへ到達できるか
- ページ内容が当該プロジェクトと関連しているか
- 別ドメインへ移動しているか
- Wayback Machine に履歴があるか
- GitHubが明示されている場合、最終コミット日を確認できるか

## Method

### 1. 受賞記録の収集

UDC 2014–2025 の公式受賞ページから、年・賞・部門・作品名・チーム名などを抽出します。

### 2. URLの採用ルール

URLは、次のような**公式に紐づけを確認できる情報源**だけから採用します。

- UDC公式受賞ページ
- UDC公式アーカイブ
- UDC公式一次選考結果ページ
- UDC公式ページからリンクされた公開候補者シート

検索エンジンの推測結果や、作品との対応を公式情報で確認できないURLは採用しません。

### 3. 現在状態の確認

確認時には `robots.txt` を尊重し、ログインやアクセス制限の回避は行いません。

| status | この調査での意味 |
|---|---|
| `reachable_related` | HTTP 200で、ページ本文またはタイトルから作品との関連を確認できた |
| `unreachable` | 404/410、または5xxが再確認でも継続した |
| `reachable_unrelated` | HTTP 200だが、現在のページから作品との関連を確認できなかった |
| `redirected_domain` | 別ドメインへリダイレクトされた |
| `unknown` | URL未掲載、robots制限、ログイン要求、判定不能など |

これらは**URLの観測状態**であり、団体・サービス・活動そのものの存続を直接表しません。

## Repository

```text
data/
  udc_projects.csv        受賞記録と現在状態のメインデータ
  udc_url_candidates.csv  公式補助資料から行ったURL再探索の記録
  README.md               データ項目と注意事項

scripts/
  collect_udc_projects.py 公式受賞ページの収集と公開状態確認
  explore_udc_urls.py     URL未掲載案件の公式資料内での再探索
  summarize_udc.py        CSVのスナップショット集計
  check_public_data.py    公開CSVの最小化とREADME件数の整合チェック（CIで実行）

requirements.txt
README.md
```

## Reproduce

Python 3.10+ を想定しています。

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
```

まず少数件で確認します。

```bash
python3 scripts/collect_udc_projects.py \
  --limit 5 \
  --output data/udc_projects_test5.csv
```

全件を収集します。

```bash
python3 scripts/collect_udc_projects.py \
  --output data/udc_projects.csv
```

公式補助資料から、URL未掲載案件を追加探索します。

```bash
python3 scripts/explore_udc_urls.py \
  --input data/udc_projects.csv \
  --output data/udc_projects.csv \
  --candidates-output data/udc_url_candidates.csv
```

現在のCSVを集計するだけならネットワークアクセスは不要です。

```bash
python3 scripts/summarize_udc.py data/udc_projects.csv
```

## Limitations

- 公開URLが公式ページに掲載されていない案件が多く、`unknown` が多数を占めます。
- URLが消えていても、プロジェクト自体が別の名称・媒体・組織内で継続している可能性があります。
- `reachable_unrelated` は「失敗」を意味しません。元URLの現在内容と作品の関連を確認できなかった、という観測です。
- Wayback Machine の収録有無はアーカイブ側の取得状況にも左右されます。
- GitHubの最終コミット日は、公式情報からGitHubリポジトリを特定できた案件だけを対象にしています。
- 本調査は記述的な観測であり、プロジェクト継続の原因を推定するものではありません。

## Public-data minimization

公開版では、研究上不要な個人名や第三者ページ本文の再配布を避けるため、チーム名・代表者名、本文抜粋、原因推定欄は保存しません。判定に必要なページ内容は実行時にのみ参照し、CSVには出典URL、HTTP状態、ページタイトル、Wayback情報など最小限のメタデータを残します。

状態名はプロジェクトそのものではなく、**確認時点のURLの観測状態**だけを表します。

## Data provenance

各行に `source_url` と `checked_at` を保持し、どの公式情報を起点に、いつ確認したかを追跡できるようにしています。URL再探索についても `data/udc_url_candidates.csv` に採用・未発見を含めて記録します。

詳しい列定義は [data/README.md](data/README.md) を参照してください。

## Scope

このリポジトリは、公開情報を使った調査方法・データ収集・再現性を示すための研究用プロジェクトです。MIT License は、このリポジトリで独自に作成したコードと文書に適用します。第三者のプロジェクト名、URL、ページタイトル等の元情報に対する権利を主張したり、第三者情報をMITで再許諾したりするものではありません。詳細は [DATA_AND_ATTRIBUTION.md](DATA_AND_ATTRIBUTION.md) を参照してください。
