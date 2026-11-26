"""Content-addressed blob store: blobs/<sha[0:2]>/<sha>. Bytes are never modified or removed."""
import hashlib
import os
import tempfile


class BlobStore:
    def __init__(self, root: str):
        self.root = root
        os.makedirs(root, exist_ok=True)

    def path(self, sha256: str) -> str:
        if len(sha256) != 64 or any(c not in "0123456789abcdef" for c in sha256):
            raise ValueError("bad sha256")
        return os.path.join(self.root, sha256[:2], sha256)

    def put(self, data: bytes) -> tuple[str, int]:
        """Write to a temp file, hash, then atomically move into place. Returns (sha256, size)."""
        sha = hashlib.sha256(data).hexdigest()
        dest = self.path(sha)
        if os.path.exists(dest):
            return sha, len(data)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.root, prefix=".upload-")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.chmod(tmp, 0o444)
            os.replace(tmp, dest)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
        return sha, len(data)

    def read(self, sha256: str) -> bytes:
        with open(self.path(sha256), "rb") as f:
            return f.read()
