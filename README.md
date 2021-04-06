# Docker Image Builder

In general, this tool parses Dockerfile and finds out all parents of the image.
It will build parent images first, because you need them ready to build this image.
Besides, if the files used to build the image didn't change, we will not rebuild the image,
just tag the old image to new git sha version instead.
This is done by parsing all COPYs/ADDs in Dockerfile and globbing files in the build context directory,
and calculating hash with its parent image digests. Then tag the intermediate image to `hash-{hash}` to cache it.

Pseudocode code as below:
```python
def build(image, git_sha):
    if image:{git_sha} exists:
        return image digest
    hash = new hash
    for parent_image in parents:
        hash.update(parent hash = build(parent_image, git_sha))
    hash.update(files_hash = current level files/COPYs/ADDs)
    if not image:hash-{hash} exists:
        docker build via Dockerfile
    return image digest
```

You can regard this tool as a docker build command wrapper.

This tool will respect .dockerignore, which means we will not use the files ignored by docker to calculate hash.

## Installation

```shell
# this will create venv/ under this folder, and install image-builder into venv.
make install

# you can create an alias for easy use
alias ib=`pwd`/venv/bin/image-builder

# or a symlink to your personal bin dir
mkdir -p ~/bin
ln -s `pwd`/venv/bin/image-builder ~/bin/ib
export PATH="$HOME/bin:${PATH}"
```

## Usage

```shell
# help messages
image-builder -h
image-builder build -h

# build image. -v for INFO verbosity level, otherwise is WARNING level
image-builder -v build . -n image1 -v abcd268122c7ea9ac79f1801108e0b59824c1341

# dry run
image-builder -dv build . -n image1 -v abcd268122c7ea9ac79f1801108e0b59824c1341

# more verbosity
image-builder -dvv build . -n image1 -v abcd268122c7ea9ac79f1801108e0b59824c1341

# specify Dockerfile
image-builder -v build . -f tools/images/image1/Dockerfile -n image1 -v abcd268122c7ea9ac79f1801108e0b59824c1341

# specify different PATH (build context directory)
image-builder -v build ~/path/to/image1 -n image1 -v abcd268122c7ea9ac79f1801108e0b59824c1341

# more build args
image-builder -v build . -n image1 -v abcd268122c7ea9ac79f1801108e0b59824c1341 --build-arg A=1 B=2 C=3

# build by only raw docker command, skipping anything else (parent image building, content hashing, image caching, etc.)
image-builder -v build . -n image1 -v abcd268122c7ea9ac79f1801108e0b59824c1341 -r

# specify another registry
image-builder -v build . -n image1 -v abcd268122c7ea9ac79f1801108e0b59824c1341 --registry another-registry.com

# specify another tag pattern, default is specified by config.GIT_SHA_TAG_PATTERN
image-builder -v build . -n image1 -v abcd268122c7ea9ac79f1801108e0b59824c1341 -t {git_sha}-untested-new

# tag to another testing tag. If the tag already exists, we won't rebuild
image-builder -v build . -n image1 -v abcd268122c7ea9ac79f1801108e0b59824c1341 -t hl-test

# tag the final images to extra tags
image-builder -v build . -n image1 -v abcd268122c7ea9ac79f1801108e0b59824c1341 -e latest latest2

# specify special location of Dockerfile and build context for each image
IMAGE_BUILDER_DOCKERFILE_packer=~/path/to/Dockerfile IMAGE_BUILDER_BUILD_CONTEXT_packer=~/path/to/images/packer/ ib build . -n packer-code -v `git rev-parse HEAD`

# debug the files hash using -o option
image-builder -v build . -n image1 -v abcd268122c7ea9ac79f1801108e0b59824c1341 -o /tmp/files-hash.log
image-builder -v build . -n image1 -v abcd268122c7ea9ac79f1801108e0b59824c1341 -o /dev/stdout
```

### configuration

Every configuration entry in [image_builder/config.py](image_builder/config.py) can be overrided by environment variables.
Such as, environment variable `IMAGE_BUILDER_DOCKER_REGISTRY` will override `image_builder.config.DOCKER_REGISTRY`.
