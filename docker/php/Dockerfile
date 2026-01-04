FROM php:8.3-fpm

# Install dependencies if needed (e.g. pdo_mysql)
# RUN docker-php-ext-install pdo_mysql

COPY ./app /var/www/html
COPY ./lib /var/www/lib

# Ensure permissions
RUN chown -R www-data:www-data /var/www/html /var/www/lib
