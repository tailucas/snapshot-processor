#!/usr/bin/env python
import dateutil.parser
import logging.handlers

import boto3
import builtins
import copy
import json
import os
import pytz
import requests
import threading
import time
import zmq

from abc import abstractmethod, ABCMeta
from datetime import datetime, timedelta
from http.client import BadStatusLine
from httplib2.error import HttpLib2Error
from io import BytesIO
from mimetypes import MimeTypes
from pathlib import Path
from pika.exceptions import AMQPConnectionError, StreamLostError, ConnectionClosedByBroker
from pydrive.auth import GoogleAuth
from pydrive.drive import GoogleDrive
from pydrive.files import FileNotUploadedError, ApiRequestError
from googleapiclient.errors import HttpError
from requests.adapters import ConnectionError
from requests.exceptions import RequestException
from sentry_sdk import capture_exception
from sentry_sdk.integrations.logging import ignore_logger
from socket import error as socket_error
from ssl import SSLEOFError
from time import sleep
from urllib.request import pathname2url
from watchdog.events import FileSystemEventHandler, FileModifiedEvent
from watchdog.observers import Observer
from zmq import ContextTerminated
from PIL import Image
from zmq import ZMQError

from pyftpdlib.authorizers import DummyAuthorizer
from pyftpdlib.handlers import FTPHandler
from pyftpdlib.servers import FTPServer

import os.path

# setup builtins used by pylib init
builtins.SENTRY_EXTRAS = []
AWS_REGION = os.environ['AWS_DEFAULT_REGION']
from . import APP_NAME
class CredsConfig:
    sentry_dsn: f'opitem:"Sentry" opfield:{APP_NAME}.dsn' = None # type: ignore
    cronitor_token: f'opitem:"cronitor" opfield:.password' = None # type: ignore
    aws_akid: f'opitem:"AWS" opfield:{AWS_REGION}.akid' = None # type: ignore
    aws_sak: f'opitem:"AWS" opfield:{AWS_REGION}.sak' = None # type: ignore
    ftp_user: f'opitem:"FTP" opfield:.username' = None # type: ignore
    ftp_pass: f'opitem:"FTP" opfield:.password' = None # type: ignore
# instantiate class
builtins.creds_config = CredsConfig()

from tailucas_pylib import app_config, \
    creds, \
    device_name, \
    log, \
    URL_WORKER_APP

from tailucas_pylib.datetime import make_timestamp, ISO_DATE_FORMAT
from tailucas_pylib.aws.metrics import post_count_metric
from tailucas_pylib.rabbit import ZMQListener, RabbitMQRelay
from tailucas_pylib.process import SignalHandler
from tailucas_pylib import threads
from tailucas_pylib.threads import thread_nanny, die, bye
from tailucas_pylib.app import ZmqRelay, AppThread
from tailucas_pylib.zmq import zmq_socket, zmq_term, Closable
from tailucas_pylib.handler import exception_handler

from botocore.exceptions import EndpointConnectionError

# Reduce Sentry noise from pika loggers
ignore_logger('pika.adapters.base_connection')
ignore_logger('pika.adapters.blocking_connection')
ignore_logger('pika.adapters.utils.connection_workflow')
ignore_logger('pika.adapters.utils.io_services_utils')
ignore_logger('pika.channel')


URL_WORKER_RABBIT_PUBLISHER = 'inproc://rabbitmq-publisher'
URL_WORKER_OBJECT_DETECTOR = 'inproc://object-detector'
URL_WORKER_CLOUD_STORAGE = 'inproc://cloud-storage'

HEARTBEAT_INTERVAL_SECONDS = 5


def wait_for_file_content(file_path):
    # FIXME: small delay to ensure that file is complete and
    # closed before attempting upload
    if os.path.exists(file_path) and os.path.isfile(file_path):
        sleep_delay = 5
        max_tries = 24
        for tries in range(1, max_tries):
            # no content
            if os.path.getsize(file_path) > 0:
                break
            # less than 5 minutes old
            if time.time() - os.path.getmtime(file_path) > 300:
                break
            log.warning(f'Waiting for {sleep_delay}s (try {tries} of {max_tries}) on empty file {file_path}')
            sleep(sleep_delay)
            if tries >= max_tries:
                break


