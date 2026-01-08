<a name="readme-top"></a>

[![Contributors][contributors-shield]][contributors-url]
[![Forks][forks-shield]][forks-url]
[![Stargazers][stars-shield]][stars-url]
[![Issues][issues-shield]][issues-url]
[![MIT License][license-shield]][license-url]

## About The Project

### Overview

**Note 1**: See my write-up on [Home Security Automation][blog-url] which provides an architectural overview of how this project works with others in my collection.

**Note 2**: While I use the word *Automation* in these projects, there is no integration with sensible frameworks like [openHAB][oh-url] or [Home Assistant][ha-url]... not yet at least. The goal behind this project was a learning opportunity by employing specific technologies and design patterns. The parts most likely to be useful are integrations with libraries like ZeroMQ, RabbitMQ, and cloud storage, where seamless behavior comes after much trial and error.

### Core Functionality

This project is a **sophisticated snapshot processor** that fetches image snapshots from IP cameras (particularly [Foscam][foscam-url]) and orchestrates multi-threaded image processing pipelines. It extends [base-app][baseapp-url] with GitHub repository at https://github.com/tailucas/base-app, and takes a git submodule dependency on [pylib][pylib-url].

**Key Features:**

* **IP Camera Integration**: Supports fetching snapshots from Foscam and other IP cameras via HTTP
* **Message-Driven Architecture**: Routes snapshots through [RabbitMQ][rabbit-url] exchange for multi-device coordination
* **Multiple Object Detection Engines** (feature flags):
  - **AWS Rekognition** (cloud-based): Fast, accurate human/object detection with metadata
  - **YOLOv8** (local): On-device object detection using PyTorch/Ultralytics for privacy/cost
* **Cloud Storage Integration**: Asynchronous uploads to Google Drive with automatic organization
* **Google Drive Management**: Periodic archival of snapshots into year-month-day folder structure, excluding starred images
* **Local FTP Server**: FTP-based snapshot push support with basic authentication for IP cameras
* **File System Watching**: Watchdog-based file system observer to detect new snapshots
* **Image Caching**: LRU cache for snapshot image data to optimize processing
* **AWS Integration**: Rekognition for object detection, CloudWatch metrics posting
* **Resilient Message Handling**: Multi-threaded ZeroMQ relay architecture with error recovery

**Platform Variants:**
- **Main branch**: Uses [AWS Rekognition][awsr-url] for cloud-based object detection (fast, accurate, priced per-image)
- **Jetson Nano branch**: Includes PWM fan control and local YOLOv8 detection (for edge devices)

Detection results include human/person confidence scores and detailed metadata for automation decision-making.

### Architecture & Design

This 1277-line Python application demonstrates patterns for building asynchronous, multi-threaded image processing pipelines.

**Core Components** (`app/__main__.py`):

* **`CameraConfig`** (line 113): Configuration holder for camera parameters including snapshot URL, authentication, and image format
* **`FileType`**: Enumeration for file handling types (SNAPSHOT, ARCHIVE, etc.)
* **`Snapshot`** (line 204): Main snapshot fetching thread extending `ZmqRelay`. Receives camera trigger events, fetches images from HTTP endpoints, caches locally, and relays to object detection
* **`DeviceEvent`** (line 342): Message wrapper for device input/output events with metadata (device ID, location, type, timestamp)
* **`CloudStorage`** (line 402): Abstract base class for cloud storage implementations
* **`GoogleDriveManager`** (line 408): Base class for Google Drive operations with OAuth token management
* **`GoogleDriveArchiver`** (line 490): Background thread for organizing Google Drive snapshots into year-month-day folders, skipping starred images
* **`GoogleDriveUploader`** (line 621): Async uploader for locally cached snapshots to Google Drive with queue management and retry logic
* **`UploadEventHandler`** (line 765): File system event handler (watchdog) for detecting new local snapshots
* **`ObjectDetector`** (line 902): Multi-detector abstraction supporting both AWS Rekognition and local YOLOv8 detection
* **`FTPServer`** (separate file): Implements FTP server for camera image push with user authentication and event logging

