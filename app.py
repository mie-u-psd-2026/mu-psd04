from flask import Flask, request, jsonify, send_from_directory
from openai import OpenAI
import json
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

DATA_FILE = "data/user_state.json"
TIMEZONE = ZoneInfo("Asia/Tokyo")
TARGET_PARTS = {"chest", "arms", "back", "shoulders", "abs", "legs", "fullBody"}
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
OLLAMA_MODEL = "qwen2.5-coder:0.5b"


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
    # dataディレクトリが未作成の環境でも動作するようにしている
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
