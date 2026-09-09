from flask import Flask, request, jsonify, send_from_directory
from openai import OpenAI
import json
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import re

DATA_FILE = "data/user_state.json"
TIMEZONE = ZoneInfo("Asia/Tokyo")
TARGET_PARTS = {"chest", "arms", "back", "shoulders", "abs", "legs", "fullBody"}
TARGET_PART_LABELS = {
    "chest": "胸",
    "arms": "腕",
    "back": "背中",
    "shoulders": "肩",
    "abs": "腹筋",
    "legs": "脚",
    "fullBody": "全身",
}
DURATIONS = {10, 20, 30, 45, 60}
XP_PER_WORKOUT = 50

app = Flask(__name__)

if app.debug:
    @app.after_request
    def add_header(response):
        if request.endpoint == 'static':
            response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
            response.headers['Pragma'] = 'no-cache'
            response.headers['Expires'] = '0'
        return response


client = OpenAI(
    base_url="http://localhost:11434/v1",
    api_key="ollama",
)
# OLLAMA_MODEL = "qwen3.5:0.8b"     # 回答不能
# OLLAMA_MODEL = "qwen2.5:1.5b"     # 応答時間許容範囲内、日本語出力不安定
OLLAMA_MODEL = "llama3.2:1b"        # 応答時間 qwen2.5:1.5b と同等、日本語出力安定

def _initial_state() -> dict:
    # 初期状態定義
    return {
        "level": 1,
        "currentXp": 0,
        "streak": 0,
        "lastWorkoutDate": None,
        "nextWorkoutPlanId": 1,
        "nextWorkoutRecordId": 1,
        "todayWorkoutPlan": None,
        "workoutHistory": [],
    }


def load_state() -> dict:
    # 初回起動時などファイル未生成の場合に KeyError とならないよう、初期状態を返す
    if not os.path.exists(DATA_FILE):
        return _initial_state()
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_state(state: dict) -> None:
    # data ディレクトリが未作成の環境でも動作するようにしている
    os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def get_today():
    # サーバーの実行環境によらず日付境界をJSTに統一するため固定タイムゾーンを使用
    return datetime.now(TIMEZONE).date()


def next_level_xp(level: int) -> int:
    return level * 100


def _error_response(status: int, title: str, detail: str, invalid_params=None):
    # 仕様書12章「APIエラー仕様」に従う
    body = {
        "type": f"https://duomuscle.example/errors/{title.lower().replace(' ', '-')}",
        "title": title,
        "status": status,
        "detail": detail,
    }
    if invalid_params:
        body["invalidParams"] = invalid_params
    return jsonify(body), status


@app.route('/api/v1/progress', methods=['GET'])
def get_progress():
    state = load_state()
    today = get_today().isoformat()
    completed_today = state["lastWorkoutDate"] == today
    return jsonify({
        "level": state["level"],
        "currentXp": state["currentXp"],
        "nextLevelXp": next_level_xp(state["level"]),
        "streak": state["streak"],
        "trainedToday": completed_today,
    })


@app.route('/api/v1/workout-plans/today', methods=['GET'])
def get_today_workout():
    state = load_state()
    plan = state["todayWorkoutPlan"]
    today = get_today().isoformat()

    if plan is None or plan["createdDate"] != today:
        return jsonify({"exists": False, "completed": False, "workoutPlan": None})

    workout_plan = {k: v for k, v in plan.items() if k not in ("completed", "createdDate")}
    return jsonify({
        "exists": True,
        "completed": plan["completed"],
        "workoutPlan": workout_plan,
    })


def _validate_workout_request(data: dict) -> list:
    invalid_params = []
    if data.get("targetPart") not in TARGET_PARTS:
        invalid_params.append({"name": "targetPart", "reason": "鍛えたい部位を選択してください。"})
    if data.get("duration") not in DURATIONS:
        invalid_params.append({"name": "duration", "reason": "運動時間を選択してください。"})
    return invalid_params


def _build_menu_prompt(target_part: str, duration: int) -> str:
    part_names = {
        "chest": "胸",
        "arms": "腕",
        "back": "背中",
        "shoulders": "肩",
        "abs": "腹筋",
        "legs": "脚",
        "fullBody": "全身",
    }

    target_name = part_names.get(target_part, target_part)

    return (
        f"{target_part}を{duration}分間鍛える筋トレメニューを、"
        "JSON配列のみで出力してください。実在する一般的な筋トレ種目のみ使用すること。"
        "種目名（nameフィールド）は必ず日本語で出力し、英語表記は使用しないこと。"
        "例：[{\"name\": \"腕立て伏せ\", \"reps\": 15, \"seconds\": null, \"sets\": 3}]"
        '各要素は {"name": 種目名, "reps": 回数またはnull, '
        '"seconds": 秒数またはnull, "sets": セット数} の形式にしてください。'
        "説明文やコードブロック記号は一切含めないでください。"
        "/no_think"     # 思考プロセスの出力を抑制
    )


