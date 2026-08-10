#!/usr/bin/env python
import copy
import hashlib
import logging
import os
import os.path
import threading
import time
from abc import ABCMeta, abstractmethod
from datetime import UTC, datetime, timedelta
from http.client import BadStatusLine, IncompleteRead
from io import BytesIO
from json import JSONDecodeError
from mimetypes import MimeTypes
from socket import gaierror as socket_gaierror
from ssl import SSLEOFError, SSLError
from time import sleep
from typing import Any
from urllib.request import pathname2url

import boto3
import dateutil.parser
import requests
import sentry_sdk
import zmq
from botocore.exceptions import EndpointConnectionError
from cachetools import LRUCache
from googleapiclient.errors import HttpError
from httplib2.error import HttpLib2Error
from pika.exceptions import (
    AMQPConnectionError,
    ConnectionClosedByBroker,
    StreamLostError,
)
from PIL import Image
from pydrive2.auth import GoogleAuth
from pydrive2.drive import GoogleDrive
from pydrive2.files import ApiRequestError, FileNotUploadedError
from requests.adapters import ConnectionError
from requests.exceptions import RequestException, Timeout
from sentry_sdk import metrics
from sentry_sdk.integrations.asyncio import AsyncioIntegration
from sentry_sdk.integrations.logging import ignore_logger
from sentry_sdk.integrations.sys_exit import SysExitIntegration
from sentry_sdk.integrations.threading import ThreadingIntegration
from ultralytics import YOLO
from ultralytics.engine.results import Results
from watchdog.events import (
    FileClosedEvent,
    FileModifiedEvent,
    FileMovedEvent,
    FileSystemEventHandler,
)
from watchdog.observers import Observer
from zmq import ContextTerminated

from tailucas_pylib import APP_NAME, DEVICE_NAME, app_config, log, threads
from tailucas_pylib.app import AppThread, ZmqRelay
from tailucas_pylib.aws.metrics import post_count_metric
from tailucas_pylib.creds import Creds
from tailucas_pylib.datetime import (
    make_iso_timestamp,
    make_timestamp,
)
from tailucas_pylib.flags import is_flag_enabled
from tailucas_pylib.handler import exception_handler
from tailucas_pylib.process import SignalHandler
from tailucas_pylib.rabbit import RabbitMQRelay, ZMQListener
from tailucas_pylib.threads import bye, die, thread_nanny
from tailucas_pylib.zmq import URL_WORKER_APP, Closable, try_close, zmq_socket, zmq_term

URL_WORKER_RABBIT_PUBLISHER = "inproc://rabbitmq-publisher"
URL_WORKER_OBJECT_DETECTOR = "inproc://object-detector"
URL_WORKER_CLOUD_STORAGE = "inproc://cloud-storage"

FEATURE_FLAG_OBJECT_DETECTION = "object-detection"
FEATURE_FLAG_CLOUD_OBJECT_DETECTION = "cloud-object-detection"
FEATURE_FLAG_LOCAL_OBJECT_DETECTION = "local-object-detection"
FEATURE_FLAG_CLOUD_STORAGE_MANAGEMENT = "cloud-storage-management"

HEARTBEAT_INTERVAL_SECONDS = 5


def create_snapshot_path(parent_path, operation, unix_timestamp, file_extension):
    return os.path.join(parent_path, f"{operation}_" + str(unix_timestamp) + "." + file_extension)


def create_publisher_struct(device_key, device_label, image_data, image_timestamp, storage_url, storage_path):
    return {
        "inputs": [
            {
                "device_key": device_key,
                "device_label": device_label,
                "type": "camera",
                "image": image_data,
                "image_timestamp": str(image_timestamp),
                "storage_url": storage_url,
                "storage_path": storage_path,
            }
        ]
    }


class CameraConfig:
    def __init__(self, device_key, device_label, camera_config, camera_storage=None):
        # extract connection configuration from something of this format:
        # username:password@ip:port,rtsp_port
        camera_auth, camera_url = camera_config.split("@")
        if ":" not in camera_auth or ":" not in camera_url:
            raise AssertionError(f"Camera parameters missing for '{device_key}.'")
        # split the rtsp port number
        camera_url_parts = camera_url.split(",")
        if len(camera_url_parts) == 1:
            camera_url = camera_url_parts[0]
            rtsp_port = camera_url.split(":")[1]
        elif len(camera_url_parts) == 2:
            camera_url, rtsp_port = camera_url_parts
        # set locals
        self._name = device_key
        self._device_key = device_key
        self._device_label = device_label
        self._basic_auth = camera_auth
        self._username, self._password = camera_auth.split(":")
        self._url = camera_url
        self._ip, self._port = camera_url.split(":")
        self._rtsp_port = rtsp_port
        self._camera_storage = camera_storage

    def __str__(self) -> str:
        return str(self._url)

    @property
    def name(self):
        return self._name

    @property
    def device_key(self):
        return self._device_key

    @property
    def device_label(self):
        return self._device_label

    @property
    def basic_auth(self):
        return self._basic_auth

    @property
    def username(self):
        return self._username

    @property
    def password(self):
        return self._password

    @property
    def url(self):
        return self._url

    @property
    def ip(self):
        return self._ip

    @property
    def port(self):
        return self._port

    @property
    def rtsp_port(self):
        return self.rtsp_port

    @property
    def camera_storage(self):
        return self._camera_storage


class FileType:
    def __init__(self):
        self.mime = MimeTypes()

    def mime_type(self, file_path):
        mime_type = self.mime.guess_type(pathname2url(file_path))
        if mime_type is not None and len(mime_type) > 0:
            return mime_type[0]
        return None

    def test_type(self, file_path, file_type):
        mime_type = self.mime_type(file_path)
        if mime_type is not None and mime_type.startswith(f"{file_type}/"):
            # return the specific file type
            return mime_type.split("/")[1]
        return None


