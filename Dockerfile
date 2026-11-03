# Odoo 20 supports Python 3.12 - 3.14 (odoo/release.py). requirements.txt pins
# its officially supported set against Ubuntu Noble, which ships Python 3.12.
FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1

# Runtime packages follow debian/control. Bookworm only ships postgresql-client
# 15, whose pg_dump refuses a PostgreSQL 16 server, so the client comes from PGDG.
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends ca-certificates curl; \
    curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc -o /usr/share/postgresql-pgdg.asc; \
    echo "deb [signed-by=/usr/share/postgresql-pgdg.asc] https://apt.postgresql.org/pub/repos/apt bookworm-pgdg main" \
        > /etc/apt/sources.list.d/pgdg.list; \
    arch="$(dpkg --print-architecture)"; \
    case "$arch" in \
        amd64) wkhtmltox_sha256=98ba0d157b50d36f23bd0dedf4c0aa28c7b0c50fcdcdc54aa5b6bbba81a3941d ;; \
        arm64) wkhtmltox_sha256=b6606157b27c13e044d0abbe670301f88de4e1782afca4f9c06a5817f3e03a9c ;; \
        *) echo "No wkhtmltox package for $arch" >&2; exit 1 ;; \
    esac; \
    curl -fsSL "https://github.com/wkhtmltopdf/packaging/releases/download/0.12.6.1-3/wkhtmltox_0.12.6.1-3.bookworm_${arch}.deb" \
        -o /tmp/wkhtmltox.deb; \
    echo "$wkhtmltox_sha256  /tmp/wkhtmltox.deb" | sha256sum -c -; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        fonts-dejavu-core \
        fonts-font-awesome \
        fonts-inconsolata \
        fonts-noto-core \
        fonts-roboto-unhinted \
        gsfonts \
        libldap-2.5-0 \
        libmagic1 \
        libpq5 \
        libsasl2-2 \
        postgresql-client-16 \
        /tmp/wkhtmltox.deb; \
    rm -f /tmp/wkhtmltox.deb; \
    apt-get purge -y --auto-remove curl; \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt /tmp/requirements.txt

# psycopg2 and python-ldap have no wheels, their build dependencies are removed
# once installed. requirements.txt only lists rl-renderPM for win32 (on Linux it
# comes with the deb package), but reportlab cannot render barcodes without it.
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends gcc libc6-dev libldap2-dev libpq-dev libsasl2-dev; \
    pip install --no-cache-dir -r /tmp/requirements.txt rl-renderPM==4.0.3; \
    apt-get purge -y --auto-remove gcc libc6-dev libldap2-dev libpq-dev libsasl2-dev; \
    rm -rf /var/lib/apt/lists/* /tmp/requirements.txt

# Only what the server needs: custom_addons is bind mounted by docker-compose.yml,
# so editing a module neither requires nor triggers a rebuild.
COPY odoo-bin /opt/odoo/odoo-bin
COPY odoo /opt/odoo/odoo
COPY addons /opt/odoo/addons
COPY docker/odoo.conf /etc/odoo/odoo.conf

RUN set -eux; \
    useradd --create-home --uid 1000 --shell /usr/sbin/nologin odoo; \
    mkdir -p /opt/odoo/custom_addons /var/lib/odoo; \
    chown odoo:odoo /var/lib/odoo

USER odoo
WORKDIR /opt/odoo
VOLUME ["/var/lib/odoo"]
EXPOSE 8069

# Called through python: the shebang of odoo-bin breaks on a CRLF checkout.
CMD ["python", "odoo-bin", "--config", "/etc/odoo/odoo.conf"]