def create_snapshot_path(parent_path, operation, unix_timestamp, file_extension):
    return os.path.join(
        parent_path,
        f'{operation}_' + str(unix_timestamp) + '.' + file_extension)


def create_publisher_struct(device_key, device_label, image_data, storage_url):
    return {
        'active_devices': [
            {
                'device_key': device_key,
                'device_label': device_label,
                'type': 'camera',
                'image': image_data,
            }
        ],
        'storage_url': storage_url
    }


class CameraConfig(object):

    def __init__(self, device_key, device_label, camera_config, camera_storage=None):
        # extract connection configuration from something of this format:
        # username:password@ip:port,rtsp_port
        camera_auth, camera_url = camera_config.split('@')
        if ':' not in camera_auth or ':' not in camera_url:
            raise AssertionError(f"Camera parameters missing for '{device_key}.'")
        # split the rtsp port number
        camera_url_parts = camera_url.split(',')
        if len(camera_url_parts) == 1:
            camera_url = camera_url_parts[0]
            rtsp_port = camera_url.split(':')[1]
        elif len(camera_url_parts) == 2:
            camera_url, rtsp_port = camera_url_parts
        # set locals
        self._name = device_key
        self._device_key = device_key
        self._device_label = device_label
        self._basic_auth = camera_auth
        self._username, self._password = camera_auth.split(':')
        self._url = camera_url
        self._ip, self._port = camera_url.split(':')
        self._rtsp_port = rtsp_port
        self._camera_storage = camera_storage

    def __str__(self) -> str:
        return self._url

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


class FileType(object):

    def __init__(self):
        self.mime = MimeTypes()

    def mime_type(self, file_path):
        mime_type = self.mime.guess_type(pathname2url(file_path))
        if mime_type is not None and len(mime_type) > 0:
            return mime_type[0]
        return None

    def test_type(self, file_path, file_type):
        mime_type = self.mime_type(file_path)
        if mime_type is not None and mime_type.startswith(f'{file_type}/'):
            # return the specific file type
            return mime_type.split('/')[1]
        return None


class Snapshot(ZmqRelay):

    def __init__(self, camera_profiles, cloud_storage_url, mq_device_topic):
        ZmqRelay.__init__(self,
            name=self.__class__.__name__,
            source_zmq_url=URL_WORKER_APP,
            source_socket_type=zmq.PULL,
            sink_zmq_url=URL_WORKER_OBJECT_DETECTOR,
            sink_socket_type=zmq.PUSH)

        self.cameras = camera_profiles
        self.default_command = app_config.get('camera', 'default_command')
        self.default_image_format = app_config.get('camera', 'default_image_format')

        self.cloud_storage_url = cloud_storage_url

        self.capture_threads = {}
        self._mq_device_topic = mq_device_topic

    def process_message(self, zmq_socket):
        output_trigger = zmq_socket.recv_pyobj()
        if isinstance(output_trigger, dict):
            for _,payload in output_trigger.items():
                device_key, device_label, camera_config_string = payload['data']
                timestamp = make_timestamp(payload['timestamp'])
                # only expect 1 item
                break
        elif isinstance(output_trigger, tuple):
            #FIXME
            if len(output_trigger) > 3:
                log.warn(f'Discarding unknown trigger type {type(output_trigger)}')
                return
            timestamp = make_timestamp()
            device_key, device_label, camera_config_string = output_trigger
        else:
            log.warn(f'Discarding unknown trigger type {type(output_trigger)}')
            return
        if device_key not in self.cameras:
            log.error(f"Camera configuration missing for '{device_label}.'")
            post_count_metric('Errors')
            return
        try:
            camera_config = CameraConfig(
                device_key=device_key,
                device_label=device_label,
                camera_config=camera_config_string,
                camera_storage=self.cameras[device_key]['storage'])
        except AssertionError:
            post_count_metric('Errors')
            return
        image_data = None
        im = None
        # grab a first frame for overall context
        for tries in range(1, 4):
            try:
                r = requests.get(f'http://{camera_config.url}/cgi-bin/CGIProxy.fcgi', params={
                    'cmd': self.default_command,
                    'usr': camera_config.username,
                    'pwd': camera_config.password,
                })
                image_data = r.content
                im = Image.open(BytesIO(image_data))
                if im.format is not None:
                    break
                else:
                    raise AssertionError(f'Bad image data detected: {im!s}')
            except (OSError, ConnectionError, RequestException, AssertionError) as e:
                log.warning(f'Problem getting image from {camera_config.url} due to {e!s}. Retrying...')
                sleep(0.1)
                if tries >= 3:
                    log.warning(f'Giving up getting image from {camera_config.url} after {tries} tries: {e!s}')
                    post_count_metric('Errors')
                    break
        if image_data is not None and im is not None and im.format is not None:
            # construct message to publish
            unix_timestamp = int((timestamp.replace(tzinfo=None) - datetime(1970, 1, 1)).total_seconds())
            log.debug(f'Basing {unix_timestamp} off of {timestamp}')
            # publisher data
            publisher_data = create_publisher_struct(
                device_key=device_key,
                device_label=device_label,
                image_data=image_data,
                storage_url=self.cloud_storage_url)
            # send image data for processing
            self.socket.send_pyobj((
                None,
                f'event.notify.{self._mq_device_topic}.{device_name}.image',
                publisher_data
            ))
            # create output file path
            normalized_name = device_key.lower().replace(' ', '-')
            output_filename = create_snapshot_path(
                parent_path=camera_config.camera_storage,
                operation=f'fetch_{normalized_name}',
                unix_timestamp=unix_timestamp,
                file_extension=self.default_image_format)
            log.info(f'{device_label} ({im.format} {im.size} {im.mode}) => {output_filename}.')
            # persist for Cloud
            im.save(output_filename)