class Snapshot(ZmqRelay):
    def __init__(self, camera_profiles, cloud_storage_url, mq_device_topic):
        ZmqRelay.__init__(
            self,
            name=self.__class__.__name__,
            source_zmq_url=URL_WORKER_APP,
            sink_zmq_url=URL_WORKER_OBJECT_DETECTOR,
        )

        self.cameras = camera_profiles
        self.default_command = app_config.get("camera", "default_command")
        self.default_image_format = app_config.get("camera", "default_image_format")

        self.cloud_storage_url = cloud_storage_url

        self.capture_threads = {}
        self._mq_device_topic = mq_device_topic

    @staticmethod
    def visit_keys(dictionary, parent_key=""):
        for key, value in dictionary.items():
            full_key = f"{parent_key}.{key}" if parent_key else key
            if isinstance(value, dict):
                Snapshot.visit_keys(value, full_key)
            elif isinstance(value, (str, int)):
                log.debug("Configuration value", extra={"full_key": full_key, "value": value})
            else:
                log.debug(
                    "Configuration value type",
                    extra={"full_key": full_key, "value_type": str(type(value))},
                )

    def process_message(self, sink_socket):
        control_payload = self.socket.recv_pyobj()
        if (
            not isinstance(control_payload, dict)
            or "snapshot" not in control_payload
            or "output_triggered" not in control_payload["snapshot"]
        ):
            log.error("Malformed event payload", extra={"control_payload": control_payload})
            return
        timestamp = make_timestamp(timestamp=control_payload["snapshot"]["timestamp"])
        output_trigger = control_payload["snapshot"]["output_triggered"]
        device_key = output_trigger["device_key"]
        device_label = output_trigger["device_label"]
        device_params = output_trigger["device_params"]
        if device_key not in self.cameras:
            log.error("Camera configuration missing", extra={"device_label": device_label})
            post_count_metric("Errors")
            return
        try:
            camera_config = CameraConfig(
                device_key=device_key,
                device_label=device_label,
                camera_config=device_params,
                camera_storage=self.cameras[device_key]["storage"],
            )
        except AssertionError:
            post_count_metric("Errors")
            return
        log.debug(
            "Fetching image data from IP camera",
            extra={"device_label": device_label, "camera_url": camera_config.url},
        )
        image_data = None
        im = None
        # grab a first frame for overall context
        for tries in range(1, 4):
            try:
                start_time = time.time() * 1000
                r = requests.get(
                    f"http://{camera_config.url}/cgi-bin/CGIProxy.fcgi",
                    params={
                        "cmd": self.default_command,
                        "usr": camera_config.username,
                        "pwd": camera_config.password,
                    },
                    timeout=4,
                )
                end_time = time.time() * 1000
                metrics.distribution(
                    name="capture_time",
                    value=end_time - start_time,
                    unit="milliseconds",
                    attributes={"device_key": device_key, "device_label": device_label},
                )
                image_data = r.content
                im = Image.open(BytesIO(image_data))
                if im.format is not None:
                    break
                else:
                    raise AssertionError(f"Bad image data detected: {im!s}")
            except (
                OSError,
                ConnectionError,
                RequestException,
                AssertionError,
                Timeout,
            ) as e:
                log.warning(
                    "Problem getting image. Retrying...",
                    extra={"camera_url": camera_config.url, "error": str(e)},
                )
                sleep(0.1)
                if tries >= 3:
                    log.warning(
                        "Giving up getting image",
                        extra={
                            "camera_url": camera_config.url,
                            "tries": tries,
                            "error": str(e),
                        },
                    )
                    post_count_metric("Errors")
                    break
        if image_data is not None and im is not None and im.format is not None:
            # construct message to publish
            unix_timestamp = int((timestamp.replace(tzinfo=None) - datetime(1970, 1, 1)).total_seconds())
            log.debug(
                "Basing unix timestamp off of timestamp",
                extra={"unix_timestamp": unix_timestamp, "timestamp": str(timestamp)},
            )
            # create output file path
            normalized_name = device_key.lower().replace(" ", "-")
            output_filename = create_snapshot_path(
                parent_path=camera_config.camera_storage,
                operation=f"fetch_{normalized_name}",
                unix_timestamp=unix_timestamp,
                file_extension=self.default_image_format,
            )
            # publisher data
            publisher_data = create_publisher_struct(
                device_key=device_key,
                device_label=device_label,
                image_data=image_data,
                image_timestamp=unix_timestamp,
                storage_url=self.cloud_storage_url,
                storage_path=output_filename,
            )
            log.debug(
                "Sending image for object detection",
                extra={
                    "device_label": device_label,
                    "image_format": im.format,
                    "image_size": im.size,
                    "image_mode": im.mode,
                },
            )
            # send image data for processing
            start_time = time.time() * 1000
            sink_socket.send_pyobj(
                (
                    f"event.notify.{self._mq_device_topic}.{DEVICE_NAME}.image",
                    publisher_data,
                )
            )
            end_time = time.time() * 1000
            metrics.distribution(
                name="snapshot_handoff_time",
                value=end_time - start_time,
                unit="milliseconds",
                attributes={"device_key": device_key, "device_label": device_label},
            )
            log.debug(
                "Saving image data",
                extra={"device_label": device_label, "output_filename": output_filename},
            )
            # persist for Cloud
            try:
                im.save(output_filename)
            except OSError as e:
                log.exception(
                    "Problem saving image",
                    extra={"output_filename": output_filename, "error": str(e)},
                )
                die(e)


class DeviceEvent:
    def __init__(self, device_key, device_type, device_location=None):
        self._device_key = device_key
        self._device_type = device_type
        self._device_location = device_location

        self._timestamp = None
        self._event_detail = None

    @property
    def device_key(self):
        return self._device_key

    @property
    def device_type(self):
        return self._device_type

    @property
    def device_location(self):
        return self._device_location

    @property
    def timestamp(self):
        if self._timestamp is None:
            self._timestamp = make_timestamp()
        return self._timestamp

    @timestamp.setter
    def timestamp(self, value):
        self._timestamp = make_timestamp(timestamp=value)

    @property
    def timestamp_string(self):
        return make_iso_timestamp(timestamp=self._timestamp)

    @property
    def event_detail(self):
        return self._event_detail

    @event_detail.setter
    def event_detail(self, value):
        self._event_detail = value

    @property
    def dict(self):
        representation = {
            "device_key": self._device_key,
            "device_type": self._device_type,
            "timestamp": self.timestamp_string,
        }
        if self._device_location:
            representation.update({"device_location": self._device_location})
        if self._event_detail:
            representation.update({"event_detail": self._event_detail})
        return representation

    def __str__(self):
        return self._device_key


