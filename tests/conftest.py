import pytest

from app import create_app


@pytest.fixture()
def app(tmp_path):
    app = create_app({"TESTING": True, "DATA_DIR": tmp_path, "DATABASE": tmp_path / "t.db",
                      "UPLOAD_DIR": tmp_path / "up", "OUTPUT_DIR": tmp_path / "out",
                      "SCHEDULER_ENABLED": False, "ANTHROPIC_API_KEY": ""})
    yield app


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def logged(client, app):
    client.post("/premier-lancement", data={"name": "Admin", "email": "a@ex.fr", "password": "motdepasse1"})
    return client