**Message Flow:**
1. Device event arrives via RabbitMQ → triggers `Snapshot` to fetch image
2. `Snapshot` fetches from camera HTTP endpoint, caches locally, relays to `ObjectDetector`
3. `ObjectDetector` processes (cloud or local) and returns detection results
4. Results sent back through RabbitMQ to other devices
5. `GoogleDriveUploader` asynchronously uploads cache to Google Drive
6. `GoogleDriveArchiver` periodically organizes Drive folders

**Configuration:**
- 8 configurable camera inputs (`INPUT_1` to `INPUT_8`)
- 8 configurable device outputs (`OUTPUT_1` to `OUTPUT_8`)
- Location-based device labeling
- Feature flags for object detection, cloud detection, local detection, storage management

**Technology Patterns:**

The application demonstrates professional integration patterns:
- **ZeroMQ**: Thread-safe inter-component messaging with relay pattern
- **RabbitMQ**: External device messaging queue
- **PyDrive**: Google Drive OAuth authentication and file management
- **Watchdog**: File system event monitoring for uploads
- **AWS Rekognition**: Cloud-based object/human detection (paid)
- **YOLOv8 + PyTorch**: Local neural network detection (free, privacy-focused)
- **Pillow**: Image manipulation and validation
- **Cachetools**: LRU image caching for efficient memory use
- **pyftpdlib**: FTP server for camera integrations
- **boto3**: AWS SDK for metrics and authentication
- **Sentry SDK**: Production error tracking with threading integration
- **tailucas-pylib**: Shared architectural patterns and utilities

See [tailucas-pylib][pylib-url] for shared patterns and base-app for the container foundation.

<p align="right">(<a href="#readme-top">back to top</a>)</p>

### Built With

Technologies that help make this project useful:

[![1Password][1p-shield]][1p-url]
[![AWS][aws-shield]][aws-url]
[![Docker][docker-shield]][docker-url]
[![Google Drive][gdrive-shield]][gdrive-url]
[![RabbitMQ][rabbit-shield]][rabbit-url]
[![Python][python-shield]][python-url]
[![Sentry][sentry-shield]][sentry-url]
[![ZeroMQ][zmq-shield]][zmq-url]

Also:

* [pydrive][gdrive-url]
* [pyftpdlib][pyftp-url]
* [watchdog][watchdog-url]

<p align="right">(<a href="#readme-top">back to top</a>)</p>


<!-- GETTING STARTED -->
## Getting Started

Here is some detail about the intended use of this package.

### Prerequisites

Beyond the Python dependencies defined in [pyproject.toml](pyproject.toml), the project requires:

* **[1Password Secrets Automation][1p-url]**: Runtime credential and configuration management (paid service with free tier)
* **[Sentry][sentry-url]**: Error tracking and monitoring (free tier available)
* **[RabbitMQ][rabbit-url]**: Message broker for device communication (self-hosted or managed service)
* **[Google Drive API][gdrive-url]**: Cloud snapshot storage and organization (free tier available)
* **[AWS Rekognition][awsr-url]**: Cloud object detection (optional, paid per-image)
* **IP Cameras**: Foscam or similar HTTP-based snapshot API (for fetching images)

Optional services:
* **Jetson Nano**: For local YOLOv8 detection with PWM fan control (see branches)
* **PyTorch**: For local YOLOv8 model inference (CPU or GPU)

### Required Tools

Install these tools and ensure they're on your environment `$PATH`:

* **`task`**: Build orchestration - https://taskfile.dev/installation/#install-script
* **`docker`** and **`docker-compose`**: Container runtime and composition - https://docs.docker.com/engine/install/
* **`uv`**: Python package manager - https://docs.astral.sh/uv/getting-started/installation/

For local development (optional):
* **`python3`**: Python 3.12+ runtime

### Installation