class DeviceEvent(object):

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
        return self.timestamp.strftime(ISO_DATE_FORMAT)

    @property
    def event_detail(self):
        return self._event_detail

    @event_detail.setter
    def event_detail(self, value):
        self._event_detail = value

    @property
    def dict(self):
        representation = {
            'device_key': self._device_key,
            'device_type': self._device_type,
            'timestamp': self.timestamp_string
        }
        if self._device_location:
            representation.update({
                'device_location': self._device_location
            })
        if self._event_detail:
            representation.update({
                'event_detail': self._event_detail
            })
        return representation

    def __str__(self):
        return self._device_key


class CloudStorage(object, metaclass=ABCMeta):
    @abstractmethod
    def cloud_storage_url(self):
        return NotImplemented


class GoogleDriveManager(CloudStorage):

    def __init__(self, gauth_creds_file, gdrive_folder):
        self._gdrive_folder = gdrive_folder
        if '~' in gauth_creds_file:
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
            log.debug(f'Google credentials not found in [{self._gauth_creds_file}]. Interactive setup may follow.')
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
            log.debug(f'Saved Google credentials to {self._gauth_creds_file}')
        return auth

    @staticmethod
    def _get_gdrive_folder_id(gdrive, gdrive_folder, parent_id='root', create=True):
        log.debug(f"Checking for existence of Google Drive folder '{gdrive_folder}'")
        file_list = gdrive.ListFile({
            'q': f"'{parent_id}' in parents and trashed=false and mimeType = 'application/vnd.google-apps.folder' and title = '{gdrive_folder}'"
        }).GetList()
        if len(file_list) == 0:
            if not create:
                return None
            log.debug(f"Creating Google Drive folder '{gdrive_folder}' in parent folder '{parent_id}'")
            folder = gdrive.CreateFile({
                'description': f'Created by {APP_NAME}', 'title': gdrive_folder,
                'mimeType': 'application/vnd.google-apps.folder',
                'parents': [{"kind": "drive#parentReference", "id": parent_id}]
            })
            folder.Upload()
            folder_id = folder['id']
            folder_link = folder['alternateLink']
        elif len(file_list) == 1:
            folder_id = file_list[0]['id']
            folder_link = file_list[0]['alternateLink']
        else:
            raise RuntimeError(f'Unexpected result listing Google Drive for {gdrive_folder}: {file_list!s}')
        log.debug(f"Google Drive folder ID for folder '{gdrive_folder}' is '{folder_id}'. Visit at {folder_link}")
        return folder_id, folder_link


