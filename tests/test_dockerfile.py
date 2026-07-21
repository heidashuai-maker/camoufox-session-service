from pathlib import Path


def test_container_uses_init_process_before_xvfb_run():
    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")

    assert "    tini" in dockerfile
    assert 'ENTRYPOINT ["tini", "--"]' in dockerfile
    assert 'CMD ["xvfb-run"' in dockerfile


if __name__ == "__main__":
    test_container_uses_init_process_before_xvfb_run()
