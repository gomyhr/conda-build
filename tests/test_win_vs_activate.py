from __future__ import print_function

import os
import subprocess
import sys

import pytest

vcvars_backup_files={}
if sys.platform == "win32":
    if 'ProgramFiles(x86)' in os.environ:
        program_files = os.environ['ProgramFiles(x86)']
    else:
        program_files = os.environ['ProgramFiles']

    vcvars_backup_files = {"vs{}".format(version): os.path.join(program_files,
                                  r'Microsoft Visual Studio {version}'.format(version=version),
                                  'VC', 'vcvarsall.bat') for version in ["9.0", "10.0", "14.0"]}
    # VC9 compiler for python - local user install
    localappdata = os.environ.get("localappdata")
    vcvars_backup_files["python_local"]=os.path.join(localappdata, 'Programs', 'Common',
                    'Microsoft', 'Visual C++ for Python', "9.0", "vcvarsall.bat")
    # VC9 compiler for python - common files
    vcvars_backup_files["python_system"] = os.path.join(program_files, 'Common Files',
                    'Microsoft', 'Visual C++ for Python', "9.0", "vcvarsall.bat")
    # Windows SDK 7.1
    vcvars_backup_files["win71sdk"] = "{program_files}\\Microsoft SDKs\\Windows\\v7.1\\Bin\\SetEnv.cmd".\
                                      format(program_files=program_files)

    vs9  = {key:vcvars_backup_files[key] for key in ['vs9.0', 'python_local', 'python_system']}
    vs10 = {key:vcvars_backup_files[key] for key in ['vs10.0', 'win71sdk']}
    vs14 = {key:vcvars_backup_files[key] for key in ['vs14.0']}

    vcs = {"9.0": vs9, "10.0": vs10, "14.0": vs14}

def write_bat_files(good_locations):
    for label, location in vcvars_backup_files.items():
        assert not os.path.exists(location)  # these should all have been moved!  bad to overwrite them!
        if not os.path.isdir(os.path.dirname(location)):
            os.makedirs(os.path.dirname(location))  # if any of these are made, they are not currently cleaned up.  Sorry.
        with open(location, "w") as f:
            print("writing {} (exit /b {})".format(location, int(label not in good_locations)))
            f.write("::  NOTE: exit code of 1 here means incorrect VS version activated.  check logic.")
            f.write("exit /b {}".format(int(label not in good_locations)))


@pytest.fixture(scope="function")
def setup_teardown(request):
    def fin():
        for f in vcvars_backup_files.values():
            # clean up any of the custom scripts we wrote to test
            if os.path.exists(f):
                os.remove(f)
            # restore the backups
            if os.path.exists(f[:-1]+'k'):
                os.rename(f[:-1]+'k', f)
    request.addfinalizer(fin)

    # backup known files
    for f in vcvars_backup_files.values():
        if os.path.exists(f):
            os.rename(f, f[:-1]+'k')

    return request



@pytest.fixture(scope="function", params=vcvars_backup_files.keys())
def compiler(request, setup_teardown):
    return request.param


@pytest.fixture(params=[32, 64])
def bits(request):
    return request.param


@pytest.mark.skipif(sys.platform!="win32", reason="windows-only test")
@pytest.mark.xfail(reason="verification of test logic")
def test_activation_logic(bits, compiler):
    from conda_build.windows import msvc_env_cmd
    # empty list here means no configuration is valid.  We should get a
    # failure.
    write_bat_files([])
    # look up which VS version we're forcing here
    compiler_version = [key for key in vcs if compiler in vcs[key]][0]
    with open('tmp_call.bat', "w") as f:
        f.write(msvc_env_cmd(bits, compiler_version))
    subprocess.check_call(['cmd.exe', '/C', 'tmp_call.bat'])


@pytest.mark.skipif(sys.platform!="win32", reason="windows-only test")
def test_activation(bits, compiler):
    write_bat_files([request.param])
    from conda_build.windows import msvc_env_cmd
    # look up which VS version we're forcing here
    compiler_version = [key for key in vcs if compiler in vcs[key]][0]
    # this will throw an exception if the subprocess return code is not 0
    #     this is effectively the test condition for all below tests.
    with open('tmp_call.bat', "w") as f:
        f.write(msvc_env_cmd(bits, compiler_version))

    try:
        subprocess.check_call(['cmd.exe', '/C', 'tmp_call.bat'])
    except:
        print("failed activation: {}, {}".format(bits, compiler))
        raise
    finally:
        os.remove('tmp_call.bat')
