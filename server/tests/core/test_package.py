from importlib.metadata import version

import aibi


def test_version_comes_from_the_package_metadata() -> None:
    assert aibi.__version__ == version("aibi")