0. **:stop_sign: Prerequisites - 1Password Secrets Automation Setup**

   This project relies on [1Password Secrets Automation][1p-url] for configuration and credential management. A 1Password Connect server container must be running in your environment.

   Your 1Password Secrets Automation vault must contain an entry called `ENV.snapshot-processor` with the following configuration variables:

   | Variable | Purpose | Example |
   |---|---|---|
   | `APP_NAME` | Application identifier for logging | `snapshot-processor` |
   | `AWS_ALT_REGION` | Alternate AWS region (dual-region setup) | `eu-west-1` |
   | `AWS_CONFIG_FILE` | AWS configuration file path | `/home/app/.aws/config` |
   | `AWS_DEFAULT_REGION` | Primary AWS region for Rekognition | `us-east-1` |
   | `CRONITOR_MONITOR_KEY` | Cronitor health check API key | *specific to your account* |
   | `DEVICE_NAME` | Container hostname | `snapshot-processor` |
   | `FTP_CREATE_DIRS` | Directories to create in FTP root | `snapshots/cam1,snapshots/cam2` |
   | `FTP_PASS` | FTP server password (basic auth) | *project specific* |
   | `FTP_UPLOAD_DIR` | FTP upload root directory | `uploads/snapshots` |
   | `FTP_USER` | FTP server username (basic auth) | *project specific* |
   | `GOOGLE_DRIVE_FOLDER` | Google Drive folder name for snapshots | *project specific* |
   | `HC_PING_URL` | Healthchecks.io URL for health monitoring | *specific to your check* |
   | `INPUT_1_LOCATION` through `INPUT_8_LOCATION` | Camera location names | *device specific* |
   | `INPUT_1_TYPE` through `INPUT_8_TYPE` | Camera types | `Camera` |
   | `MINIMUM_HUMAN_CONFIDENCE` | Confidence threshold for human detection | `0.8` |
   | `OBJECT_DETECTION_ENABLED` | Enable object detection feature | `true` |
   | `OBJECT_DETECTION_MODEL` | Detection model type | `yolov8n` |
   | `OP_CONNECT_HOST` | 1Password Connect server URL | `http://1password-connect:8080` |
   | `OP_CONNECT_TOKEN` | 1Password Connect API token | *specific to your server* |
   | `OP_VAULT` | 1Password vault ID | *specific to your vault* |
   | `OUTPUT_1_LOCATION` through `OUTPUT_8_LOCATION` | Output device location names | *device specific* |
   | `OUTPUT_1_TYPE` through `OUTPUT_8_TYPE` | Output device types | `Camera` |
   | `RABBITMQ_DEVICE_TOPIC` | RabbitMQ topic for snapshot messages | `snapshot` |
   | `RABBITMQ_EXCHANGE` | RabbitMQ exchange name | `home_automation` |
   | `RABBITMQ_SERVER_ADDRESS` | RabbitMQ broker IP/hostname | `192.168.1.100` |
   | `RUN_FTP_SERVER` | Enable FTP server feature | `true` |
   | `USER` | Process user in container | `app` |

   **Additional Credentials** (stored separately in 1Password):
   - `Google/oath/client_secret`: Google OAuth 2.0 client credentials for Drive API
   - `FTP/username`: FTP server username
   - `FTP/password`: FTP server password
   - `Cronitor/password`: Cronitor API key
   - `Sentry/__APP_NAME__/dsn`: Sentry DSN

1. **Clone Repository and Initialize Submodules**

   ```bash
   git clone https://github.com/tailucas/snapshot-processor.git
   cd snapshot-processor
   git submodule init
   git submodule update
   ```

2. **Create Data Directory and Set Permissions**

   ```bash
   task datadir
   ```

   Creates `/data` directory for FTP uploads and Google Drive token storage (UID 999).

3. **Configure Runtime Environment**

   ```bash
   task configure
   ```

   Generates `.env` from `base.env` template and 1Password secrets.

4. **Build Docker Image**

   ```bash
   task build
   ```

   Multi-stage Docker build process:
   - Extends `tailucas/base-app:latest` base image
   - Installs locale support
   - Adds backup scripts
   - Configures cron jobs
   - Installs uv-managed Python dependencies
   - Installs PyTorch, YOLOv8, OpenCV, Ultralytics
   - Copies application code
   - Configures FTP server (if enabled)