class GoogleDriveArchiver(AppThread, GoogleDriveManager):

    def __init__(self, gauth_creds_file, gdrive_folder, gdrive_folder_id, gdrive_folder_url):
        AppThread.__init__(self, name=self.__class__.__name__)
        GoogleDriveManager.__init__(self,
            gauth_creds_file=gauth_creds_file,
            gdrive_folder=gdrive_folder)

        # separate connection for archiver thread to prevent PyDrive lock-up
        self._archive_drive = GoogleDrive(self.gauth)
        self._folder_id_cache = dict()

        self._gdrive_folder_id = gdrive_folder_id
        self._gdrive_folder_url = gdrive_folder_url

    def run(self):
        while not threads.shutting_down:
            log.debug(f'Finding files in {self._gdrive_folder} ({self._gdrive_folder_id}) to archive.')
            try:
                file_list = self._archive_drive.ListFile({
                    'q': f"'{self._gdrive_folder_id}' in parents and trashed=false and mimeType != 'application/vnd.google-apps.folder'",
                    'maxResults': 100,
                })
                archived = 0
                try:
                    while True:
                        page = file_list.GetList()
                        log.info(f'Inspecting {len(page)} files for archival...')
                        for file1 in page:
                            if self.archive(gdrive=self._archive_drive,
                                            gdrive_file=file1,
                                            root_folder_id=self._gdrive_folder_id):
                                archived += 1
                except StopIteration:
                    log.info(f'Archived {archived} image snapshots.')
            except (ApiRequestError, BadStatusLine, BrokenPipeError, FileNotUploadedError, socket_error, HttpError, SSLEOFError) as e:
                raise ResourceWarning(f'Google Drive problem.') from e
            # prevent memory leaks
            self._folder_id_cache.clear()
            # sleep until tomorrow
            threads.interruptable_sleep.wait(60*60*24)

    def archive(self, gdrive, gdrive_file, root_folder_id):
        filename = gdrive_file['title']
        now = datetime.utcnow().replace(tzinfo=pytz.utc)
        created_date = dateutil.parser.parse(gdrive_file['createdDate'])
        td = now - created_date
        if td > timedelta(days=1):
            log.info(f'Archiving {filename} created {td.days} days ago.')
            ymd_date = created_date.strftime('%Y-%m-%d')
            if ymd_date in self._folder_id_cache:
                gdrive_folder_id = self._folder_id_cache[ymd_date]
            else:
                # create the required folder structure
                year_folder_name = created_date.strftime('%Y')
                year_folder_id, _ = self._get_gdrive_folder_id(gdrive, year_folder_name, root_folder_id)
                month_folder_name = created_date.strftime('%m')
                month_folder_id, _ = self._get_gdrive_folder_id(gdrive, month_folder_name, year_folder_id)
                day_folder_name = created_date.strftime('%d')
                day_folder_id, _ = self._get_gdrive_folder_id(gdrive, day_folder_name, month_folder_id)
                self._folder_id_cache[ymd_date] = day_folder_id
                gdrive_folder_id = day_folder_id
            log.debug(f'{filename} => folder key {ymd_date} => folder ID {gdrive_folder_id}')
            # reset the parent folders, include the existing parents if starred
            if gdrive_file['labels']['starred']:
                parents = list()
                for parent in gdrive_file['parents']:
                    parent_id = parent['id']
                    parents.append(parent_id)
                    log.debug(f'Comparing parent {parent_id} with archive folder id {gdrive_folder_id}')
                    if gdrive_folder_id == parent_id:
                        log.debug(f'{filename} already archived to {gdrive_folder_id}')
                        return False
                log.info(f'Archiving starred file {filename}, but leaving existing parents intact.')
                # new parent for archival
                gdrive_parents = [{"kind": "drive#parentReference", "id": gdrive_folder_id}]
                # existing parents
                for parent in parents:
                    # simply appending the parents array returned by the service is insufficient
                    # possibly due to PyDrive's change detection, or Drive
                    gdrive_parents.append({"kind": "drive#parentReference", "id": parent})
                gdrive_file['parents'] = gdrive_parents
            else:
                # otherwise, clobber the existing parent information
                gdrive_file['parents'] = [{"kind": "drive#parentReference", "id": gdrive_folder_id}]
            # update the file metadata
            gdrive_file.Upload()
            return True
        return False


