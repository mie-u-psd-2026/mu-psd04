# テスト仕様書: duoMuscle

## 1. テスト対象
- `app.py`（Flask APIバックエンド：`/api/v1/progress`, `/api/v1/workout-plans`, 
  `/api/v1/workout-plans/today`, `/api/v1/workout-records`）
- データ永続化層（`load_state` / `save_state` / `data/user_state.json`）


## 2. テスト環境
- Windows 11
- Python 3.13.15
- Flask, openai, tzdata
- Ollama起動済み（`llama3.2:1b`）
- テスト前に `data/user_state.json` を削除し、初期状態から開始する


## 3. テスト項目

### 3.1 データ永続化層（単体テスト）

| No | テスト内容 | 手順 | 期待結果 |
|----|---|---|---|
| 1 | 初回読み込み | `data/user_state.json`が存在しない状態で`load_state()`を呼ぶ | `_initial_state()`と同じ内容（level=1, currentXp=0等）が返る |
| 2 | 保存後の再読み込み | `save_state()`で任意の状態を保存後、`load_state()`で読み込む | 保存内容と完全に一致する |
| 3 | dataディレクトリ未作成時の保存 | `data/`ディレクトリが存在しない状態で`save_state()`を呼ぶ | エラーにならず、ディレクトリごと自動作成されて保存される |

### 3.2 `GET /api/v1/progress`

| No | テスト内容 | 手順 | 期待結果 |
|----|---|---|---|
| 4 | 初期状態の取得 | 初期状態のままGETを呼ぶ | `level:1, currentXp:0, nextLevelXp:100, streak:0, completed:false` |
| 5 | 当日完了済みの反映 | `lastWorkoutDate`が今日の日付の状態でGETを呼ぶ | `completed:true` が返る |
| 6 | 前日完了の場合 | `lastWorkoutDate`が前日の状態でGETを呼ぶ | `completed:false`（当日はまだ実施していない扱い） |

### 3.3 `GET /api/v1/workout-plans/today`

| No | テスト内容 | 手順 | 期待結果 |
|----|---|---|---|
| 7 | メニュー未生成 | `todayWorkoutPlan`がnullの状態でGETを呼ぶ | `exists:false, workoutPlan:null` |
| 8 | 当日生成済み | `createdDate`が今日の`todayWorkoutPlan`がある状態でGETを呼ぶ | `exists:true`、`workoutPlan`に`completed`/`createdDate`を含まない形で内容が返る |
| 9 | 日付が変わった場合（境界値） | `createdDate`が前日の`todayWorkoutPlan`が残っている状態でGETを呼ぶ | `exists:false`（前日のメニューは無効扱い） |

### 3.4 `POST /api/v1/workout-plans`（メニュー生成）

**入力の同値分割・境界値**

| 項目 | 有効クラス | 無効クラス |
|---|---|---|
| targetPart | chest/arms/back/shoulders/abs/legs/fullBody | 未指定、空文字、リスト外の値（例:"foot"） |
| duration | 10/20/30/45/60 | 未指定、0、5（リスト外の中間値）、100 |

| No | テスト内容 | 手順 | 期待結果 |
|----|---|---|---|
| 10 | 正常系 | `{"targetPart":"chest","duration":20}`で送信 | 201、`workoutPlanId`が発行され、`exercises`が1件以上返る |
| 11 | targetPart未指定 | targetPartを省いて送信 | 400、`invalidParams`に`targetPart`が含まれる |
| 12 | targetPart不正値 | `"targetPart":"foot"`で送信 | 400、`invalidParams`に`targetPart`が含まれる |
| 13 | duration境界値（リスト外） | `"duration":5`で送信 | 400、`invalidParams`に`duration`が含まれる |
| 14 | duration境界値（0） | `"duration":0`で送信 | 400 |
| 15 | 同日の再生成（上書き確認） | 10を実行後、続けて別条件でもう一度POST | 新しい`workoutPlanId`が発行され、`/workout-plans/today`が新メニューを返す（8.2の上書き仕様） |
| 16 | AI通信失敗（モック使用） | Ollama呼び出し部分をモックし例外を発生させる | 503、`type`が`ai-unavailable` |
| 17 | AI出力がJSON以外（モック使用） | モックで「思考プロセス付きテキスト」を返す | `_extract_json_array`が例外を出し、503が返る（AI応答不良として扱われる） |
| 18 | AI出力にreps/seconds共存（モック使用） | モックで両方値ありのexerciseを返す | `_normalize_exercise`によりseconds側がnullに補正されて201が返る |
| 19 | AI出力が空配列（モック使用） | モックで`[]`を返す | 有効な種目が0件のため例外→503 |

### 3.5 `POST /api/v1/workout-records`（完了記録）

**デシジョンテーブル（404/409判定）**

| 条件 | ケース1 | ケース2 | ケース3 |
|---|---|---|---|
| todayWorkoutPlanが存在する | Y | Y | N |
| workoutPlanIdが一致する | Y | Y | - |
| plan.completedがfalse | Y | N | - |
| 結果 | 201成功 | 409 | 404 |

