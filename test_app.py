import json
from unittest.mock import patch, MagicMock

import pytest

import app as app_module


@pytest.fixture
def client(tmp_path, monkeypatch):
    # 実データ(data/user_state.json)を汚さないよう、テストごとに一時ファイルへ差し替える
    data_file = tmp_path / "user_state.json"
    monkeypatch.setattr(app_module, "DATA_FILE", str(data_file))
    app_module.app.config["TESTING"] = True
    return app_module.app.test_client()


def _mock_chat_response(content: str):
    # OpenAIクライアントのレスポンス構造を模したモックを作る
    mock_message = MagicMock()
    mock_message.content = content
    mock_choice = MagicMock()
    mock_choice.message = mock_message
    mock_completion = MagicMock()
    mock_completion.choices = [mock_choice]
    return mock_completion


# --- No.1-3: データ永続化層 ---------------------------------------------

def test_load_state_returns_initial_when_file_missing(tmp_path, monkeypatch):
    # No.1: ファイル未生成時は初期状態が返る
    data_file = tmp_path / "user_state.json"
    monkeypatch.setattr(app_module, "DATA_FILE", str(data_file))

    state = app_module.load_state()

    assert state == app_module._initial_state()


def test_save_then_load_round_trip(tmp_path, monkeypatch):
    # No.2: 保存した内容が、そのまま再読み込みできる
    data_file = tmp_path / "user_state.json"
    monkeypatch.setattr(app_module, "DATA_FILE", str(data_file))
    original = app_module._initial_state()
    original["level"] = 5
    original["currentXp"] = 42

    app_module.save_state(original)
    loaded = app_module.load_state()

    assert loaded == original


def test_save_state_creates_missing_directory(tmp_path, monkeypatch):
    # No.3: dataディレクトリが存在しなくてもエラーにならず保存できる
    data_file = tmp_path / "nested" / "dir" / "user_state.json"
    monkeypatch.setattr(app_module, "DATA_FILE", str(data_file))

    app_module.save_state(app_module._initial_state())

    assert data_file.exists()


# --- No.16-19: AIモックテスト --------------------------------------------

def test_create_plan_ai_connection_failure_returns_503(client):
    # No.16: AI呼び出しが例外を投げた場合、503が返る
    with patch.object(
        app_module.client.chat.completions, "create",
        side_effect=ConnectionError("mock connection failure"),
    ):
        response = client.post(
            "/api/v1/workout-plans",
            json={"targetPart": "chest", "duration": 20},
        )

    assert response.status_code == 503
    assert response.get_json()["type"].endswith("ai-service-unavailable")


def test_create_plan_non_json_output_returns_503(client):
    # No.17: 思考プロセス混じりのテキストが返ってきた場合、JSON抽出に失敗し503になる
    thinking_text = "Thinking...\n色々考えました。\n...done thinking.\n結論として上腕を鍛えましょう。"
    with patch.object(
        app_module.client.chat.completions, "create",
        return_value=_mock_chat_response(thinking_text),
    ):
        response = client.post(
            "/api/v1/workout-plans",
            json={"targetPart": "arms", "duration": 20},
        )

    assert response.status_code == 503


def test_create_plan_normalizes_reps_and_seconds_conflict(client):
    # No.18: reps/secondsが両方指定された種目は、repsを優先しseconds側がnullに補正される
    raw_exercises = json.dumps([
        {"name": "腕立て伏せ", "reps": 10, "seconds": 30, "sets": 3},
    ])
    with patch.object(
        app_module.client.chat.completions, "create",
        return_value=_mock_chat_response(raw_exercises),
    ):
        response = client.post(
            "/api/v1/workout-plans",
            json={"targetPart": "chest", "duration": 20},
        )

    assert response.status_code == 201
    exercise = response.get_json()["exercises"][0]
    assert exercise["reps"] == 10
    assert exercise["seconds"] is None


def test_create_plan_empty_exercise_list_returns_503(client):
    # No.19: AIが空配列を返した場合、有効な種目が0件のため503になる
    with patch.object(
        app_module.client.chat.completions, "create",
        return_value=_mock_chat_response("[]"),
    ):
        response = client.post(
            "/api/v1/workout-plans",
            json={"targetPart": "legs", "duration": 30},
        )

    assert response.status_code == 503