class GoogleDriveUploader(AppThread, GoogleDriveManager):

    def __init__(self, gauth_creds_file, gdrive_folder):
        # set up remote service setup first
        GoogleDriveManager.__init__(self,
            gauth_creds_file=gauth_creds_file,
            gdrive_folder=gdrive_folder)
        AppThread.__init__(self, name=self.__class__.__name__)

        # determine the Drive folder details synchronously
        self._gdrive_folder_id, self._gdrive_folder_url = self._get_gdrive_folder_id(self.drive, self._gdrive_folder)

        self._filetype = FileType()

    def run(self):
        with exception_handler(connect_url=URL_WORKER_CLOUD_STORAGE, socket_type=zmq.PULL, and_raise=False, shutdown_on_error=True) as zmq_socket:
            while not threads.shutting_down:
                (snapshot_path, snapshot_timestamp) = zmq_socket.recv_pyobj()
                #FIXME: remove
                wait_for_file_content(snapshot_path)
                self.upload(file_path=snapshot_path, created_time=snapshot_timestamp)

    def upload(self, file_path, created_time=None):
        # upload the snapshot
        mime_type = self._filetype.mime_type(file_path)
        log.info(f"'{mime_type}' file {file_path}")
        created_date = None
        if created_time is None:
            log.debug(f"Uploading '{file_path}' to Google Drive")
        else:
            # datetime.isoformat doesn't work because of the seconds
            # separator required by RFC3339, and the extra requirement to have
            # the colon in the TZ offset if not in UTC.
            offset = created_time.strftime('%z')
            created_date = created_time.strftime('%Y-%m-%dT%H:%M:%S.00') + offset[:3] + ':' + offset[3:]
            log.debug(f"Uploading '{file_path}' to Google Drive with created time of {created_date}")
        try:
            f = self.drive.CreateFile({
                'title': os.path.basename(file_path),
                'mimeType': mime_type,
                'createdDate': created_date,
                'parents': [{"kind": "drive#fileLink", "id": self._gdrive_folder_id}]
            })
            f.SetContentFile(file_path)
            f.Upload()
        except (ApiRequestError, BadStatusLine, BrokenPipeError, FileNotUploadedError, socket_error, HttpError, SSLEOFError) as e:
            raise ResourceWarning(f'Google Drive problem.') from e
        link_msg = ""
        if 'thumbnailLink'in f:
            link = f['thumbnailLink']
            # specify our own thumbnail size
            if '=' in link:
                link = link.rsplit('=')[0]
                link += '=s1024'
            link_msg = f" Thumbnail at {link}"
        log.info(f"Uploaded '{os.path.basename(file_path)}' to Google Drive folder '{self._gdrive_folder}' (ID: '{f['id']}').{link_msg}")


class UploadEventHandler(FileSystemEventHandler, Closable):

    def __init__(self, fs_observer, snapshot_root, mq_device_topic):
        FileSystemEventHandler.__init__(self)
        Closable.__init__(self, connect_url=URL_WORKER_OBJECT_DETECTOR, socket_type=zmq.PUSH)

        self.last_modified = None
        self.device_events = dict()
        self._snapshot_root = snapshot_root

        self._fs_observer = fs_observer
        self.cloud_storage_socket = None
        self._cloud_storage_url = None

        self._mq_device_topic = mq_device_topic

    def start(self):
        # start the file system monitor
        self.cloud_storage_socket = self.get_socket(zmq.PUSH)
        self.cloud_storage_socket.connect(URL_WORKER_CLOUD_STORAGE)
        self._fs_observer.schedule(self, self._snapshot_root, recursive=True)

    @property
    def cloud_storage_url(self):
        return self._cloud_storage_url

    @cloud_storage_url.setter
    def cloud_storage_url(self, cloud_storage_url):
        self._cloud_storage_url = cloud_storage_url

    def add_image_dir(self, device_key, device_type, device_location, image_dir):
        if image_dir in self.device_events:
            raise RuntimeError(f'Image source label {device_location} is already configured.')
        # create pre-canned device events for reuse later
        self.device_events[image_dir] = DeviceEvent(
            device_key=device_key,
            device_type=device_type,
            device_location=device_location
        )

    def _get_device_event(self, event_directory):
        for image_dir, device_event in list(self.device_events.items()):
            if image_dir in event_directory:
                return copy.copy(device_event)
        return None

    @property
    def watched_dirs(self):
        return list(self.device_events.keys())

    # we listen to on-modified events because the file is
    # created and then written to subsequently.
    def on_modified(self, event):
        """
        :type event: FileModifiedEvent
        """
        super(UploadEventHandler, self).on_modified(event)
        # the file has been written to and has valid content
        if not event.is_directory:
            snapshot_path = event.src_path
            # de-duplication
            if snapshot_path != self.last_modified:
                self.last_modified = snapshot_path
            else:
                return
            # cross-check that we're in the right place
            if snapshot_path.startswith(self._snapshot_root):
                # image snapshot that can be mapped to a device?
                device_event = self._get_device_event(snapshot_path)
                if device_event:
                    log.info(f'{device_event} from {snapshot_path}')
                    file_base_name = os.path.splitext(os.path.basename(snapshot_path))[0]
                    if '_' in file_base_name:
                        # keep in sync with invocations of create_snapshot_path
                        date_string = ' '.join(file_base_name.split('_')[2:])
                    else:
                        date_string = file_base_name
                    device_event.timestamp = date_string
                    # do not notify for fetched image data
                    if 'fetch' not in snapshot_path:
                        event_payload = {
                            'active_devices': [device_event.dict],
                            'storage_url': self._cloud_storage_url
                        }
                        # start processing the image data
                        if file_base_name.endswith('.jpg') and 'object' not in snapshot_path:
                            self.socket.send_pyobj((
                                snapshot_path,
                                f'event.notify.{self._mq_device_topic}.{device_name}',
                                event_payload
                            ))
                    # upload the image snapshot to Cloud
                    self.cloud_storage_socket.send_pyobj((
                        snapshot_path,
                        device_event.timestamp
                    ))
                else:
                    log.warning(f'Ignored unmapped path event: {snapshot_path}')


