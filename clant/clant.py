#!/usr/bin/env python3

"""
Check for C and C++ code issues with clang-based tools.
"""

import argparse
import collections
import glob
import json
import multiprocessing
import os
import queue
import re
import shlex
import shutil
import subprocess
import sys
import threading

__author__ = "David Robillard"
__date__ = "2020-12-13"
__email__ = "d@drobilla.net"
__license__ = "ISC"
__version__ = "1.0.2"


class ConfigurationError(RuntimeError):
    """Raised when the configuration file syntax is invalid."""

    def __init__(self, message):
        RuntimeError.__init__(self, message)


# Options that may be used by a task
_Options = collections.namedtuple(
    "Options",
    ["auto_headers", "build_dir", "include_flags", "mapping_files", "verbose"],
)

_Task = collections.namedtuple("Task", ["func", "source", "command"])


def _message(string):
    """Print an informative message to the console."""

    sys.stdout.write("clant: %s\n" % string)
    sys.stdout.flush()


def _warning(string):
    """Print a warning message to the console."""

    sys.stderr.write("clant: warning: %s\n" % string)
    sys.stderr.flush()


def _run_command(options, cmd):
    """Run a command and return a CompletexProcess with captured output."""

    if options.verbose:
        sys.stdout.write("%s\n" % shlex.join(cmd))

    return subprocess.run(cmd, capture_output=True, check=False)


def _load_compdb(path):
    """
    Load and return the compilation database as a Python object.

    It is assumed that the current directory is already the build directory.
    """

    _message("Loading compilation database `%s'" % path)

    with open("compile_commands.json", "r") as compdb_file:
        return json.load(compdb_file)


def _get_compile_commands(compdb):
    """
    Convert a compilation database to a dictionary of compile commands.

    The returned dictionary maps filenames to compile commands as lists.
    """

    commands = {}
    for entry in compdb:
        if "arguments" in entry:
            command = entry["arguments"]
        elif "command" in entry:
            command = shlex.split(entry["command"])

        if command[0] == "ccache":
            command = command[1:]

        commands[entry["file"]] = command

    return commands


def _get_include_flags(commands):
    """
    Return a list of all include flags used in the compilation.

    This is used to be able to run tools on individual headers, which don't
    have an associated compile command to use directly.  It assumes that there
    are no clashing includes and that it makes sense to mash all the include
    directories together.
    """

    include_flags = set()
    for _, command in commands.items():
        flags = [f for f in command if f.startswith("-I")]
        for flag in flags:
            include_flags.add(flag)

    return list(include_flags)


def _get_source_files(commands):
    """Return a list of all source files in the compilation."""

    return list(commands.keys())


def _get_header_files(include_dirs):
    """Return a list of all extra headers to include in checks."""

    headers = []
    for path in include_dirs:
        headers += glob.glob(path + "/**/*.h", recursive=True)
        headers += glob.glob(path + "/**/*.hh", recursive=True)
        headers += glob.glob(path + "/**/*.hpp", recursive=True)
        headers += glob.glob(path + "/**/*.ipp", recursive=True)

    return headers


def _header_extensions(source):
    """Return a list of header extensions to also check for a source file."""

    if source.endswith(".c") or source.endswith(".m"):
        return ["h"]

    if source.endswith(".cpp") or source.endswith(".cc"):
        return ["hpp", "hh", "ipp"]

    return []


def _run_clang_tidy(options, source, command, lock):
    """Run clang-tidy on a file in a thread."""

    # pylint: disable=unused-argument

    cmd = [
        "clang-tidy",
        '--warnings-as-errors="*"',
        "--quiet",
        "-p=.",
    ]

    if options.auto_headers:
        extensions = _header_extensions(source)
        pattern = "|".join(["^\\.\\./.*\\.%s$" % x for x in extensions])
        cmd += ["--header-filter=%s" % pattern]

    cmd += [source]

    proc = _run_command(options, cmd)

    with lock:
        if proc.returncode == 0:
            sys.stdout.write("%s:1:1: note: code is tidy\n" % source)
        else:
            print("%s:1:1: error: clang-tidy issues from here:" % source)

        if len(proc.stdout) > 0:
            sys.stdout.write(proc.stdout.decode("utf-8"))


