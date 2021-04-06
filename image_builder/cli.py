import argparse
import glob
import logging
import os
from hashlib import sha256

from image_builder.config import config
from image_builder.docker import (
    docker,
    docker_silent,
    Dockerfile,
    enter_build_context,
    get_image_digest,
    locate_dockerfile,
    parse_docker_image_identity,
    parse_dockerignore,
)
from image_builder.libs.utils import expand_path, get_datetime

logger = logging.getLogger(__name__)
hash_logger = logging.getLogger("files_hash")
hash_logger.propagate = False


def _tag_to_extra_tags(args, image, tag):
    for extra_tag in args.extra_tag:
        if docker.tag(f"{image}:{tag}", f"{image}:{extra_tag}").returncode != 0:
            logger.error(f"Failed to tag image {image} to {extra_tag}")
    for extra_name in args.extra_name:
        if docker.tag(f"{image}:{tag}", f"{extra_name}").returncode != 0:
            logger.error(f"Failed to tag image {image} to {extra_name}")


def build_with_cache(args):
    # Build args will be used to render this image's Dockerfile and all its parents'
    build_arg = dict([kv.split("=") for kv in args.build_arg])
    # Pass GIT_SHA as build_arg by default
    build_arg["GIT_SHA"] = args.git_sha
    build_arg["IMAGE_TAG"] = args.git_sha_tag

    def _build(registry, image_name, git_sha):
        """build image and all its parents if needed, and return image digest.
        Use all parents digests and current image copied files to calculate hash and cache(tag) it to registry.
        """

        image = f"{registry}/{image_name}"

        # If this image is not in our registry, nothing we can do.
        if registry != config.DOCKER_REGISTRY:
            return ""

        # If image:{git_sha} already exists, then return.
        logger.info(
            f"Pulling git_sha tag {image}:{git_sha} to check if it already exists"
        )
        if (
            not args.dry_run
            and docker_silent.pull(f"{image}:{git_sha}").returncode == 0
        ):
            # better to find the hash-{hash} of this image, and return hash
            # but currently, it is not easy to find all tags of the same image digest through registry API.
            # so we return image digest instead.
            digest = get_image_digest(f"{image}:{git_sha}")
            logger.info(
                f"git_sha tag {image}:{git_sha} already exists, digest: %s", digest
            )
            if not digest:
                raise Exception("Failed to get digest for existing image")
            _tag_to_extra_tags(args, image, git_sha)
            return digest

        # Enter build context directory if it is specified
        build_context = enter_build_context(image_name)

        # Parse .dockerignore in build context
        dockerignore_files_set = parse_dockerignore(build_context)

        # Check if the dockerfile exists
        dockerfile_path = locate_dockerfile(image_name)
        if not os.path.isfile(dockerfile_path):
            logger.error(
                "%s not exists or is not a file, so %s cannot get build",
                dockerfile_path,
                image_name,
            )
            raise Exception("Building cannot continue")

        dockerfile = Dockerfile(dockerfile_path, build_arg=build_arg)

        hasher = sha256()

        # Build parents, and calc parents hash
        for parent_image in dockerfile.parent_images:
            (
                parent_image_registry,
                parent_image_name,
                parent_image_tag,
            ) = parse_docker_image_identity(parent_image)
            parent_digest = _build(
                parent_image_registry, parent_image_name, parent_image_tag
            )
            if parent_digest is None:
                raise Exception(f"Failed to get parent_digest for {image}")
            hasher.update(parent_digest.encode())
            hash_logger.info(
                "parent: %s, digest: (%s, %s), hash: %s",
                parent_image,
                parent_digest,
                parent_digest.encode(),
                hasher.hexdigest(),
            )

        # Calc current image files hash

        def update_file_hash(f):
            if not os.path.isfile(f):
                return
            if f in dockerignore_files_set:
                hash_logger.debug("ignore: %s", f)
                return
            with open(f, "rb") as open_file:
                buf = open_file.read(config.READ_FILE_BLOCKSIZE)
                while len(buf) > 0:
                    hasher.update(buf)
                    buf = open_file.read(config.READ_FILE_BLOCKSIZE)
            hash_logger.info("update: %s, hash: %s", f, hasher.hexdigest())

        srcs = [dockerfile_path] + dockerfile.copied_srcs + dockerfile.added_srcs
        # TODO: if the src is a url, download it and hash it (even crane didn't do that)
        for src in srcs:
            for f in sorted(glob.glob(src)):
                # We match every file in a directory recursively
                if os.path.isdir(f):
                    for sub_f in sorted(glob.glob(f"{f}/**", recursive=True)):
                        update_file_hash(sub_f)
                else:
                    update_file_hash(f)

        files_hash = hasher.hexdigest()
        hash_logger.info("image: %s, hash: %s", image, files_hash)

        hash_tag = config.FILES_HASH_TAG_PATTERN.format(files_hash=files_hash)
        # FIXME(harry): hack, remove this
        old_hash_image = f"docker-registry.example.com:5000/{image_name}:{hash_tag}"

        logger.info(
            f"Pulling files_hash tag {image}:{hash_tag} to check if it already exists"
        )
        # If image:hash-{hash} already exists,
        # then content didn't change, return.
        # We just need to tag it to latest code version.
        if (
            not args.dry_run
            and docker_silent.pull(f"{image}:{hash_tag}").returncode == 0
        ):
            logger.info(
                f"files_hash tag {image}:{hash_tag} already exists, "
                "it means content didn't change, we can just tag the old image to new git_sha version tag"
            )
        # FIXME(harry): hack, remove this
        elif not args.dry_run and docker_silent.pull(old_hash_image).returncode == 0:
            logger.info(f"NOTE: files_hash tag {old_hash_image} already exists!")
            # tag and push this hash image
            if docker.tag(old_hash_image, f"{image}:{hash_tag}").returncode != 0:
                logger.error("Failed to tag old hash image")
                return
            if docker.push(f"{image}:{hash_tag}").returncode != 0:
                logger.error("Failed to push hash_tag image")
                return
        # If image:hash-{hash} not exists, then build it from Dockerfile.
        else:
            logger.info(
                f"files_hash tag {image}:{hash_tag} dosen't exists, "
                "it means content may changed, gonna build it from Dockerfile"
            )
            if build_with_raw_command(args, image, dockerfile_path, hash_tag) != 0:
                logger.error(f"Failed to build {image}:{hash_tag}")
                return
            if docker.push(f"{image}:{hash_tag}").returncode != 0:
                logger.error(f"Failed to push image")
                return
            logger.info(f"image files_hash tag {image}:{hash_tag} is pushed")

        # tag and push this final image
        if docker.tag(f"{image}:{hash_tag}", f"{image}:{git_sha}").returncode != 0:
            logger.error("Failed to tag image")
            return
        _tag_to_extra_tags(args, image, git_sha)
        if docker.push(f"{image}:{git_sha}").returncode != 0:
            logger.error("Failed to push image")
            return
        digest = get_image_digest(f"{image}:{git_sha}")
        if not digest:
            logger.error("Failed to get digest for image")
            return
        logger.info(f"image {image}:{git_sha} is pushed, digest: {digest}")

        return digest

    return 0 if _build(args.registry, args.name, args.git_sha_tag) else 3