class ObjectDetector(ZmqRelay):

    def __init__(self):
        ZmqRelay.__init__(self,
            name=self.__class__.__name__,
            source_zmq_url=URL_WORKER_OBJECT_DETECTOR,
            source_socket_type=zmq.PULL,
            sink_zmq_url=URL_WORKER_RABBIT_PUBLISHER,
            sink_socket_type=zmq.PUSH)

        self._rekog_enabled = app_config.getboolean('snapshots', 'object_detection_enabled')
        self._rekog = None

    def startup(self):
        self._rekog = boto3.client('rekognition', region_name=app_config.get('rekognition', 'region'))

    def process_message(self, zmq_socket):
        (snapshot_path, publisher_topic, publisher_data) = zmq_socket.recv_pyobj()
        active_device = publisher_data['active_devices'][0]
        device_label = active_device['device_label']
        image_bytes = None
        image_source = None
        if 'image' in active_device:
            image_bytes = active_device['image']
            image_source = 'fetch'
        elif snapshot_path is not None:
            wait_for_file_content(snapshot_path)
            with open(snapshot_path, 'rb') as img_file:
                image_bytes = img_file.read()
            image_source = 'upload'
        else:
            raise ValueError("No viable image data to use.")
        # find objects using the specified model
        event_detail = None
        if self._rekog_enabled:
            try:
                response = self._rekog.detect_labels(Image={'Bytes': image_bytes})
                log.debug(f'Rekognition response to {device_label} ({image_source}): {json.dumps(response)}')
                # find Person labels
                person_count = 0
                labels = list()
                if 'Labels' in response:
                    for detect_dict in response['Labels']:
                        label_name = detect_dict['Name']
                        label_confidence = detect_dict['Confidence']
                        labels.append((label_name, label_confidence))
                        if label_name == 'Person':
                            # if instances are provided, sum them
                            num_instances = len(detect_dict['Instances'])
                            if num_instances > 0:
                                person_count += num_instances
                            else:
                                person_count += 1
                    log.info(f'Rekognition finds {len(labels)} labels from {device_label} ({image_source}): {labels!s}')
                if person_count > 0:
                    additional_info = f'{person_count} person(s) and {len(labels)} things'
                    event_detail = f'{device_label} ({image_source}): {additional_info}.'
                    log.info(event_detail)
                    active_device['event_detail'] = additional_info
            except self._rekog.exceptions.InvalidImageFormatException:
                log.warning(f'Rekognition image format error.', exc_info=True)
            except EndpointConnectionError as e:
                raise ResourceWarning('Rekognition problem.') from e
            except Exception:
                log.exception(f'Rekognition error.')
        self.socket.send_pyobj((publisher_topic, publisher_data))


