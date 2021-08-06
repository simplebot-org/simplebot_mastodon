"""Setup module installation."""

import os
import re

from setuptools import find_packages, setup

if __name__ == "__main__":
    MODULE_NAME = "simplebot_mastodon"
    DESC = "A plugin for SimpleBot, a Delta Chat(http://delta.chat/) bot"

    init_file = os.path.join(MODULE_NAME, "__init__.py")
    with open(init_file) as fh:
        version = re.search(r"__version__ = \'(.*?)\'", fh.read(), re.M).group(1)

    with open("README.rst") as fh:
        long_description = fh.read()
    with open("CHANGELOG.rst") as fh:
        long_description += fh.read()
    with open("LICENSE") as fh:
        long_description += fh.read()

    setup(
        name=MODULE_NAME,
        version=version,
        description=DESC,
        long_description=long_description,
        long_description_content_type="text/x-rst",
        keywords="simplebot plugin deltachat",
        license="MPL",
        classifiers=[
            "Development Status :: 3 - Alpha",
            "Environment :: Plugins",
            "Programming Language :: Python :: 3",
            "License :: OSI Approved :: Mozilla Public License 2.0 (MPL 2.0)",
            "Operating System :: OS Independent",
            "Topic :: Utilities",
        ],
        zip_safe=False,
        include_package_data=True,
        packages=find_packages(),
        install_requires=[
            "simplebot",
            "Mastodon.py",
            "html2text",
            "beautifulsoup4",
            "requests",
            "pydub",
        ],
        entry_points={
            "simplebot.plugins": "{0} = {0}".format(MODULE_NAME),
        },
    )