def _iwyu_output_formatter(output):
    """Convert IWYU output to standard compiler format.

    General idea taken from iwyu_tool.py.  This implementation is a bit more
    modern and tidy (in my opinion), and appeases pylint.
    """

    result = []

    correct_re = re.compile(r"^\((.*?) has correct #includes/fwd-decls\)$")
    should_add_re = re.compile(r"^(.*?) should add these lines:$")
    should_remove_re = re.compile(r"^(.*?) should remove these lines:$")
    lines_re = re.compile(r"^- (.*?)  // lines ([0-9]+)-[0-9]+$")

    # States for parsing context
    General = collections.namedtuple("General", [])
    Add = collections.namedtuple("Add", ["path"])
    Remove = collections.namedtuple("Remove", ["path"])
    List = collections.namedtuple("List", [])

    def next_state(state, line):
        if line == "---":
            return (General(), True)

        if line.startswith("The full include-list for"):
            return (List(), True)

        match = correct_re.match(line)
        if match:
            path = match.group(1)
            result.append("%s:1:1: note: includes are correct" % path)
            return (General(), True)

        match = should_add_re.match(line)
        if match:
            return (Add(match.group(1)), True)

        match = should_remove_re.match(line)
        if match:
            return (Remove(match.group(1)), True)

        return (state, False)

    state = General()
    for line in output.splitlines():
        if len(line.strip()) == 0:
            continue

        state, changed = next_state(state, line)
        if changed:
            continue

        if isinstance(state, General):
            result.append(line)
        elif isinstance(state, Add):
            result.append("%s:1:1: error: add the following line" % state.path)
            result.append(line)
        elif isinstance(state, Remove):
            match = lines_re.match(line)
            line = match.group(2) if match else "1"
            result.append(
                "%s:%s:1: error: remove this line" % (state.path, line)
            )
            result.append(match.group(1))

    return result


def _run_iwyu(options, source, command, lock):
    """Run include-what-you-use on a file in a thread."""

    cmd = ["include-what-you-use", "-Xiwyu", "--quoted_includes_first"]

    for mapping_file in options.mapping_files:
        cmd += ["-Xiwyu", "--mapping_file=" + mapping_file]

    if command is None:
        # Run on extra header using the aggregated include flags
        cmd += options.include_flags + [source]
        proc = _run_command(options, cmd)
    else:
        # Run on normal source file with a compile command
        cmd += command[1:]

        # Include headers in the same language
        # This avoids things like suggesting to remove stdbool.h in C headers
        for extension in _header_extensions(source):
            cmd += ["-Xiwyu", "--check_also=../*." + extension]

        proc = _run_command(options, cmd)

    sensible_output = _iwyu_output_formatter(proc.stderr.decode("utf-8"))

    with lock:
        if len(sensible_output) == 0:
            print("%s:1:1: warning: include-what-you-use failed" % source)
        else:
            print(os.linesep.join(sensible_output))


def _task_thread(task_queue, options, lock):
    """Thread that executes tasks from a queue until the queue is empty."""

    while not task_queue.empty():
        task = task_queue.get()
        task.func(options, task.source, task.command, lock)
        task_queue.task_done()


def _run_threads(options, tasks, num_jobs):
    """Launch threads to run tasks and wait until all tasks are completed."""

    # Put all tasks in a queue
    task_queue = queue.Queue(len(tasks))
    for task in tasks:
        task_queue.put(task)

    # Launch a set of threads to run tasks in parallel
    num_jobs = min(num_jobs, task_queue.qsize())
    lock = threading.Lock()
    threads = []
    for _ in range(num_jobs):
        thread = threading.Thread(
            target=_task_thread, args=(task_queue, options, lock), daemon=True
        )

        thread.start()
        threads += [thread]

    # Wait until everything is finished
    task_queue.join()
    for thread in threads:
        thread.join()


def _filter_files(sources, headers, exclude_patterns):
    """
    Filter out files that should not be checked.

    Returns a (sources, headers) tuple.
    """

    # Filter out explicitly excluded files
    if len(exclude_patterns) > 0:
        exclude_re = re.compile("|".join(exclude_patterns))
        sources = [s for s in sources if not exclude_re.search(s)]
        headers = [h for h in headers if not exclude_re.search(h)]

    # Filter out sources in the build directory
    # This avoids checking generated code, configuration checks, and so on
    sources = [s for s in sources if s.startswith("..")]

    return (sources, headers)


