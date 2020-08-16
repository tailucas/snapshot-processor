# snapshot_processor

Python project with ability to pull image snapshots from [Foscam](https://www.foscam.co.za/) IP cameras. The project supports upload and archive to Google Drive. The most recent extension to the project are the [dependencies needed to support face recognition](https://medium.com/@ageitgey/build-a-hardware-based-face-recognition-system-for-150-with-the-nvidia-jetson-nano-and-python-a25cb8c891fd).

## Notes for Balena Cloud

This project is structured as is for use with [Balena Cloud](https://www.balena.io/cloud/) and requires the *service variables* listed below to be set in order for the application to start properly. It is best to configure these at the level of the Balena application as opposed to the device because all device variables are local to each device.

Since this project uses additional remotes for [Git submodule](https://git-scm.com/book/en/v2/Git-Tools-Submodules) usage, a *push* to the Balena remote is insufficient to include all artifacts for the build. For this, you need to use the *balena push* command supplied by the [balena CLI](https://github.com/balena-io/balena-cli). Be sure to use the correct Balena application name when using *balena push* because the tool will not perform validation of the local context against the build deployed to the device.

```text
API_KEY_RESIN
APP_NAME
APP_ZMQ_PUBSUB_PORT
APP_ZMQ_PUSHPULL_PORT
AWS_ACCESS_KEY_ID
AWS_CONFIG_FILE
AWS_DEFAULT_REGION
AWS_SECRET_ACCESS_KEY
AWS_SHARED_CREDENTIALS_FILE
CAMERA_1_AUTH
CAMERA_1_URL
CAMERA_2_AUTH
CAMERA_2_URL
CAMERA_3_AUTH
CAMERA_3_URL
CAMERA_4_AUTH
CAMERA_4_URL
CAMERA_5_AUTH
CAMERA_5_URL
CAMERA_6_AUTH
CAMERA_6_URL
FTP_CREATE_DIRS
FTP_PASSWORD
FTP_UPLOAD_DIR
FTP_USER
GOOGLE_CLIENT_SECRETS
GOOGLE_DRIVE_FOLDER
INPUT_1_LOCATION
INPUT_1_TYPE
INPUT_2_LOCATION
INPUT_2_TYPE
INPUT_3_LOCATION
INPUT_3_TYPE
INPUT_4_LOCATION
INPUT_4_TYPE
INPUT_5_LOCATION
INPUT_5_TYPE
INPUT_6_LOCATION
INPUT_6_TYPE
OUTPUT_1_LOCATION
OUTPUT_1_TYPE
OUTPUT_2_LOCATION
OUTPUT_2_TYPE
OUTPUT_3_LOCATION
OUTPUT_3_TYPE
OUTPUT_4_LOCATION
OUTPUT_4_TYPE
OUTPUT_5_LOCATION
OUTPUT_5_TYPE
OUTPUT_6_LOCATION
OUTPUT_6_TYPE
REMOVE_KERNEL_MODULES
RSYSLOG_LOGENTRIES_SERVER
RSYSLOG_LOGENTRIES_TOKEN
RSYSLOG_SERVER
SENTRY_DSN
SSH_AUTHORIZED_KEY
```
