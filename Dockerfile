FROM python:trixie

RUN apt-get update && \
    apt-get install -y  \
        curl  \
        apt-transport-https \
        gnupg2 && \
    apt-get update -y && \
    ACCEPT_EULA=Y apt-get install -y \
        supervisor \
        nginx

WORKDIR /app
COPY . /app/

RUN pip install -r /app/requirements.txt && \
    mkdir -p /var/www/html/bot/static && \
    cp /app/nginx/nginx.conf /etc/nginx/nginx.conf

EXPOSE 80

COPY supervisord.conf /app/supervisord.conf

### LOCAL TESTING ONLY - comment out for production. ###
# ENV OAUTHLIB_INSECURE_TRANSPORT=1 
########################################################

CMD ["/usr/bin/supervisord", "-c", "/app/supervisord.conf"]