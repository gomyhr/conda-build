# (c) 2012-2014 Continuum Analytics, Inc. / http://continuum.io
# All Rights Reserved
#
# conda is distributed under the terms of the BSD 3-clause license.
# Consult LICENSE.txt or http://opensource.org/licenses/BSD-3-Clause.

"""
Tools for converting conda packages

"""
from __future__ import absolute_import, division, print_function

from copy import deepcopy
import json
import os
from os.path import abspath, expanduser, isdir, join
import pprint
import re
import sys
import tarfile

from .conda_interface import PY3

if PY3:
    from io import StringIO, BytesIO as bytes_io
else:
    from cStringIO import StringIO
    bytes_io = StringIO


BAT_PROXY = """\
@echo off
set PYFILE=%~f0
set PYFILE=%PYFILE:~0,-4%-script.py
"%~f0\\..\\..\\python.exe" "%PYFILE%" %*
"""

libpy_pat = re.compile(
    r'(lib/python\d\.\d|Lib)'
    r'/(site-packages|lib-dynload)/(\S+?)(\.cpython-\d\dm)?\.(so|pyd)')


def has_cext(t, show=False):
    matched = False
    for m in t.getmembers():
        match = libpy_pat.match(m.path)
        if match:
            if show:
                x = match.group(3)
                print("import", x.replace('/', '.'))
                matched = True
            else:
                return True
    return matched


def has_nonpy_entry_points(t, unix_to_win=True, show=False, quiet=False):
    """
    If unix_to_win=True, assumes a Unix type package (i.e., entry points
    are in the bin directory).

    unix_to_win=False means win to unix, which is not implemented yet, so it
    will only succeed if there are no entry points.
    """
    if not quiet:
        print("Checking entry points")
    bindir = 'bin/' if unix_to_win else 'Scripts/'
    matched = False
    for m in t.getmembers():
        if m.path.startswith(bindir):
            if not unix_to_win:
                if show:
                    print("Entry points with Windows to Unix are not yet " +
                          "supported")
                return True
            r = t.extractfile(m).read()
            try:
                r = r.decode('utf-8')
            except UnicodeDecodeError:
                if show:
                    print("Binary file %s" % m.path)
                matched = True
            else:
                firstline = r.splitlines()[0]
                if 'python' not in firstline:
                    if show:
                        print("Non-Python plaintext file %s" % m.path)
                    matched = True
                else:
                    if show:
                        print("Python plaintext file %s" % m.path)
    return matched


def tar_update(source, dest, file_map, verbose=True, quiet=False):
    """
    update a tarball, i.e. repack it and insert/update or remove some
    archives according file_map, which is a dictionary mapping archive names
    to either:

      - None:  meaning the archive will not be contained in the new tarball

      - a file path:  meaning the archive in the new tarball will be this
        file. Should point to an actual file on the filesystem.

      - a TarInfo object:  Useful when mapping from an existing archive. The
        file path in the archive will be the path in the TarInfo object. To
        change the path, mutate its .path attribute. The data will be used
        from the source tar file.

      - a tuple (TarInfo, data): Use this is you want to add new data to the
        dest tar file.

    Files in the source that aren't in the map will moved without any changes
    """

    # s -> t
    if isinstance(source, tarfile.TarFile):
        s = source
    else:
        if not source.endswith(('.tar', '.tar.bz2')):
            raise TypeError("path must be a .tar or .tar.bz2 path")
        s = tarfile.open(source)
    if isinstance(dest, tarfile.TarFile):
        t = dest
    else:
        t = tarfile.open(dest, 'w:bz2')

    try:
        for m in s.getmembers():
            p = m.path
            if p in file_map:
                if file_map[p] is None:
                    if verbose:
                        print('removing %r' % p)
                else:
                    if verbose:
                        print('updating %r with %r' % (p, file_map[p]))
                    if isinstance(file_map[p], tarfile.TarInfo):
                        t.addfile(file_map[p], s.extractfile(file_map[p]))
                    elif isinstance(file_map[p], tuple):
                        t.addfile(*file_map[p])
                    else:
                        t.add(file_map[p], p)
                continue
            if not quiet:
                print("keeping %r" % p)
            t.addfile(m, s.extractfile(p))

        s_names_set = set(m.path for m in s.getmembers())
        # This sorted is important!
        for p in sorted(file_map):
            if p not in s_names_set:
                if verbose:
                    print('inserting %r with %r' % (p, file_map[p]))
                if isinstance(file_map[p], tarfile.TarInfo):
                    t.addfile(file_map[p], s.extractfile(file_map[p]))
                elif isinstance(file_map[p], tuple):
                    t.addfile(*file_map[p])
                else:
                    t.add(file_map[p], p)
    finally:
        t.close()