def build_with_raw_command(args, image, dockerfile_path, image_tag):
    build_time = get_datetime()
    return docker.build(
        args.path,
        f=dockerfile_path,
        build_arg=[
            f"GIT_SHA={args.git_sha}",
            f"TIMESTAMP={build_time}",
            f"IMAGE_TAG={args.git_sha_tag}",
        ]
        + args.build_arg,
        t=f"{image}:{image_tag}",
    ).returncode


def build(args):
    if args.raw:
        ret = build_with_raw_command(args, args.name, args.file, args.git_sha_tag)
        _tag_to_extra_tags(args, args.name, args.git_sha_tag)
        return ret
    return build_with_cache(args)


def main():
    """
    e.g.
    image-builder -v build . -n image1 -v abcd268122c7ea9ac79f1801108e0b59824c1341
    """
    parser = argparse.ArgumentParser(
        epilog=main.__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "-d", "--dry-run", action="store_true", default=0, help="Dry run mode."
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="Verbosity. Default is WARNING level.",
    )

    subparsers = parser.add_subparsers(help="Sub commands", dest="subparser")
    subparsers.required = True

    build_parser = subparsers.add_parser(
        "build",
        description="Build an image from Dockerfile, caching image hierarchy",
        help="Build an image from a Dockerfile",
    )
    build_parser.add_argument(
        "path", metavar="PATH", help="The build context directory"
    )
    build_parser.add_argument(
        "-f",
        "--file",
        help="Name of the Dockerfile. If not provided, "
        "will use config.DOCKERFILE_PATH_PATTERN to compute. ",
    )
    build_parser.add_argument(
        "-v",
        "--git-sha",
        required=True,
        help="The version of code to build against, " "will pass as GIT_SHA variable",
    )
    build_parser.add_argument(
        "-n", "--name", required=True, help="The name of the image to build"
    )
    build_parser.add_argument(
        "--build-arg",
        metavar="ARG=VALUE",
        nargs="*",
        default=[],
        help="Set extra build-time variables. GIT_SHA, TIMESTAMP will be passed by default.",
    )
    build_parser.add_argument(
        "-r",
        "--raw",
        action="store_true",
        help="Whether to use raw docker build command to build, skipping caching logic",
    )
    build_parser.add_argument(
        "--registry",
        default=config.DOCKER_REGISTRY,
        help="Docker registry use to determine the image identity, "
        "can be set via IMAGE_BUILDER_DOCKER_REGISTRY environment variable, "
        'or set DOCKER_REGISTRY in config.py. Default is "%(default)s"',
    )
    build_parser.add_argument(
        "-t",
        "--tag-pattern",
        default=config.GIT_SHA_TAG_PATTERN,
        help="Tag pattern, can only include one `{git_sha}` placeholder, "
        'such as "{git_sha}-new". If the tag exists, we won\'t rebuild it. '
        'Default is "%(default)s"',
    )
    build_parser.add_argument(
        "-e",
        "--extra-tag",
        nargs="*",
        default=[],
        help="Extra tags to tag to the final images",
    )
    build_parser.add_argument(
        "--extra-name",
        nargs="*",
        default=[],
        help="Extra name and optionally with a tag in the 'name:tag' format",
    )
    build_parser.add_argument(
        "-o", "--output-hash", help="The output filename of the files hash log."
    )
    build_parser.set_defaults(func=build)

    args = parser.parse_args()
    if args.dry_run:
        # DRY_RUN env will be read in image_builder.libs.process
        os.environ["DRY_RUN"] = "1"

    if args.func == build:
        args.path = expand_path(args.path)
        if args.output_hash:
            args.output_hash = expand_path(args.output_hash)

        args.file = args.file or locate_dockerfile(args.name)
        args.file = expand_path(args.file)
        # set environ for main dockerfile for possibly retrieving later
        os.environ[
            config.DOCKERFILE_ENV_PATTERN.format(image_name=args.name)
        ] = args.file

        # change CWD to PATH
        os.chdir(args.path)

        if not args.registry:
            parser.error(
                "--registry should be provied "
                "or specified by IMAGE_BUILDER_DOCKER_REGISTRY environment variable or set DOCKER_REGISTRY in config.py"
            )
        if not all("=" in kv for kv in args.build_arg):
            parser.error("--build_arg must be in ARG=VALUE format")

        # set git_sha_tag
        try:
            args.git_sha_tag = args.tag_pattern.format(git_sha=args.git_sha)
        except KeyError:
            parser.error(
                'Wrong --tag-pattern provided. Can only include one `{git_sha}` placeholder, such as "{git_sha}-new"'
            )

    # setup logging
    level = logging.WARNING - args.verbose * 10
    logging.basicConfig(
        level=level, format="%(asctime)s %(name)s %(levelname)s %(message)s"
    )

    if args.output_hash:
        h = logging.FileHandler(args.output_hash)
        h.setLevel(logging.DEBUG)
        h.setFormatter(logging.Formatter("%(message)s"))
        hash_logger.addHandler(h)

    # Suppress warning when we don't verify ssl
    import urllib3

    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    return args.func(args)