def find_mapping_file(project_dir, name):
    """
    Find an include-what-you-use mapping file.

    If the name is an absolute path, it is simply returned, Otherwise, the file
    is searched for in the project directory, then the system
    include-what-you-use directory, in that order.
    """

    if os.path.isabs(name):
        return name

    in_project = os.path.join(project_dir, name)
    if os.path.exists(in_project):
        _message("Using mapping file `%s'" % in_project)
        return in_project

    iwyu_path = shutil.which("include-what-you-use")
    prefix = os.path.dirname(os.path.dirname(iwyu_path))
    on_system = os.path.join(prefix, "share", "include-what-you-use", name)
    if os.path.exists(on_system):
        _message("Using mapping file `%s'" % on_system)
        return on_system

    raise FileNotFoundError("Could not find mapping file `%s'" % name)


def _default_configuration():
    """Return a default configuration dictionary."""

    return {
        "auto_headers": True,
        "build_dir": "build",
        "exclude_patterns": [],
        "include_dirs": [],
        "iwyu": True,
        "jobs": multiprocessing.cpu_count(),
        "mapping_files": [],
        "tidy": True,
        "verbose": False,
    }


def _parse_version(version_string):
    """Parse a version number from a config file into a tuple of integers."""

    version = list(map(int, version_string.split(".")))
    if len(version) != 3:
        raise ConfigurationError(
            "Invalid version number `%s'" % version_string
        )

    return version


def _update_configuration(config, update):
    """Update configuration with values from another."""

    for key, value in update.items():
        if key in [
            "auto_headers",
            "build_dir",
            "iwyu",
            "tidy",
            "jobs",
            "verbose",
        ]:
            if value is not None:
                config[key] = value
        elif key in ["mapping_files", "exclude_patterns", "include_dirs"]:
            if value is not None:
                config[key] += value
        elif key != "version":
            _warning("Unknown configuration key `%s'" % key)

    return config


def _load_configuration(config_path):
    """
    Load additional configuration from a .clang.json file.

    Returns `config` with values extended or overridden from those defined in
    the file.
    """

    _message("Loading configuration `%s'" % config_path)
    project_dir = os.path.dirname(config_path)

    def check_type(key, value, required_type):
        if not isinstance(value, required_type):
            raise ConfigurationError(
                "Value for `%s' is not a %s" % (key, required_type.__name__)
            )

    def check_element_type(key, value, required_type):
        for element in value:
            if not isinstance(element, required_type):
                raise ConfigurationError(
                    "Value in `%s' is not a %s" % (key, required_type.__name__)
                )

    with open(config_path, "r") as config_file:
        file_config = json.load(config_file)

        if "version" not in file_config:
            raise ConfigurationError("Configuration file missing a version")

        config_version = file_config["version"]
        if _parse_version(config_version) > _parse_version(__version__):
            _warning(
                "Configuration version %s is newer than %s"
                % (config_version, __version__)
            )

        for key, value in file_config.items():
            if key == "auto_headers":
                check_type(key, value, bool)
            elif key == "build_dir":
                check_type(key, value, str)
            elif key in ["exclude_patterns", "include_dirs"]:
                check_type(key, value, list)
                check_element_type(key, value, str)
            elif key in ["iwyu", "tidy"]:
                check_type(key, value, bool)
            elif key == "jobs":
                check_type(key, value, int)
            elif key == "mapping_files":
                check_type(key, value, list)
                check_element_type(key, value, str)
                file_config[key] = [
                    find_mapping_file(project_dir, f) for f in value
                ]
            elif key == "verbose":
                check_type(key, value, bool)
            elif key != "version":
                _warning("Unknown configuration key `%s'" % key)

        return file_config


def _get_configuration(project_dir, args):
    """
    Get the final configuration to use.

    This merges the defaults, file configuration if present, and command line
    arguments, in that order of priority.
    """

    # Start with the default configuration
    config = _default_configuration()

    # Update with values from configuration file if present
    config_path = os.path.join(project_dir, ".clant.json")
    if os.path.exists(config_path):
        file_config = _load_configuration(config_path)
        config = _update_configuration(config, file_config)

    # Finally update with values provided by the user
    return _update_configuration(config, args)