class SnapshotFTPHandler(FTPHandler):

    def on_login(self, username):
        log.info(f'{username} logged in.')
        with exception_handler(connect_url=URL_WORKER_OBJECT_DETECTOR, socket_type=zmq.PUSH, and_raise=False) as zmq_socket:
            log.info(f'User login {username}.')
            pass

    def on_file_sent(self, file):
        log.info(f'Sent {file}.')

    def on_file_received(self, file):
        log.info(f'Received {file}.')
        with exception_handler(connect_url=URL_WORKER_OBJECT_DETECTOR, socket_type=zmq.PUSH, and_raise=False) as zmq_socket:
            log.info(f'TODO for {file}.')
            pass

    def on_incomplete_file_received(self, file):
        log.info(f'Received partial file {file}. Removing...')
        os.remove(file)


class SnapshotFTPServer(AppThread):

    def __init__(self, root_dir, port_number=21):
        AppThread.__init__(self, name=self.__class__.__name__)
        self.root_dir = root_dir
        self.port_number = port_number
        self.server = None

    def close(self):
        try:
            if self.server is not None:
                self.server.close_all()
        except Exception:
            log.warning('Problem shutting down FTP server.', exc_info=True)

    def run(self):
        # Instantiate a dummy authorizer for managing 'virtual' users
        authorizer = DummyAuthorizer()

        # Define a new user having full r/w permissions and a read-only
        # anonymous user
        authorizer.add_user(
            username=creds.ftp_user,
            password=creds.ftp_pass,
            homedir=self.root_dir,
            perm='elradfmwMT')

        # Designate the FTP handler class
        handler = SnapshotFTPHandler
        handler.authorizer = authorizer

        # Define a customized banner (string returned when client connects)
        handler.banner = f'{APP_NAME} FTP.'

        # Specify a masquerade address and the range of ports to use for
        # passive connections.  Decomment in case you're behind a NAT.
        #handler.masquerade_address = '151.25.42.11'
        #handler.passive_ports = range(60000, 65535)

        # Instantiate FTP server class and listen on 0.0.0.0:21
        address = ('', self.port_number)
        self.server = FTPServer(address, handler)

        # set a limit for connections
        self.server.max_cons = 16
        self.server.max_cons_per_ip = 5

        log.info(f'Starting FTP server on listening on {address}')
        # start ftp server
        self.server.serve_forever()