class CloudStorage(metaclass=ABCMeta):
    @abstractmethod
    def cloud_storage_url(self):
        return NotImplemented


class GoogleDriveManager(CloudStorage):
    def __init__(self, gauth_creds_file, gdrive_folder):
        self._gdrive_folder = gdrive_folder
        if "~" in gauth_creds_file:
            self._gauth_creds_file = os.path.expanduser(gauth_creds_file)
        else:
            self._gauth_creds_file = os.path.abspath(gauth_creds_file)
        self.drive = GoogleDrive(self.gauth)
        # set by the thread
        self._gdrive_folder_id = None
        self._gdrive_folder_url = None

    @property
    def cloud_storage_folder_id(self):
        return self._gdrive_folder_id

    @property
    def cloud_storage_url(self):
        return self._gdrive_folder_url

    @property
    def gauth(self):
        auth = GoogleAuth()
        if not os.path.exists(self._gauth_creds_file):
            log.debug(
                "Google credentials not found. Interactive setup may follow.",
                extra={"gauth_creds_file": self._gauth_creds_file},
            )
        # Try to load saved client credentials
        auth.LoadCredentialsFile(self._gauth_creds_file)
        if auth.credentials is None:
            # Authenticate if they're not there
            auth.LocalWebserverAuth()
        elif auth.access_token_expired:
            # Refresh them if expired
            auth.Refresh()
        else:
            # Initialize the saved creds
            auth.Authorize()
        if not os.path.exists(self._gauth_creds_file):
            # Save the current credentials to a file
            auth.SaveCredentialsFile(self._gauth_creds_file)
            log.debug(
                "Saved Google credentials",
                extra={"gauth_creds_file": self._gauth_creds_file},
            )
        return auth

    @staticmethod
    def _get_gdrive_folder_id(gdrive, gdrive_folder, parent_id="root", create=True):
        log.debug(
            "Checking for existence of Google Drive folder",
            extra={"gdrive_folder": gdrive_folder},
        )
        file_list = gdrive.ListFile(
            {
                "q": f"'{parent_id}' in parents and trashed=false and mimeType = 'application/vnd.google-apps.folder' and title = '{gdrive_folder}'"
            }
        ).GetList()
        if len(file_list) == 0:
            if not create:
                return None
            log.debug(
                "Creating Google Drive folder",
                extra={"gdrive_folder": gdrive_folder, "parent_id": parent_id},
            )
            folder = gdrive.CreateFile(
                {
                    "description": f"Created by {APP_NAME}",
                    "title": gdrive_folder,
                    "mimeType": "application/vnd.google-apps.folder",
                    "parents": [{"kind": "drive#parentReference", "id": parent_id}],
                }
            )
            folder.Upload()
            folder_id = folder["id"]
            folder_link = folder["alternateLink"]
        elif len(file_list) == 1:
            folder_id = file_list[0]["id"]
            folder_link = file_list[0]["alternateLink"]
        else:
            raise RuntimeError(f"Unexpected result listing Google Drive for {gdrive_folder}: {file_list!s}")
        log.debug(
            "Google Drive folder ID resolved",
            extra={
                "gdrive_folder": gdrive_folder,
                "folder_id": folder_id,
                "folder_link": folder_link,
            },
        )
        return folder_id, folder_link


