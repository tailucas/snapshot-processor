<a name="readme-top"></a>

[![Contributors][contributors-shield]][contributors-url]
[![Forks][forks-shield]][forks-url]
[![Stargazers][stars-shield]][stars-url]
[![Issues][issues-shield]][issues-url]
[![MIT License][license-shield]][license-url]

## About The Project

### Overview

**Note 1**: See my write-up on [Home Security Automation][blog-url] which provides an architectural overview of how this project works with others in my collection.

**Note 2**: While I use the word *Automation* in these projects, there is no integration with sensible frameworks like [openHAB][oh-url] or [Home Assistant][ha-url]... not yet at least. The goal behind this project was a learning opportunity by employing some specific technologies and opinion on design. The parts you'll most likely find useful are touch-points with third-party libraries like Flask, ZeroMQ and RabbitMQ, because seamless behavior comes after much trial and error.

This project supports fetching image snapshots from [Foscam][foscam-url] [IP cameras](https://duckduckgo.com/?q=ip+camera&t=h_&ia=web) and forwards this data to companion projects using a [RabbitMQ][rabbit-url] exchange. Most IP cameras will have varying degrees of "documented" methods to fetch image data on-demand. My use of Foscam is really based on the hardware that I currently use (at the time of writing, some of their web sites appear broken :shrug:).

Additional features include:

* a self-managed local cache of snapshot images which are asynchronously uploaded to `CloudStorage` instances which currently includes Google Drive.
* periodic management of Google Driver folders by organizing non-starred images into year-month-day folders.
* a local FTP server to support FTP-based IP camera image push using FTP basic-auth.
* object detection using [AWS Rekognition][awsr-url] with metadata-flags for human detection.

This application extends my own [boilerplate application][baseapp-url] hosted in [docker hub][baseapp-image-url] and takes its own git submodule dependency on my own [package][pylib-url].

There are also a [few branches](https://github.com/tailucas/snapshot-processor/branches) of this project, made for various platforms, one of which was to support the [Jetson Nano](https://developer.nvidia.com/embedded/jetson-nano-developer-kit) developer kit. This branch includes logic to control the PWM fan to run when detection is being done. The results weren't perfect so if you do intend to fork this branch, your mileage may vary. The main branch of this project works with [AWS Rekognition][awsr-url] and I've found it to be fast, rather accurate and returns useful metadata for person detection. The current logic makes use of the `DetectLabels` feature and carries associated [pricing](https://aws.amazon.com/rekognition/pricing/).

### Package Structure

The diagrams below show both the class inheritance structure. Here is the relationship between this project and my [pylib][pylib-url] submodule. For brevity, not everything is included such as the cloud storage management classes, but those are mostly self-contained. These are the non-obvious relationships.

![classes](/../../../../tailucas/tailucas.github.io/blob/main/assets/snapshot-processor/snapshot-processor_classes.png)

* `Snapshot` receives trigger messages and fetches image data from cameras and forwards this to `ObjectDetector` which delegates to the chosen detection mechanism to identify features in the images. Both of these are descendants of `ZMQRelay` for inter-thread message passing. You can see this flow illustrated in the diagram below.
* The `main` application thread contains an instance of `ZMQListener` which receives messages from the configured exchange and forwards them to `Snapshot`. After object detection, outgoing messages are sent via `RabbitMQRelay`.

See the diagram below for an example about how ZeroMQ is [used](https://github.com/tailucas/pylib/blob/ac05d39592c2264143ec4a37fe76b7e0369515bd/pylib/app.py#L26) as a message relay between threads.

![comms](/../../../../tailucas/tailucas.github.io/blob/main/assets/snapshot-processor/snapshot-processor_zmq-sockets.png)

* `ZMQListener` is responsible for receiving RabbitMQ messages from the network and then forwards these through a chain of `ZMQRelay` instances out the way back to the RabbitMQ publisher.

<p align="right">(<a href="#readme-top">back to top</a>)</p>

### Built With

Technologies that help make this project useful:

[![1Password][1p-shield]][1p-url]
[![AWS][aws-shield]][aws-url]
[![Docker][docker-shield]][docker-url]
[![Google Drive][gdrive-shield]][gdrive-url]
[![RabbitMQ][rabbit-shield]][rabbit-url]
[![Poetry][poetry-shield]][poetry-url]
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

Beyond the Python dependencies defined in the [Poetry configuration](pyproject.toml), the project carries hardcoded dependencies on [Sentry][sentry-url] and [1Password][1p-url] in order to function.

### Installation

* :stop_sign: This project uses [1Password Secrets Automation][1p-url] to store both application key-value pairs as well as runtime secrets. It is assumed that the connect server containers are already running on your environment. If you do not want to use this, then you'll need to fork this package and make the changes as appropriate. It's actually very easy to set up, but note that 1Password is a paid product with a free-tier for secrets automation.

* :construction: If a Google authorization token is not present on the local file system, [pydrive][gdrive-url] will initiate an [oauth workflow](https://pythonhosted.org/PyDrive/quickstart.html#authentication) which needs to be followed at least once in order to interact with Google Drive. During this flow, a URL will be logged that needs to be followed by the authorizing user at which point the client will store the token and it will be backed up regularly. As long as the token is backed up, it will remain valid until the authorization is revoked.

Here is an example of how this looks for my application and the generation of the docker-compose.yml relies on this step. Your secrets automation vault must contain an entry called `ENV.snapshot-processor` with these keys:

| Variable | Description | Example |
| --- | --- | --- |
| `APP_NAME` | Application name used in logging and metrics | `snapshot-processor` |
| `AWS_ALT_REGION` | AWS region (used for dual-region) | `eu-west-1` |
| `AWS_CONFIG_FILE` | AWS client configuration file | `/home/app/.aws/config` |
| `AWS_DEFAULT_REGION` | AWS region | `us-east-1` |
| `CRONITOR_MONITOR_KEY` | [Cronitor][cronitor-url] configuration key | *project specific* |
| `DEVICE_NAME` | Used for container host name. | `snapshot-processor` |
| `FTP_CREATE_DIRS` | IP-camera upload directories | `snapshots/cam1,snapshots/cam2` |
| `FTP_PASS` | FTP basic-auth password | *project specific* |
| `FTP_UPLOAD_DIR` | Upload directory within the FTP root | `uploads/snapshots` |
| `FTP_USER` | FTP basic-auth user | *project specific* |
| `GOOGLE_DRIVE_FOLDER` | Google Drive folder name | *project specific* |
| `HC_PING_URL` | [Healthchecks][healthchecks-url] URL | *project specific* |
| `INPUT_X_LOCATION` | Device location | *project specific* |
| `INPUT_X_TYPE` | Device type | `Camera` |
| `OBJECT_DETECTION_ENABLED` | Detect objects in snapshots | `true` |
| `OP_CONNECT_HOST` | 1Password connect server URL | *network specific* |
| `OP_CONNECT_TOKEN` | 1Password connect server token | *project specific* |
| `OP_VAULT` | 1Password vault | *project specific* |
| `OUTPUT_X_LOCATION` | Device location | *project specific* |
| `OUTPUT_X_TYPE` | Device type | `Camera` |
| `RABBITMQ_DEVICE_TOPIC` | Publication topic for this project | `snapshot` |
| `RABBITMQ_EXCHANGE` | Name of RabbitMQ exchange | `home_automation` |
| `RABBITMQ_SERVER_ADDRESS` | IP address of RabbitMQ exchange | *network specific* |
| `USER` | Process user | `app` |

With these configured, you are now able to build the application. Any variables referenced in the [application configuration][appconf-url] will be automatically replaced.

In addition to this, [additional runtime configuration](https://github.com/tailucas/snapshot-processor/blob/master/app/__main__.py#L52-L58) is used by the application, and also need to be contained within the secrets vault. With these configured, you are now able to run the application.

1. Clone the repo
   ```sh
   git clone https://github.com/tailucas/snapshot-processor.git
   ```
2. Verify that the git submodule is present.
   ```sh
   git submodule init
   git submodule update
   ```
4. Make the Docker runtime user and set directory permissions. :hand: Be sure to first review the Makefile contents for assumptions around user IDs for Docker.
   ```sh
   make user
   ```
5. Now generate the docker-compose.yml:
   ```sh
   make setup
   ```
6. And generate the Docker image:
   ```sh
   make build
   ```
7. If successful and the local environment is running the 1Password connect containers, run the application. For foreground:
   ```sh
   make run
   ```
   For background:
   ```sh
   make rund
   ```

<p align="right">(<a href="#readme-top">back to top</a>)</p>

<!-- USAGE EXAMPLES -->
## Usage

Running the application will:

* Start the RabbitMQ client.
* Start a [watchdog][watchdog-url] file system observer to detect FTP uploads.
* Start a Google Drive client to store snapshots with date-folder archive function.
* Start the main application loop `Snapshot` which waits for trigger events.

<p align="right">(<a href="#readme-top">back to top</a>)</p>

<!-- LICENSE -->
## License

Distributed under the MIT License. See [LICENSE](LICENSE) for more information.

<p align="right">(<a href="#readme-top">back to top</a>)</p>

<!-- ACKNOWLEDGMENTS -->
## Acknowledgments

* [Template on which this README is based](https://github.com/othneildrew/Best-README-Template)
* [All the Shields](https://github.com/progfay/shields-with-icon)

<p align="right">(<a href="#readme-top">back to top</a>)</p>

[![Hits](https://hits.seeyoufarm.com/api/count/incr/badge.svg?url=https%3A%2F%2Fgithub.com%2Ftailucas%2Fsnapshot-processor%2F&count_bg=%2379C83D&title_bg=%23555555&icon=&icon_color=%23E7E7E7&title=visits&edge_flat=true)](https://hits.seeyoufarm.com)

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
[poetry-url]: https://python-poetry.org/
[poetry-shield]: https://img.shields.io/static/v1?style=for-the-badge&message=Poetry&color=60A5FA&logo=Poetry&logoColor=FFFFFF&label=
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