path_mapping_bat_proxy = [
    (re.compile(r'bin/(.*)(\.py)'), r'Scripts/\1.bat'),
    (re.compile(r'bin/(.*)'), r'Scripts/\1.bat'),
]

path_mapping_unix_windows = [
    (r'lib/python(\d.\d)/', r'Lib/'),
    # Handle entry points already ending in .py. This is OK because these are
    # parsed in order. Only concern is if there are both script and script.py,
    # which seems unlikely
    (r'bin/(.*)(\.py)', r'Scripts/\1-script.py'),
    (r'bin/(.*)', r'Scripts/\1-script.py'),
]

path_mapping_windows_unix = [
    (r'Lib/', r'lib/python{pyver}/'),
    (r'Scripts/', r'bin/'),  # Not supported right now anyway
]

path_mapping_identity = [
    (r'Lib/', r'Lib/'),
    (r'lib/python{pyver}/', r'lib/python{pyver}/'),
    (r'Scripts/', r'Scripts/'),
    (r'bin/', 'bin/'),  # Not supported right now anyway
]

pyver_re = re.compile(r'python\s+(?:(?:[<>=]*)(\d.\d))?')


def get_pure_py_file_map(t, platform, quiet=False):
    info = json.loads(t.extractfile('info/index.json').read().decode('utf-8'))
    source_plat = info['platform']
    source_type = 'unix' if source_plat in {'osx', 'linux'} else 'win'
    dest_plat, dest_arch = platform.split('-')
    dest_type = 'unix' if dest_plat in {'osx', 'linux'} else 'win'

    files = t.extractfile('info/files').read().decode("utf-8").splitlines()

    if source_type == 'unix' and dest_type == 'win':
        mapping = path_mapping_unix_windows
    elif source_type == 'win' and dest_type == 'unix':
        mapping = path_mapping_windows_unix
    else:
        mapping = path_mapping_identity

    newinfo = info.copy()
    newinfo['platform'] = dest_plat
    newinfo['arch'] = 'x86_64' if dest_arch == '64' else 'x86'
    newinfo['subdir'] = platform

    pythons = list(filter(None, [pyver_re.match(p) for p in info['depends']]))
    if len(pythons) > 1:
        raise RuntimeError("Found more than one Python dependency in package %s"
            % t.name)
    elif len(pythons) == 0:
        # not a Python package
        mapping = []
    elif pythons[0].group(1):
        pyver = pythons[0].group(1)

        mapping = [(re.compile(i[0].format(pyver=pyver)),
            i[1].format(pyver=pyver)) for i in mapping]
    else:
        # No python version dependency was specified
        # Only a problem when converting from windows to unix, since
        # the python version is part of the folder structure on unix.
        if source_type == 'win' and dest_type == 'unix':
            raise RuntimeError("Python dependency must explicit when converting"
                               "from windows package to a linux packages")

    members = t.getmembers()
    file_map = {}
    for member in members:
        # Update metadata
        if member.path == 'info/index.json':
            newmember = tarfile.TarInfo('info/index.json')
            if PY3:
                newbytes = bytes(json.dumps(newinfo), 'utf-8')
            else:
                newbytes = json.dumps(newinfo)
            newmember.size = len(newbytes)
            file_map['info/index.json'] = (newmember, bytes_io(newbytes))
            continue
        elif member.path == 'info/files':
            # We have to do this at the end when we have all the files
            filemember = deepcopy(member)
            continue
        elif member.path == 'info/has_prefix':
            if source_type == 'unix' and dest_type == 'win':
                # has_prefix is not needed on Windows
                file_map['info/has_prefix'] = None

        # Move paths
        oldpath = member.path
        for old, new in mapping:
            newpath = old.sub(new, oldpath)
            if oldpath in file_map:
                # Already been handled
                break
            if newpath != oldpath:
                newmember = deepcopy(member)
                newmember.path = newpath
                assert member.path == oldpath
                file_map[oldpath] = None
                file_map[newpath] = newmember
                try:
                    loc = files.index(oldpath)
                    files[loc] = newpath
                except ValueError:
                    if not quiet:
                        print(("WARNING: Ignoring file not present in "
                              "info/files: %s") % oldpath)
            else:
                file_map[oldpath] = member

        # Make Windows compatible entry-points
        batseen = set()
        if source_type == 'unix' and dest_type == 'win':
            for old, new in path_mapping_bat_proxy:
                newpath = old.sub(new, oldpath)
                if oldpath in batseen:
                    break
                if newpath != oldpath:
                    newmember = tarfile.TarInfo(newpath)
                    if PY3:
                        data = bytes(BAT_PROXY.replace('\n', '\r\n'), 'ascii')
                    else:
                        data = BAT_PROXY.replace('\n', '\r\n')
                    newmember.size = len(data)
                    file_map[newpath] = newmember, bytes_io(data)
                    batseen.add(oldpath)
                    files.append(newpath)

    files = '\n'.join(sorted(files)) + '\n'
    if PY3:
        files = bytes(files, 'utf-8')
    filemember.size = len(files)
    file_map['info/files'] = filemember, bytes_io(files)

    return file_map


