import os
import shutil
from typing import Optional, BinaryIO
from abc import ABC, abstractmethod
import boto3
from pathlib import Path
import logging
from datetime import datetime, timedelta

from .config import settings, StorageMode

logger = logging.getLogger(__name__)


class StorageInterface(ABC):
    @abstractmethod
    async def save_upload(self, job_id: str, file_content: bytes, filename: str) -> str:
        pass

    @abstractmethod
    async def save_result(self, job_id: str, file_path: str, filename: str) -> str:
        pass

    @abstractmethod
    async def get_download_url(self, job_id: str, filename: str) -> str:
        pass

    @abstractmethod
    async def get_file_path(self, job_id: str, filename: str, file_type: str = "uploads") -> str:
        pass

    @abstractmethod
    async def cleanup(self, job_id: str) -> None:
        pass

    @abstractmethod
    async def exists(self, job_id: str, filename: str, file_type: str = "results") -> bool:
        pass


class LocalStorage(StorageInterface):
    def __init__(self):
        self.base_path = Path(settings.local_storage_path)
        self._ensure_directories()

    def _ensure_directories(self):
        (self.base_path / "uploads").mkdir(parents=True, exist_ok=True)
        (self.base_path / "results").mkdir(parents=True, exist_ok=True)

    async def save_upload(self, job_id: str, file_content: bytes, filename: str) -> str:
        upload_dir = self.base_path / "uploads" / job_id
        upload_dir.mkdir(parents=True, exist_ok=True)

        file_path = upload_dir / filename
        with open(file_path, 'wb') as f:
            f.write(file_content)

        logger.info(f"Saved upload to {file_path}")
        return str(file_path)

    async def save_result(self, job_id: str, file_path: str, filename: str) -> str:
        result_dir = self.base_path / "results" / job_id
        result_dir.mkdir(parents=True, exist_ok=True)

        dest_path = result_dir / filename
        shutil.copy2(file_path, dest_path)

        logger.info(f"Saved result to {dest_path}")
        return str(dest_path)

    async def get_download_url(self, job_id: str, filename: str) -> str:
        # For local storage, return a path that the API endpoint will serve
        return f"/download/{job_id}/{filename}"

    async def get_file_path(self, job_id: str, filename: str, file_type: str = "uploads") -> str:
        return str(self.base_path / file_type / job_id / filename)

    async def cleanup(self, job_id: str) -> None:
        for dir_type in ["uploads", "results"]:
            dir_path = self.base_path / dir_type / job_id
            if dir_path.exists():
                shutil.rmtree(dir_path)
                logger.info(f"Cleaned up {dir_path}")

    async def exists(self, job_id: str, filename: str, file_type: str = "results") -> bool:
        file_path = self.base_path / file_type / job_id / filename
        return file_path.exists()


class S3Storage(StorageInterface):
    def __init__(self):
        self.s3_client = boto3.client('s3', region_name=settings.aws_region)
        self.bucket = settings.s3_bucket

        if not self.bucket:
            raise ValueError("S3_BUCKET environment variable is required for S3 storage mode")

    async def save_upload(self, job_id: str, file_content: bytes, filename: str) -> str:
        key = f"uploads/{job_id}/{filename}"

        self.s3_client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=file_content
        )

        logger.info(f"Uploaded to S3: {key}")
        return f"s3://{self.bucket}/{key}"

    async def save_result(self, job_id: str, file_path: str, filename: str) -> str:
        key = f"results/{job_id}/{filename}"

        with open(file_path, 'rb') as f:
            self.s3_client.upload_fileobj(f, self.bucket, key)

        logger.info(f"Uploaded result to S3: {key}")
        return f"s3://{self.bucket}/{key}"

    async def get_download_url(self, job_id: str, filename: str) -> str:
        key = f"results/{job_id}/{filename}"

        url = self.s3_client.generate_presigned_url(
            'get_object',
            Params={'Bucket': self.bucket, 'Key': key},
            ExpiresIn=86400  # 24 hours
        )

        return url

    async def get_file_path(self, job_id: str, filename: str, file_type: str = "uploads") -> str:
        # For S3, download to temp location
        key = f"{file_type}/{job_id}/{filename}"
        temp_path = f"/tmp/{job_id}_{filename}"

        self.s3_client.download_file(self.bucket, key, temp_path)
        logger.info(f"Downloaded from S3 to {temp_path}")

        return temp_path

    async def cleanup(self, job_id: str) -> None:
        # List and delete all objects with the job_id prefix
        for prefix in [f"uploads/{job_id}/", f"results/{job_id}/"]:
            response = self.s3_client.list_objects_v2(
                Bucket=self.bucket,
                Prefix=prefix
            )

            if 'Contents' in response:
                objects = [{'Key': obj['Key']} for obj in response['Contents']]
                self.s3_client.delete_objects(
                    Bucket=self.bucket,
                    Delete={'Objects': objects}
                )
                logger.info(f"Deleted S3 objects with prefix: {prefix}")

    async def exists(self, job_id: str, filename: str, file_type: str = "results") -> bool:
        key = f"{file_type}/{job_id}/{filename}"

        try:
            self.s3_client.head_object(Bucket=self.bucket, Key=key)
            return True
        except self.s3_client.exceptions.ClientError:
            return False


class StorageFactory:
    @staticmethod
    def get_storage() -> StorageInterface:
        if settings.storage_mode == StorageMode.S3:
            return S3Storage()
        else:
            return LocalStorage()


# Global storage instance
storage = StorageFactory.get_storage()