5. **Run Application**

   **Foreground (interactive, logs to console)**:
   ```bash
   task run
   ```

   **Background (detached, logs to syslog)**:
   ```bash
   task rund
   ```

   The application will:
   - Initialize RabbitMQ client for device messaging
   - Start watchdog file system observer for FTP upload detection
   - Authenticate to Google Drive (OAuth flow if token missing)
   - Initialize Snapshot fetcher with camera configuration
   - Start ObjectDetector (AWS Rekognition or YOLOv8)
   - Start GoogleDriveUploader for async uploads
   - Start GoogleDriveArchiver for folder organization
   - Launch FTP server (if enabled)
   - Run main event loop

<p align="right">(<a href="#readme-top">back to top</a>)</p>

## Application Components

### Snapshot Fetching

The `Snapshot` class (line 204) manages camera snapshot fetching:
- Receives trigger events from RabbitMQ for specific cameras
- Constructs camera-specific snapshot URLs (Foscam-compatible by default)
- Fetches images via HTTP with retry logic and timeout handling
- Validates image data (size, format, MD5 checksum)
- Caches locally with LRU cache for efficiency
- Relays images to ObjectDetector via ZeroMQ

Supports multiple camera models through configurable `default_command` and `default_image_format`.

### Object Detection

The `ObjectDetector` class (line 902) provides detection abstraction:

**AWS Rekognition Mode** (cloud-based):
- Sends images to AWS Rekognition `DetectLabels` API
- Identifies objects, scenes, and human presence with confidence scores
- Returns structured metadata for automation decisions
- Scales automatically with load (pay-per-image pricing)
- Primary detection for main branch

**YOLOv8 Mode** (local, CPU/GPU):
- Uses Ultralytics YOLOv8 neural network model
- Runs inference on local hardware (CPU or GPU)
- Identifies human presence with confidence thresholds
- Privacy-focused (no cloud dependency)
- Included in optional `coding` dependency group
- Suitable for Jetson Nano and edge deployments

Feature flags control which detector is active.

### Google Drive Integration

**GoogleDriveUploader** (line 621):
- Monitors local cache for new snapshots
- Asynchronously uploads to configured Google Drive folder
- Handles OAuth authentication with token caching
- Implements retry logic for failed uploads
- Manages upload queue to avoid rate limiting

**GoogleDriveArchiver** (line 490):
- Periodically scans Google Drive folder
- Organizes snapshots into year/month/day folder structure
- Skips (preserves) starred images
- Cleans up old flat-structure files

**Google Drive OAuth Setup**:
- First run initiates OAuth flow via PyDrive
- User follows URL to authorize Google Drive access
- Token saved to `/data/snapshot_processor_creds`
- Backed up regularly (see cron jobs)

### FTP Server

The `SnapshotFTPHandler` (app/ftp_server.py) provides FTP-based upload:
- IP cameras can push snapshots directly via FTP
- Basic authentication with configurable username/password
- Creates configured upload directories automatically
- Logs all FTP events (login, upload, download)
- Optional feature (controlled by `RUN_FTP_SERVER` flag)
- Listens on port 21 (exposed in docker-compose.yml)

### File System Monitoring

The `UploadEventHandler` (line 765) watches for local FTP uploads:
- Uses watchdog library to monitor `/data/ftp` directory
- Detects file creation, movement, and completion
- De-duplicates rapid file system events
- Validates file integrity before processing
- Triggers snapshot processing pipeline

## Build System

### Task CLI (Taskfile.yml)

Primary build orchestration:

- `task build` - Build Docker image with all dependencies and application code
- `task run` - Run container in foreground with full log output
- `task rund` - Run container detached (persists after terminal close)
- `task configure` - Generate .env and docker-compose.yml from 1Password secrets
- `task datadir` - Create data directory with proper permissions (UID/GID 999)
- `task python` - Setup Python virtual environment with uv
- `task push` - Push built image to Docker Hub/registry

### Dockerfile

