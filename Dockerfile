FROM tailucas/base-app:20230102
# for system/site packages
USER root
# override dependencies
COPY requirements.txt .
# apply override
RUN /opt/app/app_setup.sh
# FIXME: use docker-compose build args
RUN useradd -r -g app ftpuser
RUN echo "ftpuser:ftppass" | chpasswd
# switch to user
USER app
COPY config ./config
COPY settings.yaml .
COPY backup_auth_token.sh .
COPY ftp_setup.sh .
# remove base_app
RUN rm -f /opt/app/base_app
# add the project application
COPY snapshot_processor .
# override entrypoint
COPY app_entrypoint.sh .
CMD ["/opt/app/entrypoint.sh"]
