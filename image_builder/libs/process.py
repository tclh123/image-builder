#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
https://github.com/tclh123/process

# Usage:

from process import process

git = process.git
git.status()

ls = process.bake('ls')
ls('-al')
ls('-l', '-a')
ls('-l', a=True)
ls.call('-al')
process.ls.call('-al')


## Keyword arguments like http://amoffat.github.io/sh/#keyword-arguments

# resolves to "curl http://duckduckgo.com/ -o page.html --silent"
curl("http://duckduckgo.com/", o="page.html", silent=True)

# or if you prefer not to use keyword arguments, this does the same thing:
curl("http://duckduckgo.com/", "-o", "page.html", "--silent")

# resolves to "adduser amoffat --system --shell=/bin/bash --no-create-home"
adduser("amoffat", system=True, shell="/bin/bash", no_create_home=True)

# or
adduser("amoffat", "--system", "--shell", "/bin/bash", "--no-create-home")
"""

import logging
import os
import shlex
import subprocess

import six

logger = logging.getLogger(__name__)


def _call(cmd, env=None, nonblock=False, shell=False, stdout=None, stderr=None):
    """if nonblock, return the process itself, otherwise return a result dict"""
    fullcmd = cmd if shell else " ".join(cmd)
    if os.environ.get("DRY_RUN"):
        logger.info("[DRY-RUN] Running process %s" % fullcmd)
        return AttrDict(returncode=0)
    logger.debug("Running process %s" % fullcmd)
    kw = dict(env=env) if env else {}
    try:
        process = subprocess.Popen(
            cmd,
            shell=shell,
            stdout=stdout or subprocess.PIPE,
            stderr=stderr or subprocess.PIPE,
            universal_newlines=True,
            **kw,
        )
    except (OSError, TypeError) as err:
        logger.error("Error occurrred when calling %s" % fullcmd)
        raise err
    if nonblock:
        return process
    out, err = process.communicate()
    out = str(out)
    err = str(err)

    result = AttrDict(
        returncode=process.returncode,
        stdout=out,
        stderr=err,
        fullcmd=fullcmd,
        _raise_if_attr_not_found=True,
    )
    return result


class AttrDict(dict):
    def __init__(self, *args, **kwargs):
        super(AttrDict, self).__init__(*args, **kwargs)

    def __getattr__(self, attr):
        if self.get("_raise_if_attr_not_found") and attr not in self:
            raise AttributeError(f"There's no attribute called {attr}")
        return self.get(attr, self.get("_default_value"))

    def __setattr__(self, attr, value):
        self[attr] = value


class Process(object):
    def __init__(self, cmds=None, **kw):
        self.cmds = cmds or []
        self.options = kw

    def __getattr__(self, name):
        # make doctest happy
        if name == "__wrapped__":
            raise AttributeError
        name = name.replace("_", "-")  # e.g. format_patch -> format-patch
        return self.bake(name)

    def __call__(self, *a, **kw):
        proc = self.bake()
        env = kw.pop("env", {})
        proc._parse_args(*a, **kw)
        return proc.call(env=env)

    def _parse_args(self, *a, **kw):
        cmds = []
        for p in a:
            if not isinstance(p, six.string_types):
                raise KeyError
            cmds.append(p)

        for k, v in kw.items():
            if len(k) == 1:
                k = "-" + k
            else:
                k = "--" + k
            if "_" in k:  # e.g. --no_ff -> --no-ff
                k = k.replace("_", "-")
            if not v:  # v in (None, '', False)
                continue
            elif isinstance(v, bool):  # v is True
                cmds.append(k)
            elif isinstance(v, six.string_types):
                cmds.append(k)
                cmds.append(v)
            elif isinstance(v, list):  # support array args
                for i in v:
                    cmds.append(k)
                    cmds.append(i)
            else:
                raise KeyError
        self.cmds += cmds

    def bake(self, *a, **kw):
        cmds = list(self.cmds)
        proc = Process(cmds, **self.options)
        proc._parse_args(*a, **kw)
        return proc

    def call(self, cmdstr="", **kw):
        kw.update(self.options)
        shell = kw.get("shell")
        if not shell:
            extra_cmds = shlex.split(cmdstr)
            cmd = self.cmds + extra_cmds
        else:
            cmd = " ".join(self.cmds)
            if cmd:
                cmd += " " + cmdstr
            else:
                cmd = cmdstr
        return _call(cmd, **kw)


process = Process()
