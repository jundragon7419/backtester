"""패키지 설치·import 확인."""


def test_import_package():
    import krx_backtester.data.krx_client  # noqa: F401
