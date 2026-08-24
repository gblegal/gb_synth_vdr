import synthvdr


def test_package_exposes_version():
    assert isinstance(synthvdr.__version__, str)
    assert synthvdr.__version__.count(".") == 2
