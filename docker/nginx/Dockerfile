FROM nginx:1.30.4@sha256:5cf90903deda2c5981b8ad05e7617ac010e389f0dde0ac83487c02c509281de6

COPY ./nginx/default.conf /etc/nginx/conf.d/default.conf
COPY ./app /var/www/html
COPY ./lib /var/www/lib
