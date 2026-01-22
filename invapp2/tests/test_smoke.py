from flask.cli import routes_command

from invapp import create_app


def _make_app():
    return create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})


def test_app_factory_smoke():
    app = _make_app()
    assert app is not None


def test_blueprints_registered():
    app = _make_app()
    for name in ["auth", "inventory", "mdi"]:
        assert name in app.blueprints


def test_critical_routes_return_ok():
    app = _make_app()
    client = app.test_client()

    login_response = client.get("/auth/login")
    assert login_response.status_code == 200

    root_response = client.get("/", follow_redirects=False)
    assert root_response.status_code in {200, 302}

    inventory_response = client.get("/inventory/", follow_redirects=False)
    assert inventory_response.status_code in {200, 302}


def test_flask_routes_listed():
    app = _make_app()
    runner = app.test_cli_runner()
    result = runner.invoke(routes_command)
    assert result.exit_code == 0