| No | テスト内容 | 手順 | 期待結果 |
|----|---|---|---|
| 20 | 正常系（レベルアップなし） | 3.4の10でメニュー生成後、正しいworkoutPlanIdで送信 | 201、`xpGained:50`、`levelUp:false` |
| 21 | プランID不一致 | 存在しないworkoutPlanIdで送信 | 404 |
| 22 | メニュー未生成の状態で送信 | todayWorkoutPlanがnullの状態で送信 | 404 |
| 23 | 完了済みへの重複完了 | 20を実行後、同じworkoutPlanIdで再送信 | 409 |
| 24 | レベルアップ境界値（ちょうど到達） | `currentXp:50, level:1`の状態で完了（合計100=閾値ちょうど） | `levelUp:true`、`currentXp:0`、`level:2` |
| 25 | レベルアップ境界値（1XP不足） | `currentXp:49, level:1`の状態で完了（合計99） | `levelUp:false`、`currentXp:99` |
| 26 | 複数レベルアップ（whileループ確認） | `currentXp:250, level:1`の状態で完了（合計300） | 100・200のレベルアップ閾値を2回超えるため`level:3`まで上がる |
| 27 | ストリーク初回 | `lastWorkoutDate:null`の状態で完了 | `streak:1` |
| 28 | ストリーク継続（境界値：前日） | `lastWorkoutDate`が前日の状態で完了 | `streak`が+1される |
| 29 | ストリーク同日（境界値） | `lastWorkoutDate`が今日の状態で完了（想定外だが念のため） | `streak`は変化しない |
| 30 | ストリークリセット（境界値：2日前） | `lastWorkoutDate`が2日前の状態で完了 | `streak:1`にリセットされる |


## 4. モックを使ったテスト方針

- Ollamaへの実通信は、CI環境や高速なテスト実行のためにモック化する
- `unittest.mock.patch`で`client.chat.completions.create`を差し替え、以下のパターンを個別に注入する：
  - 正常なJSON配列を返すケース
  - 例外（`ConnectionError`等）を発生させるケース
  - JSON以外のテキスト（思考プロセス混入）を返すケース
- モックテストとは別に、**実際のOllama（llama3.2:1b）に対する手動疎通確認を最低1回**は実施し、モックが実環境と乖離していないことを確認する


## 5. 既知の限界事項（テスト対象外・注記のみ）

- AIが生成する種目名の**意味的な正しさ**（実在する筋トレ種目か、日本語表記か）は、自動テストの対象外とする。1B級モデルの限界であり、プロンプト改善で緩和は試みているが完全な保証はできない
- レスポンス速度（非機能要件：AI応答10秒以内）は、ローカルCPU実行環境に強く依存するため、環境ごとに別途計測が必要


## 6. テスト実施結果
 
| No | 実施方法 | 結果 |
|----|---|---|
| 1  | pytest（test_app.py） | ✅ PASS |
| 2  | pytest（test_app.py） | ✅ PASS |
| 3  | pytest（test_app.py） | ✅ PASS |
| 4  | 手動（curl） | ✅ PASS |
| 5  | 手動（curl、状態ファイル手動編集） | ✅ PASS |
| 6  | 手動（curl、状態ファイル手動編集） | ✅ PASS |
| 7  | 手動（curl） | ✅ PASS |
| 8  | 手動（curl） | ✅ PASS |
| 9  | 手動（curl、状態ファイル手動編集） | ✅ PASS |
| 10 | 手動（curl） | ✅ PASS |
| 11 | 手動（curl） | ✅ PASS |
| 12 | 手動（curl） | ✅ PASS |
| 13 | 手動（curl） | ✅ PASS |
| 14 | 手動（curl） | ✅ PASS |
| 15 | 手動（curl） | ✅ PASS |
| 16 | pytest（test_app.py、モック） | ✅ PASS |
| 17 | pytest（test_app.py、モック） | ✅ PASS |
| 18 | pytest（test_app.py、モック） | ✅ PASS |
| 19 | pytest（test_app.py、モック） | ✅ PASS |
| 20 | 手動（curl） | ✅ PASS |
| 21 | 手動（curl） | ✅ PASS |
| 22 | 手動（curl） | ✅ PASS |
| 23 | 手動（curl） | ✅ PASS |
| 24 | 手動（curl、状態ファイル手動編集） | ✅ PASS |
| 25 | 手動（curl、状態ファイル手動編集） | ✅ PASS |
| 26 | 手動（curl、状態ファイル手動編集） | ✅ PASS |
| 27 | 手動（curl、状態ファイル手動編集） | ✅ PASS |
| 28 | 手動（curl、状態ファイル手動編集） | ✅ PASS |
| 29 | 手動（curl、状態ファイル手動編集） | ✅ PASS |
| 30 | 手動（curl、状態ファイル手動編集） | ✅ PASS |

**全30件、実施完了・全件PASS。**