Extends `tailucas/base-app:latest` with:
- Locale generation (LANG/LANGUAGE args)
- Backup authentication token scripts
- Cron job configuration (backup_auth_token, cleanup_snapshots)
- Python dependencies (uv-managed via pyproject.toml)
- PyTorch (CPU or GPU variants)
- OpenCV (headless variant for containers)
- YOLOv8 / Ultralytics
- Supporting libraries (numpy, PIL, matplotlib, polars, pyyaml)
- FTP server configuration
- Application code (app/__main__.py, app/ftp_server.py)
- Runs as user `app` (UID 999)

### Dependencies

**Python** (`pyproject.toml`, managed via uv):
- `tailucas-pylib>=0.5.6` - Shared utilities, threading, ZeroMQ, RabbitMQ
- `pydrive>=1.3.1` - Google Drive API integration
- `pyftpdlib>=2.0.1` - FTP server implementation
- `cachetools>=6.2.0` - LRU image caching
- `watchdog>=6.0.0` - File system event monitoring
- `awscli>=1.42.35` - AWS CLI for Rekognition integration
- `pillow>=11.3.0` - Image processing
- `boto3` (via awscli) - AWS SDK for Rekognition

**Optional/Development** (`coding` dependency group):
- `torch>=2.8.0` - PyTorch ML framework (CPU variant in container)
- `torchvision>=0.23.0` - Computer vision model hub
- `ultralytics>=8.3.202` - YOLOv8 model and training
- Custom PyTorch index: https://download.pytorch.org/whl/cpu

### Configuration

**config/app.conf** (INI format):
- `[app]`: Shutdown grace period, device name, monitor keys
- `[creds]`: Sentry DSN and Cronitor password paths
- `[rabbitmq]`: Exchange, topic, server address
- `[snapshots]`: FTP root, upload directory, detection flags
- `[rekognition]`: AWS region for Rekognition
- `[object_detection]`: YOLOv8 model selection
- `[human_detection]`: Confidence threshold
- `[gdrive]`: Google Drive folder, token path
- `[camera]`: Snapshot command, image format
- `[input_location]` / `[input_type]`: 8 camera inputs
- `[output_location]` / `[output_type]`: 8 device outputs

**Docker Compose**:
- Port 21: FTP server
- Syslog logging to Docker host
- 1Password Connect secret integration
- Volume mounts: `/data` for FTP and tokens, `/dev/log` for syslog

## Features & Capabilities

### Image Processing Pipeline

1. **Trigger**: Device event arrives via RabbitMQ
2. **Fetch**: Snapshot thread connects to IP camera via HTTP
3. **Validate**: Checks image size, format, MD5 checksum
4. **Cache**: Stores locally in LRU memory cache
5. **Detect**: Sends to Rekognition or YOLOv8 detector
6. **Store**: GoogleDriveUploader queues for async upload
7. **Publish**: Results sent back through RabbitMQ
8. **Archive**: GoogleDriveArchiver organizes on Drive

### Cron Jobs

- `backup_auth_token`: Periodic Google Drive token backup to data directory
- `cleanup_snapshots`: Cleanup old local snapshot cache

### Error Handling

The application handles various failure scenarios:
- HTTP connection errors (retries with backoff)
- RabbitMQ connection loss (auto-reconnect with exponential backoff)
- Google Drive API errors (retry with queue persistence)
- Image validation failures (delete invalid, retry later)
- AWS Rekognition throttling (request queuing)
- File system errors (log and continue)
- SSL/TLS errors (comprehensive exception handling)

### Multi-Region Support

AWS Rekognition can use dual regions:
- Primary region: `AWS_DEFAULT_REGION`
- Alternate region: `AWS_ALT_REGION`
- Useful for failover or regional optimization

### Feature Flags

Controlled via environment/config:
- `FEATURE_FLAG_OBJECT_DETECTION`: Master switch for all detection
- `FEATURE_FLAG_CLOUD_OBJECT_DETECTION`: AWS Rekognition
- `FEATURE_FLAG_LOCAL_OBJECT_DETECTION`: YOLOv8
- `FEATURE_FLAG_CLOUD_STORAGE_MANAGEMENT`: Google Drive archival

<!-- LICENSE -->
## License

Distributed under the MIT License. See [LICENSE](LICENSE) for more information.

<p align="right">(<a href="#readme-top">back to top</a>)</p>

