# pylock attestations

<!--- BADGES: START --->
[![CI](https://github.com/trailofbits/pylock-attestations/actions/workflows/tests.yml/badge.svg)](https://github.com/trailofbits/pylock-attestations/actions/workflows/tests.yml)
[![PyPI version](https://badge.fury.io/py/pylock-attestations.svg)](https://pypi.org/project/pylock-attestations)
[![Packaging status](https://repology.org/badge/tiny-repos/python:pylock-attestations.svg)](https://repology.org/project/python:pylock-attestations/versions)
<!--- BADGES: END --->

CLI tool to add attestation identities to a `pylock.toml` file.

> [!IMPORTANT]
> This CLI is currently in alpha and not ready for production use.

## Installation

Install using `uv` or `pipx`:
```sh
# with uv:
uv tool install pylock-attestations --prerelease=allow

# with pipx:
pipx install pylock-attestations
```

## Usage

Run the `pylock-attestations` command inside a project folder containing
a `pylock.toml` file to update it in-place:

```sh
cd my_project/

pylock-attestations
```


You can also specify the input and output files:

```sh
cd my_project/

pylock-attestations -i pylock.old.toml -o pylock.new.toml
```

## License
```
Copyright 2025 Trail of Bits

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.

You may obtain a copy of the License at
    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
```