def _extract_json_array(text: str) -> str:
    # 小型モデルは /no_think 指示があっても前置き文を付けることがあるため、
    # 応答文字列から最初の [ 〜最後の ] までを抽出
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if match is None:
        raise ValueError("応答にJSON配列が含まれていません")
    return match.group(0)


def _normalize_exercise(exercise: dict) -> dict | None:
    # AIの出力が仕様書11章2節の「レスポンス項目」に違反することがあるため、
    # reps/seconds の排他性と必須項目の欠落を補正
    name = exercise.get("name")
    reps = exercise.get("reps")
    seconds = exercise.get("seconds")
    sets = exercise.get("sets")

    if not name or (reps is None and seconds is None):
        return None

    if reps is not None and seconds is not None:
        seconds = None  # 多く使われている reps 優先

    return {
        "name": name,
        "reps": reps,
        "seconds": seconds,
        "sets": sets if sets is not None else 3,  # 筋トレ初心者向けに規定値3セット
    }


def _normalize_exercises(exercises: list) -> list:
    normalized = [_normalize_exercise(e) for e in exercises]
    return [e for e in normalized if e is not None]


def _generate_exercises(prompt: str) -> list:
    chat_completion = client.chat.completions.create(
        messages=[{"role": "user", "content": prompt}],
        model=OLLAMA_MODEL,
    )
    raw_text = chat_completion.choices[0].message.content
    exercises = json.loads(_extract_json_array(raw_text))
    normalized = _normalize_exercises(exercises)
    if not normalized:
        raise ValueError("有効な種目が1件も生成されませんでした")
    return normalized


def _build_new_plan(state: dict, target_part: str, duration: int, exercises: list) -> dict:
    plan_id = state["nextWorkoutPlanId"]
    label = TARGET_PART_LABELS[target_part]
    return {
        "workoutPlanId": plan_id,
        "title": f"{label}{duration}分トレーニング",
        "targetPart": target_part,
        "duration": duration,
        "exercises": exercises,
        "completed": False,
        "createdDate": get_today().isoformat(),
    }


def _to_public_plan(plan: dict) -> dict:
    # completed/createdDateはサーバー内部専用のフィールドなのでレスポンスから除外
    return {k: v for k, v in plan.items() if k not in ("completed", "createdDate")}


def _generate_plan_exercises(target_part: str, duration: int) -> list:
    prompt = _build_menu_prompt(target_part, duration)
    return _generate_exercises(prompt)


def _create_and_save_plan(target_part: str, duration: int, exercises: list) -> dict:
    state = load_state()
    plan = _build_new_plan(state, target_part, duration, exercises)
    state["todayWorkoutPlan"] = plan
    state["nextWorkoutPlanId"] = plan["workoutPlanId"] + 1
    save_state(state)
    return plan


@app.route('/api/v1/workout-plans', methods=['POST'])
def create_workout_plan():
    data = request.get_json(silent=True) or {}

    invalid_params = _validate_workout_request(data)
    if invalid_params:
        return _error_response(400, "Invalid Parameter", "入力内容に誤りがあります。", invalid_params)

    target_part = data["targetPart"]
    duration = data["duration"]

    try:
        exercises = _generate_plan_exercises(target_part, duration)
    except Exception as e:
        app.logger.error(f"Workout plan generation failed: {e}")
        return _error_response(
            503, "AI Service Unavailable",
            "AIとの通信に失敗しました。時間をおいて再度お試しください。"
        )

    plan = _create_and_save_plan(target_part, duration, exercises)
    return jsonify(_to_public_plan(plan)), 201


def _find_matching_plan(state: dict, workout_plan_id):
    plan = state["todayWorkoutPlan"]
    if plan is None or plan["workoutPlanId"] != workout_plan_id:
        return None
    return plan


def _calculate_streak(state: dict, today) -> int:
    last_date_str = state["lastWorkoutDate"]
    if last_date_str is None:
        return 1

    last_date = datetime.fromisoformat(last_date_str).date()
    if last_date == today:
        return state["streak"]
    if last_date == today - timedelta(days=1):
        return state["streak"] + 1
    return 1