class GoogleDriveArchiver(AppThread, GoogleDriveManager):
    def __init__(self, gauth_creds_file, gdrive_folder, gdrive_folder_id, gdrive_folder_url):
        AppThread.__init__(self, name=self.__class__.__name__)
        GoogleDriveManager.__init__(self, gauth_creds_file=gauth_creds_file, gdrive_folder=gdrive_folder)

        # separate connection for archiver thread to prevent PyDrive lock-up
        self._archive_drive = GoogleDrive(self.gauth)
        self._folder_id_cache = {}

        self._gdrive_folder_id = gdrive_folder_id
        self._gdrive_folder_url = gdrive_folder_url

    def run(self):
        while not threads.shutting_down:
            log.debug(
                "Finding files to archive",
                extra={
                    "gdrive_folder": self._gdrive_folder,
                    "gdrive_folder_id": self._gdrive_folder_id,
                },
            )
            try:
                file_list = self._archive_drive.ListFile(
                    {
                        "q": f"'{self._gdrive_folder_id}' in parents and trashed=false and mimeType != 'application/vnd.google-apps.folder'",
                        "maxResults": 100,
                    }
                )
                archived = 0
                try:
                    while True:
                        page = file_list.GetList()
                        log.debug(
                            "Inspecting files for archival",
                            extra={"file_count": len(page)},
                        )
                        for file1 in page:
                            if self.archive(
                                gdrive=self._archive_drive,
                                gdrive_file=file1,
                                root_folder_id=self._gdrive_folder_id,
                            ):
                                archived += 1
                except StopIteration:
                    log.debug("Archived image snapshots", extra={"archived_count": archived})
            except (
                OSError,
                ApiRequestError,
                BadStatusLine,
                IncompleteRead,
                BrokenPipeError,
                FileNotUploadedError,
                socket_gaierror,
                HttpError,
                SSLEOFError,
                TimeoutError,
            ) as e:
                log.warning(
                    "Google Drive problem archiving files. Will try again in a minute.",
                    extra={
                        "gdrive_folder": self._gdrive_folder,
                        "gdrive_folder_id": self._gdrive_folder_id,
                        "error": str(e),
                    },
                )
                threads.interruptable_sleep.wait(60)
                continue
            # prevent memory leaks
            self._folder_id_cache.clear()
            # sleep until tomorrow
            threads.interruptable_sleep.wait(60 * 60 * 24)

    def archive(self, gdrive, gdrive_file, root_folder_id):
        filename = gdrive_file["title"]
        now = datetime.now(tz=UTC)
        created_date = dateutil.parser.parse(gdrive_file["createdDate"])
        td = now - created_date
        if td > timedelta(days=1):
            log.debug(
                "Archiving file",
                extra={"filename": filename, "created_days_ago": td.days},
            )
            ymd_date = created_date.strftime("%Y-%m-%d")
            if ymd_date in self._folder_id_cache:
                gdrive_folder_id = self._folder_id_cache[ymd_date]
            else:
                # create the required folder structure
                year_folder_name = created_date.strftime("%Y")
                year_folder_id, _ = self._get_gdrive_folder_id(gdrive, year_folder_name, root_folder_id)
                month_folder_name = created_date.strftime("%m")
                month_folder_id, _ = self._get_gdrive_folder_id(gdrive, month_folder_name, year_folder_id)
                day_folder_name = created_date.strftime("%d")
                day_folder_id, _ = self._get_gdrive_folder_id(gdrive, day_folder_name, month_folder_id)
                self._folder_id_cache[ymd_date] = day_folder_id
                gdrive_folder_id = day_folder_id
            log.debug(
                "File mapped to archive folder",
                extra={
                    "filename": filename,
                    "folder_key": ymd_date,
                    "folder_id": gdrive_folder_id,
                },
            )
            # reset the parent folders, include the existing parents if starred
            if gdrive_file["labels"]["starred"]:
                parents = []
                for parent in gdrive_file["parents"]:
                    parent_id = parent["id"]
                    parents.append(parent_id)
                    log.debug(
                        "Comparing parent with archive folder id",
                        extra={
                            "parent_id": parent_id,
                            "gdrive_folder_id": gdrive_folder_id,
                        },
                    )
                    if gdrive_folder_id == parent_id:
                        log.debug(
                            "File already archived",
                            extra={
                                "filename": filename,
                                "gdrive_folder_id": gdrive_folder_id,
                            },
                        )
                        return False
                log.debug(
                    "Archiving starred file, but leaving existing parents intact.",
                    extra={"filename": filename},
                )
                # new parent for archival
                gdrive_parents = [{"kind": "drive#parentReference", "id": gdrive_folder_id}]
                # existing parents
                for parent in parents:
                    # simply appending the parents array returned by the service is insufficient
                    # possibly due to PyDrive's change detection, or Drive
                    gdrive_parents.append({"kind": "drive#parentReference", "id": parent})
                gdrive_file["parents"] = gdrive_parents
            else:
                # otherwise, clobber the existing parent information
                gdrive_file["parents"] = [{"kind": "drive#parentReference", "id": gdrive_folder_id}]
            # update the file metadata
            gdrive_file.Upload()
            return True
        return False


class GoogleDriveUploader(AppThread, GoogleDriveManager):
    def __init__(self, gauth_creds_file, gdrive_folder):
        # set up remote service setup first
        GoogleDriveManager.__init__(self, gauth_creds_file=gauth_creds_file, gdrive_folder=gdrive_folder)
        AppThread.__init__(self, name=self.__class__.__name__)

        # determine the Drive folder details synchronously
        self._gdrive_folder_id, self._gdrive_folder_url = self._get_gdrive_folder_id(self.drive, self._gdrive_folder)

        self._filetype = FileType()

    def run(self):
        with exception_handler(
            connect_url=URL_WORKER_CLOUD_STORAGE,
            socket_type=zmq.PULL,
            and_raise=False,
            shutdown_on_error=True,
        ) as zmq_socket:
            while not threads.shutting_down:
                (snapshot_path, snapshot_timestamp) = zmq_socket.recv_pyobj()
                if "fetch" in snapshot_path:
                    log.debug("Not uploading snapshot", extra={"snapshot_path": snapshot_path})
                    continue
                while not self.upload(file_path=snapshot_path, created_time=snapshot_timestamp):
                    # never spin
                    threads.interruptable_sleep.wait(1)

    def upload(self, file_path, created_time=None):
        # verify the snapshot
        try:
            with Image.open(file_path) as img:
                img.verify()
        except OSError, SyntaxError:
            log.warning("Not uploading corrupted image file", extra={"file_path": file_path})
            return True
        # upload the snapshot
        mime_type = self._filetype.mime_type(file_path)
        file_size = os.path.getsize(filename=file_path)
        log.debug(
            "File ready for upload",
            extra={
                "mime_type": mime_type,
                "file_path": file_path,
                "file_size_bytes": file_size,
            },
        )
        created_date = None
        if created_time is None:
            log.debug("Uploading file to Google Drive", extra={"file_path": file_path})
        else:
            # datetime.isoformat doesn't work because of the seconds
            # separator required by RFC3339, and the extra requirement to have
            # the colon in the TZ offset if not in UTC.
            offset = created_time.strftime("%z")
            created_date = created_time.strftime("%Y-%m-%dT%H:%M:%S.00") + offset[:3] + ":" + offset[3:]
            log.debug(
                "Uploading file to Google Drive with created time",
                extra={"file_path": file_path, "created_date": created_date},
            )
        file_base_name = os.path.basename(file_path)
        try:
            f = self.drive.CreateFile(
                {
                    "title": file_base_name,
                    "mimeType": mime_type,
                    "createdDate": created_date,
                    "parents": [{"kind": "drive#fileLink", "id": self._gdrive_folder_id}],
                }
            )
            f.SetContentFile(file_path)
            f.Upload()
        except (
            OSError,
            ApiRequestError,
            BadStatusLine,
            IncompleteRead,
            BrokenPipeError,
            FileNotUploadedError,
            HttpError,
            SSLEOFError,
            TimeoutError,
        ) as e:
            log.warning(
                "Google Drive problem uploading file",
                extra={"file_path": file_path, "error": str(e)},
            )
            return False
        thumbnail_link = None
        if "thumbnailLink" in f:
            link = f["thumbnailLink"]
            # specify our own thumbnail size
            if "=" in link:
                link = link.rsplit("=")[0]
                link += "=s1024"
            thumbnail_link = link
        upload_file_id = f["id"]
        log.debug(
            "Uploaded file to Google Drive folder",
            extra={
                "file_base_name": file_base_name,
                "upload_file_id": upload_file_id,
                "gdrive_folder": self._gdrive_folder,
                "thumbnail_link": thumbnail_link,
            },
        )
        remove_upload = False
        if "fileSize" in f:
            upload_size = int(f["fileSize"])
            if upload_size != file_size:
                log.warning(
                    "Upload file size mismatch",
                    extra={
                        "file_base_name": file_base_name,
                        "upload_file_id": upload_file_id,
                        "expected_bytes": file_size,
                        "uploaded_bytes": upload_size,
                    },
                )
                remove_upload = True
            else:
                log.debug(
                    "Upload file size matches. Validating checksum...",
                    extra={
                        "file_base_name": file_base_name,
                        "file_size_bytes": file_size,
                    },
                )
                # checksum files if sizes are the same
                file_checksum = None
                with open(file_path, "rb") as file_content:
                    data = file_content.read()
                    file_checksum = hashlib.md5(data).hexdigest()
                if "md5Checksum" in f:
                    upload_checksum = f["md5Checksum"]
                    log.debug(
                        "Source/Upload checksum compared",
                        extra={
                            "file_base_name": file_base_name,
                            "source_checksum": file_checksum,
                            "upload_checksum": upload_checksum,
                        },
                    )
                    if file_checksum:
                        if file_checksum == upload_checksum:
                            log.debug(
                                "Upload checksum matches",
                                extra={
                                    "file_base_name": file_base_name,
                                    "checksum": file_checksum,
                                },
                            )
                        else:
                            log.warning(
                                "Upload checksum mismatch",
                                extra={
                                    "file_base_name": file_base_name,
                                    "upload_file_id": upload_file_id,
                                    "expected_checksum": file_checksum,
                                    "uploaded_checksum": upload_checksum,
                                },
                            )
                            remove_upload = True
        if remove_upload:
            log.debug(
                "Trashing file from Google Drive folder",
                extra={
                    "file_base_name": file_base_name,
                    "upload_file_id": upload_file_id,
                    "gdrive_folder": self._gdrive_folder,
                },
            )
            for _tries in range(1, 3):
                try:
                    f.Trash()
                    break
                except (BrokenPipeError, JSONDecodeError, ConnectionResetError, SSLError) as e:
                    log.warning(
                        "Problem trashing file. Retrying...",
                        extra={"upload_file_id": upload_file_id, "error": str(e)},
                    )
                    sleep(1)
            # and retry the upload
            return False
        # all good, treat as done
        return True


