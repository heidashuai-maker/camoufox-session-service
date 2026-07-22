from pathlib import Path


def test_container_uses_init_process_before_xvfb_run():
    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")

    assert "    tini" in dockerfile
    assert 'ENTRYPOINT ["tini", "--"]' in dockerfile
    assert 'CMD ["xvfb-run"' in dockerfile


def test_compose_does_not_add_a_second_init_process():
    compose = Path("compose.yaml").read_text(encoding="utf-8")

    assert "init: true" not in compose


if __name__ == "__main__":
    test_container_uses_init_process_before_xvfb_run()