def _apply_xp(state: dict, xp_gained: int) -> tuple:
    current_xp = state["currentXp"] + xp_gained
    level = state["level"]
    level_up = False

    # 1回のトレーニングで複数レベル上がる可能性を考慮しwhileで判定
    while current_xp >= next_level_xp(level):
        current_xp -= next_level_xp(level)
        level += 1
        level_up = True

    return current_xp, level, level_up


def _build_record(state: dict, plan: dict, today_str: str) -> dict:
    return {
        "workoutRecordId": state["nextWorkoutRecordId"],
        "date": today_str,
        "targetPart": plan["targetPart"],
        "duration": plan["duration"],
        "exercises": plan["exercises"],
        "xpGained": XP_PER_WORKOUT,
    }


def _persist_completion(state, plan, current_xp, level, new_streak, today_str, record):
    plan["completed"] = True
    state["todayWorkoutPlan"] = plan
    state["currentXp"] = current_xp
    state["level"] = level
    state["streak"] = new_streak
    state["lastWorkoutDate"] = today_str
    state["workoutHistory"].insert(0, record)
    state["workoutHistory"] = state["workoutHistory"][:30]
    state["nextWorkoutRecordId"] = record["workoutRecordId"] + 1
    save_state(state)


def _complete_workout(state: dict, plan: dict) -> dict:
    today = get_today()
    today_str = today.isoformat()
    new_streak = _calculate_streak(state, today)
    current_xp, level, level_up = _apply_xp(state, XP_PER_WORKOUT)
    previous_level = state["level"]
    record = _build_record(state, plan, today_str)

    _persist_completion(state, plan, current_xp, level, new_streak, today_str, record)

    return {
        "record": record,
        "currentXp": current_xp,
        "level": level,
        "levelUp": level_up,
        "previousLevel": previous_level,
        "streak": new_streak,
    }


def _build_completion_message(level_up: bool) -> str:
    return (
        "レベルアップおめでとう！この調子で続けていきましょう！" if level_up
        else "トレーニングお疲れさま！この調子で続けていきましょう！"
    )


def _build_completion_response(result: dict) -> dict:
    record = result["record"]
    return {
        "workoutRecordId": record["workoutRecordId"],
        "xpGained": record["xpGained"],
        "currentXp": result["currentXp"],
        "level": result["level"],
        "levelUp": result["levelUp"],
        "previousLevel": result["previousLevel"],
        "streak": result["streak"],
        "message": _build_completion_message(result["levelUp"]),
    }


@app.route('/api/v1/workout-records', methods=['POST'])
def create_workout_record():
    data = request.get_json(silent=True) or {}
    state = load_state()

    plan = _find_matching_plan(state, data.get("workoutPlanId"))
    if plan is None:
        return _error_response(404, "Resource Not Found", "指定された筋トレメニューが見つかりませんでした。")
    if plan["completed"]:
        return _error_response(409, "Workout Already Completed", "この筋トレはすでに完了しています。")

    result = _complete_workout(state, plan)
    return jsonify(_build_completion_response(result)), 201


@app.route('/')
def index():
    return send_from_directory(app.static_folder, 'index.html')


@app.route('/send_api', methods=['POST'])
def send_api():
    data = request.get_json()

    if not data or 'text' not in data:
        app.logger.error("Request JSON is missing or does not contain 'text' field.")
        return jsonify({"error": "Missing 'text' in request body"}), 400

    received_text = data['text']
    if not received_text.strip():
        app.logger.error("Received text is empty or whitespace.")
        return jsonify({"error": "Input text cannot be empty"}), 400

    system_prompt = "140字以内で回答してください。"
    if 'context' in data and data['context'] and data['context'].strip():
        system_prompt = data['context'].strip()
        app.logger.info(f"Using custom system prompt from context: {system_prompt}")
    else:
        app.logger.info(f"Using default system prompt: {system_prompt}")

    try:
        chat_completion = client.chat.completions.create(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": received_text}
            ],
            model=OLLAMA_MODEL,
        )

        if chat_completion.choices and chat_completion.choices[0].message:
            processed_text = chat_completion.choices[0].message.content
        else:
            processed_text = "AIから有効な応答がありませんでした。"

        return jsonify({"message": "AIによってデータが処理されました。", "processed_text": processed_text})

    except Exception as e:
        app.logger.error(f"Ollama API call failed: {e}")
        return jsonify({"error": f"AIサービスとの通信中にエラーが発生しました。"}), 500


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