class UploadEventHandler(FileSystemEventHandler, Closable):
    def __init__(self, fs_observer, snapshot_root, mq_device_topic):
        FileSystemEventHandler.__init__(self)
        Closable.__init__(self, connect_url=URL_WORKER_OBJECT_DETECTOR, socket_type=zmq.PUSH)

        self.device_events = {}
        self._snapshot_root = snapshot_root

        self._fs_observer = fs_observer
        self.cloud_storage_socket = None
        self._cloud_storage_url = None

        self._mq_device_topic = mq_device_topic
        self._path_cache: LRUCache = LRUCache(maxsize=128)

    def start(self):
        # start the file system monitor
        self.cloud_storage_socket = zmq_socket(socket_type=zmq.PUSH)
        self.cloud_storage_socket.connect(URL_WORKER_CLOUD_STORAGE)
        self._fs_observer.schedule(self, self._snapshot_root, recursive=True)

    def close(self):
        # not calling Observer unschedule on daemon to avoid stuck on lock bug
        Closable.close(self)
        try_close(self.cloud_storage_socket)

    @property
    def cloud_storage_url(self):
        return self._cloud_storage_url

    @cloud_storage_url.setter
    def cloud_storage_url(self, cloud_storage_url):
        self._cloud_storage_url = cloud_storage_url

    def add_image_dir(self, device_key, device_type, device_location, image_dir):
        if image_dir in self.device_events:
            raise RuntimeError(f"Image source label {device_location} is already configured.")
        # create pre-canned device events for reuse later
        self.device_events[image_dir] = DeviceEvent(
            device_key=device_key,
            device_type=device_type,
            device_location=device_location,
        )

    def _get_device_event(self, event_directory):
        for image_dir, device_event in list(self.device_events.items()):
            if image_dir in event_directory:
                return copy.copy(device_event)
        return None

    @property
    def watched_dirs(self):
        return list(self.device_events.keys())

    # if a snapshot is renamed after object detection
    @staticmethod
    def _decode_path(path: bytes | str) -> str:
        if isinstance(path, bytes):
            return path.decode()
        return path

    # if a snapshot is renamed after object detection
    def on_moved(self, event):
        if isinstance(event, FileMovedEvent):
            src_path = self._decode_path(event.src_path)
            dest_path = self._decode_path(event.dest_path)
            log.debug(
                "File moved event",
                extra={"src_path": src_path, "dest_path": dest_path},
            )
            self.on_fs_event(snapshot_path=dest_path)

    # if a snapshot has been fully written
    def on_closed(self, event):
        if isinstance(event, FileClosedEvent):
            src_path = self._decode_path(event.src_path)
            log.debug("File closed event", extra={"src_path": src_path})

    # we listen to on-modified events because the file is
    # created and then written to subsequently.
    def on_modified(self, event):
        if isinstance(event, FileModifiedEvent):
            src_path = self._decode_path(event.src_path)
            log.debug("File modified event", extra={"src_path": src_path})
            self.on_fs_event(snapshot_path=src_path)

    def on_fs_event(self, snapshot_path: str):
        if threads.shutting_down:
            log.debug(
                "Ignoring file system event due to shutdown",
                extra={"snapshot_path": snapshot_path},
            )
            return
        if snapshot_path in self._path_cache:
            log.debug("Snapshot path already processed", extra={"snapshot_path": snapshot_path})
            return
        self._path_cache[snapshot_path] = True
        # cross-check that we're in the right place
        if not snapshot_path.startswith(self._snapshot_root):
            log.debug("Ignored unmapped path event", extra={"snapshot_path": snapshot_path})
            return
        # image snapshot that can be mapped to a device?
        device_event = self._get_device_event(snapshot_path)
        if device_event is None:
            log.debug("No device mapping from path", extra={"snapshot_path": snapshot_path})
            return
        log.debug(
            "Device event from snapshot path",
            extra={"device_event": str(device_event), "snapshot_path": snapshot_path},
        )
        file_base_name = os.path.splitext(os.path.basename(snapshot_path))[0]
        date_string = " ".join(file_base_name.split("_")[2:]) if "_" in file_base_name else file_base_name
        # keep in sync with invocations of create_snapshot_path
        device_event.timestamp = date_string
        # do not notify again for fetched image data
        if "fetch" not in snapshot_path and "detect" not in snapshot_path and "human" not in snapshot_path:
            unix_timestamp = int((device_event.timestamp.replace(tzinfo=None) - datetime(1970, 1, 1)).total_seconds())
            publisher_data = create_publisher_struct(
                device_key=device_event["device_key"],
                device_label=device_event["device_label"],
                image_data=device_event["image_data"],
                image_timestamp=unix_timestamp,
                storage_url=self._cloud_storage_url,
                storage_path=snapshot_path,
            )
            # start processing the image data
            if file_base_name.endswith(".jpg") and "object" not in snapshot_path:
                self.socket.send_pyobj(
                    (
                        f"event.notify.{self._mq_device_topic}.{DEVICE_NAME}",
                        publisher_data,
                    )
                )
        else:
            log.debug(
                "Not generating redundant snapshot event",
                extra={"snapshot_path": snapshot_path},
            )
        log.debug(
            "Uploading snapshot to cloud storage based on file system event",
            extra={"snapshot_path": snapshot_path, "date_string": date_string},
        )
        # upload the image snapshot to Cloud
        if self.cloud_storage_socket is not None:
            self.cloud_storage_socket.send_pyobj((snapshot_path, device_event.timestamp))


