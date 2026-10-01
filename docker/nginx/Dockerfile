FROM nginx:1.30.4@sha256:5cf90903deda2c5981b8ad05e7617ac010e389f0dde0ac83487c02c509281de6

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get upgrade -y \
    && rm -rf /var/lib/apt/lists/*

# default.conf のログは日ごとのファイルでここへ書く。本番は nginx-logs ボリュームを
# 重ね、起動時にも作るが、ボリュームの無い `nginx -t` / `nginx -T`（CI）でも開けるようにする。
RUN mkdir -p /var/log/nginx/site && chown nginx:nginx /var/log/nginx/site

COPY ./nginx/default.conf /etc/nginx/conf.d/default.conf
COPY ./app /var/www/html
COPY ./lib /var/www/lib
