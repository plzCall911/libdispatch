#!/usr/bin/env python
# encoding: utf-8
"""
static_link.py
Applies DSO-like behaviour to static libraries. Multiple archives are merged
into a single archive, and, optionally, hidden symbols are made local.

Also takes a |--localize-hidden| option that points to a file containing an
explicit list of symbols to hide, one per line.

Inspiration:
- https://github.com/MLton/mlton/blob/master/bin/static-library
- https://github.com/DynamoRIO/dynamorio/blob/master/core/CMakeLists.txt
"""
from __future__ import print_function
import os
import pipes
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import argparse
from os.path import join

VERBOSE = False


def shelljoin(args):
    return ' '.join(pipes.quote(arg) for arg in args)


def echo_err(s):
    print(s, file=sys.stderr)


def run(args):
    if VERBOSE:
        echo_err('+ %s' % shelljoin(args))
    subprocess.check_call(args)


def iter_hidden_symbols(objdump_path, object_file):
    """
    Uses objdump to filter the symbol list in |object_file| to those symbols
    marked as having hidden visibility.
    """
    cmd = [objdump_path, '-t', object_file]
    process = subprocess.Popen(cmd, stdout=subprocess.PIPE)

    for line in iter(process.stdout.readline, ''):
        m = re.search(r'\.hidden (\w+)$', line)
        if not m:
            continue

        symbol = m.group(1)
        if re.match(r'__\S*get_pc_thunk', symbol):
            # Avoid localising GCC internal symbols. See links mentioned above.
            continue

        yield symbol

    process.wait()
    if process.returncode != 0:
        raise subprocess.CalledProcessError(process.returncode, cmd)


def iter_global_symbols(nm_path, object_file):
    """
    Iterates over symbols matched by `nm --defined-only --extern-only`
    """
    cmd = [
        nm_path,
        '--defined-only',
        '--extern-only',
        '--print-file-name',
        object_file]
    process = subprocess.Popen(cmd, stdout=subprocess.PIPE)

    for line in iter(process.stdout.readline, ''):
        # print line
        m = re.search(r'\b(\w+)$', line)
        if not m: continue
        yield m.group(1)

    process.wait()
    if process.returncode != 0:
        raise subprocess.CalledProcessError(process.returncode, cmd)


def main(
        cc,
        cflags,
        objcopy,
        objdump,
        ar,
        nm,
        ranlib,
        verbose,
        localize_hidden,
        keep_global_regex,
        is_final_link,
        output,
        archives):
    global VERBOSE
    if verbose:
        VERBOSE = True

    output_archive_dir, output_archive_name = os.path.split(output)
    output_archive_name_wo_ext, _ = os.path.splitext(output_archive_name)

    temp_dir = tempfile.mkdtemp('_%s' % output_archive_name_wo_ext)
    partial_link_path = join(
        temp_dir, '%s.o' % output_archive_name_wo_ext)
    symbol_list_path = join(
        temp_dir, '%s.locals' % output_archive_name_wo_ext)

    link_command = [cc]
    link_command.extend(shlex.split(cflags))
    link_command.extend([
        '-nostartfiles',
        '-nodefaultlibs',
        '-Wl,--build-id=none',
        is_final_link and '-Wl,-Ur' or '-Wl,-r',
        '-Wl,--whole-archive',
    ])
    link_command.extend(archives)
    link_command.extend([
        '-Wl,--no-whole-archive',
        '-o', partial_link_path
    ])
    run(link_command)

    with open(symbol_list_path, 'wb') as f:
        if localize_hidden:
            for symbol in iter_hidden_symbols(objdump, partial_link_path):
                f.write('%s\n' % symbol)

        for symbol in iter_global_symbols(nm, partial_link_path):
            if re.search(keep_global_regex, symbol):
                continue
            f.write('%s\n' % symbol)

    run([objcopy, '--localize-symbols', symbol_list_path, partial_link_path])
    run([ar, 'cr', join(temp_dir, output_archive_name), partial_link_path])
    run([ranlib, join(temp_dir, output_archive_name)])
    run(['mv', join(temp_dir, output_archive_name), output])
    run(['rm', '-fr', temp_dir])


if __name__ == '__main__':
    class EnvDefault(argparse.Action):

        def __init__(self, envvar, required=True, default=None, **kwargs):
            if envvar and envvar in os.environ:
                default = os.environ[envvar]
            if required and default is not None:
                required = False
            super(EnvDefault, self).__init__(
                default=default, required=required, **kwargs)

        def __call__(self, parser, namespace, values, option_string=None):
            setattr(namespace, self.dest, values)

    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--no-localize-hidden', dest='localize_hidden',
        action='store_false', default=True,
        help='Whether symbols with hidden visibility are made local.')
    parser.add_argument(
        '--keep-global-regex', default=r'.*', type=re.compile,
        help='Whitelist regex to determine which symbols are kept as global.')
    parser.add_argument(
        '--is-final-link', action='store_true', default=False,
        help='See objcopy documentation for the -r, -Ur flags.')
    parser.add_argument(
        '-o', '--output', default='archive.a', type=os.path.abspath)
    parser.add_argument(
        '--with-cc', action=EnvDefault, dest='cc', envvar='CC', default='cc')
    parser.add_argument(
        '--cflags', action=EnvDefault, envvar='CFLAGS', default='')
    parser.add_argument(
        '--with-objcopy', action=EnvDefault, dest='objcopy', envvar='OBJCOPY',
        default='objcopy')
    parser.add_argument(
        '--with-objdump', action=EnvDefault, dest='objdump', envvar='OBJDUMP',
        default='objdump')
    parser.add_argument(
        '--with-ar', action=EnvDefault, dest='ar', envvar='AR', default='ar')
    parser.add_argument(
        '--with-nm', action=EnvDefault, dest='nm', envvar='NM', default='nm')
    parser.add_argument(
        '--with-ranlib', action=EnvDefault, dest='ranlib', envvar='RANLIB',
        default='ranlib')
    parser.add_argument('--verbose', '-v', action='store_true', default=False)
    parser.add_argument('archives', nargs='+', type=os.path.abspath)

    args = parser.parse_args()
    main(**vars(args))