class ObjectDetector(ZmqRelay):
    def __init__(self):
        ZmqRelay.__init__(
            self,
            name=self.__class__.__name__,
            source_zmq_url=URL_WORKER_OBJECT_DETECTOR,
            sink_zmq_url=URL_WORKER_RABBIT_PUBLISHER,
        )

        self._od_enabled = app_config.getboolean("snapshots", "object_detection_enabled")
        self._rekog: Any = None
        self._path_cache: LRUCache = LRUCache(maxsize=128)
        self._local_model: YOLO | None = None
        self._minimum_confidence = app_config.getfloat("object_detection", "minimum_confidence")

    def startup(self):
        if is_flag_enabled(FEATURE_FLAG_OBJECT_DETECTION) and is_flag_enabled(FEATURE_FLAG_CLOUD_OBJECT_DETECTION):
            self._rekog = boto3.client("rekognition", region_name=app_config.get("rekognition", "region"))
        model_url = app_config.get("object_detection", "model_url")
        if model_url and len(model_url) > 0:
            log.info(
                "Using Ultralytics model for local object detection",
                extra={"model_url": model_url},
            )
            self._local_model = YOLO(model_url)
        else:
            model_name = app_config.get("object_detection", "model_name")
            log.info(
                "Using Ultralytics model for local object detection",
                extra={"model_name": model_name},
            )
            self._local_model = YOLO(model_name)
        log.info(
            "classes available",
            extra={"class_count": len(self._local_model.names), "class_names": self._local_model.names},
        )

    def process_message(self, sink_socket):
        (publisher_topic, publisher_data) = self.socket.recv_pyobj()
        input_device = publisher_data["inputs"][0]
        device_label = input_device["device_label"]
        snapshot_path = input_device["storage_path"]
        if snapshot_path in self._path_cache:
            log.debug(
                "Snapshot path already processed",
                extra={"snapshot_path": snapshot_path, "device_label": device_label},
            )
            return
        self._path_cache[snapshot_path] = device_label
        image_bytes = None
        image_source = None
        if "image" in input_device:
            image_bytes = input_device["image"]
            image_source = "fetch"
        else:
            with open(snapshot_path, "rb") as img_file:
                image_bytes = img_file.read()
            image_source = "upload"
        # find objects using the specified model
        event_detail = None
        if is_flag_enabled(FEATURE_FLAG_OBJECT_DETECTION) and self._od_enabled:
            log.debug(
                "Detecting objects in cached image",
                extra={"image_source": image_source, "snapshot_path": snapshot_path},
            )
            if is_flag_enabled(FEATURE_FLAG_LOCAL_OBJECT_DETECTION) and self._local_model is not None:
                im = Image.open(BytesIO(image_bytes))
                results = None
                try:
                    start_time = time.time() * 1000
                    results = self._local_model.predict(source=im, conf=self._minimum_confidence)
                    end_time = time.time() * 1000
                    metrics.distribution(
                        name="detect_time",
                        value=end_time - start_time,
                        unit="milliseconds",
                        attributes={"device_label": device_label},
                    )
                except Exception:
                    log.exception("Local detection error.")
                if results:
                    # find Person labels
                    person_detected = False
                    person_count = 0
                    face_count = 0
                    labels = []
                    for result in results:
                        if not isinstance(result, Results):
                            continue
                        person_detected = False
                        for detect_dict in result.summary():
                            log.debug("Local inference", extra={"inference": detect_dict})
                            label_name = detect_dict["name"]
                            label_confidence = float(detect_dict["confidence"])
                            labels.append((label_name, label_confidence))
                            metrics.distribution(
                                name="detect_confidence",
                                value=label_confidence * 100,
                                unit="percent",
                                attributes={"label_name": label_name},
                            )
                            if "person" in label_name:
                                person_detected = True
                                person_count += 1
                            if "face" in label_name:
                                person_detected = True
                                face_count += 1
                        if person_detected:
                            detect_filename = snapshot_path.replace("fetch", "detect")
                            log.debug(
                                "Saving person detection result",
                                extra={"detect_filename": detect_filename},
                            )
                            try:
                                result.save(filename=detect_filename)
                            except Exception:
                                log.exception(
                                    "Unable to save detection result",
                                    extra={"detect_filename": detect_filename},
                                )
                            human_detect_filename = snapshot_path.replace("fetch", "human")
                            log.debug(
                                "Renaming snapshot after person detection",
                                extra={
                                    "snapshot_path": snapshot_path,
                                    "human_detect_filename": human_detect_filename,
                                },
                            )
                            os.rename(snapshot_path, human_detect_filename)
                    log.debug(
                        "YOLO labels found",
                        extra={
                            "label_count": len(labels),
                            "device_label": device_label,
                            "labels": labels,
                            "snapshot_path": snapshot_path,
                        },
                    )
                    if person_detected:
                        additional_info = ""
                        if person_count > 0:
                            additional_info += f"{person_count} person(s)"
                        if face_count > 0:
                            if len(additional_info) > 0:
                                additional_info += ", "
                            additional_info += f"{face_count} face(s)"
                        event_detail = f"{device_label} ({image_source}): {additional_info}."
                        log.debug(
                            "Object detection event detail",
                            extra={"event_detail": event_detail},
                        )
                        input_device["event_detail"] = additional_info
            elif self._rekog is not None:
                log.debug(
                    "Detecting objects in cached image",
                    extra={"image_source": image_source, "snapshot_path": snapshot_path},
                )
                try:
                    response = self._rekog.detect_labels(Image={"Bytes": image_bytes})
                    log.debug(
                        "Rekognition response",
                        extra={
                            "device_label": device_label,
                            "image_source": image_source,
                            "response": response,
                        },
                    )
                    # find Person labels
                    person_count = 0
                    labels = []
                    if "Labels" in response:
                        for detect_dict in response["Labels"]:
                            label_name = detect_dict["Name"]
                            label_confidence = float(detect_dict["Confidence"])
                            labels.append((label_name, label_confidence))
                            if label_name == "Person" and label_confidence >= self._minimum_confidence:
                                # if instances are provided, sum them
                                num_instances = len(detect_dict["Instances"])
                                if num_instances > 0:
                                    person_count += num_instances
                                else:
                                    person_count += 1
                                human_detect_filename = snapshot_path.replace("fetch", "human")
                                log.debug(
                                    "Renaming snapshot after person detection",
                                    extra={
                                        "snapshot_path": snapshot_path,
                                        "human_detect_filename": human_detect_filename,
                                    },
                                )
                                os.rename(snapshot_path, human_detect_filename)
                        log.debug(
                            "Rekognition labels found",
                            extra={
                                "label_count": len(labels),
                                "device_label": device_label,
                                "image_source": image_source,
                                "labels": labels,
                            },
                        )
                    if person_count > 0:
                        additional_info = f"{person_count} person(s) and {len(labels)} things"
                        event_detail = f"{device_label} ({image_source}): {additional_info}."
                        log.debug(
                            "Object detection event detail",
                            extra={"event_detail": event_detail},
                        )
                        input_device["event_detail"] = additional_info
                except self._rekog.exceptions.InvalidImageFormatException:
                    log.warning("Rekognition image format error.", exc_info=True)
                except EndpointConnectionError as e:
                    raise ResourceWarning("Rekognition problem.") from e
                except Exception:
                    log.exception("Rekognition error.")
            else:
                log.debug(
                    "No viable object detection methods. Check feature flags.",
                    extra={"image_source": image_source, "snapshot_path": snapshot_path},
                )
        else:
            log.debug(
                "Not detecting objects due to feature flag or config",
                extra={
                    "image_source": image_source,
                    "snapshot_path": snapshot_path,
                    "feature_flag": FEATURE_FLAG_OBJECT_DETECTION,
                },
            )
        log.debug(
            "Sending detection data",
            extra={"device_label": device_label, "publisher_topic": publisher_topic},
        )
        sink_socket.send_pyobj((publisher_topic, publisher_data))


