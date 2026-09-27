"""Qualify the exact wheel with its S3 extra against the real pinned storage service."""

from service_qualification import qualify

if __name__ == "__main__":
    qualify("s3")
