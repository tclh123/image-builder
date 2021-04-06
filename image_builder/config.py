import os

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DOCKER_REGISTRY = "docker-registry.example.com:5000"
DOCKERFILE_PATH_PATTERN = os.path.join(
    os.path.dirname(PROJECT_ROOT), "images/{image_name}/Dockerfile"
)

FILES_HASH_TAG_PATTERN = "hash-{files_hash}"
GIT_SHA_TAG_PATTERN = "{git_sha}-untested"

READ_FILE_BLOCKSIZE = 65536

DOCKER_REGISTRY_IMAGE_API = "https://{registry}/v2/{image_name}/manifests/{image_tag}"

ENV_PREFIX = "IMAGE_BUILDER_"

DOCKERFILE_ENV_PATTERN = ENV_PREFIX + "DOCKERFILE_{image_name}"
BUILD_CONTEXT_ENV_PATTERN = ENV_PREFIX + "BUILD_CONTEXT_{image_name}"


class Config:
    def __getattr__(self, name):
        """retrieve config value, read environment variables to override configs"""
        cfg_value = os.environ.get(ENV_PREFIX + name, globals().get(name))
        if cfg_value is None:
            raise AttributeError(f"module {__name__} has no attribute {name}")
        return cfg_value


config = Config()
