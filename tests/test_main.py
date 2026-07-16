from camoufox_service.__main__ import main


def test_main_uses_configured_host_and_port(monkeypatch):
    called = {}
    monkeypatch.setenv("HOST", "127.0.0.1")
    monkeypatch.setenv("PORT", "3100")
    monkeypatch.setattr("uvicorn.run", lambda app, **options: called.update(app=app, **options))

    main()

    assert called == {
        "app": "camoufox_service.app:app",
        "host": "127.0.0.1",
        "port": 3100,
    }
