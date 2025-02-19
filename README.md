Clant
=====

Clant (CLAng liNT) is a fast and easy to use wrapper script for checking C and
C++ code with the clang-based tools [clang-tidy][] and
[include-what-you-use][].

Though these tools are helpful, they can be tricky to configure correctly.  For
example, it is tricky to run checks in headers, mixing C and C++ code can be a
hassle, and the interface and output of the bundled wrapper scripts are
inconsistent.

Clant aims to handle all of these details and provide a simple interface for
running these tools on a project with nice output.  Tools are run in parallel
for maximum speed, and output is printed in the standard make and GCC format
supported by most editors.

Example
-------

Using Clant looks something like this:

    $ cd /mylib
    $ clant
    clant: Entering directory `/mylib/build'
    clant: Loading compilation database `compile_commands.json'
    ../src/mylib.c:1:1: note: includes are correct
    ../src/mylib.c:239:1: error: function 'run' has a definition with different parameter names [readability-inconsistent-declaration-parameter-name,-warnings-as-errors]
    run(Thing* result);
    ^
    ../src/mylib.c:576:1: note: the definition seen here
    run(Thing* out)
    ^
    ../src/mylib.c:239:1: note: differing parameters are named here: ('result'), in definition: ('out')
    run(Something* result);
    ^              ~~~~~~
                   out

    ../test/test_something.c:26:1: error: remove this line
    #include <stdlib.h>
    ../test/test_something.c:1:1: note: code is tidy
    clant: Leaving directory `/mylib/build/build'

Installation
------------

Clant is a Python script that can simply be run anywhere, installation is not
necessary.

It can, however, be installed with [pip][]:

    pip install clant

Installation from the source directory is also possible:

    cd path_to_clant_source
    pip install .

Usage
-----

Ideally, you can simply run clant from your project directory:

    cd path_to_my_project
    clant

Clant will assume that a build exists in the `build` subdirectory, and look for
`compile_commands.json` there.  To use a different build directory, pass it as
a parameter:

    clant release

By default, the number of threads supported by the CPU will be used.  To use a
different number of threads, use the `-j` option:

    clant -j 4

Individual tools can be disabled for faster runs or to suppress warnings while
working on issues:

    clant --no-tidy
    clant --no-iwyu

### Include Mapping Files

[Mapping files][] are supported by include-what-you-use to specify include file
mappings for third-party libraries or other things that the tool does not
automatically understand.  Additional mapping files can be given with the
`--mapping` option:

    clant --mapping /path/to/somelibrary.imp
    clant --mapping someotherlibrary.imp

If they are not absolute paths, mapping files will be searched for first in the
project directory, then in the system include-what-you-use directory (relative
to the binary, typically `/usr/share/include-what-you-use`).

### Excluding Files

Sometimes, certain files can't be realistically changed to reach the same level
of cleanliness as the rest of the project, for example generated or third-party
code.  Such files can be excluded by providing a regular expression with the
`--exclude` option:

    clant --exclude '.*generated.*\.c'

Warnings
--------

Warnings are configured using the standard `.clang-tidy` mechanism.  The
simplest approach is to simply add a single `.clang-tidy` file to the project
root, and enable or suppress any warnings there.

To be more fine-grained, for example to specify stricter warnings for headers
than implementations, separate files can be used.  The `.clang-tidy` file in
the closest parent directory to the source being checked will be used.  Note,
however, that headers aren't checked on their own, but only while checking
files that include them, depending on the configuration.

Using from Python
-----------------

Clant is also installed as a Python package called `clant`, which contains a
single module, also called `clant`.

The command-line utility is implemented in `clant.main()`, which takes no
arguments since they are read from `sys.argv`.

That is a simple wrapper for `clant.run()`, which takes parameters that loosely
correspond with command line parameters, but using lists for multiple values
and more appropriate names.  Only `build_dir` is required.  For example:

```python

from clant import clant

clant.run(auto_headers=True,
          build_dir="build",
          exclude_patterns=[".*gen.*"],
          headers=False,
          iwyu=False,
          jobs=4,
          mapping_files=["qt5_11.imp"],
          tidy=True,
          verbose=False)
```

Configuration File
------------------

A configuration can be included in a project by adding a `.clant.json` file in
the project root directory.  This file must be a JSON object, where the keys
correspond to the keyword parameters of `clant.run()` described above.

The one exception is the `version` key, which must be present, and represents
the version of Clant the configuration file is for.  Currently, this is only
used to print a warning if the configuration is newer, but it may be used in
the future to handle any potential compatibility issues.

For example, this configuration uses all of the supported keys:

```json
{
  "build_dir": "release",
  "exclude_patterns": [".*gen.*"],
  "iwyu": false,
  "jobs": 4,
  "mapping_files": ["qt5_11.imp"],
  "tidy": true,
  "verbose": false,
  "version": "2.0.0"
}
```

If command line parameters are also given, then values from the configuration
file will override them, except for lists, which will be added.

Assumptions
-----------

Aside from some of the defaults described above, Clant is a somewhat
opinionated tool that assumes a few things for ease of use:

  - `clang-tidy` and `include-what-you-use` are installed, that is, these
    commands are available in the `PATH`.

  - The build directory is an immediate child of the project directory.

  - The build directory contains a valid [JSON Compilation Database][] named
    `compile_commands.json`.

Feel free to submit a patch if any of these are problematic for you.

 -- David Robillard <d@drobilla.net>

[clang-tidy]: https://clang.llvm.org/extra/clang-tidy/
[include-what-you-use]: https://include-what-you-use.org/
[pip]: https://pypi.org/project/pip/
[Mapping files]: https://github.com/include-what-you-use/include-what-you-use/blob/master/docs/IWYUMappings.md
[JSON Compilation Database]: https://clang.llvm.org/docs/JSONCompilationDatabase.html
