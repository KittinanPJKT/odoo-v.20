# Odoo

[![Build Status](https://runbot.odoo.com/runbot/badge/flat/1/master.svg)](https://runbot.odoo.com/runbot)
[![Tech Doc](https://img.shields.io/badge/master-docs-875A7B.svg?style=flat&colorA=8F8F8F)](https://www.odoo.com/documentation/master)
[![Help](https://img.shields.io/badge/master-help-875A7B.svg?style=flat&colorA=8F8F8F)](https://www.odoo.com/forum/help-1)
[![Nightly Builds](https://img.shields.io/badge/master-nightly-875A7B.svg?style=flat&colorA=8F8F8F)](https://nightly.odoo.com/)

Odoo is a suite of web based open source business apps.

The main Odoo Apps include an [Open Source CRM](https://www.odoo.com/page/crm),
[Website Builder](https://www.odoo.com/app/website),
[eCommerce](https://www.odoo.com/app/ecommerce),
[Warehouse Management](https://www.odoo.com/app/inventory),
[Project Management](https://www.odoo.com/app/project),
[Billing &amp; Accounting](https://www.odoo.com/app/accounting),
[Point of Sale](https://www.odoo.com/app/point-of-sale-shop),
[Human Resources](https://www.odoo.com/app/employees),
[Marketing](https://www.odoo.com/app/social-marketing),
[Manufacturing](https://www.odoo.com/app/manufacturing),
[...](https://www.odoo.com/)

Odoo Apps can be used as stand-alone applications, but they also integrate seamlessly so you get
a full-featured [Open Source ERP](https://www.odoo.com) when you install several Apps.

## Getting started with Odoo

For a standard installation please follow the [Setup instructions](https://www.odoo.com/documentation/master/administration/install/install.html)
from the documentation.

To learn the software, we recommend the [Odoo eLearning](https://www.odoo.com/slides),
or [Scale-up, the business game](https://www.odoo.com/page/scale-up-business-game).
Developers can start with [the developer tutorials](https://www.odoo.com/documentation/master/developer/howtos.html).

## Run with Docker

This runs Odoo 20 from the source in this repository, together with PostgreSQL 16.
You only need Docker: Python and PostgreSQL do not have to be installed on your machine.
This setup is for local development, see [Limitations](#limitations).

### Requirements

- Docker with the Compose plugin (check with `docker compose version`)
- Port 8069 free on your machine
- At least 5 GB of free disk space (the image alone is about 2.4 GB)

### Start Odoo

```bash
git clone -b 20.0 https://github.com/KittinanPJKT/odoo-v.20.git
cd odoo-v.20
docker compose up --build
```

The first build downloads all dependencies and takes several minutes. Odoo is ready
when the log shows `HTTP service running on 0.0.0.0:8069`.

Open <http://localhost:8069> and fill in the form to create your first database.

The warning `invalid addons directory '/opt/odoo/custom_addons'` is expected as long as
`custom_addons/` has no modules.

### Develop your own modules

Put your modules in `custom_addons/`. This folder is mounted into the container, so you
do not need to rebuild the image after changing a module.

| Change | What to do |
| --- | --- |
| Added a new module | Run `docker compose restart odoo`, then use **Update Apps List** in the Apps menu (requires developer mode) |
| Changed Python code | Run `docker compose restart odoo` |
| Changed views, data or models | Run the update command below, then `docker compose restart odoo` |

```bash
docker compose exec odoo python odoo-bin --config /etc/odoo/odoo.conf -d <database> -u <module> --stop-after-init --no-http
```

### Useful commands

| Command | Purpose |
| --- | --- |
| `docker compose logs -f odoo` | Follow the Odoo log |
| `docker compose down` | Stop Odoo and keep your data |
| `docker compose up --build` | Start again, and apply changes to the Odoo source or `requirements.txt` |
| `docker compose down -v` | Stop Odoo and **permanently delete all databases and files** |

Databases and uploaded files are stored in the named volumes `db-data` and `odoo-data`.
To back up a database including its files, use <http://localhost:8069/web/database/manager>.

### Limitations

- **Do not expose this setup to other machines.** Odoo is only reachable from your own
  machine, and the database password is `odoo` unless you set `POSTGRES_PASSWORD` before
  the first start.
- **The master password is not saved.** The container cannot write to its configuration
  file, so a master password only lasts until Odoo restarts. After that, the database
  manager accepts any password.
- **Keep port 8069.** If you only change the host port in `docker-compose.yml`, PDF reports
  lose their styling without any visible error. If the port is taken, stop whatever is
  using it.
- **Right-to-left languages** such as Arabic and Hebrew may not display correctly, because
  `rtlcss` is not installed.
- Tested on Windows 11 with Docker Desktop on x86_64. The arm64 build has not been tested.

## Security

If you believe you have found a security issue, check our [Responsible Disclosure page](https://www.odoo.com/security-report)
for details and get in touch with us via email.