def main():
    creds = Creds()
    creds.validate_creds()
    # sentry instrumentation
    log.debug("Loading Sentry.io instrumentation...")
    sentry_dsn = creds.get_creds(app_config.get("creds", "sentry_dsn").replace("__APP_NAME__", APP_NAME))
    sentry_sdk.init(
        dsn=sentry_dsn,
        enable_logs=True,
        enable_metrics=True,
        integrations=[
            AsyncioIntegration(),
            SysExitIntegration(capture_successful_exits=True),
            ThreadingIntegration(propagate_scope=True),
        ],
        send_default_pii=True,
    )
    # reduce ultralytics logging noise
    ignore_logger(name="ultralytics")
    # control listener
    mq_server_address = app_config.get("rabbitmq", "server_address")
    mq_exchange_name = app_config.get("rabbitmq", "mq_exchange")
    mq_device_topic = app_config.get("rabbitmq", "device_topic")
    mq_control_listener = ZMQListener(
        zmq_url=URL_WORKER_APP,
        mq_server_address=mq_server_address,
        mq_exchange_name=f"{mq_exchange_name}_control",
        mq_topic_filter=f"event.trigger.{mq_device_topic}",
        mq_exchange_type="direct",
    )
    # RabbitMQ relay
    try:
        mq_relay = RabbitMQRelay(
            zmq_url=URL_WORKER_RABBIT_PUBLISHER,
            mq_server_address=mq_server_address,
            mq_exchange_name=mq_exchange_name,
            mq_topic_filter=mq_device_topic,
            mq_exchange_type="topic",
        )
    except AMQPConnectionError as e:
        log.exception("RabbitMQ failure at startup.")
        die(exception=e)
        bye()
    # file system listener
    observer = Observer()
    observer.name = observer.__class__.__name__
    snapshot_root = app_config.get("snapshots", "root_dir")
    upload_event_handler = UploadEventHandler(
        snapshot_root=snapshot_root,
        fs_observer=observer,
        mq_device_topic=mq_relay.device_topic,
    )
    # construct the device representation
    input_types = dict(app_config.items("input_type"))
    input_locations = dict(app_config.items("input_location"))
    output_types = dict(app_config.items("output_type"))
    output_locations = dict(app_config.items("output_location"))
    device_info: dict[str, list[dict[str, str]]] = {}
    device_info["inputs"] = []
    for field, input_type in list(input_types.items()):
        input_location = input_locations[field]
        device_key = f"{input_locations[field]} {input_type}"
        device_info["inputs"].append({"type": input_type, "location": input_location, "device_key": device_key})
        if input_type.lower() == "camera":
            upload_event_handler.add_image_dir(
                device_key=device_key,
                device_type=input_type,
                device_location=input_location,
                image_dir=os.path.join(
                    app_config.get("snapshots", "upload_dir"),
                    input_location.lower().replace(" ", ""),
                ),
            )
    device_info["outputs"] = []
    camera_profiles = {}
    for field, output_type in list(output_types.items()):
        output_device = {}
        if field in output_locations:
            output_location = output_locations[field]
            device_key = f"{output_location} {output_type}"
            output_device["location"] = output_location
        else:
            device_key = output_type
        output_device.update({"type": output_type, "device_key": device_key})
        device_info["outputs"].append(output_device)
        if output_type.lower() == "camera":
            # now build the profile for internal use
            camera_profile = output_device.copy()
            camera_profile.update(
                {
                    "storage": os.path.join(
                        snapshot_root,
                        app_config.get("snapshots", "upload_dir"),
                        output_location.lower().replace(" ", ""),
                    )
                }
            )
            camera_profiles[device_key] = camera_profile
    log.info(
        "Monitoring directories for changes",
        extra={
            "snapshot_root": snapshot_root,
            "watched_dirs": upload_event_handler.watched_dirs,
        },
    )
    # object detection
    object_detector = None
    if app_config.getboolean("snapshots", "object_detection_enabled"):
        object_detector = ObjectDetector()
    # ensure that auth is properly set up first
    google_drive_uploader = None
    google_drive_archiver = None
    if is_flag_enabled(FEATURE_FLAG_CLOUD_STORAGE_MANAGEMENT):
        try:
            google_drive_uploader = GoogleDriveUploader(
                gauth_creds_file=app_config.get("gdrive", "creds_file"),
                gdrive_folder=app_config.get("gdrive", "folder"),
            )
            google_drive_archiver = GoogleDriveArchiver(
                gauth_creds_file=app_config.get("gdrive", "creds_file"),
                gdrive_folder=app_config.get("gdrive", "folder"),
                gdrive_folder_id=google_drive_uploader.cloud_storage_folder_id,
                gdrive_folder_url=google_drive_uploader.cloud_storage_url,
            )
        except HttpLib2Error:
            log.warning(
                "Google Drive will be unavailable until the next restart.",
                exc_info=True,
            )
            # acceptable if GDrive setup attempted first
            google_drive_uploader = None
            google_drive_archiver = None
        except Exception as e:
            die(exception=e)
            bye()
    else:
        log.warning(
            "Not enabling cloud storage management due to disabled feature flag",
            extra={"feature_flag": FEATURE_FLAG_CLOUD_STORAGE_MANAGEMENT},
        )
    # tell the uploader about the Cloud storage URL
    cloud_storage_url = None
    if google_drive_uploader:
        cloud_storage_url = google_drive_uploader.cloud_storage_url
    upload_event_handler.cloud_storage_url = cloud_storage_url
    snapshotter = Snapshot(
        camera_profiles=camera_profiles,
        cloud_storage_url=cloud_storage_url,
        mq_device_topic=mq_relay.device_topic,
    )
    # start threads
    mq_control_listener.start()
    mq_relay.start()
    snapshotter.start()
    # start the collectors
    observer.start()
    # track external thread explicitly
    threads.threads_tracked.add(observer.getName())
    # must be main thread
    signal_handler = SignalHandler()
    publisher_socket = zmq_socket(zmq.PUSH)
    try:
        # startup completed
        # back to INFO logging
        log.setLevel(logging.INFO)
        if object_detector:
            object_detector.start()
        if google_drive_uploader is not None:
            # start Google Drive uploader
            google_drive_uploader.start()
        if google_drive_archiver is not None:
            # start the Google Driver archiver last
            google_drive_archiver.start()
        # start processing file system events
        upload_event_handler.start()
        # start thread nanny
        nanny = threading.Thread(daemon=True, name="nanny", target=thread_nanny, args=(signal_handler,))
        nanny.start()
        # start heartbeat loop
        publisher_socket.connect(URL_WORKER_RABBIT_PUBLISHER)
        while not threads.shutting_down:
            publisher_socket.send_pyobj(
                (
                    f"event.heartbeat.{mq_relay.device_topic}",
                    {
                        "inputs": device_info["inputs"],
                        "outputs": device_info["outputs"],
                    },
                )
            )
            threads.interruptable_sleep.wait(HEARTBEAT_INTERVAL_SECONDS)
        raise RuntimeWarning("Shutting down...")
    except KeyboardInterrupt, RuntimeWarning, ContextTerminated:
        die()
        log.debug("Shutting down RabbitMQ control listener...")
        mq_control_listener.stop()
        log.debug("Shutting down RabbitMQ relay...")
        try:
            mq_relay.close()
        except (AMQPConnectionError, ConnectionClosedByBroker, StreamLostError) as e:
            log.warning("Problem when closing RabbitMQ relay", extra={"error": str(e)})
        log.debug("Shutting down application threads...")
        upload_event_handler.close()
        # since this thread and the signal handler are one and the same
        publisher_socket.close()
    finally:
        zmq_term()
    bye()


if __name__ == "__main__":
    main()
