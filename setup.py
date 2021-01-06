from setuptools import setup

setup(
    name="clant",
    version="1.0.2",
    description="A unified frontend for clang linting tools",
    long_description=open('README.md', 'r').read(),
    long_description_content_type='text/markdown',
    url="https://gitlab.com/drobilla/clant",
    author="David Robillard",
    author_email="d@drobilla.net",
    license="ISC",
    packages=["clant"],
    entry_points={
        "console_scripts": [
            "clant = clant.clant:main",
        ],
    },
    install_requires=[],
    classifiers=[
        "Development Status :: 5 - Production/Stable",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: ISC License (ISCL)",
        "Operating System :: POSIX",
        "Programming Language :: C",
        "Programming Language :: C++",
        "Programming Language :: Objective C",
        "Programming Language :: Python :: 3.4",
        "Programming Language :: Python :: 3.5",
        "Programming Language :: Python :: 3.6",
        "Programming Language :: Python :: 3.7",
        "Programming Language :: Python :: 3.8",
    ],
)