def conda_convert(file_path, output_dir=".", show_imports=False, platforms=None, force=False,
                  dependencies=None, verbose=False, quiet=True, dry_run=False):
    if not show_imports and platforms is None:
        sys.exit('Error: --platform option required for conda package conversion')

    with tarfile.open(file_path) as t:
        if show_imports:
            has_cext(t, show=True)
            return

        if not force and has_cext(t, show=show_imports):
            print("WARNING: Package %s has C extensions, skipping. Use -f to "
                  "force conversion." % file_path, file=sys.stderr)
            return

        fn = os.path.basename(file_path)

        info = json.loads(t.extractfile('info/index.json')
                          .read().decode('utf-8'))
        source_type = 'unix' if info['platform'] in {'osx', 'linux'} else 'win'

        if dependencies:
            info['depends'].extend(dependencies)

        nonpy_unix = False
        nonpy_win = False

        if 'all' in platforms:
            platforms = ['osx-64', 'linux-32', 'linux-64', 'win-32', 'win-64']
        base_output_dir = output_dir
        for platform in platforms:
            info['subdir'] = platform
            output_dir = join(base_output_dir, platform)
            if abspath(expanduser(join(output_dir, fn))) == file_path:
                if not quiet:
                    print("Skipping %s/%s. Same as input file" % (platform, fn))
                continue
            if not PY3:
                platform = platform.decode('utf-8')
            dest_plat = platform.split('-')[0]
            dest_type = 'unix' if dest_plat in {'osx', 'linux'} else 'win'

            if source_type == 'unix' and dest_type == 'win':
                nonpy_unix = nonpy_unix or has_nonpy_entry_points(t,
                    unix_to_win=True,
                    show=verbose,
                    quiet=quiet)
            if source_type == 'win' and dest_type == 'unix':
                nonpy_win = nonpy_win or has_nonpy_entry_points(t,
                    unix_to_win=False,
                    show=verbose,
                    quiet=quiet)

            if nonpy_unix and not force:
                print(("WARNING: Package %s has non-Python entry points, "
                       "skipping %s to %s conversion. Use -f to force.") %
                      (file_path, info['platform'], platform), file=sys.stderr)
                continue

            if nonpy_win and not force:
                print(("WARNING: Package %s has entry points, which are not "
                       "supported yet. Skipping %s to %s conversion. Use -f to force.") %
                      (file_path, info['platform'], platform), file=sys.stderr)
                continue

            file_map = get_pure_py_file_map(t, platform, quiet=quiet)

            if dry_run:
                if not quiet:
                    print("Would convert %s from %s to %s" %
                        (file_path, info['platform'], dest_plat))
                if verbose:
                    pprint.pprint(file_map)
                continue
            else:
                if not quiet:
                    print("Converting %s from %s to %s" %
                        (file_path, info['platform'], platform))

            if not isdir(output_dir):
                os.makedirs(output_dir)
            tar_update(t, join(output_dir, fn), file_map,
                verbose=verbose, quiet=quiet)
