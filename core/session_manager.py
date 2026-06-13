"""Session file management utilities."""

import os


def get_session_path(sessions_dir: str, phone: str) -> str | None:
    safe = phone.replace("+", "").replace(" ", "")
    path = os.path.join(sessions_dir, f"{safe}.session")
    return path if os.path.exists(path) else None


def delete_session_file(sessions_dir: str, phone: str) -> bool:
    path = get_session_path(sessions_dir, phone)
    if path and os.path.exists(path):
        os.remove(path)
        return True
    return False


def list_session_files(sessions_dir: str) -> list[str]:
    if not os.path.isdir(sessions_dir):
        return []
    return [
        f.replace(".session", "")
        for f in os.listdir(sessions_dir)
        if f.endswith(".session") and not f.startswith("_tmp_")
    ]
