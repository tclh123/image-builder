import os
from datetime import datetime


def expand_path(path):
    return os.path.abspath(os.path.expanduser(path))


def get_datetime():
    return datetime.utcnow().isoformat()
