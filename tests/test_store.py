"""SQLite 会话持久化单元测试。"""

import pandas as pd

import store


def test_save_load_and_list(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "test.db")
    store.init_db()

    df = pd.DataFrame({"a": [1, 2]})
    store.save_session(
        "s1",
        "x.csv",
        df,
        df.copy(),
        [{"role": "user", "content": "hi"}],
        [{"desc": "去重", "code": "df = df.drop_duplicates()"}],
    )

    loaded = store.load_session("s1")
    assert loaded["filename"] == "x.csv"
    assert len(loaded["df"]) == 2
    assert loaded["history"][0]["content"] == "hi"
    assert loaded["steps"][0]["desc"] == "去重"

    sessions = store.list_sessions()
    assert len(sessions) == 1
    assert sessions[0]["session_id"] == "s1"