def main():
    # control listener
    mq_server_address=app_config.get('rabbitmq', 'server_address')
    mq_exchange_name=app_config.get('rabbitmq', 'mq_exchange')
    mq_device_topic=app_config.get('rabbitmq', 'device_topic')
    mq_control_listener = ZMQListener(
        zmq_url=URL_WORKER_APP,
        mq_server_address=mq_server_address,
        mq_exchange_name=f'{mq_exchange_name}_control',
        mq_topic_filter=f'event.control.{mq_device_topic}',
        mq_exchange_type='direct')
    # RabbitMQ relay
    try:
        mq_relay = RabbitMQRelay(
            zmq_url=URL_WORKER_RABBIT_PUBLISHER,
            mq_server_address=mq_server_address,
            mq_exchange_name=mq_exchange_name,
            mq_topic_filter=mq_device_topic,
            mq_exchange_type='topic')
    except AMQPConnectionError as e:
        log.exception('RabbitMQ failure at startup.')
        die(exception=e)
        bye()
    # file system listener
    observer = Observer()
    observer.name = observer.__class__.__name__
    snapshot_root = app_config.get('snapshots', 'root_dir')
    upload_event_handler = UploadEventHandler(
        snapshot_root=snapshot_root,
        fs_observer=observer,
        mq_device_topic=mq_relay.device_topic)
    # construct the device representation
    input_types = dict(app_config.items('input_type'))
    input_locations = dict(app_config.items('input_location'))
    output_types = dict(app_config.items('output_type'))
    output_locations = dict(app_config.items('output_location'))
    device_info = dict()
    device_info['inputs'] = list()
    for field, input_type in list(input_types.items()):
        input_location = input_locations[field]
        device_key = f'{input_locations[field]} {input_type}'
        device_info['inputs'].append({
            'type': input_type,
            'location': input_location,
            'device_key': device_key
        })
        if input_type.lower() == 'camera':
            upload_event_handler.add_image_dir(
                device_key=device_key,
                device_type=input_type,
                device_location=input_location,
                image_dir=os.path.join(
                    app_config.get('snapshots', 'upload_dir'),
                    input_location.lower().replace(' ', '')))
    device_info['outputs'] = list()
    camera_profiles = {}
    for field, output_type in list(output_types.items()):
        output_device = {}
        if field in output_locations:
            output_location = output_locations[field]
            device_key = f'{output_location} {output_type}'
            output_device['location'] = output_location
        else:
            device_key = output_type
        output_device.update({
            'type': output_type,
            'device_key': device_key
        })
        device_info['outputs'].append(output_device)
        if output_type.lower() == 'camera':
            # now build the profile for internal use
            camera_profile = output_device.copy()
            camera_profile.update({
                'storage': os.path.join(
                    snapshot_root, 
                    app_config.get('snapshots', 'upload_dir'), 
                    output_location.lower().replace(' ', ''))
            })
            camera_profiles[device_key] = camera_profile
    log.info(f'Monitoring directories in {snapshot_root} for changes: {upload_event_handler.watched_dirs!s}')
    # object detection
    object_detector = None
    if app_config.getboolean('snapshots', 'object_detection_enabled'):
        object_detector = ObjectDetector()
    # ensure that auth is properly set up first
    try:
        google_drive_uploader = GoogleDriveUploader(
            gauth_creds_file=app_config.get('gdrive', 'creds_file'),
            gdrive_folder=app_config.get('gdrive', 'folder'))
        google_drive_archiver = GoogleDriveArchiver(
            gauth_creds_file=app_config.get('gdrive', 'creds_file'),
            gdrive_folder=app_config.get('gdrive', 'folder'),
            gdrive_folder_id=google_drive_uploader.cloud_storage_folder_id,
            gdrive_folder_url=google_drive_uploader.cloud_storage_url)
    except HttpLib2Error:
        log.warning('Google Drive will be unavailable until the next restart.', exc_info=True)
        # acceptable if GDrive setup attempted first
        google_drive_uploader = None
        google_drive_archiver = None
    except Exception as e:
        die(exception=e)
        bye()
    # tell the uploader about the Cloud storage URL
    cloud_storage_url = None
    if google_drive_uploader is not None:
        cloud_storage_url = google_drive_uploader.cloud_storage_url
    upload_event_handler.cloud_storage_url = cloud_storage_url
    snapshotter = Snapshot(
        camera_profiles=camera_profiles,
        cloud_storage_url=cloud_storage_url,
        mq_device_topic=mq_relay.device_topic)
    # FTP server
    ftp_server = SnapshotFTPServer(root_dir=app_config.get('snapshots', 'root_dir'))
    # start threads
    mq_control_listener.start()
    mq_relay.start()
    snapshotter.start()
    ftp_server.start()
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
        nanny = threading.Thread(name='nanny', target=thread_nanny, args=(signal_handler,))
        nanny.setDaemon(True)
        nanny.start()
        # start heartbeat loop
        publisher_socket.connect(URL_WORKER_RABBIT_PUBLISHER)
        while not threads.shutting_down:
            heartbeat_payload = {
                #TODO statistics
                'device_info': device_info
            }
            publisher_socket.send_pyobj((f'event.heartbeat.{mq_relay.device_topic}', heartbeat_payload))
            threads.interruptable_sleep.wait(HEARTBEAT_INTERVAL_SECONDS)
        raise RuntimeWarning("Shutting down...")
    except(KeyboardInterrupt, RuntimeWarning, ContextTerminated) as e:
        die()
        message = "Shutting down {}..."
        log.info(message.format('FTP server'))
        ftp_server.close()
        log.info(message.format('RabbitMQ control'))
        mq_control_listener.stop()
        log.info(message.format('RabbitMQ relay'))
        try:
            mq_relay.close()
        except (AMQPConnectionError, ConnectionClosedByBroker, StreamLostError) as e:
            log.warning(f'When closing: {e!s}')
        log.info(message.format('Application threads'))
        observer.stop()
        observer.join()
        upload_event_handler.close()
        # since this thread and the signal handler are one and the same
        publisher_socket.close()
    finally:
        zmq_term()
    bye()


if __name__ == "__main__":
    main()