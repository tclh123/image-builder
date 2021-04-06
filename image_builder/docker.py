import glob
import logging
import operator
import os
import sys
from functools import reduce

import requests
from dockerfile_parse import DockerfileParser
from image_builder.config import config
from image_builder.libs.process import Process, process

logger = logging.getLogger(__name__)

docker = Process(stdout=sys.stdout, stderr=sys.stderr).docker
docker_silent = process.docker


class Dockerfile(DockerfileParser):
    def __init__(self, path, build_arg=None):
        # FIXME: use build_arg instead of parent_env
        super().__init__(path, parent_env=build_arg)
        self.build_arg = build_arg
        logger.debug("input build_arg: %s", build_arg)
        self.path = path

        # HACK: update build_arg with default arg in Dockerfile
        self.arg_replace = False
        default_args = dict(
            value.split("=") for value in self.get_by_instruction("ARG") if "=" in value
        )
        logger.debug("default args in Dockerfile: %s", default_args)
        default_args.update(build_arg)
        self.build_arg = default_args
        logger.debug("computed build_arg: %s", self.build_arg)
        self.arg_replace = True

    # FIXME: support ARG expansion in dockerfile_parse, then remove those hacks
    @property
    def lines(self):
        """
        :return: list containing lines (unicode) from Dockerfile
        """
        from dockerfile_parse.parser import b2u

        if self.cache_content and self.cached_content:
            return self.cached_content.splitlines(True)

        try:
            with self._open_dockerfile("rb") as dockerfile:
                lines = [b2u(l) for l in dockerfile.readlines()]

                # HACK:
                if self.arg_replace:
                    # lambda produce something like ('${REGISTRY}', 'docker-registry.example.com:5000')
                    f1 = lambda arg: (
                        f"${{{arg}}}",
                        self.build_arg.get(arg, f"${{{arg}}}"),
                    )
                    f2 = lambda arg: (f"${arg}", self.build_arg.get(arg, f"${arg}"))
                    lines = [
                        l.replace(*f1("REGISTRY"))
                        .replace(*f2("REGISTRY"))
                        .replace(*f1("GIT_SHA"))
                        .replace(*f2("GIT_SHA"))
                        .replace(*f1("IMAGE_TAG"))
                        .replace(*f2("IMAGE_TAG"))
                        .replace(*f1("APP_DIR"))
                        .replace(*f2("APP_DIR"))
                        for l in lines
                    ]

                if self.cache_content:
                    self.cached_content = "".join(lines)
                return lines
        except (IOError, OSError) as ex:
            logger.error("Couldn't retrieve lines from dockerfile: %r", ex)
            raise

    @property
    def values_by_instruction(self):
        ret = {}
        for insndesc in self.structure:
            insn = insndesc["instruction"]
            ret.setdefault(insn, []).append(insndesc["value"])
        return ret

    def get_by_instruction(self, instruction):
        return self.values_by_instruction.get(instruction, [])

    @property
    def copys(self):
        # We don't care files copied from other stage
        return [
            v for v in self.get_by_instruction("COPY") if not v.startswith("--from")
        ]

    @property
    def copied_srcs(self):
        return [copy.split()[0] for copy in self.copys]

    @property
    def adds(self):
        return self.get_by_instruction("ADD")

    @property
    def added_srcs(self):
        return [add.split()[0] for add in self.adds]


def parse_docker_image_identity(image):
    """[[registry-address]:port/]name:tag"""
    registry, image = image.rsplit("/", 1) if "/" in image else ("", image)
    image_name, image_tag = image.split(":") if ":" in image else (image, "")
    return registry, image_name, image_tag


def get_image_digest(image):
    registry, image_name, image_tag = parse_docker_image_identity(image)
    r = requests.head(
        config.DOCKER_REGISTRY_IMAGE_API.format(
            registry=registry, image_name=image_name, image_tag=image_tag
        ),
        headers={"Accept": "application/vnd.docker.distribution.manifest.v2+json"},
        verify=False,
    )
    return r.headers.get("docker-content-digest", "")


def locate_dockerfile(image_name):
    """locate Dockerfile by environment variables or defined path pattern"""
    return os.environ.get(
        config.DOCKERFILE_ENV_PATTERN.format(image_name=image_name),
        config.DOCKERFILE_PATH_PATTERN.format(image_name=image_name),
    )


def enter_build_context(image_name):
    build_context = os.environ.get(
        config.BUILD_CONTEXT_ENV_PATTERN.format(image_name=image_name)
    )
    if build_context is not None:
        os.chdir(build_context)
        return build_context
    return os.getcwd()


def parse_dockerignore(path):
    r"""Parse .dockerignore file under the path, return files set.
    The rule based on https://docs.docker.com/engine/reference/builder/#dockerignore-file

    >>> from tempfile import NamedTemporaryFile
    >>> f = NamedTemporaryFile(delete=False)
    >>> _ = f.write(b"Makefile\n")
    >>> _ = f.write(b"README*\n")
    >>> _ = f.write(b"image_builder\n")
    >>> _ = f.write(b"!**\n")
    >>> _ = f.write(b"README*\n")
    >>> _ = f.write(b"s?tup.py\n")
    >>> f.close()
    >>> parse_dockerignore(f.name)
    {'README.md', 'setup.py'}
    >>> import os; os.unlink(f.name)
    """
    path = os.path.join(path, ".dockerignore") if os.path.isdir(path) else path
    if not os.path.isfile(path):
        logger.warning(
            "failed to parse .dockerignore, %s is not a file or not exists", path
        )
        return []

    def _glob(pattern):
        return reduce(
            operator.add,
            (
                glob.glob(f"{f}/**", recursive=True) if os.path.isdir(f) else [f]
                for f in glob.glob(pattern, recursive=True)
            ),
            [],
        )

    files = set()
    with open(path) as f:
        for line in f:
            line = line.rstrip()
            if line.startswith("#"):
                continue
            if line.startswith("!"):
                files.difference_update(_glob(line[1:]))
            files.update(_glob(line))
    return files


if __name__ == "__main__":
    import doctest

    doctest.testmod()