<!-- ACKNOWLEDGMENTS -->
## Acknowledgments

* [Template on which this README is based](https://github.com/othneildrew/Best-README-Template)
* [All the Shields](https://github.com/progfay/shields-with-icon)

<p align="right">(<a href="#readme-top">back to top</a>)</p>

<!-- MARKDOWN LINKS & IMAGES -->
<!-- https://www.markdownguide.org/basic-syntax/#reference-style-links -->
[contributors-shield]: https://img.shields.io/github/contributors/tailucas/snapshot-processor.svg?style=for-the-badge
[contributors-url]: https://github.com/tailucas/snapshot-processor/graphs/contributors
[forks-shield]: https://img.shields.io/github/forks/tailucas/snapshot-processor.svg?style=for-the-badge
[forks-url]: https://github.com/tailucas/snapshot-processor/network/members
[stars-shield]: https://img.shields.io/github/stars/tailucas/snapshot-processor.svg?style=for-the-badge
[stars-url]: https://github.com/tailucas/snapshot-processor/stargazers
[issues-shield]: https://img.shields.io/github/issues/tailucas/snapshot-processor.svg?style=for-the-badge
[issues-url]: https://github.com/tailucas/snapshot-processor/issues
[license-shield]: https://img.shields.io/github/license/tailucas/snapshot-processor.svg?style=for-the-badge
[license-url]: https://github.com/tailucas/snapshot-processor/blob/master/LICENSE

[blog-url]: https://tailucas.github.io/update/2023/06/18/home-security-automation.html

[baseapp-url]: https://github.com/tailucas/base-app
[baseapp-image-url]: https://hub.docker.com/repository/docker/tailucas/base-app/general
[pylib-url]: https://github.com/tailucas/pylib

[ha-url]: https://www.home-assistant.io/
[oh-url]: https://www.openhab.org/docs/

[appconf-url]: https://github.com/tailucas/snapshot-processor/blob/master/config/app.conf
[foscam-url]: https://duckduckgo.com/?q=foscam&t=h_

[1p-url]: https://developer.1password.com/docs/connect/
[1p-shield]: https://img.shields.io/static/v1?style=for-the-badge&message=1Password&color=0094F5&logo=1Password&logoColor=FFFFFF&label=
[aws-url]: https://aws.amazon.com/
[aws-shield]: https://img.shields.io/static/v1?style=for-the-badge&message=Amazon+AWS&color=232F3E&logo=Amazon+AWS&logoColor=FFFFFF&label=
[awsr-url]: https://aws.amazon.com/rekognition/
[cronitor-url]: https://cronitor.io/
[docker-shield]: https://img.shields.io/static/v1?style=for-the-badge&message=Docker&color=2496ED&logo=Docker&logoColor=FFFFFF&label=
[docker-url]: https://www.docker.com/
[gdrive-shield]: https://img.shields.io/static/v1?style=for-the-badge&message=Google+Drive&color=4285F4&logo=Google+Drive&logoColor=FFFFFF&label=
[gdrive-url]: https://pythonhosted.org/PyDrive/
[healthchecks-url]: https://healthchecks.io/
[pyftp-url]: https://pypi.org/project/pyftpdlib/
[python-url]: https://www.python.org/
[python-shield]: https://img.shields.io/static/v1?style=for-the-badge&message=Python&color=3776AB&logo=Python&logoColor=FFFFFF&label=
[rabbit-url]: https://www.rabbitmq.com/
[rabbit-shield]: https://img.shields.io/static/v1?style=for-the-badge&message=RabbitMQ&color=FF6600&logo=RabbitMQ&logoColor=FFFFFF&label=
[sentry-url]: https://sentry.io/
[sentry-shield]: https://img.shields.io/static/v1?style=for-the-badge&message=Sentry&color=362D59&logo=Sentry&logoColor=FFFFFF&label=
[watchdog-url]: https://pypi.org/project/watchdog/
[zmq-url]: https://zeromq.org/
[zmq-shield]: https://img.shields.io/static/v1?style=for-the-badge&message=ZeroMQ&color=DF0000&logo=ZeroMQ&logoColor=FFFFFF&label=