def run(build_dir, **kwargs):
    """
    Run checks on an entire project.

    :param bool auto_headers: Automatically include matching language headers
    in checks.

    :param str build_dir: Path to build directory.

    :param str exclude_patterns: List of regular expressions for files to
                                 exclude from checks.

    :param bool iwyu: Run include-what-you-use.

    :param int jobs: Maximum number of parallel jobs to run.

    :param list mapping_files: List of IWYU mapping filenames.

    :param bool tidy: Run clang-tidy.

    :param bool verbose: Print all executed commands.
    """

    project_dir = os.path.dirname(os.path.abspath(build_dir))
    config = _get_configuration(project_dir, kwargs)

    # Get a list of absolute paths to all extra include dirs
    include_dirs = config["include_dirs"]
    if len(include_dirs) > 0:
        include_dirs = [os.path.abspath(d) for d in include_dirs]

    # Move into the build directory
    orig_cwd = os.getcwd()
    _message("Entering directory `%s'" % os.path.abspath(build_dir))
    os.chdir(build_dir)

    # Make include dirs relative to the build for consistency with sources
    include_dirs = [os.path.relpath(d) for d in include_dirs]

    # Load compile commands from compilation database
    compdb = _load_compdb("compile_commands.json")
    commands = _get_compile_commands(compdb)
    sources = _get_source_files(commands)

    # Get extra header files to check
    headers = _get_header_files(include_dirs)

    # Filter out excluded files and files in the build directory
    sources, headers = _filter_files(
        sources, headers, config["exclude_patterns"]
    )

    # Generate list of all tasks
    tasks = []

    if config["iwyu"]:
        for source in sources:
            tasks += [_Task(_run_iwyu, source, commands[source])]

        for header in headers:
            tasks += [_Task(_run_iwyu, header, None)]

    if config["tidy"]:
        for source in sources:
            tasks += [_Task(_run_clang_tidy, source, commands[source])]

        for header in headers:
            tasks += [_Task(_run_clang_tidy, header, None)]

    _run_threads(
        _Options(
            config["auto_headers"],
            build_dir,
            _get_include_flags(commands),
            config["mapping_files"],
            config["verbose"],
        ),
        tasks,
        config["jobs"],
    )

    _message("Leaving directory `%s'" % os.path.abspath(build_dir))
    os.chdir(orig_cwd)


def main():
    """Run the command line tool."""

    parser = argparse.ArgumentParser(
        usage="%(prog)s [OPTION]... [BUILD_DIR]",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "--exclude",
        metavar="REGEX",
        dest="exclude_patterns",
        default=[],
        action="append",
        help="regular expression for files to ignore",
    )

    parser.add_argument(
        "--include",
        metavar="DIR",
        dest="include_dirs",
        default=[],
        action="append",
        help="directory of extra headers to check",
    )

    parser.add_argument(
        "-j",
        metavar="JOBS",
        dest="jobs",
        type=int,
        help="maximum number of parallel tasks",
    )

    parser.add_argument(
        "--mapping",
        metavar="FILE",
        dest="mapping_files",
        default=[],
        action="append",
        help="add include-what-you-use mapping file",
    )

    parser.add_argument(
        "--no-auto-headers",
        dest="auto_headers",
        action="store_false",
        help="don't override clang-tidy header regex based on language",
    )

    parser.add_argument(
        "--no-iwyu",
        dest="iwyu",
        action="store_false",
        help="don't run include-what-you-use",
    )

    parser.add_argument(
        "--no-tidy",
        dest="tidy",
        action="store_false",
        help="don't run clang-tidy",
    )

    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="print all executed commands",
    )

    parser.add_argument(
        "-V",
        "--version",
        action="store_true",
        help="print version information and exit",
    )

    parser.add_argument(
        "build_dir",
        nargs="?",
        default="build",
        help='path to build directory (default: "build")',
    )

    args = parser.parse_args(sys.argv[1:])
    if args.version:
        print("Clant %s" % __version__)
        sys.exit(0)

    try:
        run(**vars(args))
    except (ConfigurationError, FileNotFoundError) as error:
        sys.stderr.write("clant: error: %s\n" % error)
        sys.exit(1)


if __name__ == "__main__":
    main()
